from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from agents.lifecycle import validate_transition
from agents.schemas import CompanyState, FounderResult, LifecycleStage

COMMAND = re.compile(r"^/(approve|reject|revise|pause|resume|pivot)\s*$")
BOT = re.compile(r"(?:\[bot\]|bot$|agent)", re.I)


@dataclass(frozen=True)
class ApprovalDecision:
    command: str
    target_stage: LifecycleStage


def parse_approval_command(body: str) -> str | None:
    match = COMMAND.fullmatch(body.strip())
    return match.group(1) if match else None


def decide_command(state: CompanyState, command: str) -> ApprovalDecision:
    current = state.lifecycle_stage
    if command == "pause":
        return ApprovalDecision(command, LifecycleStage.PAUSED)
    if command == "resume":
        if current != LifecycleStage.PAUSED:
            raise ValueError("resume is only valid while paused")
        return ApprovalDecision(command, state.paused_from or LifecycleStage.DISCOVERY)
    targets = {
        "approve": LifecycleStage.MVP_PLANNING,
        "reject": LifecycleStage.DISCOVERY,
        "revise": LifecycleStage.IDEA_EVALUATION,
        "pivot": LifecycleStage.PIVOT_REVIEW
        if state.selected_venture
        else LifecycleStage.DISCOVERY,
    }
    target = targets[command]
    validate_transition(current, target)
    return ApprovalDecision(command, target)


def apply_command(state: CompanyState, decision: ApprovalDecision) -> CompanyState:
    updated = state.model_copy(deep=True)
    if decision.command == "pause":
        updated.paused_from = state.lifecycle_stage
    elif decision.command == "resume":
        updated.paused_from = None
        updated.sleep_mode = False
        updated.consecutive_failures = 0
    elif decision.command == "reject":
        updated.selected_venture = None
    updated.lifecycle_stage = decision.target_stage
    return updated


def validate_human_founder_result(
    payload: dict[str, str], *, actor: str, actor_has_write: bool
) -> FounderResult:
    if not actor_has_write or BOT.search(actor):
        raise ValueError("founder evidence must be recorded by a verified human")
    if payload.get("recorded_by") != actor:
        raise ValueError("recorded_by must match the verified actor")
    evidence_url = payload.get("evidence_url", "")
    if not evidence_url.startswith(("https://", "http://")):
        raise ValueError("founder evidence URL must be HTTP(S)")
    recorded_at = payload.get("recorded_at") or datetime.now(UTC).isoformat()
    return FounderResult.model_validate({**payload, "recorded_at": recorded_at})


def founder_result_counts_as_validation(result: FounderResult) -> bool:
    return not BOT.search(result.recorded_by) and result.source_type in {
        "human_commit",
        "verified_issue",
    }
