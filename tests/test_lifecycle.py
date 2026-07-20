import pytest

from agents.lifecycle import (
    action_allowed,
    allowed_actions,
    can_review_pivot,
    validate_action_transition,
    validate_transition,
)
from agents.schemas import ActionType, LifecycleStage, StateTransition


def test_valid_and_invalid_transitions():
    validate_transition(LifecycleStage.DISCOVERY, LifecycleStage.EVIDENCE_VALIDATION)
    with pytest.raises(ValueError):
        validate_transition(LifecycleStage.DISCOVERY, LifecycleStage.OPERATING)


def test_discovery_actions_match_the_agent_contract():
    expected = {
        ActionType.COLLECT_SIGNALS,
        ActionType.CREATE_PROBLEM_CANDIDATE,
        ActionType.VALIDATE_EVIDENCE,
        ActionType.WRITE_REPORT,
        ActionType.NO_OP,
    }
    assert set(allowed_actions(LifecycleStage.DISCOVERY)) == expected
    assert all(action_allowed(LifecycleStage.DISCOVERY, item) for item in expected)
    assert not action_allowed(LifecycleStage.DISCOVERY, ActionType.CREATE_IDEA_CANDIDATES)


def test_discovery_analysis_can_advance_to_evidence_validation():
    for action in {
        ActionType.CREATE_PROBLEM_CANDIDATE,
        ActionType.VALIDATE_EVIDENCE,
    }:
        validate_action_transition(
            LifecycleStage.DISCOVERY,
            action,
            StateTransition.model_validate(
                {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"}
            ),
        )


def test_discovery_report_cannot_advance_the_lifecycle():
    with pytest.raises(ValueError):
        validate_action_transition(
            LifecycleStage.DISCOVERY,
            ActionType.WRITE_REPORT,
            StateTransition.model_validate(
                {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"}
            ),
        )


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
