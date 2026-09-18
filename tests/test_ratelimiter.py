"""Testes do limitador de pedidos, com relogio simulado (sem rede real)."""
from __future__ import annotations

from nsrecruiter.api.ratelimiter import GeneralRateLimiter, RateLimitSnapshot, TelegramRateLimiter


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_general_limiter_no_wait_before_first_response() -> None:
    limiter = GeneralRateLimiter(clock=FakeClock())
    assert limiter.wait_time() == 0.0


def test_general_limiter_waits_for_reset_when_budget_exhausted() -> None:
    clock = FakeClock()
    limiter = GeneralRateLimiter(clock=clock)
    limiter.update_from_headers(RateLimitSnapshot(limit=50, remaining=0, reset_seconds=30.0))
    assert limiter.wait_time() == 30.0
    clock.advance(10.0)
    assert limiter.wait_time() == 20.0


def test_general_limiter_exposes_limit_and_remaining_for_the_dashboard() -> None:
    limiter = GeneralRateLimiter(clock=FakeClock())
    assert limiter.limit is None
    assert limiter.remaining is None
    limiter.update_from_headers(RateLimitSnapshot(limit=50, remaining=37, reset_seconds=12.0))
    assert limiter.limit == 50
    assert limiter.remaining == 37


def test_general_limiter_no_wait_when_budget_available() -> None:
    limiter = GeneralRateLimiter(clock=FakeClock())
    limiter.update_from_headers(RateLimitSnapshot(limit=50, remaining=12, reset_seconds=30.0))
    assert limiter.wait_time() == 0.0


def test_general_limiter_respects_429_suspension() -> None:
    clock = FakeClock()
    limiter = GeneralRateLimiter(clock=clock)
    limiter.register_429(retry_after=45.0)
    assert limiter.wait_time() == 45.0
    clock.advance(45.0)
    assert limiter.wait_time() == 0.0


def test_general_limiter_429_suspension_never_shrinks() -> None:
    clock = FakeClock()
    limiter = GeneralRateLimiter(clock=clock)
    limiter.register_429(retry_after=45.0)
    limiter.register_429(retry_after=5.0)
    assert limiter.wait_time() == 45.0


def test_telegram_limiter_enforces_interval_after_send() -> None:
    clock = FakeClock()
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    assert limiter.seconds_until_next_send() == 0.0
    limiter.mark_sent()
    assert limiter.seconds_until_next_send() == 182.0
    clock.advance(182.0)
    assert limiter.seconds_until_next_send() == 0.0


def test_telegram_limiter_retry_after_bigger_than_cooldown_wins() -> None:
    clock = FakeClock()
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    limiter.mark_sent()
    assert limiter.seconds_until_next_send() == 182.0

    limiter.mark_retry_after(retry_after=300.0)
    assert limiter.seconds_until_next_send() == 302.0


def test_telegram_limiter_retry_after_never_shortens_existing_wait() -> None:
    clock = FakeClock()
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    limiter.mark_sent()
    limiter.mark_retry_after(retry_after=5.0)
    assert limiter.seconds_until_next_send() == 182.0


def test_restore_last_sent_at_reconstructs_cooldown_after_restart() -> None:
    clock = FakeClock(start=1000.0)
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    # Um envio bem sucedido ocorreu 100s antes do "restart" (na mesma base temporal do clock).
    limiter.restore_last_sent_at(last_sent_timestamp=900.0)
    assert limiter.seconds_until_next_send() == 82.0


def test_restore_last_sent_at_does_not_shorten_a_longer_existing_wait() -> None:
    clock = FakeClock()
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    limiter.mark_retry_after(retry_after=300.0)
    limiter.restore_last_sent_at(last_sent_timestamp=0.0)
    assert limiter.seconds_until_next_send() == 302.0


def test_restore_last_sent_at_in_the_past_allows_immediate_send() -> None:
    clock = FakeClock(start=10_000.0)
    limiter = TelegramRateLimiter(interval_seconds=182.0, clock=clock)
    limiter.restore_last_sent_at(last_sent_timestamp=0.0)
    assert limiter.seconds_until_next_send() == 0.0
