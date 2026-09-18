"""Cliente do stream de Server-Sent Events de fundacoes.

Nao passa pelo GeneralRateLimiter -- esta ligacao explicitamente nao conta
para o rate limit da API (ver regras da plataforma). O limite aqui e de 5
ligacoes concorrentes por IP, por isso so se abre uma de cada vez.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

FOUNDING_STREAM_URL = "https://www.nationstates.net/api/founding"


@dataclass
class SseEvent:
    id: str | None = None
    event: str = "message"
    data: str = ""


async def iter_sse_events(
    http_client: httpx.AsyncClient,
    user_agent: str,
    last_event_id: str | None = None,
) -> AsyncIterator[SseEvent]:
    """Itera os eventos de uma unica ligacao. O generator termina quando a ligacao cai."""
    headers = {"User-Agent": user_agent, "Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id

    async with http_client.stream("GET", FOUNDING_STREAM_URL, headers=headers, timeout=None) as response:
        response.raise_for_status()

        data_lines: list[str] = []
        event_id: str | None = None
        event_type = "message"

        async for raw_line in response.aiter_lines():
            line = raw_line.rstrip("\r\n")

            if line == "":
                if data_lines:
                    yield SseEvent(id=event_id, event=event_type, data="\n".join(data_lines))
                data_lines = []
                event_type = "message"
                continue

            if line.startswith(":"):
                continue  # comentario/keep-alive do servidor

            field_name, separator, value = line.partition(":")
            if not separator:
                continue
            if value.startswith(" "):
                value = value[1:]

            if field_name == "data":
                data_lines.append(value)
            elif field_name == "id":
                event_id = value
            elif field_name == "event":
                event_type = value
            # "retry" e ignorado de proposito: a reconexao e feita pelo coletor.
