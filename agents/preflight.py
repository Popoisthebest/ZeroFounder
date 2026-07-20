from __future__ import annotations

import hashlib
import json
from datetime import date

from agents.schemas import PreflightDecision, RepositoryCheckpoint, TriggerReason


def build_preflight_decision(
    *,
    checkpoint: RepositoryCheckpoint,
    signal_quality: dict[str, float],
    issue_ids: list[int],
    comment_ids: list[int],
    product_sha: str | None,
    metrics_hash: str | None,
    due_experiment: bool,
    daily_review_due: bool,
    weekly_review_due: bool,
    manual: bool,
    min_new_signals: int,
    strong_evidence_threshold: float,
) -> PreflightDecision:
    known_signals = set(checkpoint.last_signal_ids)
    new_signal_ids = sorted(
        signal_id for signal_id in signal_quality if signal_id not in known_signals
    )
    new_issues = sorted(set(issue_ids) - set(checkpoint.processed_issue_ids))
    new_comments = sorted(set(comment_ids) - set(checkpoint.processed_comment_ids))
    reasons: list[TriggerReason] = []
    if len(new_signal_ids) >= min_new_signals:
        reasons.append(TriggerReason.NEW_SIGNALS)
    elif any(signal_quality[item] >= strong_evidence_threshold for item in new_signal_ids):
        reasons.append(TriggerReason.STRONG_SIGNAL)
    if new_issues:
        reasons.append(TriggerReason.NEW_ISSUE)
    if new_comments:
        reasons.append(TriggerReason.APPROVAL_COMMAND)
    if product_sha and product_sha != checkpoint.last_product_sha:
        reasons.append(TriggerReason.PRODUCT_CHANGED)
    if metrics_hash and metrics_hash != checkpoint.last_metrics_hash:
        reasons.append(TriggerReason.METRICS_CHANGED)
    if due_experiment:
        reasons.append(TriggerReason.EXPERIMENT_DUE)
    if daily_review_due:
        reasons.append(TriggerReason.DAILY_REVIEW)
    if weekly_review_due:
        reasons.append(TriggerReason.WEEKLY_REVIEW)
    if manual:
        reasons.append(TriggerReason.MANUAL)
    material = {
        "signals": new_signal_ids,
        "issues": new_issues,
        "comments": new_comments,
        "product_sha": product_sha,
        "metrics_hash": metrics_hash,
        "reasons": reasons,
    }
    key = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
    return PreflightDecision(
        should_call_model=bool(reasons),
        reasons=reasons,
        new_signal_ids=new_signal_ids,
        issue_ids=new_issues,
        comment_ids=new_comments,
        product_sha=product_sha,
        metrics_hash=metrics_hash,
        idempotency_key=key,
    )


def checkpoint_after_material_work(
    checkpoint: RepositoryCheckpoint,
    decision: PreflightDecision,
    *,
    today: date | None = None,
) -> RepositoryCheckpoint:
    if not decision.should_call_model:
        return checkpoint.model_copy(deep=True)
    updated = checkpoint.model_copy(deep=True)
    updated.last_signal_ids = sorted(set(updated.last_signal_ids + decision.new_signal_ids))[-5000:]
    updated.processed_issue_ids = sorted(set(updated.processed_issue_ids + decision.issue_ids))[
        -5000:
    ]
    updated.processed_comment_ids = sorted(
        set(updated.processed_comment_ids + decision.comment_ids)
    )[-5000:]
    updated.idempotency_keys = (updated.idempotency_keys + [decision.idempotency_key])[-1000:]
    updated.last_product_sha = decision.product_sha or updated.last_product_sha
    updated.last_metrics_hash = decision.metrics_hash or updated.last_metrics_hash
    if TriggerReason.DAILY_REVIEW in decision.reasons:
        updated.last_daily_review = today or date.today()
    if TriggerReason.WEEKLY_REVIEW in decision.reasons:
        updated.last_weekly_review = today or date.today()
    return updated
