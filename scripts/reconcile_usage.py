from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.schemas import UsageDay, UsageLedger  # noqa: E402


def parse_date(value: str) -> date:
    return datetime.now(UTC).date() if value == "today" else date.fromisoformat(value)


def reconcile_day(
    day: UsageDay,
    *,
    now: datetime,
    reservation_ttl_seconds: int,
) -> dict[str, int]:
    before = {
        "completed_inference_calls": day.inference_calls,
        "reserved_inference_calls": day.reserved_inference_calls,
        "failed_after_request_calls": day.failed_after_request_calls,
        "skipped_runs": day.skipped_runs,
        "inference_call_upper_bound": day.inference_call_upper_bound,
        "request_fingerprints": len(day.request_fingerprints),
    }
    cutoff = now - timedelta(seconds=reservation_ttl_seconds)
    day.reservations = [item for item in day.reservations if item.reserved_at >= cutoff]
    day.reserved_inference_calls = len(day.reservations)

    # This legacy field was derived from scheduled/skipped job count, not HTTP requests.
    day.inference_call_upper_bound = 0

    if day.inference_calls == 0 and not day.call_records:
        active_fingerprints = {item.fingerprint for item in day.reservations}
        day.request_fingerprints = [
            item for item in day.request_fingerprints if item in active_fingerprints
        ]
    confirmed_fingerprints = {item.fingerprint for item in day.call_records}
    for fingerprint in confirmed_fingerprints:
        if fingerprint not in day.request_fingerprints:
            day.request_fingerprints.append(fingerprint)

    after = {
        "completed_inference_calls": day.inference_calls,
        "reserved_inference_calls": day.reserved_inference_calls,
        "failed_after_request_calls": day.failed_after_request_calls,
        "skipped_runs": day.skipped_runs,
        "inference_call_upper_bound": day.inference_call_upper_bound,
        "request_fingerprints": len(day.request_fingerprints),
    }
    return {
        key: before[key] - after[key]
        for key in before
        if before[key] != after[key]
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="today")
    parser.add_argument("--path", type=Path, default=Path("company/usage.json"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    ledger = (
        UsageLedger.model_validate_json(args.path.read_text())
        if args.path.exists()
        else UsageLedger()
    )
    target = parse_date(args.date)
    day = next((item for item in ledger.days if item.date == target), None)
    if day is None:
        day = UsageDay(date=target)
        ledger.days.append(day)
    changes = reconcile_day(
        day,
        now=datetime.now(UTC),
        reservation_ttl_seconds=int(os.getenv("MODEL_RESERVATION_TTL_SECONDS", "1800")),
    )
    changed = bool(changes)
    if args.apply and changed:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(ledger.model_dump_json(indent=2) + "\n")
    print(
        json.dumps(
            {
                "date": target.isoformat(),
                "mode": "apply" if args.apply else "dry-run",
                "changed": changed,
                "changes": changes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
