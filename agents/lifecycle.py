from __future__ import annotations

from agents.schemas import ActionType, LifecycleStage, StateTransition

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


STAGE_ACTIONS: dict[LifecycleStage, frozenset[ActionType]] = {
    LifecycleStage.DISCOVERY: frozenset(
        {
            ActionType.COLLECT_SIGNALS,
            ActionType.CREATE_PROBLEM_CANDIDATE,
            ActionType.VALIDATE_EVIDENCE,
            ActionType.WRITE_REPORT,
            ActionType.NO_OP,
        }
    ),
    LifecycleStage.EVIDENCE_VALIDATION: frozenset(
        {ActionType.VALIDATE_EVIDENCE, ActionType.WRITE_REPORT, ActionType.NO_OP}
    ),
    LifecycleStage.IDEA_EVALUATION: {
        ActionType.CREATE_IDEA_CANDIDATES,
        ActionType.EVALUATE_IDEAS,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.DISTRIBUTION_CHECK: frozenset(
        {ActionType.CHECK_DISTRIBUTION, ActionType.WRITE_REPORT}
    ),
    LifecycleStage.IDEA_SELECTED: frozenset(
        {ActionType.SELECT_IDEA, ActionType.REQUEST_FOUNDER_APPROVAL}
    ),
    LifecycleStage.FOUNDER_APPROVAL: frozenset(
        {ActionType.NO_OP, ActionType.WRITE_REPORT}
    ),
    LifecycleStage.MVP_PLANNING: frozenset(
        {ActionType.CREATE_PRODUCT_SPEC, ActionType.WRITE_REPORT}
    ),
    LifecycleStage.INFRASTRUCTURE_SELECTION: {
        ActionType.SELECT_INFRASTRUCTURE,
        ActionType.OPEN_ISSUE,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.MVP_BUILDING: {
        ActionType.CREATE_CODE_PATCH,
        ActionType.PROPOSE_DEPENDENCY,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.PRE_LAUNCH: frozenset(
        {ActionType.CREATE_CONTENT, ActionType.WRITE_REPORT, ActionType.UPDATE_STATE}
    ),
    LifecycleStage.DISTRIBUTION_REQUIRED: {
        ActionType.CREATE_CONTENT,
        ActionType.CREATE_EXPERIMENT,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.VALIDATION_RUNNING: {
        ActionType.ANALYZE_FEEDBACK,
        ActionType.RECORD_VALIDATION,
        ActionType.CREATE_EXPERIMENT,
        ActionType.WRITE_REPORT,
        ActionType.UPDATE_STATE,
    },
    LifecycleStage.OPERATING: {
        ActionType.ANALYZE_FEEDBACK,
        ActionType.CREATE_CONTENT,
        ActionType.UPDATE_STRATEGY,
        ActionType.CREATE_EXPERIMENT,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.GROWTH_EXPERIMENT: {
        ActionType.CREATE_EXPERIMENT,
        ActionType.RECORD_VALIDATION,
        ActionType.WRITE_REPORT,
    },
    LifecycleStage.PIVOT_REVIEW: frozenset(
        {ActionType.RECOMMEND_PIVOT, ActionType.WRITE_REPORT}
    ),
    LifecycleStage.PIVOTING: frozenset(
        {ActionType.UPDATE_STRATEGY, ActionType.UPDATE_STATE, ActionType.WRITE_REPORT}
    ),
    LifecycleStage.PAUSED: frozenset({ActionType.NO_OP, ActionType.WRITE_REPORT}),
}


DISCOVERY_ACTION_TARGETS: dict[ActionType, frozenset[LifecycleStage]] = {
    ActionType.COLLECT_SIGNALS: frozenset({LifecycleStage.DISCOVERY}),
    ActionType.CREATE_PROBLEM_CANDIDATE: frozenset(
        {LifecycleStage.DISCOVERY, LifecycleStage.EVIDENCE_VALIDATION}
    ),
    ActionType.VALIDATE_EVIDENCE: frozenset(
        {LifecycleStage.DISCOVERY, LifecycleStage.EVIDENCE_VALIDATION}
    ),
    ActionType.WRITE_REPORT: frozenset({LifecycleStage.DISCOVERY}),
    ActionType.NO_OP: frozenset(),
}


def allowed_actions(stage: LifecycleStage) -> tuple[ActionType, ...]:
    permitted = set(STAGE_ACTIONS[stage]) | {ActionType.NO_OP}
    return tuple(sorted(permitted, key=lambda item: item.value))


def action_allowed(stage: LifecycleStage, action: ActionType | str) -> bool:
    try:
        parsed = action if isinstance(action, ActionType) else ActionType(action)
    except ValueError:
        return False
    return parsed in allowed_actions(stage)


def validate_action_transition(
    current: LifecycleStage,
    action: ActionType,
    transition: StateTransition | None,
) -> None:
    if transition is None:
        return
    if transition.from_stage != current:
        raise ValueError("state transition source mismatch")
    validate_transition(current, transition.to_stage)
    if current == LifecycleStage.DISCOVERY:
        targets = DISCOVERY_ACTION_TARGETS[action]
        if transition.to_stage not in targets:
            raise ValueError(
                f"action {action.value} cannot transition DISCOVERY to {transition.to_stage.value}"
            )
