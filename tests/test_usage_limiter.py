from datetime import UTC, datetime

import pytest

from agents.schemas import UsageDay, UsageLedger
from agents.usage_limiter import UsageLimiter, UsageLimitReached


def test_daily_and_per_run_limits():
    limiter = UsageLimiter(daily_limit=2)
    limiter.reserve("chat", "a")
    limiter.reserve("embedding", "b")
    with pytest.raises(UsageLimitReached):
        limiter.reserve("chat", "c")


def test_duplicate_request_rejected():
    today = datetime.now(UTC).date()
    limiter = UsageLimiter(UsageLedger(days=[UsageDay(date=today)]), daily_limit=8)
    limiter.reserve("chat", "same")
    with pytest.raises(UsageLimitReached):
        limiter.reserve("chat", "same")
