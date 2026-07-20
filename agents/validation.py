from __future__ import annotations

from agents.approval import founder_result_counts_as_validation
from agents.schemas import (
    Experiment,
    ExperimentStatus,
    FounderResults,
    ValidationSnapshot,
    ValidationThresholds,
)


def verified_founder_activities(results: FounderResults) -> int:
    return len(
        {
            result.result_id
            for result in results.records
            if founder_result_counts_as_validation(result)
        }
    )


def distribution_gate(
    results: FounderResults,
    *,
    feedback_paths_verified: bool,
    experiments: list[Experiment],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    valid_results = [
        result for result in results.records if founder_result_counts_as_validation(result)
    ]
    if not valid_results:
        reasons.append("no verified human distribution activity")
    if not any(result.evidence_url for result in valid_results):
        reasons.append("no distribution evidence URL")
    if not feedback_paths_verified:
        reasons.append("feedback paths are not verified")
    if not any(experiment.status == ExperimentStatus.ACTIVE for experiment in experiments):
        reasons.append("no active validation experiment")
    return not reasons, reasons


def validation_gate(
    snapshot: ValidationSnapshot, thresholds: ValidationThresholds
) -> tuple[bool, list[str]]:
    checks = {
        "validation period incomplete": snapshot.validation_days
        >= thresholds.validation_period_days,
        "insufficient distribution activities": snapshot.distribution_activities
        >= thresholds.min_distribution_activities,
        "insufficient user or visit signals": snapshot.user_or_visit_signals
        >= thresholds.min_user_or_visit_signals,
        "insufficient feedback": snapshot.feedback_items >= thresholds.min_feedback_items,
        "insufficient growth experiments": snapshot.growth_experiments
        >= thresholds.min_growth_experiments,
        "insufficient distinct feedback authors": snapshot.distinct_feedback_authors
        >= thresholds.min_distinct_feedback_authors,
        "feedback paths are not verified": snapshot.feedback_paths_verified,
    }
    reasons = [reason for reason, passed in checks.items() if not passed]
    return not reasons, reasons


def pivot_review_allowed(
    snapshot: ValidationSnapshot,
    thresholds: ValidationThresholds,
    *,
    minimum_failure_indicators: int = 2,
) -> tuple[bool, list[str]]:
    validated, reasons = validation_gate(snapshot, thresholds)
    if not validated:
        return False, reasons
    if len(set(snapshot.failure_indicators)) < minimum_failure_indicators:
        return False, ["multiple independent failure indicators are required"]
    return True, []
