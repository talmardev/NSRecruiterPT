"""Testes da expiracao de alvos que ficam demasiado tempo na fila (status='queued').

Sem prioridade nem fixacao manual, sai aos 6h; com prioridade (bandeira) ou fixacao
manual, sai aos 8h.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from nsrecruiter import db
from nsrecruiter.models import TargetSource
from nsrecruiter.queue_expiry import expire_stale_targets


def _connection(tmp_path: Path):
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    return connection


def _hours_ago_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _queue(connection, nation_id: str, hours_ago: float, priority: bool = False) -> None:
    db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, _hours_ago_iso(hours_ago))
    db.mark_target_queued(connection, nation_id, _hours_ago_iso(hours_ago), priority=priority)


def _status_of(connection, nation_id: str) -> str:
    row = connection.execute("SELECT status FROM targets WHERE nation_id = ?", (nation_id,)).fetchone()
    return row["status"]


def test_unprioritized_target_expires_after_6h(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _queue(connection, "antiga", hours_ago=7.0)

    expired = expire_stale_targets(connection)

    assert expired == 1
    row = connection.execute("SELECT * FROM targets WHERE nation_id = 'antiga'").fetchone()
    assert row["status"] == "rejected"
    assert row["rejection_category"] == "expired"


def test_unprioritized_target_survives_before_6h(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _queue(connection, "recente", hours_ago=5.0)

    assert expire_stale_targets(connection) == 0
    assert _status_of(connection, "recente") == "queued"


def test_flag_priority_target_survives_6h_but_expires_after_8h(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _queue(connection, "com_bandeira", hours_ago=7.0, priority=True)

    assert expire_stale_targets(connection) == 0
    assert _status_of(connection, "com_bandeira") == "queued"

    connection.execute(
        "UPDATE targets SET queued_at = ? WHERE nation_id = 'com_bandeira'", (_hours_ago_iso(9.0),)
    )
    assert expire_stale_targets(connection) == 1
    assert _status_of(connection, "com_bandeira") == "rejected"


def test_manually_pinned_target_survives_6h_but_expires_after_8h(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _queue(connection, "fixada", hours_ago=7.0)
    db.toggle_target_pin(connection, "fixada", _hours_ago_iso(7.0))

    assert expire_stale_targets(connection) == 0
    assert _status_of(connection, "fixada") == "queued"

    connection.execute("UPDATE targets SET queued_at = ? WHERE nation_id = 'fixada'", (_hours_ago_iso(9.0),))
    assert expire_stale_targets(connection) == 1
    assert _status_of(connection, "fixada") == "rejected"


def test_expires_mixed_batch_in_a_single_pass(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    _queue(connection, "sem_prioridade_antiga", hours_ago=7.0)
    _queue(connection, "sem_prioridade_recente", hours_ago=1.0)
    _queue(connection, "com_bandeira_antiga", hours_ago=9.0, priority=True)
    _queue(connection, "com_bandeira_recente", hours_ago=7.0, priority=True)

    expired = expire_stale_targets(connection)

    assert expired == 2
    assert _status_of(connection, "sem_prioridade_antiga") == "rejected"
    assert _status_of(connection, "sem_prioridade_recente") == "queued"
    assert _status_of(connection, "com_bandeira_antiga") == "rejected"
    assert _status_of(connection, "com_bandeira_recente") == "queued"
