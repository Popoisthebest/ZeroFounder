from datetime import UTC, datetime, timedelta

import pytest

from agents.schemas import InferenceReservation, UsageDay, UsageLedger
from agents.usage_limiter import (
    UsageLimiter,
    UsageLimitReached,
    request_fingerprint,
    required_inference_calls,
)


def fp(value: str) -> str:
    return request_fingerprint({"value": value})


def test_skipped_run_does_not_increase_inference_usage():
    limiter = UsageLimiter(daily_limit=2)
    before = limiter.today().inference_calls
    assert limiter.run_usage()["completed_inference_calls"] == 0
    assert limiter.today().inference_calls == before


def test_blocked_run_does_not_increase_usage():
    today = datetime.now(UTC).date()
    day = UsageDay(date=today, chat_calls=2, completed_inference_calls=2)
    limiter = UsageLimiter(UsageLedger(days=[day]), daily_limit=2)
    with pytest.raises(UsageLimitReached):
        limiter.reserve("chat", fp("blocked"))
    assert day.inference_calls == 2
    assert day.reserved_inference_calls == 0


def test_actual_request_is_recorded_once():
    limiter = UsageLimiter(daily_limit=2)
    reservation = limiter.reserve("chat", fp("actual"))
    limiter.complete_request(reservation, failed_after_request=False)
    day = limiter.today()
    assert day.completed_inference_calls == 1
    assert day.chat_calls == 1
    assert day.reserved_inference_calls == 0
    assert len(day.call_records) == 1


def test_daily_and_per_run_limits():
    limiter = UsageLimiter(daily_limit=2)
    first = limiter.reserve("chat", fp("a"))
    limiter.complete_request(first, failed_after_request=False)
    second = limiter.reserve("embedding", fp("b"))
    limiter.complete_request(second, failed_after_request=False)
    with pytest.raises(UsageLimitReached):
        limiter.reserve("chat", fp("c"))


def test_duplicate_request_rejected_after_completed_request():
    limiter = UsageLimiter(daily_limit=8)
    reservation = limiter.reserve("chat", fp("same"))
    limiter.complete_request(reservation, failed_after_request=False)
    with pytest.raises(UsageLimitReached):
        limiter.reserve("chat", fp("same"))


def test_diagnostic_mode_requires_one_call():
    assert required_inference_calls(True) == 1
    assert required_inference_calls(False) == 2


def test_stale_reservation_is_automatically_released():
    current = datetime(2026, 7, 20, 12, tzinfo=UTC)
    stale = InferenceReservation(
        reservation_id="res-stale",
        kind="chat",
        fingerprint=fp("stale"),
        reserved_at=current - timedelta(hours=1),
        run_id="old-run",
    )
    day = UsageDay(
        date=current.date(),
        reserved_inference_calls=1,
        reservations=[stale],
    )
    limiter = UsageLimiter(
        UsageLedger(days=[day]),
        daily_limit=8,
        reservation_ttl_seconds=60,
        now=lambda: current,
    )
    assert limiter.today().reserved_inference_calls == 0
    assert limiter.today().reservations == []


def test_release_run_reservations_cleans_up_exception_capacity():
    limiter = UsageLimiter(daily_limit=8)
    limiter.reserve("chat", fp("exception"))
    assert limiter.today().reserved_inference_calls == 1
    assert limiter.release_run_reservations() == 1
    assert limiter.today().reserved_inference_calls == 0
