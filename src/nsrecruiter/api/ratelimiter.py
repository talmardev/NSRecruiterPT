"""Limitadores de pedidos: limite geral da API e cooldown de telegramas."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

Clock = Callable[[], float]


@dataclass
class RateLimitSnapshot:
    limit: int | None = None
    remaining: int | None = None
    reset_seconds: float | None = None


class GeneralRateLimiter:
    """Limite partilhado por todos os pedidos HTTP (validacao e envio)."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._remaining: int | None = None
        self._limit: int | None = None
        self._reset_at: float | None = None
        self._suspended_until: float = 0.0

    @property
    def remaining(self) -> int | None:
        """Pedidos que sobram na janela atual, segundo a ultima resposta. None antes da primeira."""
        return self._remaining

    @property
    def limit(self) -> int | None:
        """Tamanho total da janela (RateLimit-Limit), segundo a ultima resposta."""
        return self._limit

    def update_from_headers(self, snapshot: RateLimitSnapshot) -> None:
        now = self._clock()
        if snapshot.limit is not None:
            self._limit = snapshot.limit
        if snapshot.remaining is not None:
            self._remaining = snapshot.remaining
        if snapshot.reset_seconds is not None:
            self._reset_at = now + snapshot.reset_seconds

    def register_429(self, retry_after: float) -> None:
        now = self._clock()
        self._suspended_until = max(self._suspended_until, now + retry_after)
        self._remaining = 0

    def wait_time(self) -> float:
        now = self._clock()
        wait_for_suspension = max(0.0, self._suspended_until - now)
        if self._remaining is not None and self._remaining <= 0 and self._reset_at is not None:
            wait_for_budget = max(0.0, self._reset_at - now)
        else:
            wait_for_budget = 0.0
        return max(wait_for_suspension, wait_for_budget)


class TelegramRateLimiter:
    """Cooldown especifico de envio de telegramas (182s por omissao)."""

    def __init__(self, interval_seconds: float, clock: Clock = time.monotonic) -> None:
        self._interval_seconds = interval_seconds
        self._clock = clock
        self._next_allowed_at: float = 0.0

    def seconds_until_next_send(self) -> float:
        return max(0.0, self._next_allowed_at - self._clock())

    def mark_sent(self) -> None:
        self._next_allowed_at = self._clock() + self._interval_seconds

    def mark_retry_after(self, retry_after: float) -> None:
        # max() garante que um X-Retry-After menor nunca encurta uma espera ja agendada.
        candidate = self._clock() + retry_after + 2.0
        self._next_allowed_at = max(self._next_allowed_at, candidate)

    def restore_last_sent_at(self, last_sent_timestamp: float) -> None:
        """Reconstroi o cooldown a partir de um envio anterior (retomar apos reiniciar).

        `last_sent_timestamp` tem de estar na mesma base temporal que o `clock`
        injetado (por omissao, ambos usam `time.monotonic`; em producao usa-se
        `time.time` nos dois para o cooldown sobreviver a um restart)."""
        candidate = last_sent_timestamp + self._interval_seconds
        self._next_allowed_at = max(self._next_allowed_at, candidate)
