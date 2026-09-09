"""Validador: confirma tgcanrecruit antes de por um alvo na fila de envio."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NsApiError
from nsrecruiter.api.shards import fetch_tgcanrecruit, fetch_tgcanrecruit_and_flag, flag_matches_presets
from nsrecruiter.config import Config
from nsrecruiter.models import extract_name_base
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.validator")

# Mesmo perfil conservador confirmado com o utilizador para erros de rede.
_BACKOFF_SCHEDULE_SECONDS = (5.0, 10.0, 20.0)
_MAX_VALIDATION_ATTEMPTS = 3
_IDLE_POLL_INTERVAL_SECONDS = 2.0

# Padroes bloqueados no nome (substring, minusculas) -- nunca se envia nada a estas
# nacoes, decidido com o utilizador. Verificado antes de qualquer pedido a API, para
# nem gastar uma validacao com elas.
_BLOCKED_NAME_SUBSTRINGS = ("facist", "facista", "fascista", "nazi")


def _has_blocked_name(nation_id: str) -> bool:
    return any(substring in nation_id for substring in _BLOCKED_NAME_SUBSTRINGS)


# Deteta provaveis alts: nomes que so diferem no sufixo numerico (ex: "yamagoochie0065"
# e "yamagoochie65"), descobertos dentro da mesma janela de tempo -- decidido com o
# utilizador. Bases muito curtas (< 3 caracteres) ficam de fora para nao gerar falsos
# positivos em nomes genericos.
_ALT_NAME_LOOKBACK_DAYS = 30.0
_MIN_NAME_BASE_LENGTH = 3
_ALT_REJECTION_REASON = f"provavel alt (nome-base repetido nos ultimos {int(_ALT_NAME_LOOKBACK_DAYS):d} dias)"


def _alt_lookback_cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=_ALT_NAME_LOOKBACK_DAYS)).isoformat(timespec="seconds")


async def _validate_one(
    connection: sqlite3.Connection,
    client: NsApiClient,
    config: Config,
    region_members: frozenset[str],
    row: sqlite3.Row,
) -> None:
    nation_id = row["nation_id"]
    now_iso = utc_now_iso()

    if _has_blocked_name(nation_id):
        db.mark_target_rejected(connection, nation_id, "nome bloqueado (padrao fascista/nazi)", now_iso)
        logger.info("Rejeitado %s: nome bloqueado.", row["nation_name"])
        return

    if nation_id in region_members:
        db.mark_target_rejected(connection, nation_id, "ja esta na regiao", now_iso)
        logger.info("Rejeitado %s: ja esta na regiao.", row["nation_name"])
        return

    name_base = extract_name_base(nation_id)
    if len(name_base) >= _MIN_NAME_BASE_LENGTH:
        similar_count = db.count_targets_with_name_base_since(
            connection, name_base, nation_id, _alt_lookback_cutoff_iso()
        )
        if similar_count > 0:
            db.mark_target_rejected(connection, nation_id, _ALT_REJECTION_REASON, now_iso)
            logger.info("Rejeitado %s: provavel alt (base '%s').", row["nation_name"], name_base)
            return

    try:
        if config.priority_flag_countries:
            can_recruit, flag_url = await fetch_tgcanrecruit_and_flag(client, nation_id, config.region)
        else:
            can_recruit, flag_url = await fetch_tgcanrecruit(client, nation_id, config.region), None
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
        priority = flag_url is not None and flag_matches_presets(flag_url, config.priority_flag_countries)
        db.mark_target_queued(connection, nation_id, now_iso, priority=priority)
        if priority:
            logger.info("Na fila (prioridade - bandeira): %s", row["nation_name"])
        else:
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
