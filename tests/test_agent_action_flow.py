import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agents.approval import apply_command, decide_command
from agents.bootstrap import initial_company_state
from agents.schemas import ActionEnvelope, CompanyState, LifecycleStage, RepositoryCheckpoint
from scripts.apply_agent_action import apply_validated_action
from scripts.create_agent_branch import create_agent_branch

ROOT = Path(__file__).parents[1]
APPLIED_AT = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)


def _initialize_runtime(root: Path) -> str:
    (root / "company").mkdir(parents=True)
    (root / "signals/raw").mkdir(parents=True)
    (root / "runtime").mkdir(parents=True)
    (root / "company/state.json").write_text(
        initial_company_state().model_dump_json(indent=2) + "\n"
    )
    (root / "company/checkpoints.json").write_text(
        RepositoryCheckpoint().model_dump_json(indent=2) + "\n"
    )
    signal_id = "signal-001"
    signal = {
        "signal_id": signal_id,
        "source_pack": "small-business",
        "source_type": "rss",
        "url": "https://evidence.example/signal-001",
        "title": "Repeated reconciliation work",
        "summary": "Teams manually reconcile updates across spreadsheets and messages.",
        "collected_at": "2026-07-20T00:00:00Z",
        "published_at": "2026-07-19T00:00:00Z",
        "content_hash": "a" * 64,
    }
    (root / "signals/raw/signals.jsonl").write_text(json.dumps(signal) + "\n")
    return signal_id


def _write_preflight(root: Path, signal_id: str, suffix: str = "a") -> Path:
    path = root / "runtime/preflight.json"
    path.write_text(
        json.dumps(
            {
                "should_call_model": True,
                "reasons": ["new_signals"],
                "new_signal_ids": [signal_id],
                "idempotency_key": suffix * 64,
            }
        )
    )
    return path


def _write_action(root: Path, payload: dict) -> Path:
    path = root / "runtime/action.json"
    path.write_text(
        ActionEnvelope.model_validate(payload).model_dump_json(indent=2, by_alias=True)
        + "\n"
    )
    return path


def _create_problem_payload(signal_id: str) -> dict:
    return {
        "role": "researcher",
        "action_type": "create_problem_candidate",
        "title": "Problem candidate",
        "summary": "Create one evidence-backed problem candidate.",
        "rationale": "Stored evidence shows a repeated manual workaround.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": [signal_id],
        "problem_candidate": {
            "problem_id": "problem-001",
            "title": "Repeated manual reconciliation",
            "target_users": ["small teams"],
            "description": "Small teams repeatedly reconcile the same updates manually.",
            "current_workaround": "They combine spreadsheets, screenshots, and messages.",
        },
        "state_transition": {"from": "DISCOVERY", "to": "EVIDENCE_VALIDATION"},
    }


def test_create_problem_candidate_applies_file_and_exact_state_transition(tmp_path: Path):
    signal_id = _initialize_runtime(tmp_path)
    initial = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    action_path = _write_action(tmp_path, _create_problem_payload(signal_id))
    action, changed = apply_validated_action(
        tmp_path,
        action_path,
        _write_preflight(tmp_path, signal_id),
        applied_at=APPLIED_AT,
    )

    assert initial.lifecycle_stage == LifecycleStage.DISCOVERY
    assert changed
    assert action.action_type.value == "create_problem_candidate"
    assert ActionEnvelope.model_validate_json(action_path.read_text()) == action
    problem_path = tmp_path / "research/problems/problem-001.json"
    assert problem_path.exists()
    problem = json.loads(problem_path.read_text())
    assert problem["evidence_ids"] == [signal_id]
    assert problem["evidence"][0]["evidence_id"] == signal_id
    assert problem["evidence"][0]["url"] == "https://evidence.example/signal-001"

    state = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.EVIDENCE_VALIDATION
    assert state.selected_venture is None
    assert state.last_agent_run == APPLIED_AT
    unchanged = {
        key: value
        for key, value in state.model_dump(mode="json").items()
        if key not in {"lifecycle_stage", "last_agent_run"}
    }
    initial_unchanged = {
        key: value
        for key, value in initial.model_dump(mode="json").items()
        if key not in {"lifecycle_stage", "last_agent_run"}
    }
    assert unchanged == initial_unchanged


def test_validate_evidence_can_advance_from_validation_to_idea_evaluation(tmp_path: Path):
    signal_id = _initialize_runtime(tmp_path)
    create_path = _write_action(tmp_path, _create_problem_payload(signal_id))
    preflight_path = _write_preflight(tmp_path, signal_id)
    apply_validated_action(
        tmp_path, create_path, preflight_path, applied_at=APPLIED_AT
    )
    validate_path = _write_action(
        tmp_path,
        {
            "role": "researcher",
            "action_type": "validate_evidence",
            "title": "Validate evidence",
            "summary": "Validate the evidence supporting the stored problem.",
            "rationale": "Independent evidence is available for lifecycle review.",
            "risk_level": "low",
            "requires_approval": False,
            "evidence_ids": [signal_id],
            "state_transition": {
                "from": "EVIDENCE_VALIDATION",
                "to": "IDEA_EVALUATION",
            },
        },
    )
    apply_validated_action(
        tmp_path,
        validate_path,
        _write_preflight(tmp_path, signal_id, "b"),
        applied_at=APPLIED_AT,
    )
    state = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION
    assert state.selected_venture is None


def test_pause_and_resume_restore_the_runtime_stage():
    state = initial_company_state().model_copy(
        update={"lifecycle_stage": LifecycleStage.EVIDENCE_VALIDATION}
    )
    paused = apply_command(state, decide_command(state, "pause"))
    assert paused.lifecycle_stage == LifecycleStage.PAUSED
    assert paused.paused_from == LifecycleStage.EVIDENCE_VALIDATION
    resumed = apply_command(paused, decide_command(paused, "resume"))
    assert resumed.lifecycle_stage == LifecycleStage.EVIDENCE_VALIDATION
    assert resumed.paused_from is None


def test_create_branch_job_applies_then_tests_before_commit_and_push():
    workflow = yaml.safe_load((ROOT / ".github/workflows/agent.yml").read_text())
    steps = workflow["jobs"]["create-branch"]["steps"]

    def step_index(predicate) -> int:
        return next(index for index, step in enumerate(steps) if predicate(step))

    checkout = step_index(lambda step: step.get("uses", "").startswith("actions/checkout@"))
    branch = step_index(lambda step: step.get("name") == "Create local agent branch")
    apply = step_index(lambda step: step.get("id") == "apply")
    pytest_step = step_index(lambda step: step.get("run") == "python -m pytest")
    commit = step_index(lambda step: step.get("id") == "commit")
    assert checkout < branch < apply < pytest_step < commit
    assert "continue-on-error" not in steps[pytest_step]


def test_local_agent_branch_is_created_before_changes(tmp_path: Path):
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "runtime").mkdir()
    action_path = _write_action(tmp_path, _create_problem_payload("signal-001"))
    branch = create_agent_branch(tmp_path, action_path, "12345")
    current = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == "agent/12345-create-problem-candidate"
    assert current == branch
