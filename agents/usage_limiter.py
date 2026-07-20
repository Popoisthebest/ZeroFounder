from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from agents.schemas import (
    InferenceCallRecord,
    InferenceReservation,
    UsageDay,
    UsageLedger,
)


class UsageLimitReached(RuntimeError):
    pass


def request_fingerprint(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def required_inference_calls(diagnostic_mode: bool) -> int:
    return 1 if diagnostic_mode else 2


class UsageLimiter:
    """Reserve immediately before transport and finalize only after transport is invoked."""

    def __init__(
        self,
        ledger: UsageLedger | None = None,
        daily_limit: int | None = None,
        *,
        max_run_calls: int = 2,
        reservation_ttl_seconds: int | None = None,
        now: Callable[[], datetime] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.ledger = ledger or UsageLedger()
        self.daily_limit = (
            int(os.getenv("DAILY_MODEL_CALL_LIMIT", "8"))
            if daily_limit is None
            else daily_limit
        )
        self.max_run_calls = max_run_calls
        self.reservation_ttl_seconds = reservation_ttl_seconds or int(
            os.getenv("MODEL_RESERVATION_TTL_SECONDS", "1800")
        )
        self._now = now or (lambda: datetime.now(UTC))
        self.run_id = (run_id or os.getenv("GITHUB_RUN_ID") or "local")[:128]
        self.run_calls = 0
        self.run_failed_after_request_calls = 0
        self.run_http_failed_calls = 0
        self.run_response_validation_failed_calls = 0
        self._run_reservations: set[str] = set()
        self.cleanup_stale_reservations()

    @classmethod
    def from_path(
        cls,
        path: Path,
        daily_limit: int | None = None,
        **kwargs: object,
    ) -> UsageLimiter:
        if path.exists():
            return cls(UsageLedger.model_validate_json(path.read_text()), daily_limit, **kwargs)
        return cls(daily_limit=daily_limit, **kwargs)

    def today(self) -> UsageDay:
        current = self._now().date()
        for day in self.ledger.days:
            if day.date == current:
                return day
        day = UsageDay(date=current)
        self.ledger.days.append(day)
        return day

    def cleanup_stale_reservations(self) -> int:
        cutoff = self._now() - timedelta(seconds=self.reservation_ttl_seconds)
        released = 0
        for day in self.ledger.days:
            active = [item for item in day.reservations if item.reserved_at >= cutoff]
            released += len(day.reservations) - len(active)
            day.reservations = active
            day.reserved_inference_calls = len(active)
        return released

    def can_start(self, required_calls: int) -> bool:
        day = self.today()
        projected = day.inference_calls + day.reserved_inference_calls + required_calls
        return projected <= self.daily_limit

    def reserve(self, kind: str, fingerprint: str) -> str:
        """Create a short-lived reservation immediately before ``client.post``."""
        self.cleanup_stale_reservations()
        day = self.today()
        active_fingerprints = {item.fingerprint for item in day.reservations}
        if fingerprint in day.request_fingerprints or fingerprint in active_fingerprints:
            raise UsageLimitReached("duplicate model request")
        if day.inference_calls + day.reserved_inference_calls + 1 > self.daily_limit:
            raise UsageLimitReached("daily model call limit reached")
        if self.run_calls + len(self._run_reservations) >= self.max_run_calls:
            raise UsageLimitReached("per-run model call limit reached")
        if kind not in {"chat", "embedding"}:
            raise ValueError("unknown inference kind")
        now = self._now()
        reservation_material = f"{fingerprint}:{self.run_id}:{now.isoformat()}"
        reservation_hash = hashlib.sha256(reservation_material.encode()).hexdigest()[:24]
        reservation_id = f"res-{reservation_hash}"
        day.reservations.append(
            InferenceReservation(
                reservation_id=reservation_id,
                kind=kind,
                fingerprint=fingerprint,
                reserved_at=now,
                run_id=self.run_id,
            )
        )
        day.reserved_inference_calls = len(day.reservations)
        self._run_reservations.add(reservation_id)
        return reservation_id

    def complete_request(self, reservation_id: str, *, failed_after_request: bool) -> str:
        """Finalize one reservation after the HTTP transport was invoked."""
        day = self.today()
        reservation = next(
            (item for item in day.reservations if item.reservation_id == reservation_id), None
        )
        if reservation is None:
            raise ValueError("unknown inference reservation")
        day.reservations = [
            item for item in day.reservations if item.reservation_id != reservation_id
        ]
        day.reserved_inference_calls = len(day.reservations)
        self._run_reservations.discard(reservation_id)
        if reservation.kind == "chat":
            day.chat_calls += 1
        else:
            day.embedding_calls += 1
        day.completed_inference_calls += 1
        day.failed_after_request_calls += int(failed_after_request)
        day.http_failed_calls += int(failed_after_request)
        if reservation.fingerprint not in day.request_fingerprints:
            day.request_fingerprints.append(reservation.fingerprint)
        request_id = reservation_id.replace("res-", "req-", 1)
        day.call_records.append(
            InferenceCallRecord(
                request_id=request_id,
                kind=reservation.kind,
                fingerprint=reservation.fingerprint,
                requested_at=self._now(),
                failed_after_request=failed_after_request,
                http_failed=failed_after_request,
            )
        )
        self.run_calls += 1
        self.run_failed_after_request_calls += int(failed_after_request)
        self.run_http_failed_calls += int(failed_after_request)
        self._publish_run_usage()
        return request_id

    def mark_http_failed(self, request_id: str) -> bool:
        """Classify a finalized inference as an HTTP/transport failure."""
        for day in self.ledger.days:
            record = next(
                (item for item in day.call_records if item.request_id == request_id), None
            )
            if record is None or record.http_failed:
                continue
            if record.response_validation_failed:
                return False
            record.http_failed = True
            day.http_failed_calls += 1
            self.run_http_failed_calls += 1
            if not record.failed_after_request:
                record.failed_after_request = True
                day.failed_after_request_calls += 1
                self.run_failed_after_request_calls += 1
            self._publish_run_usage()
            return True
        return False

    def mark_response_validation_failed(self, request_id: str) -> bool:
        """Classify an HTTP-completed inference whose response could not be validated."""
        for day in self.ledger.days:
            record = next(
                (item for item in day.call_records if item.request_id == request_id), None
            )
            if record is None or record.response_validation_failed:
                continue
            if record.http_failed:
                return False
            record.response_validation_failed = True
            day.response_validation_failed_calls += 1
            self.run_response_validation_failed_calls += 1
            if not record.failed_after_request:
                record.failed_after_request = True
                day.failed_after_request_calls += 1
                self.run_failed_after_request_calls += 1
            self._publish_run_usage()
            return True
        return False

    def mark_request_failed(self, request_id: str) -> bool:
        """Backward-compatible alias for an HTTP failure classification."""
        return self.mark_http_failed(request_id)

    def release(self, reservation_id: str) -> bool:
        """Return unused capacity without creating a completed call record."""
        released = False
        for day in self.ledger.days:
            before = len(day.reservations)
            day.reservations = [
                item for item in day.reservations if item.reservation_id != reservation_id
            ]
            released = released or len(day.reservations) != before
            day.reserved_inference_calls = len(day.reservations)
        self._run_reservations.discard(reservation_id)
        if released:
            self._publish_run_usage()
        return released

    def release_run_reservations(self) -> int:
        released = sum(self.release(item) for item in tuple(self._run_reservations))
        self._run_reservations.clear()
        return released

    def record_catalog(self) -> None:
        self.today().catalog_calls += 1

    def record_failure(self) -> None:
        self.today().failures += 1

    def record_skipped_run(self) -> None:
        self.today().skipped_runs += 1

    def run_usage(self) -> dict[str, int]:
        return {
            "completed_inference_calls": self.run_calls,
            "reserved_inference_calls": len(self._run_reservations),
            "failed_after_request_calls": self.run_failed_after_request_calls,
            "http_failed_calls": self.run_http_failed_calls,
            "response_validation_failed_calls": self.run_response_validation_failed_calls,
        }

    def _publish_run_usage(self) -> None:
        output = os.getenv("GITHUB_OUTPUT")
        if not output:
            return
        usage = self.run_usage()
        with Path(output).open("a", encoding="utf-8") as handle:
            for key, value in usage.items():
                handle.write(f"{key}={value}\n")

    def as_daily_delta(self, target: date | None = None) -> UsageDay:
        target = target or self._now().date()
        for day in self.ledger.days:
            if day.date == target:
                return day
        return UsageDay(date=target)
