from datetime import UTC, date, datetime

from agents.schemas import (
    Experiment,
    FounderResult,
    FounderResults,
    ValidationSnapshot,
    ValidationThresholds,
)
from agents.validation import distribution_gate, pivot_review_allowed


def test_distribution_rejects_bot_results():
    results = FounderResults(
        records=[
            FounderResult(
                result_id="result-001",
                recorded_by="github-actions[bot]",
                recorded_at=datetime.now(UTC),
                source_type="verified_issue",
                evidence_url="https://example.com/proof",
                activity="Posted",
                outcome="No verified human action",
            )
        ]
    )
    experiment = Experiment(
        experiment_id="exp-001",
        hypothesis="A specific permitted channel reaches target users",
        change="Publish an approved post manually",
        target_metric="Qualified replies",
        success_condition="At least three qualified replies",
        failure_condition="No qualified replies",
        start_date=date.today(),
        review_date=date.today(),
        status="active",
    )
    passed, reasons = distribution_gate(
        results, feedback_paths_verified=True, experiments=[experiment]
    )
    assert not passed
    assert "no verified human distribution activity" in reasons


def test_pivot_requires_all_thresholds_and_multiple_failures():
    thresholds = ValidationThresholds()
    incomplete = ValidationSnapshot(
        validation_days=14,
        distribution_activities=2,
        user_or_visit_signals=0,
        feedback_items=3,
        growth_experiments=2,
        distinct_feedback_authors=2,
        feedback_paths_verified=True,
        active_experiment=False,
        failure_indicators=["no response", "no repeat use"],
    )
    assert not pivot_review_allowed(incomplete, thresholds)[0]
    complete = incomplete.model_copy(
        update={"user_or_visit_signals": 10, "active_experiment": True}
    )
    assert pivot_review_allowed(complete, thresholds)[0]
