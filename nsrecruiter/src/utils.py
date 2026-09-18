"""Utilitarios pequenos e transversais."""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_to_unix_timestamp(iso_text: str) -> float:
    return datetime.fromisoformat(iso_text).timestamp()
