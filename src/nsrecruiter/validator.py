"""Validador: confirma tgcanrecruit antes de por um alvo na fila de envio."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.exceptions import NotFoundError, NsApiError
from nsrecruiter.api.shards import fetch_tgcanrecruit, fetch_tgcanrecruit_and_flag, flag_matches_presets
from nsrecruiter.config import Config
from nsrecruiter.models import (
    MIN_NAME_BASE_LENGTH,
    MIN_SHARED_NAME_TOKENS,
    extract_name_base,
    significant_name_tokens,
)
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.validator")

# Mesmo perfil conservador confirmado com o utilizador para erros de rede.
_BACKOFF_SCHEDULE_SECONDS = (5.0, 10.0, 20.0)
_MAX_VALIDATION_ATTEMPTS = 3
_IDLE_POLL_INTERVAL_SECONDS = 2.0

# Padroes bloqueados no nome (substring, minusculas): nunca se envia nada a estas
# nacoes, decidido com o utilizador. Verificado antes de qualquer pedido a API, para
# nem gastar uma validacao com elas.
_BLOCKED_NAME_SUBSTRINGS = ("facist", "facista", "fascista", "nazi")


_BLOCKED_NAME_REASON = "nome bloqueado (padrao fascista/nazi)"


def _matched_blocked_substring(nation_id: str) -> str | None:
    return next((substring for substring in _BLOCKED_NAME_SUBSTRINGS if substring in nation_id), None)


# Deteta provaveis alts: nomes que so diferem no sufixo numerico (ex: "yamagoochie0065"
# e "yamagoochie65"), descobertos dentro da mesma janela de tempo, decidido com o
# utilizador. Bases muito curtas ficam de fora para nao gerar falsos positivos em
# nomes genericos (limiar em MIN_NAME_BASE_LENGTH, partilhado com a analise manual).
_ALT_NAME_LOOKBACK_DAYS = 30.0
_ALT_REJECTION_REASON = f"provavel alt (nome-base repetido nos ultimos {int(_ALT_NAME_LOOKBACK_DAYS):d} dias)"


def _alt_lookback_cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=_ALT_NAME_LOOKBACK_DAYS)).isoformat(timespec="seconds")


# Deteta lotes gerados a partir de listas externas (ex: "2018_azerbaijan_grand_prix",
# "2018_german_grand_prix", nomes de corridas de Formula 1): nao partilham uma base
# comum como os alts acima, mas repetem 2+ palavras distintivas em pouco tempo.
# Janela curta (1h, nao dias) e exigir 2+ palavras (nao 1) foi decidido com o
# utilizador para minimizar o risco de rejeitar coincidencias entre jogadores reais.
_BATCH_TOKEN_LOOKBACK_MINUTES = 60.0
_BATCH_TOKEN_REJECTION_REASON = (
    f"provavel lote gerado (2+ palavras do nome repetidas nos ultimos "
    f"{int(_BATCH_TOKEN_LOOKBACK_MINUTES):d} min)"
)


def _batch_token_lookback_cutoff_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=_BATCH_TOKEN_LOOKBACK_MINUTES)).isoformat(
        timespec="seconds"
    )


async def _validate_one(
    connection: sqlite3.Connection,
    client: NsApiClient,
    config: Config,
    region_members: frozenset[str],
    row: sqlite3.Row,
) -> None:
    nation_id = row["nation_id"]
    now_iso = utc_now_iso()

    matched_substring = _matched_blocked_substring(nation_id)
    if matched_substring is not None:
        db.mark_target_rejected(
            connection, nation_id, _BLOCKED_NAME_REASON, now_iso, heuristic=True, detail=matched_substring
        )
        logger.info("Rejeitado %s: nome bloqueado (%s).", row["nation_name"], matched_substring)
        return

    if nation_id in region_members:
        db.mark_target_rejected(connection, nation_id, "ja esta na regiao", now_iso)
        logger.info("Rejeitado %s: ja esta na regiao.", row["nation_name"])
        return

    name_base = extract_name_base(nation_id)
    if len(name_base) >= MIN_NAME_BASE_LENGTH:
        similar_count = db.count_targets_with_name_base_since(
            connection, name_base, nation_id, _alt_lookback_cutoff_iso(), row["discovered_at"]
        )
        if similar_count > 0:
            db.mark_target_rejected(
                connection, nation_id, _ALT_REJECTION_REASON, now_iso, heuristic=True, detail=name_base
            )
            logger.info("Rejeitado %s: provavel alt (base '%s').", row["nation_name"], name_base)
            return

    tokens = significant_name_tokens(nation_id)
    if tokens:
        shared_count = db.count_targets_sharing_tokens_since(
            connection,
            tokens,
            nation_id,
            _batch_token_lookback_cutoff_iso(),
            row["discovered_at"],
            MIN_SHARED_NAME_TOKENS,
        )
        if shared_count > 0:
            tokens_detail = ",".join(sorted(tokens))
            db.mark_target_rejected(
                connection, nation_id, _BATCH_TOKEN_REJECTION_REASON, now_iso,
                heuristic=True, detail=tokens_detail,
            )
            logger.info(
                "Rejeitado %s: provavel lote gerado (palavras partilhadas: %s).",
                row["nation_name"],
                tokens_detail,
            )
            return

    try:
        if config.priority_flag_countries:
            can_recruit, flag_url = await fetch_tgcanrecruit_and_flag(client, nation_id, config.region)
        else:
            can_recruit, flag_url = await fetch_tgcanrecruit(client, nation_id, config.region), None
    except NotFoundError:
        # Permanente (a nacao deixou de existir entretanto), nao vale a pena gastar
        # as tentativas com backoff, que sao para falhas transitorias.
        db.mark_target_rejected(connection, nation_id, "nacao deixou de existir (404 na validacao)", now_iso)
        logger.info("Rejeitado %s: nacao ja nao existe.", row["nation_name"])
        return
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
