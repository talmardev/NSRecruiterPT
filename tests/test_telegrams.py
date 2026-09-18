"""Testes de send_telegram: interpretacao da resposta, com a rede sempre mockada.

Nunca aponta para a API real, nem com chaves falsas. Um pedido a=sendTG contra
o servidor real seria uma acao real contra um servico de terceiros, mesmo que as
chaves sejam invalidas.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from nsrecruiter.api.client import NsApiClient
from nsrecruiter.api.ratelimiter import GeneralRateLimiter
from nsrecruiter.api.telegrams import send_telegram
from nsrecruiter.config import Config


def _config() -> Config:
    return Config(
        region="minharegiao",
        nation="Teste",
        contact=None,
        client_key="clientkey",
        telegram_id="123",
        secret_key="secretkey",
        send_interval_seconds=182.0,
        db_path=Path("unused.db"),
        lockfile_path=Path("unused.lock"),
        log_dir=Path("unused_logs"),
        log_level="INFO",
    )


def _client_with(handler) -> tuple[NsApiClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api_client = NsApiClient("UA/1.0 (nation:Teste)", GeneralRateLimiter(), http_client=http_client)
    return api_client, http_client


def test_send_telegram_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    api_client, http_client = _client_with(handler)

    async def scenario() -> None:
        outcome = await send_telegram(api_client, _config(), "alvo")
        assert outcome.success is True
        assert outcome.http_status == 200
        await http_client.aclose()

    asyncio.run(scenario())


def test_send_telegram_cooldown_429_reads_x_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"X-Retry-After": "175"}, content=b"")

    api_client, http_client = _client_with(handler)

    async def scenario() -> None:
        outcome = await send_telegram(api_client, _config(), "alvo")
        assert outcome.success is False
        assert outcome.http_status == 429
        assert outcome.retry_after == 175.0
        await http_client.aclose()

    asyncio.run(scenario())


def test_send_telegram_429_without_x_retry_after_falls_back_to_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"")

    api_client, http_client = _client_with(handler)

    async def scenario() -> None:
        outcome = await send_telegram(api_client, _config(), "alvo")
        assert outcome.success is False
        assert outcome.retry_after == 180.0
        await http_client.aclose()

    asyncio.run(scenario())


def test_send_telegram_403_is_reported_but_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"")

    api_client, http_client = _client_with(handler)

    async def scenario() -> None:
        outcome = await send_telegram(api_client, _config(), "alvo")
        assert outcome.success is False
        assert outcome.http_status == 403
        await http_client.aclose()

    asyncio.run(scenario())


def test_send_telegram_uses_one_call_per_recipient_with_expected_params() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(200, content=b"")

    api_client, http_client = _client_with(handler)

    async def scenario() -> None:
        await send_telegram(api_client, _config(), "alvo")
        await http_client.aclose()

    asyncio.run(scenario())

    assert len(seen_requests) == 1
    query = dict(seen_requests[0].url.params)
    assert query["a"] == "sendTG"
    assert query["client"] == "clientkey"
    assert query["tgid"] == "123"
    assert query["key"] == "secretkey"
    assert query["to"] == "alvo"
    assert query["v"] == "13"
