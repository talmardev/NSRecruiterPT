"""Testes do vigia que pede um reinicio quando deixam de aparecer nacoes novas."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nsrecruiter import db, runtime
from nsrecruiter.api.exceptions import NetworkError
from nsrecruiter.models import TargetSource


def _seconds_ago_iso(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _run_watchdog(connection, started_at_unix: float, timeout: float) -> None:
    # O cliente da API nunca e usado a serio: fetch_new_nations e substituido em cada teste.
    asyncio.run(asyncio.wait_for(runtime.watch_for_silence(connection, None, started_at_unix), timeout))


def _api_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(client: object) -> list[str]:
        return ["qualquer"]

    monkeypatch.setattr(runtime, "fetch_new_nations", _ok)


@pytest.fixture
def connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "_WATCHDOG_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(runtime, "_NO_DISCOVERY_RESTART_SECONDS", 60.0)
    ligacao = db.connect(tmp_path / "test.db")
    db.init_schema(ligacao)
    yield ligacao
    ligacao.close()


def test_watchdog_asks_for_restart_after_long_silence(connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _api_answers(monkeypatch)
    db.insert_discovered_target(connection, "antiga", "Antiga", TargetSource.SSE, _seconds_ago_iso(600))

    with pytest.raises(runtime.NoNewNationsError):
        _run_watchdog(connection, started_at_unix=time.time() - 600, timeout=2.0)


def test_watchdog_does_not_restart_while_the_network_is_down(connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corte de rede a noite inteira: ha silencio, mas reiniciar de hora a hora nao
    adianta. Fica a espera e volta a verificar quando a rede voltar."""
    probes = 0

    async def _down(client: object) -> list[str]:
        nonlocal probes
        probes += 1
        raise NetworkError("getaddrinfo failed")

    monkeypatch.setattr(runtime, "fetch_new_nations", _down)
    db.insert_discovered_target(connection, "antiga", "Antiga", TargetSource.SSE, _seconds_ago_iso(600))

    with pytest.raises(asyncio.TimeoutError):
        _run_watchdog(connection, started_at_unix=time.time() - 600, timeout=0.3)
    assert probes > 1, "devia continuar a verificar a rede, nao desistir a primeira"


def test_watchdog_stays_quiet_while_nations_keep_arriving(connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _api_answers(monkeypatch)
    db.insert_discovered_target(connection, "recente", "Recente", TargetSource.SSE, _seconds_ago_iso(5))

    with pytest.raises(asyncio.TimeoutError):
        _run_watchdog(connection, started_at_unix=time.time() - 600, timeout=0.2)


def test_watchdog_counts_from_startup_after_a_long_stop(connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _api_answers(monkeypatch)
    db.insert_discovered_target(connection, "antiga", "Antiga", TargetSource.SSE, _seconds_ago_iso(600))

    with pytest.raises(asyncio.TimeoutError):
        _run_watchdog(connection, started_at_unix=time.time(), timeout=0.2)
