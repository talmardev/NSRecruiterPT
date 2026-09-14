"""Orquestracao dos componentes assincronos: coletor, validador, emissor e dashboard."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

import httpx

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter, TelegramRateLimiter
from nsrecruiter.api.shards import fetch_region_nations
from nsrecruiter.backup import correr_backups_periodicos
from nsrecruiter.collector import run_collector
from nsrecruiter.config import Config
from nsrecruiter.dashboard import run_dashboard
from nsrecruiter.dispatcher import run_dispatcher
from nsrecruiter.logging_setup import LogEntry
from nsrecruiter.queue_expiry import run_queue_expiry_loop
from nsrecruiter.runtime_status import RuntimeStatus
from nsrecruiter.utils import iso_to_unix_timestamp
from nsrecruiter.validator import run_validator_loop

logger = logging.getLogger("nsrecruiter.runtime")

_DISPATCHER_GRACE_PERIOD_SECONDS = 20.0


async def run_app(config: Config, log_buffer: "deque[LogEntry]", dry_run: bool = False) -> None:
    connection = db.connect(config.db_path)
    db.init_schema(connection)

    http_client = httpx.AsyncClient(timeout=15.0)
    general_limiter = GeneralRateLimiter()
    api_client = NsApiClient(config.user_agent, general_limiter, http_client=http_client)

    # time.time (relogio de parede), nao time.monotonic: o cooldown de envio tem de
    # sobreviver a um restart, e o monotonic reinicia a cada arranque do processo.
    tg_limiter = TelegramRateLimiter(config.send_interval_seconds, clock=time.time)
    last_sent_iso = db.get_last_successful_send_at(connection)
    if last_sent_iso is not None:
        tg_limiter.restore_last_sent_at(iso_to_unix_timestamp(last_sent_iso))
        logger.info("Cooldown de envio restaurado a partir do ultimo envio bem sucedido (%s).", last_sent_iso)

    if dry_run:
        logger.info("MODO DRY-RUN: nenhuma chamada a a=sendTG sera feita.")

    if config.priority_flag_countries:
        logger.info(
            "Prioridade por bandeira ativa para: %s.", ", ".join(config.priority_flag_countries)
        )

    try:
        region_members = frozenset(await fetch_region_nations(api_client, config.region))
        logger.info("Membros atuais da regiao carregados: %d.", len(region_members))
    except Exception:
        logger.exception("Falha ao carregar a lista de membros da regiao; a continuar com uma lista vazia.")
        region_members = frozenset()

    runtime_status = RuntimeStatus()
    start_time = time.monotonic()

    collector_task = asyncio.create_task(
        run_collector(connection, api_client, http_client, config.user_agent), name="coletor"
    )
    validator_task = asyncio.create_task(
        run_validator_loop(connection, api_client, config, region_members), name="validador"
    )
    expiry_task = asyncio.create_task(run_queue_expiry_loop(connection), name="expirador")
    dispatcher_task = asyncio.create_task(
        run_dispatcher(connection, api_client, tg_limiter, config, runtime_status, dry_run), name="emissor"
    )
    dashboard_task = asyncio.create_task(
        run_dashboard(
            connection, api_client, config, general_limiter, tg_limiter, runtime_status,
            log_buffer, start_time, dry_run,
        ),
        name="dashboard",
    )

    tarefa_backup: asyncio.Task[None] | None = None
    if config.pasta_backup is not None:
        assert config.intervalo_backup_horas is not None  # garantido por load_config
        logger.info(
            "Backup automatico ativo: a cada %gh, em %s.", config.intervalo_backup_horas, config.pasta_backup
        )
        tarefa_backup = asyncio.create_task(
            correr_backups_periodicos(connection, config.db_path, config.pasta_backup, config.intervalo_backup_horas),
            name="backup",
        )

    tasks = {collector_task, validator_task, expiry_task, dispatcher_task, dashboard_task}
    if tarefa_backup is not None:
        tasks.add(tarefa_backup)
    try:
        # Qualquer uma a terminar primeiro (normalmente o dashboard, quando 'q' e premido)
        # avanca para a limpeza -- gather() ficaria bloqueado a espera das outras, que nunca acabam sozinhas.
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for finished in done:
            exc = finished.exception()
            if exc is not None:
                raise exc
    finally:
        logger.info("A encerrar de forma limpa...")
        runtime_status.request_shutdown()
        collector_task.cancel()
        validator_task.cancel()
        expiry_task.cancel()
        if tarefa_backup is not None:
            tarefa_backup.cancel()
        if not dashboard_task.done():
            dashboard_task.cancel()
        if not dispatcher_task.done():
            # Da tempo ao emissor de acabar um envio que ja esteja em curso, em vez de o cortar a meio.
            try:
                await asyncio.wait_for(dispatcher_task, timeout=_DISPATCHER_GRACE_PERIOD_SECONDS)
            except TimeoutError:
                logger.warning("Emissor nao terminou a tempo de acabar o envio em curso; a forcar paragem.")
            except asyncio.CancelledError:
                pass
        await asyncio.gather(
            collector_task, validator_task, expiry_task, dashboard_task, dispatcher_task,
            *([tarefa_backup] if tarefa_backup is not None else []),
            return_exceptions=True,
        )
        await api_client.aclose()
        connection.close()
