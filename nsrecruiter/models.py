"""Tipos partilhados pelo dominio da aplicacao."""
from __future__ import annotations

from enum import Enum


class TargetStatus(str, Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    VALIDATING = "validating"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    REJECTED = "rejected"


class TargetSource(str, Enum):
    SSE = "sse"
    POLL = "poll"
    MANUAL = "manual"


class AppState(str, Enum):
    SENDING = "sending"
    WAITING = "waiting"
    PAUSED = "paused"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"


def normalize_nation(name: str) -> str:
    """Forma canonica de um nome de nacao, usada como chave (minusculas, underscores)."""
    return name.strip().lower().replace(" ", "_")
