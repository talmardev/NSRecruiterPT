"""Testes do parser de linhas SSE (Server-Sent Events), sem rede real."""
from __future__ import annotations

import asyncio

import httpx

from nsrecruiter.api.sse import SseEvent, iter_sse_events


def _transport_for(body: bytes) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


async def _collect_events(body: bytes) -> list[SseEvent]:
    async with httpx.AsyncClient(transport=_transport_for(body)) as client:
        return [event async for event in iter_sse_events(client, "UA/1.0")]


def test_iter_sse_events_parses_id_and_data() -> None:
    body = b"id: 1\ndata: @@testlandia@@ was founded in %%testregion%%.\n\n"
    events = asyncio.run(_collect_events(body))
    assert len(events) == 1
    assert events[0].id == "1"
    assert events[0].data == "@@testlandia@@ was founded in %%testregion%%."


def test_iter_sse_events_handles_replay_gap() -> None:
    body = b"event: replay-gap\ndata: \n\nid: 2\ndata: segunda mensagem\n\n"
    events = asyncio.run(_collect_events(body))
    assert events[0].event == "replay-gap"
    assert events[1].event == "message"
    assert events[1].data == "segunda mensagem"


def test_iter_sse_events_ignores_comments() -> None:
    body = b": keep-alive\nid: 3\ndata: mensagem\n\n"
    events = asyncio.run(_collect_events(body))
    assert len(events) == 1
    assert events[0].data == "mensagem"
