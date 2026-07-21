from __future__ import annotations

import json
from pathlib import Path

from agents.schemas import ActionEnvelope, ActionType, CompanyState, FileChange, ProblemCandidate


def materialize_idea_candidates(action: ActionEnvelope, root: Path) -> FileChange:
    if action.action_type != ActionType.CREATE_IDEA_CANDIDATES or not action.idea_candidates:
        raise ValueError("idea candidate action is required")
    if action.files:
        raise ValueError("model-provided file paths are forbidden for idea candidates")
    if action.state_transition:
        raise ValueError("create_idea_candidates cannot change lifecycle stage")

    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    if not state.active_problem_id:
        raise ValueError("active_problem_id is required")
    problem_path = root / f"research/problems/{state.active_problem_id}.json"
    problem = ProblemCandidate.model_validate_json(problem_path.read_text())
    allowed_evidence = set(problem.evidence_ids)
    for candidate in action.idea_candidates:
        if not set(candidate.evidence_ids).issubset(allowed_evidence):
            raise ValueError("idea candidate references evidence outside active problem")

    payload = {
        "problem_id": state.active_problem_id,
        "lifecycle_stage": state.lifecycle_stage.value,
        "idea_candidates": [
            candidate.model_dump(mode="json") for candidate in action.idea_candidates
        ],
    }
    return FileChange(
        path=f"research/ideas/{state.active_problem_id}.json",
        content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
