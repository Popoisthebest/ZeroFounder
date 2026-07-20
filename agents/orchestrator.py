from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agents.context_builder import build_context
from agents.github_models import GitHubModelsClient, safe_no_op
from agents.preflight import build_preflight_decision
from agents.schemas import CompanyState, MarketSignal, RepositoryCheckpoint
from agents.usage_limiter import UsageLimiter


def _signal_quality(root: Path) -> dict[str, float]:
    output: dict[str, float] = {}
    for path in (root / "signals/raw").glob("*.jsonl"):
        for line in path.read_text(errors="replace").splitlines():
            try:
                signal = MarketSignal.model_validate_json(line)
                output[signal.signal_id] = 0.5
            except ValueError:
                continue
    return output


def preflight(root: Path, event_path: Path | None, event_name: str) -> dict:
    checkpoint = RepositoryCheckpoint.model_validate_json(
        (root / "company/checkpoints.json").read_text()
    )
    event: dict = {}
    if event_path and event_path.exists():
        loaded = json.loads(event_path.read_text())
        event = loaded if isinstance(loaded, dict) else {}
    issue = event.get("issue") if isinstance(event.get("issue"), dict) else {}
    comment = event.get("comment") if isinstance(event.get("comment"), dict) else {}
    issue_ids = [issue["id"]] if isinstance(issue.get("id"), int) else []
    comment_ids = [comment["id"]] if isinstance(comment.get("id"), int) else []
    metrics = (root / "company/metrics.json").read_bytes()
    strategy = json.loads((root / "company/strategy.json").read_text())
    now = datetime.now(UTC)
    review = strategy["review"]
    daily_due = checkpoint.last_daily_review != now.date() and now.hour >= int(review["daily_hour"])
    weekly_due = (
        now.isoweekday() == int(review["weekly_day"])
        and checkpoint.last_weekly_review != now.date()
        and now.hour >= int(review["weekly_hour"])
    )
    evidence = strategy["evidence"]
    decision = build_preflight_decision(
        checkpoint=checkpoint,
        signal_quality=_signal_quality(root),
        issue_ids=issue_ids,
        comment_ids=comment_ids,
        product_sha=os.getenv("PRODUCT_SHA"),
        metrics_hash=hashlib.sha256(metrics).hexdigest(),
        due_experiment=False,
        daily_review_due=daily_due,
        weekly_review_due=weekly_due,
        manual=event_name == "workflow_dispatch",
        min_new_signals=int(
            os.getenv("MIN_NEW_SIGNALS_FOR_ANALYSIS", evidence["min_new_signals_for_analysis"])
        ),
        strong_evidence_threshold=float(
            os.getenv("STRONG_EVIDENCE_THRESHOLD", evidence["strong_evidence_threshold"])
        ),
    )
    return decision.model_dump(mode="json", by_alias=True)


def run_model(root: Path) -> dict:
    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    if state.sleep_mode:
        return safe_no_op("sleep mode is active").model_dump(mode="json", by_alias=True)
    limiter = UsageLimiter.from_path(root / "company/usage.json")
    client = GitHubModelsClient(os.environ["GITHUB_TOKEN"], limiter)
    try:
        catalog = client.catalog()
    except Exception as exc:
        return safe_no_op(str(exc)).model_dump(mode="json", by_alias=True)
    model = client.select_chat_model(catalog)
    if not model:
        return safe_no_op("no compatible text model").model_dump(mode="json", by_alias=True)
    prompt = (root / "agents/prompts/core.md").read_text()
    context = build_context(root)
    action = client.chat_action(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": context},
        ],
    )
    return action.model_dump(mode="json", by_alias=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "model"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "preflight":
        result = preflight(
            root,
            Path(os.environ["GITHUB_EVENT_PATH"]) if os.getenv("GITHUB_EVENT_PATH") else None,
            os.getenv("GITHUB_EVENT_NAME", "schedule"),
        )
    else:
        result = run_model(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"mode": args.mode, "completed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
