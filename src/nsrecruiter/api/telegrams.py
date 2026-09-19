"""Envio de telegramas via a Telegrams API (a=sendTG). Uma chamada por destinatario."""
from __future__ import annotations

from dataclasses import dataclass

from nsrecruiter.api.client import NsApiClient, parse_retry_after, parse_x_retry_after
from nsrecruiter.api.exceptions import NetworkError
from nsrecruiter.config import Config

_DEFAULT_TG_RETRY_AFTER_SECONDS = 180.0


@dataclass
class SendOutcome:
    success: bool
    http_status: int | None
    retry_after: float | None
    error_reason: str | None


async def send_telegram(client: NsApiClient, config: Config, to_nation: str) -> SendOutcome:
    params = {
        "a": "sendTG",
        "client": config.client_key,
        "tgid": config.telegram_id,
        "key": config.secret_key,
        "to": to_nation,
    }

    try:
        response = await client.request_for_send(params)
    except NetworkError as exc:
        return SendOutcome(success=False, http_status=None, retry_after=None, error_reason=f"erro de rede: {exc}")

    if response.status_code == 403:
        return SendOutcome(
            success=False, http_status=403, retry_after=None,
            error_reason="403 (User-Agent invalido ou nao identificavel)",
        )

    if response.status_code == 429:
        retry_after = parse_x_retry_after(response)
        if retry_after is None:
            retry_after = parse_retry_after(response) or _DEFAULT_TG_RETRY_AFTER_SECONDS
        return SendOutcome(
            success=False, http_status=429, retry_after=retry_after, error_reason="cooldown de envio ativo"
        )

    if 200 <= response.status_code < 300:
        return SendOutcome(success=True, http_status=response.status_code, retry_after=None, error_reason=None)

    return SendOutcome(
        success=False,
        http_status=response.status_code,
        retry_after=None,
        error_reason=f"HTTP {response.status_code}",
    )
