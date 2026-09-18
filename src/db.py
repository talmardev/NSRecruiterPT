"""Camada de acesso a base de dados SQLite (fila, historico, estado)."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from nsrecruiter.models import TargetSource, TargetStatus, extract_name_base, significant_name_tokens

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
    rejection_category TEXT,
    rejection_detail TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_targets_status ON targets(status);

CREATE TABLE IF NOT EXISTS target_name_tokens (
    nation_id TEXT NOT NULL REFERENCES targets(nation_id),
    token TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_name_tokens_token ON target_name_tokens(token);
CREATE INDEX IF NOT EXISTS idx_target_name_tokens_nation ON target_name_tokens(nation_id);

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
    _ensure_column(connection, "targets", "rejection_category", "rejection_category TEXT")
    _ensure_column(connection, "targets", "rejection_detail", "rejection_detail TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_targets_name_base ON targets(name_base)")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_targets_rejection_category ON targets(rejection_category)"
    )
    _backfill_name_base(connection)
    _backfill_name_tokens(connection)


def _backfill_name_base(connection: sqlite3.Connection) -> None:
    """Preenche name_base para bases de dados criadas antes desta coluna existir."""
    rows = connection.execute("SELECT nation_id FROM targets WHERE name_base = ''").fetchall()
    connection.executemany(
        "UPDATE targets SET name_base = ? WHERE nation_id = ?",
        [(extract_name_base(row["nation_id"]), row["nation_id"]) for row in rows],
    )


def _backfill_name_tokens(connection: sqlite3.Connection) -> None:
    """Preenche target_name_tokens para alvos gravados antes desta tabela existir."""
    rows = connection.execute(
        "SELECT nation_id FROM targets WHERE nation_id NOT IN (SELECT DISTINCT nation_id FROM target_name_tokens)"
    ).fetchall()
    pairs = [
        (row["nation_id"], token)
        for row in rows
        for token in significant_name_tokens(row["nation_id"])
    ]
    if pairs:
        connection.executemany("INSERT INTO target_name_tokens (nation_id, token) VALUES (?, ?)", pairs)


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
    cursor = connection.execute(
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
    if cursor.rowcount:
        tokens = significant_name_tokens(nation_id)
        if tokens:
            connection.executemany(
                "INSERT INTO target_name_tokens (nation_id, token) VALUES (?, ?)",
                [(nation_id, token) for token in tokens],
            )


def count_targets_with_name_base_since(
    connection: sqlite3.Connection, name_base: str, exclude_nation_id: str, since_iso: str, before_iso: str
) -> int:
    """Quantos outros alvos com a mesma base de nome (sem sufixo numerico) foram
    descobertos entre `since_iso` (inclusive) e `before_iso` (exclusive). Usado para
    detetar provaveis alts. O limite superior conta so quem apareceu ANTES deste alvo,
    para que o primeiro de uma rajada nunca seja rejeitado por causa dos que vem a seguir.
    Em caso de empate exato em discovered_at (resolucao de 1s, onde uma rajada rapida cai
    facilmente no mesmo segundo), desempata pela ordem de insercao (rowid); sem isto,
    uma rajada inteira descoberta no mesmo segundo nunca se apanhava a ela propria."""
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM targets "
        "WHERE name_base = ? AND nation_id != ? AND discovered_at >= ? AND ("
        "  discovered_at < ? OR (discovered_at = ? AND rowid < (SELECT rowid FROM targets WHERE nation_id = ?))"
        ")",
        (name_base, exclude_nation_id, since_iso, before_iso, before_iso, exclude_nation_id),
    ).fetchone()
    return row["n"]


def count_targets_sharing_tokens_since(
    connection: sqlite3.Connection,
    tokens: Iterable[str],
    exclude_nation_id: str,
    since_iso: str,
    before_iso: str,
    min_shared_tokens: int,
) -> int:
    """Quantas outras nacoes descobertas entre `since_iso` (inclusive) e `before_iso`
    (exclusive) partilham pelo menos `min_shared_tokens` das palavras dadas. Usado
    para detetar lotes gerados a partir de listas externas (ex: '..._grand_prix').
    Mesmo desempate por rowid que count_targets_with_name_base_since para empates
    exatos em discovered_at (resolucao de 1s)."""
    token_list = list(tokens)
    if not token_list:
        return 0
    placeholders = ",".join("?" for _ in token_list)
    rows = connection.execute(
        f"""
        SELECT t.nation_id
        FROM target_name_tokens tnt
        JOIN targets t ON t.nation_id = tnt.nation_id
        WHERE tnt.token IN ({placeholders})
          AND t.nation_id != ?
          AND t.discovered_at >= ?
          AND (
            t.discovered_at < ?
            OR (t.discovered_at = ? AND t.rowid < (SELECT rowid FROM targets WHERE nation_id = ?))
          )
        GROUP BY t.nation_id
        HAVING COUNT(DISTINCT tnt.token) >= ?
        """,
        (*token_list, exclude_nation_id, since_iso, before_iso, before_iso, exclude_nation_id, min_shared_tokens),
    ).fetchall()
    return len(rows)


def find_queued_token_share_pairs(
    connection: sqlite3.Connection, min_shared_tokens: int
) -> list[tuple[str, str, str, str, frozenset[str]]]:
    """Pares de nacoes atualmente na fila (status='queued') que partilham pelo menos
    `min_shared_tokens` palavras significativas -- sem limite de tempo, ao contrario
    da deteçao automatica (so olha para a ultima hora). Usado pela analise manual da
    fila (tecla 'a' no ecra de revisao), para sugerir padroes ainda nao apanhados
    pelos filtros automaticos (ex: um lote mais espacado no tempo)."""
    rows = connection.execute(
        """
        SELECT a.nation_id AS a_id, ta.nation_name AS a_name,
               b.nation_id AS b_id, tb.nation_name AS b_name,
               a.token AS token
        FROM target_name_tokens a
        JOIN target_name_tokens b ON a.token = b.token AND a.nation_id < b.nation_id
        JOIN targets ta ON ta.nation_id = a.nation_id
        JOIN targets tb ON tb.nation_id = b.nation_id
        WHERE ta.status = ? AND tb.status = ?
        """,
        (TargetStatus.QUEUED.value, TargetStatus.QUEUED.value),
    ).fetchall()

    grouped: dict[tuple[str, str, str, str], set[str]] = {}
    for row in rows:
        key = (row["a_id"], row["a_name"], row["b_id"], row["b_name"])
        grouped.setdefault(key, set()).add(row["token"])

    return [
        (a_id, a_name, b_id, b_name, frozenset(tokens))
        for (a_id, a_name, b_id, b_name), tokens in grouped.items()
        if len(tokens) >= min_shared_tokens
    ]


def find_queued_name_base_clusters(
    connection: sqlite3.Connection, min_base_length: int, min_cluster_size: int = 2
) -> list[sqlite3.Row]:
    """Nacoes atualmente na fila (status='queued') cuja base de nome (sem sufixo
    numerico, ex: 'yamagoochie0065' e 'yamagoochie65' -> 'yamagoochie') e partilhada
    por pelo menos `min_cluster_size` alvos -- mesma logica da deteçao automatica
    (validator.py), mas sem limite de tempo e reaplicada a quem ja esta na fila (ex:
    passou a validacao antes desta deteçao existir, ou nao foi apanhado por outra
    razao). Usado pela analise manual da fila (tecla 'a' no ecra de revisao)."""
    return connection.execute(
        """
        SELECT name_base, nation_id, nation_name FROM targets
        WHERE status = ? AND length(name_base) >= ? AND name_base IN (
            SELECT name_base FROM targets
            WHERE status = ? AND length(name_base) >= ?
            GROUP BY name_base HAVING COUNT(*) >= ?
        )
        ORDER BY name_base
        """,
        (
            TargetStatus.QUEUED.value, min_base_length,
            TargetStatus.QUEUED.value, min_base_length, min_cluster_size,
        ),
    ).fetchall()


def reject_targets_as_heuristic(
    connection: sqlite3.Connection, nation_ids: Iterable[str], reason: str, detail: str, now_iso: str
) -> int:
    """Rejeita em bloco os alvos indicados (usado ao confirmar uma sugestao da analise
    manual da fila). So afeta quem ainda estiver 'queued' nesse momento -- protege
    contra a rara corrida com o emissor a despachar um deles entretanto. Devolve
    quantos foram mesmo rejeitados."""
    count = 0
    for nation_id in nation_ids:
        row = connection.execute("SELECT status FROM targets WHERE nation_id = ?", (nation_id,)).fetchone()
        if row is not None and row["status"] == TargetStatus.QUEUED.value:
            mark_target_rejected(connection, nation_id, reason, now_iso, heuristic=True, detail=detail)
            count += 1
    return count


def next_discovered_target(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM targets WHERE status = ? ORDER BY discovered_at ASC LIMIT 1",
        (TargetStatus.DISCOVERED.value,),
    ).fetchone()


def mark_target_queued(
    connection: sqlite3.Connection, nation_id: str, now_iso: str, priority: bool = False
) -> None:
    # attempts volta a 0: o contador de retries da fase de validacao nao deve
    # descontar das tentativas de revalidacao pre-envio (fase seguinte, dispatcher.py).
    connection.execute(
        "UPDATE targets SET status = ?, queued_at = ?, validated_at = ?, updated_at = ?, priority = ?, attempts = 0 "
        "WHERE nation_id = ?",
        (TargetStatus.QUEUED.value, now_iso, now_iso, now_iso, int(priority), nation_id),
    )


def mark_target_rejected(
    connection: sqlite3.Connection,
    nation_id: str,
    reason: str,
    now_iso: str,
    heuristic: bool = False,
    detail: str | None = None,
) -> None:
    """`heuristic=True` marca uma rejeicao por deteçao de padrao (nome bloqueado, alt,
    lote gerado) em vez de um facto direto da API -- essas sao as unicas revisiveis e
    reversiveis no ecra de revisao do dashboard (tecla 'v'). `detail` e a chave do
    "cluster" dentro dessa razao (ex: a base partilhada, ou as palavras repetidas)."""
    connection.execute(
        "UPDATE targets SET status = ?, status_reason = ?, rejection_category = ?, rejection_detail = ?, "
        "validated_at = ?, updated_at = ? WHERE nation_id = ?",
        (
            TargetStatus.REJECTED.value,
            reason,
            "heuristic" if heuristic else None,
            detail,
            now_iso,
            now_iso,
            nation_id,
        ),
    )


def count_targets_by_rejection_category(connection: sqlite3.Connection, category: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM targets WHERE status = ? AND rejection_category = ?",
        (TargetStatus.REJECTED.value, category),
    ).fetchone()
    return row["n"]


def list_rejection_clusters(connection: sqlite3.Connection, category: str) -> list[sqlite3.Row]:
    """Agrupa as rejeicoes de uma categoria por (razao, detalhe) -- ex: "provavel lote
    gerado" + "grand,prix" e um cluster, separado de "provavel lote gerado" + "card,coletor"."""
    return connection.execute(
        "SELECT status_reason, rejection_detail, COUNT(*) AS n, MAX(validated_at) AS latest_at "
        "FROM targets WHERE status = ? AND rejection_category = ? "
        "GROUP BY status_reason, rejection_detail ORDER BY latest_at DESC",
        (TargetStatus.REJECTED.value, category),
    ).fetchall()


def revert_targets_by_rejection_group(
    connection: sqlite3.Connection, status_reason: str, detail: str | None, now_iso: str
) -> int:
    """Devolve ao estado 'discovered' todos os alvos de um cluster de rejeicao (tecla
    Enter no ecra de revisao) -- para serem revalidados do zero. Devolve quantos alvos
    foram repostos."""
    detail_clause = "rejection_detail IS NULL" if detail is None else "rejection_detail = ?"
    where_params = (TargetStatus.REJECTED.value, "heuristic", status_reason)
    if detail is not None:
        where_params += (detail,)
    cursor = connection.execute(
        "UPDATE targets SET status = ?, status_reason = NULL, rejection_category = NULL, "
        "rejection_detail = NULL, validated_at = NULL, attempts = 0, updated_at = ? "
        f"WHERE status = ? AND rejection_category = ? AND status_reason = ? AND {detail_clause}",
        (TargetStatus.DISCOVERED.value, now_iso, *where_params),
    )
    return cursor.rowcount


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


def set_target_priority(connection: sqlite3.Connection, nation_id: str, priority: bool, now_iso: str) -> None:
    """So mexe em `priority` -- nunca em `queued_at`, para nao alterar a posicao do
    alvo dentro da sua fatia FIFO. So aplica a quem ainda estiver 'queued' nesse
    momento, para nao reviver um alvo que o emissor ja tenha despachado entretanto."""
    connection.execute(
        "UPDATE targets SET priority = ?, updated_at = ? WHERE nation_id = ? AND status = ?",
        (int(priority), now_iso, nation_id, TargetStatus.QUEUED.value),
    )


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


def list_expired_unprioritized_queued_targets(connection: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    """Alvos 'queued' sem prioridade (bandeira) nem fixacao manual, em fila desde antes
    de `cutoff_iso` -- saem aos 6h por omissao."""
    return connection.execute(
        "SELECT nation_id, nation_name FROM targets "
        "WHERE status = ? AND priority = 0 AND pinned_at IS NULL AND queued_at <= ?",
        (TargetStatus.QUEUED.value, cutoff_iso),
    ).fetchall()


def list_expired_prioritized_queued_targets(connection: sqlite3.Connection, cutoff_iso: str) -> list[sqlite3.Row]:
    """Alvos 'queued' com prioridade (bandeira) ou fixacao manual, em fila desde antes
    de `cutoff_iso` -- saem aos 8h em vez de 6h."""
    return connection.execute(
        "SELECT nation_id, nation_name FROM targets "
        "WHERE status = ? AND (priority != 0 OR pinned_at IS NOT NULL) AND queued_at <= ?",
        (TargetStatus.QUEUED.value, cutoff_iso),
    ).fetchall()


def mark_targets_expired(connection: sqlite3.Connection, nation_ids: Iterable[str], reason: str, now_iso: str) -> int:
    """Rejeita (rejection_category='expired') quem excedeu o tempo maximo de espera na
    fila. So afeta quem ainda estiver 'queued' nesse momento -- protege contra a corrida
    com o emissor a despachar um deles entretanto. Devolve quantos foram mesmo expirados."""
    count = 0
    for nation_id in nation_ids:
        row = connection.execute("SELECT status FROM targets WHERE nation_id = ?", (nation_id,)).fetchone()
        if row is not None and row["status"] == TargetStatus.QUEUED.value:
            connection.execute(
                "UPDATE targets SET status = ?, status_reason = ?, rejection_category = ?, "
                "validated_at = ?, updated_at = ? WHERE nation_id = ?",
                (TargetStatus.REJECTED.value, reason, "expired", now_iso, now_iso, nation_id),
            )
            count += 1
    return count


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
