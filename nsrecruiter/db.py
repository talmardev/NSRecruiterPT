"""Camada de acesso a base de dados SQLite (fila, historico, estado)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nsrecruiter.models import TargetSource, TargetStatus, extract_name_base

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    nation_id TEXT PRIMARY KEY,
    nation_name TEXT NOT NULL,
    status TEXT NOT NULL,
    status_reason TEXT,
    source TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    queued_at TEXT,
    validated_at TEXT,
    sent_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    pinned_at TEXT,
    name_base TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_targets_status ON targets(status);
CREATE INDEX IF NOT EXISTS idx_targets_name_base ON targets(name_base);

CREATE TABLE IF NOT EXISTS send_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nation_id TEXT NOT NULL REFERENCES targets(nation_id),
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    http_status INTEGER,
    retry_after REAL,
    error_reason TEXT,
    blocked_externally INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_send_history_nation ON send_history(nation_id);

CREATE TABLE IF NOT EXISTS kv_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.row_factory = sqlite3.Row
    return connection


def init_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    _ensure_column(connection, "targets", "priority", "priority INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "targets", "pinned_at", "pinned_at TEXT")
    _ensure_column(connection, "targets", "name_base", "name_base TEXT NOT NULL DEFAULT ''")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_targets_name_base ON targets(name_base)")
    _backfill_name_base(connection)


def _backfill_name_base(connection: sqlite3.Connection) -> None:
    """Preenche name_base para bases de dados criadas antes desta coluna existir."""
    rows = connection.execute("SELECT nation_id FROM targets WHERE name_base = ''").fetchall()
    connection.executemany(
        "UPDATE targets SET name_base = ? WHERE nation_id = ?",
        [(extract_name_base(row["nation_id"]), row["nation_id"]) for row in rows],
    )


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Migra bases de dados criadas antes desta coluna existir (CREATE TABLE IF NOT EXISTS
    nao adiciona colunas novas a tabelas ja existentes)."""
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def target_exists(connection: sqlite3.Connection, nation_id: str) -> bool:
    row = connection.execute("SELECT 1 FROM targets WHERE nation_id = ?", (nation_id,)).fetchone()
    return row is not None


def insert_discovered_target(
    connection: sqlite3.Connection,
    nation_id: str,
    nation_name: str,
    source: TargetSource,
    now_iso: str,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO targets "
        "(nation_id, nation_name, status, source, discovered_at, attempts, name_base, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
        (
            nation_id,
            nation_name,
            TargetStatus.DISCOVERED.value,
            source.value,
            now_iso,
            extract_name_base(nation_id),
            now_iso,
        ),
    )


def count_targets_with_name_base_since(
    connection: sqlite3.Connection, name_base: str, exclude_nation_id: str, since_iso: str
) -> int:
    """Quantos outros alvos partilham a mesma base de nome (sem sufixo numerico),
    descobertos desde `since_iso` -- usado para detetar provaveis alts."""
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM targets WHERE name_base = ? AND nation_id != ? AND discovered_at >= ?",
        (name_base, exclude_nation_id, since_iso),
    ).fetchone()
    return row["n"]


def next_discovered_target(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM targets WHERE status = ? ORDER BY discovered_at ASC LIMIT 1",
        (TargetStatus.DISCOVERED.value,),
    ).fetchone()


def mark_target_queued(
    connection: sqlite3.Connection, nation_id: str, now_iso: str, priority: bool = False
) -> None:
    connection.execute(
        "UPDATE targets SET status = ?, queued_at = ?, validated_at = ?, updated_at = ?, priority = ? "
        "WHERE nation_id = ?",
        (TargetStatus.QUEUED.value, now_iso, now_iso, now_iso, int(priority), nation_id),
    )


def mark_target_rejected(connection: sqlite3.Connection, nation_id: str, reason: str, now_iso: str) -> None:
    connection.execute(
        "UPDATE targets SET status = ?, status_reason = ?, validated_at = ?, updated_at = ? WHERE nation_id = ?",
        (TargetStatus.REJECTED.value, reason, now_iso, now_iso, nation_id),
    )


def increment_target_attempts(connection: sqlite3.Connection, nation_id: str, attempts: int, now_iso: str) -> None:
    connection.execute(
        "UPDATE targets SET attempts = ?, updated_at = ? WHERE nation_id = ?",
        (attempts, now_iso, nation_id),
    )


_QUEUE_ORDER_BY = (
    "ORDER BY CASE WHEN pinned_at IS NOT NULL THEN 0 ELSE 1 END ASC, "
    "pinned_at DESC, priority DESC, queued_at ASC"
)


def next_queued_target(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        f"SELECT * FROM targets WHERE status = ? {_QUEUE_ORDER_BY} LIMIT 1",
        (TargetStatus.QUEUED.value,),
    ).fetchone()


def list_queued_targets(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        f"SELECT * FROM targets WHERE status = ? {_QUEUE_ORDER_BY}",
        (TargetStatus.QUEUED.value,),
    ).fetchall()


def toggle_target_pin(connection: sqlite3.Connection, nation_id: str, now_iso: str) -> bool:
    """Alterna a fixacao manual no topo da fila (tecla Enter no modo de selecao da
    fila do dashboard). Devolve o novo estado: True se ficou fixado, False se foi
    desfixado ou se o alvo nao existe."""
    row = connection.execute("SELECT pinned_at FROM targets WHERE nation_id = ?", (nation_id,)).fetchone()
    if row is None:
        return False
    new_pinned_at = None if row["pinned_at"] else now_iso
    connection.execute(
        "UPDATE targets SET pinned_at = ?, updated_at = ? WHERE nation_id = ?",
        (new_pinned_at, now_iso, nation_id),
    )
    return new_pinned_at is not None


def mark_target_sent(connection: sqlite3.Connection, nation_id: str, now_iso: str) -> None:
    connection.execute(
        "UPDATE targets SET status = ?, sent_at = ?, updated_at = ? WHERE nation_id = ?",
        (TargetStatus.SENT.value, now_iso, now_iso, nation_id),
    )


def insert_send_history(
    connection: sqlite3.Connection,
    nation_id: str,
    attempted_at: str,
    success: bool,
    http_status: int | None,
    retry_after: float | None,
    error_reason: str | None,
    blocked_externally: bool = False,
) -> None:
    connection.execute(
        "INSERT INTO send_history "
        "(nation_id, attempted_at, success, http_status, retry_after, error_reason, blocked_externally) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            nation_id,
            attempted_at,
            int(success),
            http_status,
            retry_after,
            error_reason,
            int(blocked_externally),
        ),
    )


def get_last_successful_send_at(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT attempted_at FROM send_history WHERE success = 1 ORDER BY attempted_at DESC LIMIT 1"
    ).fetchone()
    return row["attempted_at"] if row else None


def count_sent_since(connection: sqlite3.Connection, since_iso: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM send_history WHERE success = 1 AND attempted_at >= ?", (since_iso,)
    ).fetchone()
    return row["n"]


def count_sent_total(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS n FROM send_history WHERE success = 1").fetchone()
    return row["n"]


def count_send_attempts_since(connection: sqlite3.Connection, since_iso: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM send_history WHERE attempted_at >= ?", (since_iso,)
    ).fetchone()
    return row["n"]


def count_send_attempts_total(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) AS n FROM send_history").fetchone()
    return row["n"]


def count_targets_by_status(connection: sqlite3.Connection, status: TargetStatus) -> int:
    row = connection.execute("SELECT COUNT(*) AS n FROM targets WHERE status = ?", (status.value,)).fetchone()
    return row["n"]


def failure_reason_breakdown(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT COALESCE(error_reason, 'desconhecido') AS reason, COUNT(*) AS n "
        "FROM send_history WHERE success = 0 GROUP BY reason ORDER BY n DESC"
    ).fetchall()


def rejection_reason_breakdown(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT COALESCE(status_reason, 'desconhecido') AS reason, COUNT(*) AS n FROM targets "
        "WHERE status = ? GROUP BY reason ORDER BY n DESC",
        (TargetStatus.REJECTED.value,),
    ).fetchall()


def get_kv(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM kv_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_kv(connection: sqlite3.Connection, key: str, value: str, now_iso: str) -> None:
    connection.execute(
        "INSERT INTO kv_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now_iso),
    )
