"""Testes do dashboard: logica pura (estado, formatacao) e renderizacao offline com Rich."""
from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console

from nsrecruiter import dashboard, db
from nsrecruiter.api.ratelimiter import GeneralRateLimiter, RateLimitSnapshot, TelegramRateLimiter
from nsrecruiter.config import Config
from nsrecruiter.logging_setup import LogEntry
from nsrecruiter.models import AppState, TargetSource
from nsrecruiter.runtime_status import RuntimeStatus


def test_format_mmss_zero_pads() -> None:
    assert dashboard._format_mmss(0) == "00:00"
    assert dashboard._format_mmss(5) == "00:05"
    assert dashboard._format_mmss(134) == "02:14"
    assert dashboard._format_mmss(-3) == "00:00"


def test_format_duration_zero_pads() -> None:
    assert dashboard._format_duration(0) == "00:00:00"
    assert dashboard._format_duration(3725) == "01:02:05"


def test_make_bar_has_fixed_width_at_any_fraction() -> None:
    for fraction in (0.0, 0.01, 0.5, 0.99, 1.0):
        bar = dashboard._make_bar(fraction, style="green")
        assert len(bar.plain) == dashboard._BAR_WIDTH


def test_make_bar_clamps_out_of_range_fractions() -> None:
    assert dashboard._make_bar(-1.0, style="green").plain == dashboard._BAR_EMPTY_CHAR * dashboard._BAR_WIDTH
    assert dashboard._make_bar(2.0, style="green").plain == dashboard._BAR_FILLED_CHAR * dashboard._BAR_WIDTH


def test_make_bar_fill_proportional_to_fraction() -> None:
    bar = dashboard._make_bar(0.25, style="green")
    filled = bar.plain.count(dashboard._BAR_FILLED_CHAR)
    assert filled == round(0.25 * dashboard._BAR_WIDTH)


def test_limit_color_thresholds() -> None:
    assert dashboard._limit_color(None, None) == "grey70"
    assert dashboard._limit_color(40, 50) == "green"
    assert dashboard._limit_color(15, 50) == "yellow"
    assert dashboard._limit_color(5, 50) == "red"


def test_compute_app_state_paused_overrides_everything() -> None:
    runtime_status = RuntimeStatus()
    runtime_status.toggle_pause()
    limiter = GeneralRateLimiter()
    limiter.register_429(retry_after=100.0)
    assert dashboard.compute_app_state(runtime_status, limiter) is AppState.PAUSED


def test_compute_app_state_rate_limited_when_general_limiter_suspended() -> None:
    runtime_status = RuntimeStatus()
    limiter = GeneralRateLimiter()
    limiter.register_429(retry_after=30.0)
    assert dashboard.compute_app_state(runtime_status, limiter) is AppState.RATE_LIMITED


def test_compute_app_state_blocked_externally() -> None:
    runtime_status = RuntimeStatus()
    runtime_status.mark_blocked_externally(60.0)
    limiter = GeneralRateLimiter()
    assert dashboard.compute_app_state(runtime_status, limiter) is AppState.BLOCKED


def test_compute_app_state_sending_while_dispatching() -> None:
    runtime_status = RuntimeStatus()
    runtime_status.currently_dispatching = True
    limiter = GeneralRateLimiter()
    assert dashboard.compute_app_state(runtime_status, limiter) is AppState.SENDING


def test_compute_app_state_waiting_by_default() -> None:
    runtime_status = RuntimeStatus()
    limiter = GeneralRateLimiter()
    assert dashboard.compute_app_state(runtime_status, limiter) is AppState.WAITING


def _config(tmp_path: Path) -> Config:
    return Config(
        region="Portugal",
        nation="New Libertalia Kingdom",
        contact=None,
        client_key="clientkey1234567890",
        telegram_id="123456789",
        secret_key="secretkeyabcdef1234567890",
        send_interval_seconds=182.0,
        db_path=tmp_path / "test.db",
        lockfile_path=tmp_path / "test.lock",
        log_dir=tmp_path / "logs",
        log_level="INFO",
    )


def test_gather_state_reflects_db_contents(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    now = "2026-09-08T12:00:00+00:00"
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, now)
    db.mark_target_queued(connection, "a", now)
    db.insert_discovered_target(connection, "b", "B", TargetSource.SSE, now)
    db.mark_target_rejected(connection, "b", "ja esta na regiao", now)

    general_limiter = GeneralRateLimiter()
    general_limiter.update_from_headers(RateLimitSnapshot(limit=50, remaining=42, reset_seconds=20.0))
    tg_limiter = TelegramRateLimiter(interval_seconds=182.0)
    runtime_status = RuntimeStatus()
    log_entries = [LogEntry(timestamp="12:00:00", level="INFO", message="ola")]

    state = dashboard.gather_state(
        connection, _config(tmp_path), general_limiter, tg_limiter, runtime_status,
        log_entries, start_time=time.monotonic(), dry_run=False,
    )

    assert state.region == "Portugal"
    assert state.queue_size == 1
    assert state.rejected_total == 1
    assert state.general_remaining == 42
    assert state.general_limit == 50
    assert state.app_state is AppState.WAITING
    assert state.log_entries == log_entries
    connection.close()


def test_render_produces_output_without_crashing(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    general_limiter = GeneralRateLimiter()
    tg_limiter = TelegramRateLimiter(interval_seconds=182.0)
    runtime_status = RuntimeStatus()

    state = dashboard.gather_state(
        connection, _config(tmp_path), general_limiter, tg_limiter, runtime_status,
        [LogEntry(timestamp="12:00:00", level="ERROR", message="algo correu mal")],
        start_time=time.monotonic(), dry_run=True,
    )

    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100)
    console.print(layout)
    output = console.export_text()

    assert "Portugal" in output
    assert "New Libertalia Kingdom" in output
    assert "EM ESPERA" in output
    assert "MODO DRY-RUN" in output
    assert "algo correu mal" in output
    connection.close()


def test_move_selection_clamps_at_bounds(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    for nation_id, ts in (
        ("a", "2026-09-08T00:00:00+00:00"),
        ("b", "2026-09-08T00:00:01+00:00"),
        ("c", "2026-09-08T00:00:02+00:00"),
    ):
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
        db.mark_target_queued(connection, nation_id, ts)
    rows = db.list_queued_targets(connection)

    assert dashboard._move_selection(rows, "a", delta=1) == "b"
    assert dashboard._move_selection(rows, "c", delta=1) == "c"
    assert dashboard._move_selection(rows, "a", delta=-1) == "a"
    assert dashboard._move_selection(rows, None, delta=0) == "a"
    assert dashboard._move_selection([], "a", delta=1) is None
    connection.close()


def test_visible_queue_window_returns_all_rows_when_within_window(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "a", "2026-09-08T00:00:00+00:00")
    rows = db.list_queued_targets(connection)

    visible, selected_index = dashboard._visible_queue_window(rows, "a", window_size=12)
    assert [row["nation_id"] for row in visible] == ["a"]
    assert selected_index == 0
    connection.close()


def test_visible_queue_window_centers_on_selection_for_long_queues(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    for i in range(20):
        nation_id = f"n{i:02d}"
        ts = f"2026-09-08T00:00:{i:02d}+00:00"
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, ts)
        db.mark_target_queued(connection, nation_id, ts)
    rows = db.list_queued_targets(connection)

    visible, selected_index = dashboard._visible_queue_window(rows, "n15", window_size=10)
    assert len(visible) == 10
    assert visible[selected_index]["nation_id"] == "n15"
    connection.close()


def test_gather_state_queue_view_active_populates_rows(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "a", "2026-09-08T00:00:00+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        queue_view_active=True, queue_selected_nation_id="a",
    )
    assert state.queue_view_active is True
    assert [row["nation_id"] for row in state.queue_rows] == ["a"]
    assert state.queue_selected_nation_id == "a"
    connection.close()


def test_gather_state_queue_view_inactive_leaves_rows_empty(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    db.insert_discovered_target(connection, "a", "A", TargetSource.SSE, "2026-09-08T00:00:00+00:00")
    db.mark_target_queued(connection, "a", "2026-09-08T00:00:00+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
    )
    assert state.queue_view_active is False
    assert state.queue_rows == []
    connection.close()


def test_render_queue_view_shows_nations_and_tags(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    db.insert_discovered_target(
        connection, "alvo", "Nova Silvania", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    db.mark_target_queued(connection, "alvo", "2026-09-08T00:00:00+00:00", priority=True)
    db.toggle_target_pin(connection, "alvo", "2026-09-08T00:00:01+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        queue_view_active=True, queue_selected_nation_id="alvo",
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    # Altura explicita: o painel do corpo tem tamanho flexivel (ratio=1) partilhado com
    # header/log/footer de tamanho fixo: sem isto a Console usa uma altura pequena a
    # mais para o corpo caber, e o teste nem chegaria a ver o conteudo da fila.
    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "Nova Silvania" in output
    assert "FIXADO" in output
    assert "bandeira" in output
    assert "fixar/desfixar" in output
    connection.close()


def test_render_queue_view_shows_empty_message_when_queue_is_empty(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        queue_view_active=True, queue_selected_nation_id=None,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "fila vazia" in output
    connection.close()


def _reject_heuristic(connection, nation_id: str, reason: str, detail: str | None, when: str) -> None:
    db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, when)
    db.mark_target_rejected(connection, nation_id, reason, when, heuristic=True, detail=detail)


def test_move_cluster_selection_clamps_at_bounds(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    _reject_heuristic(connection, "a", "provavel lote gerado", "grand,prix", "2026-09-08T00:00:00+00:00")
    _reject_heuristic(connection, "b", "provavel alt", "yamagoochie", "2026-09-08T00:00:01+00:00")
    clusters = db.list_rejection_clusters(connection, "heuristic")
    keys = [dashboard._cluster_key(row) for row in clusters]

    assert dashboard._move_cluster_selection(clusters, keys[0], delta=1) == keys[1]
    assert dashboard._move_cluster_selection(clusters, keys[1], delta=1) == keys[1]
    assert dashboard._move_cluster_selection(clusters, keys[0], delta=-1) == keys[0]
    assert dashboard._move_cluster_selection([], keys[0], delta=1) is None
    connection.close()


def test_gather_state_rejection_review_active_populates_clusters(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    _reject_heuristic(connection, "a", "provavel lote gerado", "grand,prix", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(connection, "b", "b", TargetSource.SSE, "2026-09-08T00:00:01+00:00")
    db.mark_target_rejected(connection, "b", "tgcanrecruit=0", "2026-09-08T00:00:01+00:00")  # nao heuristico

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True,
    )
    assert state.pattern_rejected_total == 1
    assert len(state.rejection_clusters) == 1
    assert state.rejection_clusters[0]["rejection_detail"] == "grand,prix"
    connection.close()


def test_gather_state_rejection_review_inactive_leaves_clusters_empty_but_counts_total(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    _reject_heuristic(connection, "a", "provavel lote gerado", "grand,prix", "2026-09-08T00:00:00+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
    )
    assert state.rejection_review_active is False
    assert state.rejection_clusters == []
    assert state.pattern_rejected_total == 1
    connection.close()


def test_render_rejection_review_shows_cluster_and_detail(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    _reject_heuristic(connection, "a", "provavel lote gerado", "grand,prix", "2026-09-08T00:00:00+00:00")
    _reject_heuristic(connection, "c", "provavel lote gerado", "grand,prix", "2026-09-08T00:00:05+00:00")

    clusters = db.list_rejection_clusters(connection, "heuristic")
    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_selected_key=dashboard._cluster_key(clusters[0]),
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "provavel lote gerado" in output
    assert "grand,prix" in output
    assert "2" in output
    connection.close()


def test_render_rejection_review_shows_empty_message_when_no_clusters(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "nenhuma rejeicao" in output
    connection.close()


def test_render_footer_mentions_rejection_review_shortcut(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=140)
    console.print(layout)
    output = console.export_text()

    assert "rever rejeicoes" in output
    connection.close()


def test_cluster_token_share_pairs_groups_direct_pair() -> None:
    pairs = [("a", "A", "b", "B", frozenset({"grand", "prix"}))]
    clusters = dashboard._cluster_token_share_pairs(pairs)
    assert len(clusters) == 1
    assert clusters[0]["nation_ids"] == ["a", "b"]
    assert clusters[0]["tokens"] == {"grand", "prix"}
    assert clusters[0]["names"] == {"a": "A", "b": "B"}


def test_cluster_token_share_pairs_merges_transitively() -> None:
    # A-B partilham um par, B-C partilham outro; nunca comparamos A com C
    # diretamente, mas devem ficar no mesmo cluster (componente conectado).
    pairs = [
        ("a", "A", "b", "B", frozenset({"grand", "prix"})),
        ("b", "B", "c", "C", frozenset({"grand", "circuit"})),
    ]
    clusters = dashboard._cluster_token_share_pairs(pairs)
    assert len(clusters) == 1
    assert clusters[0]["nation_ids"] == ["a", "b", "c"]
    assert clusters[0]["tokens"] == {"grand", "prix", "circuit"}


def test_cluster_token_share_pairs_keeps_unrelated_pairs_separate() -> None:
    pairs = [
        ("a", "A", "b", "B", frozenset({"grand", "prix"})),
        ("x", "X", "y", "Y", frozenset({"card", "coletor"})),
    ]
    clusters = dashboard._cluster_token_share_pairs(pairs)
    assert len(clusters) == 2
    assert {tuple(c["nation_ids"]) for c in clusters} == {("a", "b"), ("x", "y")}


def test_cluster_name_base_rows_groups_by_base() -> None:
    rows = [
        {"name_base": "yamagoochie", "nation_id": "yamagoochie0026", "nation_name": "yamagoochie0026"},
        {"name_base": "yamagoochie", "nation_id": "yamagoochie26", "nation_name": "yamagoochie26"},
    ]
    clusters = dashboard._cluster_name_base_rows(rows)
    assert len(clusters) == 1
    assert clusters[0]["nation_ids"] == ["yamagoochie0026", "yamagoochie26"]
    assert clusters[0]["tokens"] == {"yamagoochie"}
    assert clusters[0]["names"] == {"yamagoochie0026": "yamagoochie0026", "yamagoochie26": "yamagoochie26"}


def test_cluster_name_base_rows_keeps_different_bases_separate() -> None:
    rows = [
        {"name_base": "yamagoochie", "nation_id": "yamagoochie0026", "nation_name": "yamagoochie0026"},
        {"name_base": "yamagoochie", "nation_id": "yamagoochie26", "nation_name": "yamagoochie26"},
        {"name_base": "outra", "nation_id": "outra1", "nation_name": "outra1"},
        {"name_base": "outra", "nation_id": "outra2", "nation_name": "outra2"},
    ]
    clusters = dashboard._cluster_name_base_rows(rows)
    assert len(clusters) == 2
    assert {tuple(c["nation_ids"]) for c in clusters} == {
        ("yamagoochie0026", "yamagoochie26"),
        ("outra1", "outra2"),
    }


def test_move_suggestion_selection_clamps_at_bounds() -> None:
    clusters = [
        {"tokens": frozenset({"grand", "prix"}), "nation_ids": ["a", "b"], "names": {"a": "A", "b": "B"}},
        {"tokens": frozenset({"card", "coletor"}), "nation_ids": ["x", "y"], "names": {"x": "X", "y": "Y"}},
    ]
    key_a = dashboard._suggestion_key(clusters[0])
    key_b = dashboard._suggestion_key(clusters[1])

    assert dashboard._move_suggestion_selection(clusters, key_a, delta=1) == key_b
    assert dashboard._move_suggestion_selection(clusters, key_b, delta=1) == key_b
    assert dashboard._move_suggestion_selection([], key_a, delta=1) is None


def test_gather_state_suggestions_mode_finds_queued_pattern_ignoring_time_gap(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
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

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    assert len(state.suggested_clusters) == 1
    assert state.suggested_clusters[0]["tokens"] == {"grand", "prix"}
    connection.close()


def test_gather_state_suggestions_mode_includes_name_base_clusters(tmp_path: Path) -> None:
    """Caso real reportado pelo utilizador: 'yamagoochie0026'/'yamagoochie26' (e o resto
    do lote) ficaram 'queued' sem serem apanhados como provaveis alts. A analise manual
    da fila tem de conseguir sugeri-los mesmo sem palavra alguma partilhada (nomes sem
    '_'), que e o que find_queued_token_share_pairs sozinho nunca apanharia."""
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    db.insert_discovered_target(
        connection, "yamagoochie0026", "yamagoochie0026", TargetSource.SSE, "2026-09-08T00:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie0026", "2026-09-08T00:00:00+00:00")
    db.insert_discovered_target(
        connection, "yamagoochie26", "yamagoochie26", TargetSource.SSE, "2026-09-08T05:00:00+00:00"
    )
    db.mark_target_queued(connection, "yamagoochie26", "2026-09-08T05:00:00+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    assert len(state.suggested_clusters) == 1
    assert state.suggested_clusters[0]["nation_ids"] == ["yamagoochie0026", "yamagoochie26"]
    assert state.suggested_clusters[0]["tokens"] == {"yamagoochie"}
    connection.close()


def test_gather_state_suggestions_mode_sorts_largest_cluster_first(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    # Cluster de 2 por palavras partilhadas.
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
    # Cluster de 3 por base de nome: devia aparecer primeiro (maior).
    for suffix in ("0026", "26", "0027"):
        nation_id = f"yamagoochie{suffix}"
        db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, "2026-09-08T00:00:00+00:00")
        db.mark_target_queued(connection, nation_id, "2026-09-08T00:00:00+00:00")

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    assert len(state.suggested_clusters) == 2
    assert len(state.suggested_clusters[0]["nation_ids"]) == 3
    assert len(state.suggested_clusters[1]["nation_ids"]) == 2
    connection.close()


def test_gather_state_rejected_mode_does_not_populate_suggestions(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
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

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="rejected",
    )
    assert state.suggested_clusters == []
    connection.close()


def test_render_suggestions_shows_names_and_tokens(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
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

    initial_state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    selected_key = dashboard._suggestion_key(initial_state.suggested_clusters[0])

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
        suggestion_selected_key=selected_key,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "2018 Azerbaijan Grand Prix" in output
    assert "2018 German Grand Prix" in output
    assert "grand,prix" in output
    connection.close()


def test_render_suggestions_shows_empty_message_when_no_new_patterns(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)

    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100, height=40)
    console.print(layout)
    output = console.export_text()

    assert "nenhum padrao novo" in output
    connection.close()


def test_render_footer_mentions_analyze_and_suggestions_shortcuts(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)

    state_rejected = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="rejected",
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state_rejected)
    console = Console(record=True, width=140)
    console.print(layout)
    assert "analisar fila" in console.export_text()

    state_suggestions = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
        rejection_review_active=True, rejection_review_mode="suggestions",
    )
    layout2 = dashboard.build_layout()
    dashboard.render(layout2, state_suggestions)
    console2 = Console(record=True, width=140)
    console2.print(layout2)
    output2 = console2.export_text()
    assert "rejeitar grupo" in output2
    assert "ver confirmadas" in output2

    connection.close()


def test_render_footer_mentions_queue_view_shortcut(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    console = Console(record=True, width=100)
    console.print(layout)
    output = console.export_text()

    assert "ver/priorizar fila" in output
    connection.close()


def test_render_footer_mentions_all_shortcuts(tmp_path: Path) -> None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    state = dashboard.gather_state(
        connection, _config(tmp_path), GeneralRateLimiter(), TelegramRateLimiter(interval_seconds=182.0),
        RuntimeStatus(), [], start_time=time.monotonic(), dry_run=False,
    )
    layout = dashboard.build_layout()
    dashboard.render(layout, state)

    # Largura maior que as outras: esta e a unica linha (o rodape) com todos os
    # atalhos lado a lado, e a 100 colunas ja nao cabe tudo sem cortar.
    console = Console(record=True, width=140)
    console.print(layout)
    output = console.export_text()

    assert "pausar/retomar" in output
    assert "atualizar fila agora" in output
    assert "ver/priorizar fila" in output
    assert "rever rejeicoes" in output
    assert "sair" in output
    connection.close()
