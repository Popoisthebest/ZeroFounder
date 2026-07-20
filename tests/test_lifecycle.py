import pytest

from agents.lifecycle import can_review_pivot, validate_transition
from agents.schemas import LifecycleStage


def test_valid_and_invalid_transitions():
    validate_transition(LifecycleStage.DISCOVERY, LifecycleStage.EVIDENCE_VALIDATION)
    with pytest.raises(ValueError):
        validate_transition(LifecycleStage.DISCOVERY, LifecycleStage.OPERATING)


def test_pivot_requires_all_validation_prerequisites():
    thresholds = {
        "validation_period_days": 14,
        "min_distribution_activities": 2,
        "min_user_or_visit_signals": 10,
        "min_feedback_items": 3,
        "min_growth_experiments": 2,
    }
    assert can_review_pivot(
        validation_days=14,
        distribution_activities=2,
        user_or_visit_signals=10,
        feedback_items=3,
        growth_experiments=2,
        thresholds=thresholds,
    )
    assert not can_review_pivot(
        validation_days=14,
        distribution_activities=2,
        user_or_visit_signals=0,
        feedback_items=3,
        growth_experiments=2,
        thresholds=thresholds,
    )
