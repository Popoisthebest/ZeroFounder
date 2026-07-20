from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.github_client import GitHubClient
from agents.schemas import UsageDay, UsageLedger

POSITIVE = {"helpful", "useful", "thanks", "great", "좋아요", "유용", "감사"}
NEGATIVE = {"broken", "bad", "confusing", "error", "버그", "오류", "불편"}


def sentiment(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"positive": 0, "negative": 0, "unknown": 0}
    for item in items:
        text = f"{item.get('title', '')} {item.get('body', '')}".lower()
        positive = any(word in text for word in POSITIVE)
        negative = any(word in text for word in NEGATIVE)
        if positive and not negative:
            counts["positive"] += 1
        elif negative and not positive:
            counts["negative"] += 1
        else:
            counts["unknown"] += 1
    return counts


def collect_metrics(client: GitHubClient) -> dict[str, Any]:
    repository = client.repository_info()
    issues = client._request(
        "GET", f"/repos/{client.repository}/issues", params={"state": "all", "per_page": 100}
    )
    pulls = client._request(
        "GET", f"/repos/{client.repository}/pulls", params={"state": "all", "per_page": 100}
    )
    pure_issues = [item for item in issues if "pull_request" not in item]

    def labels(item: dict[str, Any]) -> set[str]:
        return {label["name"] for label in item.get("labels", [])}

    feedback = [
        item for item in pure_issues if labels(item) & {"feedback", "bug", "feature-request"}
    ]
    try:
        model_usage = client.model_usage_today()
    except Exception:
        model_usage = None
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "product_features": 0,
        "open_issues": sum(item.get("state") == "open" for item in pure_issues),
        "bug_reports": sum("bug" in labels(item) for item in pure_issues),
        "feature_requests": sum("feature-request" in labels(item) for item in pure_issues),
        "resolved_requests": sum(
            item.get("state") == "closed" and bool(labels(item) & {"bug", "feature-request"})
            for item in pure_issues
        ),
        "open_pull_requests": sum(item.get("state") == "open" for item in pulls),
        "merged_pull_requests": sum(bool(item.get("merged_at")) for item in pulls),
        "stars": int(repository.get("stargazers_count", 0)),
        "forks": int(repository.get("forks_count", 0)),
        "agent_success_rate": None,
        "model_calls": (
            model_usage["completed_inference_calls"] if model_usage is not None else None
        ),
        "failed_after_request_calls": (
            model_usage["failed_after_request_calls"] if model_usage is not None else None
        ),
        "http_failed_calls": (
            model_usage.get("http_failed_calls", 0) if model_usage is not None else None
        ),
        "response_validation_failed_calls": (
            model_usage.get("response_validation_failed_calls", 0)
            if model_usage is not None
            else None
        ),
        "active_model_reservations": (
            model_usage["reserved_inference_calls"] if model_usage is not None else None
        ),
        "skipped_model_runs": model_usage["skipped_runs"] if model_usage is not None else None,
        "experiments": 0,
        "successful_experiments": 0,
        "recent_product_changes": 0,
        "feedback_sentiment": sentiment(feedback),
        "visitor_count": None,
    }


def write_daily_metrics(path: Path, metrics: dict[str, Any]) -> bool:
    if path.exists():
        current = json.loads(path.read_text())
        current_date = str(current.get("as_of") or "")[:10]
        new_date = str(metrics.get("as_of") or "")[:10]
        if current_date == new_date:
            return False
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    return True


def write_daily_usage_snapshot(path: Path, usage: dict[str, int] | None) -> bool:
    if usage is None:
        return False
    ledger = UsageLedger.model_validate_json(path.read_text()) if path.exists() else UsageLedger()
    today = datetime.now(UTC).date()
    existing = next((item for item in ledger.days if item.date == today), None)
    if existing:
        values = (
            usage["completed_inference_calls"],
            usage["reserved_inference_calls"],
            usage["failed_after_request_calls"],
            usage.get("http_failed_calls", 0),
            usage.get("response_validation_failed_calls", 0),
            usage["skipped_runs"],
        )
        current = (
            existing.completed_inference_calls,
            existing.reserved_inference_calls,
            existing.failed_after_request_calls,
            existing.http_failed_calls,
            existing.response_validation_failed_calls,
            existing.skipped_runs,
        )
        if current == values and existing.inference_call_upper_bound == 0:
            return False
        existing.completed_inference_calls = values[0]
        existing.reserved_inference_calls = values[1]
        existing.failed_after_request_calls = values[2]
        existing.http_failed_calls = values[3]
        existing.response_validation_failed_calls = values[4]
        existing.skipped_runs = values[5]
        existing.inference_call_upper_bound = 0
    else:
        ledger.days.append(
            UsageDay(
                date=today,
                completed_inference_calls=usage["completed_inference_calls"],
                reserved_inference_calls=usage["reserved_inference_calls"],
                failed_after_request_calls=usage["failed_after_request_calls"],
                http_failed_calls=usage.get("http_failed_calls", 0),
                response_validation_failed_calls=usage.get(
                    "response_validation_failed_calls", 0
                ),
                skipped_runs=usage["skipped_runs"],
            )
        )
    path.write_text(ledger.model_dump_json(indent=2) + "\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("company/metrics.json"))
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    metrics = collect_metrics(client)
    changed = write_daily_metrics(args.output, metrics)
    usage = None
    if metrics.get("model_calls") is not None:
        usage = {
            "completed_inference_calls": int(metrics["model_calls"]),
            "reserved_inference_calls": int(metrics["active_model_reservations"]),
            "failed_after_request_calls": int(metrics["failed_after_request_calls"]),
            "http_failed_calls": int(metrics["http_failed_calls"]),
            "response_validation_failed_calls": int(
                metrics["response_validation_failed_calls"]
            ),
            "skipped_runs": int(metrics["skipped_model_runs"]),
        }
    usage_changed = write_daily_usage_snapshot(args.output.parent / "usage.json", usage)
    print(json.dumps({"metrics_updated": changed, "usage_updated": usage_changed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
