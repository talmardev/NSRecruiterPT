"""Excecoes da camada de API do NationStates."""
from __future__ import annotations


class NsApiError(Exception):
    """Erro generico de comunicacao com a API do NationStates."""


class ForbiddenError(NsApiError):
    """A API devolveu 403 -- tipicamente User-Agent em falta ou invalido."""


class GeneralRateLimitedError(NsApiError):
    """A API devolveu 429 no limite geral (50 pedidos / 30s)."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Limite geral atingido; aguardar {retry_after:.0f}s (Retry-After).")
        self.retry_after = retry_after


class NotFoundError(NsApiError):
    """A API devolveu 404 -- a nacao/regiao pedida nao existe (ja nao existe, no caso
    de uma nacao que estava na fila ha algum tempo). Ao contrario de um erro de rede
    ou XML invalido, isto e permanente: nao vale a pena tentar de novo."""


class NetworkError(NsApiError):
    """Falha de rede (timeout, ligacao recusada, etc.) -- candidata a retry com backoff."""
