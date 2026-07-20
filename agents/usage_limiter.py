from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from agents.schemas import UsageDay, UsageLedger


class UsageLimitReached(RuntimeError):
    pass


def request_fingerprint(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class UsageLimiter:
    def __init__(self, ledger: UsageLedger | None = None, daily_limit: int | None = None) -> None:
        self.ledger = ledger or UsageLedger()
        self.daily_limit = daily_limit or int(os.getenv("DAILY_MODEL_CALL_LIMIT", "8"))
        self.run_calls = 0

    @classmethod
    def from_path(cls, path: Path, daily_limit: int | None = None) -> UsageLimiter:
        if path.exists():
            return cls(UsageLedger.model_validate_json(path.read_text()), daily_limit)
        return cls(daily_limit=daily_limit)

    def today(self) -> UsageDay:
        current = datetime.now(UTC).date()
        for day in self.ledger.days:
            if day.date == current:
                return day
        day = UsageDay(date=current)
        self.ledger.days.append(day)
        return day

    def reserve(self, kind: str, fingerprint: str) -> None:
        day = self.today()
        if fingerprint in day.request_fingerprints:
            raise UsageLimitReached("duplicate model request")
        if day.inference_calls >= self.daily_limit:
            raise UsageLimitReached("daily model call limit reached")
        if self.run_calls >= 2:
            raise UsageLimitReached("per-run model call limit reached")
        if kind not in {"chat", "embedding"}:
            raise ValueError("unknown inference kind")
        if kind == "chat":
            day.chat_calls += 1
        else:
            day.embedding_calls += 1
        day.request_fingerprints.append(fingerprint)
        self.run_calls += 1

    def record_catalog(self) -> None:
        self.today().catalog_calls += 1

    def record_failure(self) -> None:
        self.today().failures += 1

    def as_daily_delta(self, target: date | None = None) -> UsageDay:
        target = target or datetime.now(UTC).date()
        for day in self.ledger.days:
            if day.date == target:
                return day
        return UsageDay(date=target)
