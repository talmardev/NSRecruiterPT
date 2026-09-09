"""Expira alvos que ficam demasiado tempo na fila de envio (status='queued'), para
nao ocupar espaco indefinidamente. Sem prioridade nem fixacao manual: expira aos 6h.
Com prioridade (bandeira de PRIORITY_FLAG_COUNTRIES ou fixacao manual no topo da fila,
tecla Enter no modo de selecao): expira aos 8h.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from nsrecruiter import db
from nsrecruiter.utils import utc_now_iso

logger = logging.getLogger("nsrecruiter.queue_expiry")

DEFAULT_EXPIRY_HOURS = 6.0
PRIORITY_EXPIRY_HOURS = 8.0

_SWEEP_INTERVAL_SECONDS = 60.0

_DEFAULT_EXPIRED_REASON = f"expirado da fila (mais de {int(DEFAULT_EXPIRY_HOURS)}h em espera, sem prioridade nem fixacao)"
_PRIORITY_EXPIRED_REASON = f"expirado da fila (mais de {int(PRIORITY_EXPIRY_HOURS)}h em espera, mesmo com prioridade/fixacao)"


def _cutoff_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def expire_stale_targets(connection: sqlite3.Connection) -> int:
    """Uma passagem pela fila: rejeita quem excedeu o prazo (6h sem prioridade nem
    fixacao, 8h com qualquer uma das duas). Devolve quantos foram expirados no total."""
    now_iso = utc_now_iso()
    expired = 0

    default_rows = db.list_expired_unprioritized_queued_targets(connection, _cutoff_iso(DEFAULT_EXPIRY_HOURS))
    if default_rows:
        count = db.mark_targets_expired(
            connection, [row["nation_id"] for row in default_rows], _DEFAULT_EXPIRED_REASON, now_iso
        )
        if count:
            logger.info(
                "Removidas %d nacao(oes) da fila (mais de %dh em espera, sem prioridade nem fixacao): %s",
                count, int(DEFAULT_EXPIRY_HOURS), ", ".join(row["nation_name"] for row in default_rows),
            )
        expired += count

    priority_rows = db.list_expired_prioritized_queued_targets(connection, _cutoff_iso(PRIORITY_EXPIRY_HOURS))
    if priority_rows:
        count = db.mark_targets_expired(
            connection, [row["nation_id"] for row in priority_rows], _PRIORITY_EXPIRED_REASON, now_iso
        )
        if count:
            logger.info(
                "Removidas %d nacao(oes) da fila (mais de %dh em espera, com prioridade/fixacao): %s",
                count, int(PRIORITY_EXPIRY_HOURS), ", ".join(row["nation_name"] for row in priority_rows),
            )
        expired += count

    return expired


async def run_queue_expiry_loop(connection: sqlite3.Connection) -> None:
    """Ciclo de fundo: verifica periodicamente a fila e remove quem excedeu o prazo maximo de espera."""
    while True:
        expire_stale_targets(connection)
        await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
