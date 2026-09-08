"""Emissor: envia telegramas de recrutamento respeitando o cooldown entre envios.

Um unico worker, um alvo de cada vez. Revalida tgcanrecruit imediatamente antes
de cada envio -- um pedido de validacao custa muito menos do que desperdicar um
slot inteiro de cooldown a enviar para quem ja nao pode receber.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NsApiError
from nsrecruiter.api.ratelimiter import TelegramRateLimiter
from nsrecruiter.api.shards import fetch_tgcanrecruit
from nsrecruiter.api.telegrams import send_telegram
from nsrecruiter.config import Config
from nsrecruiter.runtime_status import RuntimeStatus
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.dispatcher")

_IDLE_POLL_INTERVAL_SECONDS = 2.0
_WAIT_CHECK_INTERVAL_SECONDS = 1.0


def _next_target(
    connection: sqlite3.Connection, dry_run: bool, dry_run_sent: set[str]
) -> sqlite3.Row | None:
    if not dry_run:
        return db.next_queued_target(connection)
    for row in db.list_queued_targets(connection):
        if row["nation_id"] not in dry_run_sent:
            return row
    return None


async def _wait_for_cooldown(tg_limiter: TelegramRateLimiter, runtime_status: RuntimeStatus) -> None:
    # Em pequenos incrementos (em vez de um so sleep longo) para reagir depressa
    # a uma pausa ou a um pedido de saida, sem esperar o cooldown todo primeiro.
    while True:
        remaining = tg_limiter.seconds_until_next_send()
        if remaining <= 0 or runtime_status.shutdown_requested.is_set():
            return
        await asyncio.sleep(min(_WAIT_CHECK_INTERVAL_SECONDS, remaining))


async def _dispatch_one(
    connection: sqlite3.Connection,
    client: NsApiClient,
    tg_limiter: TelegramRateLimiter,
    config: Config,
    row: sqlite3.Row,
    dry_run: bool,
    dry_run_sent: set[str],
    runtime_status: RuntimeStatus,
) -> None:
    nation_id = row["nation_id"]
    now_iso = utc_now_iso()

    try:
        can_still_recruit = await fetch_tgcanrecruit(client, nation_id, config.region)
    except NsApiError as exc:
        logger.warning("Falha ao revalidar %s antes do envio (%s); tenta-se de novo.", row["nation_name"], exc)
        return

    if not can_still_recruit:
        db.mark_target_rejected(connection, nation_id, "tgcanrecruit=0 na revalidacao pre-envio", now_iso)
        logger.info("Rejeitado na revalidacao final: %s (ja nao pode receber TG).", row["nation_name"])
        return

    if dry_run:
        dry_run_sent.add(nation_id)
        tg_limiter.mark_sent()
        logger.info("[DRY-RUN] Enviaria agora para %s (nao foi feita nenhuma chamada a a=sendTG).", row["nation_name"])
        return

    outcome = await send_telegram(client, config, row["nation_name"])
    attempted_at = utc_now_iso()

    if outcome.success:
        tg_limiter.mark_sent()
        db.mark_target_sent(connection, nation_id, attempted_at)
        db.insert_send_history(
            connection, nation_id, attempted_at, success=True,
            http_status=outcome.http_status, retry_after=None, error_reason=None,
        )
        logger.info("Enviado: %s", row["nation_name"])
        return

    if outcome.http_status == 429:
        retry_after = outcome.retry_after or 180.0
        tg_limiter.mark_retry_after(retry_after)
        runtime_status.mark_blocked_externally(retry_after)
        db.insert_send_history(
            connection, nation_id, attempted_at, success=False, http_status=429,
            retry_after=retry_after, error_reason=outcome.error_reason, blocked_externally=True,
        )
        # Chegamos aqui depois de ja termos esperado o nosso proprio cooldown (_wait_for_cooldown),
        # por isso um 429 neste ponto so pode ser outro oficial a usar a mesma chave.
        logger.info(
            "Bloqueado por uso externo da chave (outro oficial da regiao?); novo envio em %.0fs.", retry_after
        )
        return

    db.insert_send_history(
        connection, nation_id, attempted_at, success=False, http_status=outcome.http_status,
        retry_after=None, error_reason=outcome.error_reason,
    )
    logger.warning("Falha ao enviar para %s: %s", row["nation_name"], outcome.error_reason)


async def run_dispatcher(
    connection: sqlite3.Connection,
    client: NsApiClient,
    tg_limiter: TelegramRateLimiter,
    config: Config,
    runtime_status: RuntimeStatus,
    dry_run: bool = False,
) -> None:
    dry_run_sent: set[str] = set()

    while not runtime_status.shutdown_requested.is_set():
        await runtime_status.wait_while_paused()
        if runtime_status.shutdown_requested.is_set():
            break

        row = _next_target(connection, dry_run, dry_run_sent)
        if row is None:
            await runtime_status.sleep_or_shutdown(_IDLE_POLL_INTERVAL_SECONDS)
            continue

        await _wait_for_cooldown(tg_limiter, runtime_status)
        if runtime_status.shutdown_requested.is_set():
            break

        runtime_status.currently_dispatching = True
        try:
            await _dispatch_one(connection, client, tg_limiter, config, row, dry_run, dry_run_sent, runtime_status)
        finally:
            runtime_status.currently_dispatching = False
