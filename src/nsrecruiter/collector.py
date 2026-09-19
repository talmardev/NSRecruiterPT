"""Coletor: descobre nacoes novas via SSE, com fallback de polling se a ligacao falhar."""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3

import httpx

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NsApiError
from nsrecruiter.api.shards import fetch_flag, fetch_new_nations, flag_matches_presets
from nsrecruiter.api.sse import iter_sse_events
from nsrecruiter.config import Config
from nsrecruiter.models import TargetSource, normalize_nation
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.collector")

_FOUNDING_NATION_PATTERN = re.compile(r"@@([^@]+)@@")

# Perfil de retry conservador confirmado com o utilizador: ate 3 tentativas,
# 5s -> 10s -> 20s. Ao fim delas o coletor no passa para polling em vez de
# desistir: e o "fallback" pedido nas regras da plataforma.
_SSE_RECONNECT_BACKOFF_SECONDS = (5.0, 10.0, 20.0)
_SSE_MAX_CONSECUTIVE_FAILURES = 3
_SSE_RETRY_COOLDOWN_SECONDS = 300.0
_POLL_FALLBACK_INTERVAL_SECONDS = 30.0


def extract_founded_nation(event_data: str) -> str | None:
    """Extrai o nome entre @@..@@ do texto de um evento de fundacao."""
    match = _FOUNDING_NATION_PATTERN.search(event_data)
    return match.group(1) if match else None


def _record_discovery(connection: sqlite3.Connection, raw_name: str, source: TargetSource) -> None:
    nation_id = normalize_nation(raw_name)
    if not nation_id or db.target_exists(connection, nation_id):
        return
    db.insert_discovered_target(connection, nation_id, raw_name, source, utc_now_iso())
    logger.info("Nova nacao descoberta (%s): %s", source.value, raw_name)


async def _run_sse_once(
    connection: sqlite3.Connection,
    http_client: httpx.AsyncClient,
    user_agent: str,
    last_event_id: str | None,
) -> str | None:
    """Consome uma ligacao SSE ate cair. Devolve o ultimo id visto, para retomar depois."""
    async for event in iter_sse_events(http_client, user_agent, last_event_id):
        if event.id:
            last_event_id = event.id
            db.set_kv(connection, "sse_last_event_id", last_event_id, utc_now_iso())

        if event.event == "replay-gap":
            logger.warning(
                "O servidor nao conseguiu reenviar eventos desde o ultimo id: "
                "algumas fundacoes podem ter sido perdidas durante a interrupcao."
            )
            continue

        nation_name = extract_founded_nation(event.data)
        if nation_name is None:
            continue
        _record_discovery(connection, nation_name, TargetSource.SSE)

    return last_event_id


async def _poll_fallback_once(connection: sqlite3.Connection, client: NsApiClient) -> None:
    try:
        nations = await fetch_new_nations(client)
    except NsApiError as exc:
        logger.warning("Falha no polling de novas nacoes: %s", exc)
        return
    for raw_name in nations:
        _record_discovery(connection, raw_name, TargetSource.POLL)


async def _recheck_queued_flags(connection: sqlite3.Connection, client: NsApiClient, config: Config) -> None:
    """Reavalia a bandeira de quem ja esta 'queued'. Sem isto, a prioridade de um alvo
    ficava decidida para sempre no momento da validacao inicial; nunca mais mudava,
    mesmo que a nacao trocasse de bandeira entretanto (a fila pode ter horas de
    backlog) ou que PRIORITY_FLAG_COUNTRIES fosse atualizado depois. So corre se a
    funcionalidade estiver ativa, e nunca mexe em queued_at (nao pode alterar a
    posicao FIFO do alvo, so a fatia de prioridade em que cai)."""
    if not config.priority_flag_countries:
        return
    for row in db.list_queued_targets(connection):
        nation_id = row["nation_id"]
        try:
            flag_url = await fetch_flag(client, nation_id)
        except NsApiError as exc:
            logger.warning("Falha ao reavaliar bandeira de %s: %s", row["nation_name"], exc)
            continue
        priority = flag_matches_presets(flag_url, config.priority_flag_countries)
        if bool(row["priority"]) != priority:
            db.set_target_priority(connection, nation_id, priority, utc_now_iso())
            logger.info(
                "Prioridade de %s atualizada para %s (bandeira reavaliada).",
                row["nation_name"],
                "sim" if priority else "nao",
            )


async def force_refresh(connection: sqlite3.Connection, client: NsApiClient, config: Config) -> None:
    """Verificacao manual pontual (tecla r do dashboard): um pedido a q=newnations,
    fora do ritmo normal do coletor, e reavaliacao da bandeira de quem ja esta na
    fila. Nao mexe na lista de membros da regiao."""
    logger.info("Atualizacao manual da fila pedida.")
    await _poll_fallback_once(connection, client)
    await _recheck_queued_flags(connection, client, config)


async def run_collector(
    connection: sqlite3.Connection,
    client: NsApiClient,
    http_client: httpx.AsyncClient,
    user_agent: str,
) -> None:
    """Ciclo principal: SSE como fonte primaria, polling como rede de seguranca."""
    last_event_id = db.get_kv(connection, "sse_last_event_id")
    consecutive_failures = 0

    while True:
        try:
            last_event_id = await _run_sse_once(connection, http_client, user_agent, last_event_id)
            logger.warning("Ligacao SSE terminou inesperadamente; a reconectar.")
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Ligacao SSE cortada: %s", exc)

        consecutive_failures += 1

        if consecutive_failures <= _SSE_MAX_CONSECUTIVE_FAILURES:
            backoff = _SSE_RECONNECT_BACKOFF_SECONDS[
                min(consecutive_failures - 1, len(_SSE_RECONNECT_BACKOFF_SECONDS) - 1)
            ]
            logger.info("A reconectar ao SSE dentro de %.0fs (tentativa %d).", backoff, consecutive_failures)
            await asyncio.sleep(backoff)
            continue

        logger.warning(
            "SSE indisponivel apos %d tentativas; a passar para polling de fallback durante %.0fs.",
            consecutive_failures,
            _SSE_RETRY_COOLDOWN_SECONDS,
        )
        loop_time = asyncio.get_running_loop().time
        fallback_until = loop_time() + _SSE_RETRY_COOLDOWN_SECONDS
        while loop_time() < fallback_until:
            await _poll_fallback_once(connection, client)
            await asyncio.sleep(_POLL_FALLBACK_INTERVAL_SECONDS)

        logger.info("A tentar o SSE outra vez.")
        consecutive_failures = 0
