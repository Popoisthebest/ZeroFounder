from __future__ import annotations

from agents.schemas import LifecycleStage

ALLOWED_TRANSITIONS: dict[LifecycleStage, frozenset[LifecycleStage]] = {
    LifecycleStage.DISCOVERY: frozenset(
        {LifecycleStage.DISCOVERY, LifecycleStage.EVIDENCE_VALIDATION, LifecycleStage.PAUSED}
    ),
    LifecycleStage.EVIDENCE_VALIDATION: frozenset(
        {LifecycleStage.DISCOVERY, LifecycleStage.IDEA_EVALUATION, LifecycleStage.PAUSED}
    ),
    LifecycleStage.IDEA_EVALUATION: frozenset(
        {
            LifecycleStage.IDEA_EVALUATION,
            LifecycleStage.DISTRIBUTION_CHECK,
            LifecycleStage.DISCOVERY,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.DISTRIBUTION_CHECK: frozenset(
        {
            LifecycleStage.IDEA_SELECTED,
            LifecycleStage.IDEA_EVALUATION,
            LifecycleStage.DISCOVERY,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.IDEA_SELECTED: frozenset(
        {LifecycleStage.FOUNDER_APPROVAL, LifecycleStage.MVP_PLANNING, LifecycleStage.PAUSED}
    ),
    LifecycleStage.FOUNDER_APPROVAL: frozenset(
        {
            LifecycleStage.MVP_PLANNING,
            LifecycleStage.IDEA_EVALUATION,
            LifecycleStage.DISCOVERY,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.MVP_PLANNING: frozenset(
        {LifecycleStage.INFRASTRUCTURE_SELECTION, LifecycleStage.PAUSED}
    ),
    LifecycleStage.INFRASTRUCTURE_SELECTION: frozenset(
        {
            LifecycleStage.INFRASTRUCTURE_SELECTION,
            LifecycleStage.MVP_BUILDING,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.MVP_BUILDING: frozenset(
        {LifecycleStage.MVP_BUILDING, LifecycleStage.PRE_LAUNCH, LifecycleStage.PAUSED}
    ),
    LifecycleStage.PRE_LAUNCH: frozenset(
        {
            LifecycleStage.PRE_LAUNCH,
            LifecycleStage.DISTRIBUTION_REQUIRED,
            LifecycleStage.MVP_BUILDING,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.DISTRIBUTION_REQUIRED: frozenset(
        {
            LifecycleStage.DISTRIBUTION_REQUIRED,
            LifecycleStage.VALIDATION_RUNNING,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.VALIDATION_RUNNING: frozenset(
        {
            LifecycleStage.VALIDATION_RUNNING,
            LifecycleStage.OPERATING,
            LifecycleStage.GROWTH_EXPERIMENT,
            LifecycleStage.PIVOT_REVIEW,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.OPERATING: frozenset(
        {
            LifecycleStage.OPERATING,
            LifecycleStage.GROWTH_EXPERIMENT,
            LifecycleStage.PIVOT_REVIEW,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.GROWTH_EXPERIMENT: frozenset(
        {
            LifecycleStage.GROWTH_EXPERIMENT,
            LifecycleStage.VALIDATION_RUNNING,
            LifecycleStage.OPERATING,
            LifecycleStage.PIVOT_REVIEW,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.PIVOT_REVIEW: frozenset(
        {
            LifecycleStage.VALIDATION_RUNNING,
            LifecycleStage.OPERATING,
            LifecycleStage.PIVOTING,
            LifecycleStage.PAUSED,
        }
    ),
    LifecycleStage.PIVOTING: frozenset({LifecycleStage.DISCOVERY, LifecycleStage.PAUSED}),
    LifecycleStage.PAUSED: frozenset(LifecycleStage),
}


def validate_transition(current: LifecycleStage, target: LifecycleStage) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"invalid lifecycle transition: {current} -> {target}")


def can_review_pivot(
    *,
    validation_days: int,
    distribution_activities: int,
    user_or_visit_signals: int,
    feedback_items: int,
    growth_experiments: int,
    thresholds: dict[str, int],
) -> bool:
    return all(
        (
            validation_days >= thresholds["validation_period_days"],
            distribution_activities >= thresholds["min_distribution_activities"],
            user_or_visit_signals >= thresholds["min_user_or_visit_signals"],
            feedback_items >= thresholds["min_feedback_items"],
            growth_experiments >= thresholds["min_growth_experiments"],
        )
    )


STAGE_ACTIONS = {
    LifecycleStage.DISCOVERY: {"collect_signals", "create_problem_candidate", "write_report"},
    LifecycleStage.EVIDENCE_VALIDATION: {"validate_evidence", "write_report"},
    LifecycleStage.IDEA_EVALUATION: {
        "create_idea_candidates",
        "evaluate_ideas",
        "write_report",
    },
    LifecycleStage.DISTRIBUTION_CHECK: {"check_distribution", "write_report"},
    LifecycleStage.IDEA_SELECTED: {"select_idea", "request_founder_approval"},
    LifecycleStage.FOUNDER_APPROVAL: {"no_op", "write_report"},
    LifecycleStage.MVP_PLANNING: {"create_product_spec", "write_report"},
    LifecycleStage.INFRASTRUCTURE_SELECTION: {
        "select_infrastructure",
        "open_issue",
        "write_report",
    },
    LifecycleStage.MVP_BUILDING: {
        "create_code_patch",
        "propose_dependency",
        "write_report",
    },
    LifecycleStage.PRE_LAUNCH: {"create_content", "write_report", "update_state"},
    LifecycleStage.DISTRIBUTION_REQUIRED: {
        "create_content",
        "create_experiment",
        "write_report",
    },
    LifecycleStage.VALIDATION_RUNNING: {
        "analyze_feedback",
        "record_validation",
        "create_experiment",
        "write_report",
        "update_state",
    },
    LifecycleStage.OPERATING: {
        "analyze_feedback",
        "create_content",
        "update_strategy",
        "create_experiment",
        "write_report",
    },
    LifecycleStage.GROWTH_EXPERIMENT: {
        "create_experiment",
        "record_validation",
        "write_report",
    },
    LifecycleStage.PIVOT_REVIEW: {"recommend_pivot", "write_report"},
    LifecycleStage.PIVOTING: {"update_strategy", "update_state", "write_report"},
    LifecycleStage.PAUSED: {"no_op", "write_report"},
}


def action_allowed(stage: LifecycleStage, action: str) -> bool:
    return action in STAGE_ACTIONS[stage]
