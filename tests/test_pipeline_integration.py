"""Coletor + validador a trabalhar juntos, com toda a rede mockada (sem tocar na API real)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter
from nsrecruiter.collector import run_collector
from nsrecruiter.config import Config
from nsrecruiter.validator import run_validator_loop

_SSE_BODY = b"id: 1\ndata: @@novanacao@@ was founded in %%outraregiao%%.\n\n"


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/founding":
        return httpx.Response(200, content=_SSE_BODY)
    if "q=tgcanrecruit" in str(request.url):
        return httpx.Response(200, content=b'<NATION id="novanacao"><TGCANRECRUIT>1</TGCANRECRUIT></NATION>')
    return httpx.Response(200, content=b"<UNKNOWN/>")


def _make_config(tmp_path: Path) -> Config:
    return Config(
        region="outraregiao",
        nation="Teste",
        contact=None,
        client_key="x",
        telegram_id="y",
        secret_key="z",
        send_interval_seconds=182.0,
        db_path=tmp_path / "test.db",
        lockfile_path=tmp_path / "test.lock",
        log_dir=tmp_path / "logs",
        log_level="INFO",
    )


async def _run_scenario(tmp_path: Path) -> str | None:
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
    config = _make_config(tmp_path)

    collector_task = asyncio.create_task(run_collector(connection, api_client, http_client, config.user_agent))
    validator_task = asyncio.create_task(run_validator_loop(connection, api_client, config, frozenset()))

    try:
        status = None
        for _ in range(100):
            row = connection.execute("SELECT status FROM targets WHERE nation_id = 'novanacao'").fetchone()
            if row is not None:
                status = row["status"]
                if status == "queued":
                    break
            await asyncio.sleep(0.05)
        return status
    finally:
        collector_task.cancel()
        validator_task.cancel()
        await asyncio.gather(collector_task, validator_task, return_exceptions=True)
        await http_client.aclose()
        connection.close()


def test_discovered_nation_flows_through_to_queued(tmp_path: Path) -> None:
    final_status = asyncio.run(_run_scenario(tmp_path))
    if final_status is None:
        pytest.fail("a nacao nunca chegou a aparecer em 'targets'")
    assert final_status == "queued"
