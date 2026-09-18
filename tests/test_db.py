"""Testes da camada de acesso a base de dados (SQLite real em ficheiro temporario)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nsrecruiter import db
from nsrecruiter.models import TargetSource


def _connection(tmp_path: Path) -> sqlite3.Connection:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    return connection


def test_insert_discovered_target_then_target_exists(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    assert not db.target_exists(connection, "testlandia")
    db.insert_discovered_target(
        connection, "testlandia", "Testlandia", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    assert db.target_exists(connection, "testlandia")


def test_insert_discovered_target_is_idempotent(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.insert_discovered_target(connection, "testlandia", "Testlandia (outra vez)", TargetSource.POLL, now)
    row = connection.execute("SELECT COUNT(*) AS n FROM targets").fetchone()
    assert row["n"] == 1


def test_next_discovered_target_returns_oldest_first(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "segunda", "Segunda", TargetSource.SSE, "2026-09-08T00:00:02+00:00")
    db.insert_discovered_target(connection, "primeira", "Primeira", TargetSource.SSE, "2026-09-08T00:00:01+00:00")
    row = db.next_discovered_target(connection)
    assert row is not None
    assert row["nation_id"] == "primeira"


def test_mark_target_queued_updates_status_and_clears_from_discovered(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.mark_target_queued(connection, "testlandia", now)
    row = connection.execute("SELECT * FROM targets WHERE nation_id = ?", ("testlandia",)).fetchone()
    assert row["status"] == "queued"
    assert db.next_discovered_target(connection) is None


def test_mark_target_rejected_stores_reason(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "testlandia", "ja esta na regiao", now)
    row = connection.execute("SELECT * FROM targets WHERE nation_id = ?", ("testlandia",)).fetchone()
    assert row["status"] == "rejected"
    assert row["status_reason"] == "ja esta na regiao"


def test_increment_target_attempts(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.increment_target_attempts(connection, "testlandia", 1, now)
    row = connection.execute("SELECT * FROM targets WHERE nation_id = ?", ("testlandia",)).fetchone()
    assert row["attempts"] == 1


def test_insert_discovered_target_computes_name_base(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, now)
    row = connection.execute("SELECT name_base FROM targets WHERE nation_id = 'yamagoochie0065'").fetchone()
    assert row["name_base"] == "yamagoochie"


def test_count_targets_with_name_base_since_counts_earlier_matching_targets(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    count = db.count_targets_with_name_base_since(
        connection, "yamagoochie", "yamagoochie65", "2026-09-01T00:00:00+00:00", "2026-09-08T00:00:10+00:00"
    )
    assert count == 1


def test_count_targets_with_name_base_since_excludes_self(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    count = db.count_targets_with_name_base_since(
        connection, "yamagoochie", "yamagoochie0065", "2026-09-01T00:00:00+00:00", "2026-09-09T00:00:00+00:00"
    )
    assert count == 0


def test_count_targets_with_name_base_since_ignores_targets_before_window(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie0065", "Yamagoochie0065", TargetSource.SSE, "2026-01-01T00:00:00+00:00"
    )
    count = db.count_targets_with_name_base_since(
        connection, "yamagoochie", "yamagoochie65", "2026-09-01T00:00:00+00:00", "2026-09-08T00:00:10+00:00"
    )
    assert count == 0


def test_count_targets_with_name_base_since_ignores_targets_discovered_after(tmp_path: Path) -> None:
    """O primeiro de uma rajada nunca deve contar os que vem a seguir a ele."""
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie65", "Yamagoochie65", TargetSource.SSE, "2026-09-08T00:00:10+00:00"
    )
    count = db.count_targets_with_name_base_since(
        connection, "yamagoochie", "yamagoochie0065", "2026-09-01T00:00:00+00:00", "2026-09-08T00:00:00+00:00"
    )
    assert count == 0


def test_count_targets_with_name_base_since_desempata_mesmo_segundo_pela_ordem_insercao(
    tmp_path: Path,
) -> None:
    """Uma rajada descoberta no mesmo segundo (resolucao do timestamp) tem de se apanhar
    a ela propria: quem foi inserido depois conta quem veio antes, nunca o contrario."""
    connection = _connection(tmp_path)
    mesmo_instante = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "alvo177", "Alvo177", TargetSource.SSE, mesmo_instante)
    db.insert_discovered_target(connection, "alvo178", "Alvo178", TargetSource.SSE, mesmo_instante)

    contagem_primeiro = db.count_targets_with_name_base_since(
        connection, "alvo", "alvo177", "2026-09-01T00:00:00+00:00", mesmo_instante
    )
    contagem_segundo = db.count_targets_with_name_base_since(
        connection, "alvo", "alvo178", "2026-09-01T00:00:00+00:00", mesmo_instante
    )

    assert contagem_primeiro == 0, "o primeiro inserido nao deve contar o que veio depois dele"
    assert contagem_segundo == 1, "o segundo inserido devia apanhar o primeiro, mesmo com o mesmo timestamp"


def test_init_schema_backfills_name_base_for_pre_existing_rows(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    # Simula uma linha gravada antes de esta coluna existir (name_base a '', o default).
    connection.execute(
        "INSERT INTO targets (nation_id, nation_name, status, source, discovered_at, attempts, updated_at) "
        "VALUES ('yamagoochie0065', 'Yamagoochie0065', 'discovered', 'sse', ?, 0, ?)",
        (now, now),
    )

    db.init_schema(connection)

    row = connection.execute("SELECT name_base FROM targets WHERE nation_id = 'yamagoochie0065'").fetchone()
    assert row["name_base"] == "yamagoochie"


def test_insert_discovered_target_populates_name_tokens(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix", TargetSource.SSE, now
    )
    tokens = {
        row["token"]
        for row in connection.execute(
            "SELECT token FROM target_name_tokens WHERE nation_id = '2018_azerbaijan_grand_prix'"
        )
    }
    assert tokens == {"azerbaijan", "grand", "prix"}


def test_insert_discovered_target_is_idempotent_for_tokens_too(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "grand_prix_fan", "Grand Prix Fan", TargetSource.SSE, now)
    db.insert_discovered_target(connection, "grand_prix_fan", "Grand Prix Fan (outra vez)", TargetSource.POLL, now)
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM target_name_tokens WHERE nation_id = 'grand_prix_fan'"
    ).fetchone()
    assert row["n"] == 2  # "grand" e "prix"; "fan" e curto de mais


def test_count_targets_sharing_tokens_since_matches_on_two_shared_words(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix", TargetSource.SSE,
        "2026-09-08T01:26:00+00:00",
    )
    count = db.count_targets_sharing_tokens_since(
        connection, {"german", "grand", "prix"}, "2018_german_grand_prix",
        "2026-09-08T00:26:00+00:00", "2026-09-08T01:51:00+00:00", min_shared_tokens=2,
    )
    assert count == 1


def test_count_targets_sharing_tokens_since_ignores_single_shared_word(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "grand_kingdom", "Grand Kingdom", TargetSource.SSE, "2026-09-08T01:00:00+00:00"
    )
    count = db.count_targets_sharing_tokens_since(
        connection, {"grand", "duchy"}, "grand_duchy",
        "2026-09-08T00:00:00+00:00", "2026-09-08T02:00:00+00:00", min_shared_tokens=2,
    )
    assert count == 0


def test_count_targets_sharing_tokens_since_excludes_self(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix", TargetSource.SSE,
        "2026-09-08T01:00:00+00:00",
    )
    count = db.count_targets_sharing_tokens_since(
        connection, {"azerbaijan", "grand", "prix"}, "2018_azerbaijan_grand_prix",
        "2026-09-08T00:00:00+00:00", "2026-09-08T02:00:00+00:00", min_shared_tokens=2,
    )
    assert count == 0


def test_count_targets_sharing_tokens_since_ignores_targets_outside_window(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix", TargetSource.SSE,
        "2026-09-01T00:00:00+00:00",
    )
    count = db.count_targets_sharing_tokens_since(
        connection, {"german", "grand", "prix"}, "2018_german_grand_prix",
        "2026-09-08T00:00:00+00:00", "2026-09-08T02:00:00+00:00", min_shared_tokens=2,
    )
    assert count == 0


def test_count_targets_sharing_tokens_since_desempata_mesmo_segundo_pela_ordem_insercao(
    tmp_path: Path,
) -> None:
    connection = _connection(tmp_path)
    mesmo_instante = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix", TargetSource.SSE, mesmo_instante
    )
    db.insert_discovered_target(
        connection, "2018_german_grand_prix", "2018 German Grand Prix", TargetSource.SSE, mesmo_instante
    )

    contagem_primeiro = db.count_targets_sharing_tokens_since(
        connection, {"azerbaijan", "grand", "prix"}, "2018_azerbaijan_grand_prix",
        "2026-09-08T00:00:00+00:00", mesmo_instante, min_shared_tokens=2,
    )
    contagem_segundo = db.count_targets_sharing_tokens_since(
        connection, {"german", "grand", "prix"}, "2018_german_grand_prix",
        "2026-09-08T00:00:00+00:00", mesmo_instante, min_shared_tokens=2,
    )

    assert contagem_primeiro == 0, "o primeiro inserido nao deve contar o que veio depois dele"
    assert contagem_segundo == 1, "o segundo inserido devia apanhar o primeiro, mesmo com o mesmo timestamp"


def test_count_targets_sharing_tokens_since_returns_zero_for_empty_tokens(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    count = db.count_targets_sharing_tokens_since(
        connection, set(), "alvo", "2026-09-08T00:00:00+00:00", "2026-09-08T02:00:00+00:00", min_shared_tokens=2,
    )
    assert count == 0


def test_init_schema_backfills_name_tokens_for_pre_existing_rows(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    # Simula uma linha gravada antes de esta tabela existir.
    connection.execute(
        "INSERT INTO targets (nation_id, nation_name, status, source, discovered_at, attempts, updated_at) "
        "VALUES ('2018_azerbaijan_grand_prix', '2018 Azerbaijan Grand Prix', 'discovered', 'sse', ?, 0, ?)",
        (now, now),
    )

    db.init_schema(connection)

    tokens = {
        row["token"]
        for row in connection.execute(
            "SELECT token FROM target_name_tokens WHERE nation_id = '2018_azerbaijan_grand_prix'"
        )
    }
    assert tokens == {"azerbaijan", "grand", "prix"}


def test_mark_target_rejected_heuristic_sets_category_and_detail(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "a", "provavel alt (...)", now, heuristic=True, detail="yamagoochie")
    row = connection.execute("SELECT * FROM targets WHERE nation_id = 'a'").fetchone()
    assert row["rejection_category"] == "heuristic"
    assert row["rejection_detail"] == "yamagoochie"


def test_mark_target_rejected_non_heuristic_leaves_category_empty(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "a", "tgcanrecruit=0", now)
    row = connection.execute("SELECT * FROM targets WHERE nation_id = 'a'").fetchone()
    assert row["rejection_category"] is None
    assert row["rejection_detail"] is None


def test_count_targets_by_rejection_category_counts_only_that_category(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.insert_discovered_target(connection, "b", "B", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "a", "provavel alt", now, heuristic=True, detail="x")
    db.mark_target_rejected(connection, "b", "tgcanrecruit=0", now)
    assert db.count_targets_by_rejection_category(connection, "heuristic") == 1


def test_list_rejection_clusters_groups_by_reason_and_detail(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    for nation_id in ("2018_azerbaijan_grand_prix", "2018_german_grand_prix"):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, now)
        db.mark_target_rejected(
            connection, nation_id, "provavel lote gerado", now, heuristic=True, detail="grand,prix"
        )
    db.insert_discovered_target(connection, "yamagoochie65", "Yamagoochie65", TargetSource.SSE, now)
    db.mark_target_rejected(
        connection, "yamagoochie65", "provavel alt", now, heuristic=True, detail="yamagoochie"
    )

    clusters = db.list_rejection_clusters(connection, "heuristic")

    by_detail = {row["rejection_detail"]: row["n"] for row in clusters}
    assert by_detail == {"grand,prix": 2, "yamagoochie": 1}


def test_revert_targets_by_rejection_group_restores_whole_cluster(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    for nation_id in ("2018_azerbaijan_grand_prix", "2018_german_grand_prix"):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, now)
        db.mark_target_rejected(
            connection, nation_id, "provavel lote gerado", now, heuristic=True, detail="grand,prix"
        )
    db.insert_discovered_target(connection, "yamagoochie65", "Yamagoochie65", TargetSource.SSE, now)
    db.mark_target_rejected(
        connection, "yamagoochie65", "provavel alt", now, heuristic=True, detail="yamagoochie"
    )

    reverted = db.revert_targets_by_rejection_group(connection, "provavel lote gerado", "grand,prix", "2026-09-08T01:00:00+00:00")

    assert reverted == 2
    rows = {
        row["nation_id"]: row
        for row in connection.execute("SELECT * FROM targets").fetchall()
    }
    assert rows["2018_azerbaijan_grand_prix"]["status"] == "discovered"
    assert rows["2018_azerbaijan_grand_prix"]["status_reason"] is None
    assert rows["2018_azerbaijan_grand_prix"]["rejection_category"] is None
    # o outro cluster nao deve ser afetado
    assert rows["yamagoochie65"]["status"] == "rejected"


def test_revert_targets_by_rejection_group_handles_null_detail(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "a", "nome bloqueado", now, heuristic=True, detail=None)

    reverted = db.revert_targets_by_rejection_group(connection, "nome bloqueado", None, "2026-09-08T01:00:00+00:00")

    assert reverted == 1
    row = connection.execute("SELECT status FROM targets WHERE nation_id = 'a'").fetchone()
    assert row["status"] == "discovered"


def test_find_queued_token_share_pairs_finds_matches_regardless_of_time_gap(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    # Muito mais de 1h de diferenca: o filtro automatico (janela de 1h) nao apanharia,
    # mas a analise manual da fila nao tem limite de tempo.
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix",
        TargetSource.SSE, "2026-09-08T00:00:00+00:00",
    )
    db.mark_target_queued(connection, "2018_azerbaijan_grand_prix", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(
        connection, "2018_german_grand_prix", "2018 German Grand Prix",
        TargetSource.SSE, "2026-09-08T05:00:00+00:00",
    )
    db.mark_target_queued(connection, "2018_german_grand_prix", "2026-09-08T05:00:00+00:00")

    pairs = db.find_queued_token_share_pairs(connection, min_shared_tokens=2)

    assert len(pairs) == 1
    a_id, a_name, b_id, b_name, tokens = pairs[0]
    assert {a_id, b_id} == {"2018_azerbaijan_grand_prix", "2018_german_grand_prix"}
    assert tokens == {"grand", "prix"}


def test_find_queued_token_share_pairs_ignores_single_shared_word(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "grand_kingdom", "Grand Kingdom", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "grand_kingdom", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "grand_duchy", "Grand Duchy", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "grand_duchy", "2026-09-08T00:00:00+00:00")

    pairs = db.find_queued_token_share_pairs(connection, min_shared_tokens=2)

    assert pairs == []


def test_find_queued_token_share_pairs_ignores_non_queued_targets(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "2018_azerbaijan_grand_prix", "2018 Azerbaijan Grand Prix",
        TargetSource.SSE, "2026-09-08T00:00:00+00:00",
    )
    db.mark_target_queued(connection, "2018_azerbaijan_grand_prix", "2026-09-08T00:00:00+00:00")
    # Este nao esta em 'queued' (ficou 'discovered'); nao deve entrar no par.
    db.insert_discovered_target(
        connection, "2018_german_grand_prix", "2018 German Grand Prix",
        TargetSource.SSE, "2026-09-08T05:00:00+00:00",
    )

    pairs = db.find_queued_token_share_pairs(connection, min_shared_tokens=2)

    assert pairs == []


def test_find_queued_name_base_clusters_finds_matches_regardless_of_time_gap(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    # yamagoochie0026 e yamagoochie26 partilham a base 'yamagoochie': caso real que
    # a deteçao automatica (validator.py) deveria ter apanhado na validacao, mas para
    # quem ja escapou e ficou 'queued', a analise manual da fila serve de rede.
    db.insert_discovered_target(
        connection, "yamagoochie0026", "yamagoochie0026", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie0026", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(
        connection, "yamagoochie26", "yamagoochie26", TargetSource.SSE, "2026-09-08T05:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie26", "2026-09-08T05:00:00+00:00")

    rows = db.find_queued_name_base_clusters(connection, min_base_length=3)

    assert {row["nation_id"] for row in rows} == {"yamagoochie0026", "yamagoochie26"}
    assert {row["name_base"] for row in rows} == {"yamagoochie"}


def test_find_queued_name_base_clusters_ignores_bases_below_cluster_size(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie0026", "yamagoochie0026", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie0026", "2026-09-08T00:00:00+00:00")

    rows = db.find_queued_name_base_clusters(connection, min_base_length=3, min_cluster_size=2)

    assert rows == []


def test_find_queued_name_base_clusters_ignores_bases_shorter_than_minimum(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    # Base 'a1'/'a2' -> 'a', demasiado curta e generica; nao deve gerar falso positivo.
    db.insert_discovered_target(connection, "a1", "a1", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "a1", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "a2", "a2", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "a2", "2026-09-08T00:00:00+00:00")

    rows = db.find_queued_name_base_clusters(connection, min_base_length=3)

    assert rows == []


def test_find_queued_name_base_clusters_ignores_non_queued_targets(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(
        connection, "yamagoochie0026", "yamagoochie0026", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie0026", "2026-09-08T00:00:00+00:00")
    # Este nao esta em 'queued' (ficou 'discovered'); nao deve entrar no cluster.
    db.insert_discovered_target(
        connection, "yamagoochie26", "yamagoochie26", TargetSource.SSE, "2026-09-08T05:00:00+00:00"
    )

    rows = db.find_queued_name_base_clusters(connection, min_base_length=3)

    assert rows == []


def test_reject_targets_as_heuristic_only_affects_queued_targets(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_queued(connection, "a", now)
    db.insert_discovered_target(connection, "b", "B", TargetSource.SSE, now)
    db.mark_target_sent(connection, "b", now)  # ja nao esta 'queued'

    count = db.reject_targets_as_heuristic(
        connection, ["a", "b"], "provavel padrao novo (deteccao manual na fila)", "grand,prix", now
    )

    assert count == 1
    row_a = connection.execute("SELECT * FROM targets WHERE nation_id = 'a'").fetchone()
    assert row_a["status"] == "rejected"
    assert row_a["rejection_category"] == "heuristic"
    assert row_a["rejection_detail"] == "grand,prix"
    row_b = connection.execute("SELECT status FROM targets WHERE nation_id = 'b'").fetchone()
    assert row_b["status"] == "sent"


def test_kv_roundtrip_and_update(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    assert db.get_kv(connection, "sse_last_event_id") is None
    db.set_kv(connection, "sse_last_event_id", "100", "2026-09-08T00:00:00+00:00")
    assert db.get_kv(connection, "sse_last_event_id") == "100"
    db.set_kv(connection, "sse_last_event_id", "200", "2026-09-08T00:00:01+00:00")
    assert db.get_kv(connection, "sse_last_event_id") == "200"


def test_next_queued_target_returns_oldest_first(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "segunda", "Segunda", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "primeira", "Primeira", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "segunda", "2026-09-08T00:00:05+00:00")
    db.mark_target_queued(connection, "primeira", "2026-09-08T00:00:01+00:00")
    row = db.next_queued_target(connection)
    assert row is not None
    assert row["nation_id"] == "primeira"


def test_list_queued_targets_returns_all_in_order(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    for nation_id, queued_at in (("a", "2026-09-08T00:00:02+00:00"), ("b", "2026-09-08T00:00:01+00:00")):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, queued_at)
        db.mark_target_queued(connection, nation_id, queued_at)
    rows = db.list_queued_targets(connection)
    assert [row["nation_id"] for row in rows] == ["b", "a"]


def test_next_queued_target_prioritizes_flag_match_over_arrival_order(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "primeira", "Primeira", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "segunda", "Segunda", TargetSource.SSE, "2026-09-08T00:00:01+00:00")
    db.mark_target_queued(connection, "primeira", "2026-09-08T00:00:01+00:00", priority=False)
    db.mark_target_queued(connection, "segunda", "2026-09-08T00:00:02+00:00", priority=True)
    row = db.next_queued_target(connection)
    assert row is not None
    assert row["nation_id"] == "segunda"


def test_list_queued_targets_orders_priority_first_then_by_arrival(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "b", "B", TargetSource.SSE, "2026-09-08T00:00:01+00:00")
    db.insert_discovered_target(connection, "c", "C", TargetSource.SSE, "2026-09-08T00:00:02+00:00")
    db.mark_target_queued(connection, "a", "2026-09-08T00:00:01+00:00", priority=False)
    db.mark_target_queued(connection, "b", "2026-09-08T00:00:02+00:00", priority=False)
    db.mark_target_queued(connection, "c", "2026-09-08T00:00:03+00:00", priority=True)
    rows = db.list_queued_targets(connection)
    assert [row["nation_id"] for row in rows] == ["c", "a", "b"]


def test_toggle_target_pin_pins_then_unpins(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_queued(connection, "a", now)

    assert db.toggle_target_pin(connection, "a", "2026-09-08T00:00:01+00:00") is True
    row = connection.execute("SELECT pinned_at FROM targets WHERE nation_id = 'a'").fetchone()
    assert row["pinned_at"] == "2026-09-08T00:00:01+00:00"

    assert db.toggle_target_pin(connection, "a", "2026-09-08T00:00:02+00:00") is False
    row = connection.execute("SELECT pinned_at FROM targets WHERE nation_id = 'a'").fetchone()
    assert row["pinned_at"] is None


def test_toggle_target_pin_returns_false_for_unknown_nation(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    assert db.toggle_target_pin(connection, "nao_existe", "2026-09-08T00:00:00+00:00") is False


def test_next_queued_target_prioritizes_pin_over_flag_priority_and_arrival(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    for nation_id, ts in (("a", "2026-09-08T00:00:00+00:00"), ("b", "2026-09-08T00:00:01+00:00"), ("c", "2026-09-08T00:00:02+00:00")):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
    db.mark_target_queued(connection, "a", "2026-09-08T00:00:01+00:00", priority=False)
    db.mark_target_queued(connection, "b", "2026-09-08T00:00:02+00:00", priority=True)
    db.mark_target_queued(connection, "c", "2026-09-08T00:00:03+00:00", priority=False)

    db.toggle_target_pin(connection, "c", "2026-09-08T00:00:04+00:00")

    row = db.next_queued_target(connection)
    assert row is not None
    assert row["nation_id"] == "c"


def test_list_queued_targets_orders_most_recently_pinned_first(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    for nation_id, ts in (("a", "2026-09-08T00:00:00+00:00"), ("b", "2026-09-08T00:00:01+00:00")):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
        db.mark_target_queued(connection, nation_id, ts)

    db.toggle_target_pin(connection, "a", "2026-09-08T00:00:05+00:00")
    db.toggle_target_pin(connection, "b", "2026-09-08T00:00:06+00:00")

    rows = db.list_queued_targets(connection)
    assert [row["nation_id"] for row in rows] == ["b", "a"]


def test_list_expired_unprioritized_queued_targets_excludes_priority_and_pinned(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    for nation_id, ts in (
        ("sem_prioridade", "2026-09-08T00:00:00+00:00"),
        ("com_bandeira", "2026-09-08T00:00:01+00:00"),
        ("fixada", "2026-09-08T00:00:02+00:00"),
    ):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
    db.mark_target_queued(connection, "sem_prioridade", "2026-09-08T00:00:00+00:00", priority=False)
    db.mark_target_queued(connection, "com_bandeira", "2026-09-08T00:00:00+00:00", priority=True)
    db.mark_target_queued(connection, "fixada", "2026-09-08T00:00:00+00:00", priority=False)
    db.toggle_target_pin(connection, "fixada", "2026-09-08T00:00:03+00:00")

    rows = db.list_expired_unprioritized_queued_targets(connection, "2026-09-08T12:00:00+00:00")
    assert [row["nation_id"] for row in rows] == ["sem_prioridade"]


def test_list_expired_unprioritized_queued_targets_respects_cutoff(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    db.insert_discovered_target(connection, "recente", "Recente", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "recente", "2026-09-08T11:00:00+00:00", priority=False)
    assert db.list_expired_unprioritized_queued_targets(connection, "2026-09-08T00:00:00+00:00") == []


def test_list_expired_prioritized_queued_targets_matches_flag_or_pin(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    for nation_id, ts in (
        ("sem_prioridade", "2026-09-08T00:00:00+00:00"),
        ("com_bandeira", "2026-09-08T00:00:01+00:00"),
        ("fixada", "2026-09-08T00:00:02+00:00"),
    ):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
    db.mark_target_queued(connection, "sem_prioridade", "2026-09-08T00:00:00+00:00", priority=False)
    db.mark_target_queued(connection, "com_bandeira", "2026-09-08T00:00:00+00:00", priority=True)
    db.mark_target_queued(connection, "fixada", "2026-09-08T00:00:00+00:00", priority=False)
    db.toggle_target_pin(connection, "fixada", "2026-09-08T00:00:03+00:00")

    rows = db.list_expired_prioritized_queued_targets(connection, "2026-09-09T00:00:00+00:00")
    assert {row["nation_id"] for row in rows} == {"com_bandeira", "fixada"}


def test_mark_targets_expired_rejects_with_expired_category(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.mark_target_queued(connection, "testlandia", now)

    count = db.mark_targets_expired(connection, ["testlandia"], "expirado da fila", "2026-09-08T12:00:00+00:00")
    assert count == 1

    row = connection.execute("SELECT * FROM targets WHERE nation_id = 'testlandia'").fetchone()
    assert row["status"] == "rejected"
    assert row["rejection_category"] == "expired"
    assert row["status_reason"] == "expirado da fila"
    assert db.count_targets_by_rejection_category(connection, "expired") == 1


def test_mark_targets_expired_skips_targets_no_longer_queued(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.mark_target_queued(connection, "testlandia", now)
    db.mark_target_sent(connection, "testlandia", now)

    count = db.mark_targets_expired(connection, ["testlandia"], "expirado da fila", "2026-09-08T12:00:00+00:00")
    assert count == 0
    row = connection.execute("SELECT status FROM targets WHERE nation_id = 'testlandia'").fetchone()
    assert row["status"] == "sent"


def test_mark_target_sent_updates_status_and_clears_from_queue(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "testlandia", "Testlandia", TargetSource.SSE, now)
    db.mark_target_queued(connection, "testlandia", now)
    db.mark_target_sent(connection, "testlandia", now)
    row = connection.execute("SELECT * FROM targets WHERE nation_id = ?", ("testlandia",)).fetchone()
    assert row["status"] == "sent"
    assert db.next_queued_target(connection) is None


def test_get_last_successful_send_at_picks_most_recent_success(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    assert db.get_last_successful_send_at(connection) is None
    db.insert_send_history(
        connection, "a", "2026-09-08T00:00:00+00:00", success=True,
        http_status=200, retry_after=None, error_reason=None,
    )
    db.insert_send_history(
        connection, "a", "2026-09-08T00:05:00+00:00", success=False,
        http_status=429, retry_after=180.0, error_reason="cooldown",
    )
    assert db.get_last_successful_send_at(connection) == "2026-09-08T00:00:00+00:00"


def test_insert_send_history_stores_blocked_externally_flag(tmp_path: Path) -> None:
    connection = _connection(tmp_path)
    now = "2026-09-08T00:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.insert_send_history(
        connection, "a", now, success=False, http_status=429,
        retry_after=180.0, error_reason="cooldown", blocked_externally=True,
    )
    row = connection.execute("SELECT * FROM send_history WHERE nation_id = 'a'").fetchone()
    assert row["blocked_externally"] == 1
