"""Validador: confirma tgcanrecruit antes de por um alvo na fila de envio."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NsApiError
from nsrecruiter.api.shards import fetch_tgcanrecruit
from nsrecruiter.config import Config
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.validator")

# Mesmo perfil conservador confirmado com o utilizador para erros de rede.
_BACKOFF_SCHEDULE_SECONDS = (5.0, 10.0, 20.0)
_MAX_VALIDATION_ATTEMPTS = 3
_IDLE_POLL_INTERVAL_SECONDS = 2.0


async def _validate_one(
    connection: sqlite3.Connection,
    client: NsApiClient,
    config: Config,
    region_members: frozenset[str],
    row: sqlite3.Row,
) -> None:
    nation_id = row["nation_id"]
    now_iso = utc_now_iso()

    if nation_id in region_members:
        db.mark_target_rejected(connection, nation_id, "ja esta na regiao", now_iso)
        logger.info("Rejeitado %s: ja esta na regiao.", row["nation_name"])
        return

    try:
        can_recruit = await fetch_tgcanrecruit(client, nation_id, config.region)
    except NsApiError as exc:
        attempts = row["attempts"] + 1
        if attempts >= _MAX_VALIDATION_ATTEMPTS:
            db.mark_target_rejected(
                connection, nation_id, f"falha a validar apos {attempts} tentativas: {exc}", now_iso
            )
            logger.warning(
                "Desisti de validar %s apos %d tentativas: %s", row["nation_name"], attempts, exc
            )
            return
        db.increment_target_attempts(connection, nation_id, attempts, now_iso)
        backoff = _BACKOFF_SCHEDULE_SECONDS[min(attempts - 1, len(_BACKOFF_SCHEDULE_SECONDS) - 1)]
        logger.info(
            "Falha a validar %s (tentativa %d/%d); nova tentativa em %.0fs.",
            row["nation_name"],
            attempts,
            _MAX_VALIDATION_ATTEMPTS,
            backoff,
        )
        await asyncio.sleep(backoff)
        return

    if can_recruit:
        db.mark_target_queued(connection, nation_id, now_iso)
        logger.info("Na fila: %s", row["nation_name"])
    else:
        reason = "tgcanrecruit=0 (recrutamento bloqueado, nacao Class, ou TG recente da regiao)"
        db.mark_target_rejected(connection, nation_id, reason, now_iso)
        logger.info("Rejeitado %s: %s", row["nation_name"], reason)


async def run_validator_loop(
    connection: sqlite3.Connection,
    client: NsApiClient,
    config: Config,
    region_members: frozenset[str],
) -> None:
    """Consome a fila de descobertas (SQLite) e decide fila ou rejeicao para cada uma."""
    while True:
        row = db.next_discovered_target(connection)
        if row is None:
            await asyncio.sleep(_IDLE_POLL_INTERVAL_SECONDS)
            continue
        await _validate_one(connection, client, config, region_members, row)
