from datetime import UTC, datetime, timedelta

from agents.schemas import InferenceReservation, UsageDay
from agents.usage_limiter import request_fingerprint
from scripts.reconcile_usage import reconcile_day


def test_reconcile_removes_stale_and_legacy_non_http_usage():
    now = datetime(2026, 7, 20, 12, tzinfo=UTC)
    fingerprint = request_fingerprint({"old": True})
    day = UsageDay(
        date=now.date(),
        reserved_inference_calls=1,
        inference_call_upper_bound=8,
        request_fingerprints=[fingerprint],
        reservations=[
            InferenceReservation(
                reservation_id="res-old",
                kind="chat",
                fingerprint=fingerprint,
                reserved_at=now - timedelta(hours=1),
                run_id="old-run",
            )
        ],
    )
    changes = reconcile_day(day, now=now, reservation_ttl_seconds=60)
    assert changes["reserved_inference_calls"] == 1
    assert changes["inference_call_upper_bound"] == 8
    assert changes["request_fingerprints"] == 1
    assert day.inference_calls == 0
    assert day.reservations == []
