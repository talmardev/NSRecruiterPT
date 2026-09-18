"""Testes do refresh manual (tecla 'r'): descoberta de novas nacoes + reavaliacao da
bandeira de quem ja esta na fila. Toda a rede mockada, nunca a API real."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from nsrecruiter import db
from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter
from nsrecruiter.collector import force_refresh
from nsrecruiter.config import Config
from nsrecruiter.models import TargetSource


def _config(tmp_path: Path, priority_flag_countries: tuple[str, ...] = ()) -> Config:
    return Config(
        region="minharegiao",
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
        priority_flag_countries=priority_flag_countries,
    )


def _seed_queued_target(connection, nation_id: str, queued_at: str, priority: bool = False) -> None:
    db.insert_discovered_target(connection, nation_id, nation_id, TargetSource.SSE, queued_at)
    db.mark_target_queued(connection, nation_id, queued_at, priority=priority)


def test_manual_refresh_promotes_queued_target_whose_flag_now_matches_preset(tmp_path: Path) -> None:
    flag_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "q=newnations" in url:
            return httpx.Response(200, content=b"<NEWNATIONS></NEWNATIONS>")
        if "q=flag" in url:
            flag_requests.append(dict(request.url.params)["nation"])
            return httpx.Response(
                200,
                content=b'<NATION id="alvo">'
                b"<FLAG>https://www.nationstates.net/images/flags/Portugal.svg</FLAG>"
                b"</NATION>",
            )
        raise AssertionError(f"pedido inesperado: {url}")

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        queued_at = "2026-09-08T00:00:00+00:00"
        _seed_queued_target(connection, "alvo", queued_at, priority=False)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path, priority_flag_countries=("Portugal",))

        await force_refresh(connection, api_client, config)
        await http_client.aclose()
        row = connection.execute("SELECT * FROM targets WHERE nation_id = 'alvo'").fetchone()
        connection.close()
        return row

    row = asyncio.run(scenario())
    assert flag_requests == ["alvo"]
    assert row["priority"] == 1, "a prioridade devia ter sido atualizada apos a bandeira passar a bater certo"
    assert row["queued_at"] == "2026-09-08T00:00:00+00:00", "queued_at nao pode mudar (alteraria a posicao FIFO)"


def test_manual_refresh_does_not_check_flags_when_priority_feature_disabled(tmp_path: Path) -> None:
    flag_requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "q=newnations" in url:
            return httpx.Response(200, content=b"<NEWNATIONS></NEWNATIONS>")
        if "q=flag" in url:
            flag_requests.append(dict(request.url.params)["nation"])
            return httpx.Response(200, content=b'<NATION id="alvo"><FLAG>x.svg</FLAG></NATION>')
        raise AssertionError(f"pedido inesperado: {url}")

    async def scenario():
        connection = db.connect(tmp_path / "test.db")
        db.init_schema(connection)
        _seed_queued_target(connection, "alvo", "2026-09-08T00:00:00+00:00")

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
        config = _config(tmp_path, priority_flag_countries=())

        await force_refresh(connection, api_client, config)
        await http_client.aclose()
        connection.close()

    asyncio.run(scenario())
    assert flag_requests == [], "sem PRIORITY_FLAG_COUNTRIES configurado nao ha nada para comparar"
