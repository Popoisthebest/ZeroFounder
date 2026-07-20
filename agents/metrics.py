from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.github_client import GitHubClient

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
        "model_calls": 0,
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("company/metrics.json"))
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    changed = write_daily_metrics(args.output, collect_metrics(client))
    print(json.dumps({"metrics_updated": changed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
