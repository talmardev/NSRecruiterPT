"""Cliente HTTP para a API do NationStates: User-Agent, versao fixa e limite geral partilhado."""
from __future__ import annotations

import asyncio

import httpx

from nsrecruiter.api.exceptions import ForbiddenError, GeneralRateLimitedError, NetworkError, NotFoundError
from nsrecruiter.api.ratelimiter import GeneralRateLimiter, RateLimitSnapshot

API_BASE_URL = "https://www.nationstates.net/cgi-bin/api.cgi"
API_VERSION = "13"
DEFAULT_TIMEOUT_SECONDS = 15.0


def parse_rate_limit_headers(response: httpx.Response) -> RateLimitSnapshot:
    def _parse_int(name: str) -> int | None:
        raw = response.headers.get(name)
        return int(raw) if raw is not None and raw.isdigit() else None

    def _parse_float(name: str) -> float | None:
        raw = response.headers.get(name)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    return RateLimitSnapshot(
        limit=_parse_int("RateLimit-Limit"),
        remaining=_parse_int("RateLimit-Remaining"),
        reset_seconds=_parse_float("RateLimit-Reset"),
    )


def _parse_seconds_header(response: httpx.Response, name: str) -> float | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_retry_after(response: httpx.Response) -> float | None:
    return _parse_seconds_header(response, "Retry-After")


def parse_x_retry_after(response: httpx.Response) -> float | None:
    return _parse_seconds_header(response, "X-Retry-After")


class NsApiClient:
    """Ponto unico de acesso ao api.cgi -- fixa v=13 e aplica sempre o limite geral."""

    def __init__(
        self,
        user_agent: str,
        general_limiter: GeneralRateLimiter,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._limiter = general_limiter
        self._http = http_client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)
        self._owns_http_client = http_client is None

    async def _raw_request(self, params: dict[str, str]) -> httpx.Response:
        """Aplica o limite geral (espera + atualizacao a partir dos headers) e devolve a
        resposta tal e qual, sem interpretar o status code -- isso fica para quem chama."""
        await asyncio.sleep(self._limiter.wait_time())
        query = {**params, "v": API_VERSION}
        try:
            response = await self._http.get(
                API_BASE_URL, params=query, headers={"User-Agent": self._user_agent}
            )
        except httpx.HTTPError as exc:
            raise NetworkError(str(exc)) from exc

        self._limiter.update_from_headers(parse_rate_limit_headers(response))
        return response

    async def request(self, params: dict[str, str]) -> httpx.Response:
        """Para shards de leitura: lanca excecao em 403/429 (aqui um 429 so pode ser o
        limite geral, nao ha nenhum outro cooldown envolvido nestes pedidos)."""
        response = await self._raw_request(params)

        if response.status_code == 403:
            raise ForbiddenError(
                "A API devolveu 403 -- confirma se o User-Agent esta correto e identificavel."
            )
        if response.status_code == 404:
            alvo = params.get("nation") or params.get("region") or "?"
            raise NotFoundError(f"A API devolveu 404 -- nao existe: {alvo}")
        if response.status_code == 429:
            retry_after = parse_retry_after(response) or 30.0
            self._limiter.register_429(retry_after)
            raise GeneralRateLimitedError(retry_after)

        return response

    async def request_for_send(self, params: dict[str, str]) -> httpx.Response:
        """Para a=sendTG: nunca lanca em 429 -- pode ser o cooldown normal de TG
        (X-Retry-After), que nao e um erro. So atualiza o limite geral se a resposta
        trouxer tambem um Retry-After genuino (sinal de que o limite geral foi atingido)."""
        response = await self._raw_request(params)
        if response.status_code == 429:
            general_retry_after = parse_retry_after(response)
            if general_retry_after is not None:
                self._limiter.register_429(general_retry_after)
        return response

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()
