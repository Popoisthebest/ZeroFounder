import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from agents.approval import apply_command, decide_command
from agents.bootstrap import initial_company_state
from agents.candidate_validator import validate_create_idea_candidates_content
from agents.quality import validate_changed_file_contract
from agents.schemas import (
    ActionEnvelope,
    CompanyState,
    FileChange,
    LifecycleStage,
    MaterializedActionEnvelope,
    RepositoryCheckpoint,
)
from scripts.apply_agent_action import apply_validated_action
from scripts.commit_agent_changes import (
    commit_agent_changes,
    validate_materialized_action_for_commit,
)
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


def _write_preflight(
    root: Path,
    signal_id: str | None,
    suffix: str = "a",
    signal_ids: list[str] | None = None,
    metrics_hash: str | None = None,
) -> Path:
    ids = signal_ids if signal_ids is not None else ([signal_id] if signal_id else [])
    path = root / "runtime/preflight.json"
    path.write_text(
        json.dumps(
            {
                "should_call_model": True,
                "reasons": ["new_signals"] if ids else ["manual"],
                "new_signal_ids": ids,
                "metrics_hash": metrics_hash,
                "idempotency_key": suffix * 64,
            }
        )
    )
    return path


def _write_action(root: Path, payload: dict) -> Path:
    path = root / "runtime/model-action.json"
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


def _idea_candidate(idea_id: str, evidence_ids: list[str]) -> dict:
    return {
        "idea_id": idea_id,
        "name": "List jump helper",
        "summary": "긴 목록에서 반복 탐색을 줄이는 점프형 조작 도구입니다.",
        "target_users": ["operators"],
        "proposed_solution": "검증된 간격 이동과 위치 복귀를 기존 목록 흐름에 추가합니다.",
        "value_proposition": "반복 스크롤과 수동 위치 기억을 줄여 작업 흐름을 단순화합니다.",
        "differentiation": "대시보드가 아니라 기존 목록 조작의 마찰을 직접 줄입니다.",
        "revenue_model": "팀 단위 고급 설정을 유료화할 수 있습니다.",
        "feasibility": "정적 MVP에서 목록 상태와 단축 조작만 구현하면 됩니다.",
        "evidence_ids": evidence_ids,
        "risks": ["기존 단축키 습관을 바꾸지 않을 수 있습니다."],
        "evaluation_dimensions": ["반복 사용 가능성", "무료 MVP 구현성"],
    }


def _write_idea_evaluation_fixture(root: Path) -> None:
    _initialize_runtime(root)
    signal_two = {
        "signal_id": "signal-002",
        "source_pack": "small-business",
        "source_type": "rss",
        "url": "https://evidence.example/signal-002",
        "title": "Repeated list row returns",
        "summary": "Operators repeatedly return to the same list rows.",
        "collected_at": "2026-07-20T00:00:00Z",
        "published_at": "2026-07-19T00:00:00Z",
        "content_hash": "b" * 64,
    }
    with (root / "signals/raw/signals.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(signal_two) + "\n")
    state = CompanyState(
        lifecycle_stage=LifecycleStage.IDEA_EVALUATION,
        active_problem_id="problem-001",
    )
    (root / "company/state.json").write_text(state.model_dump_json(indent=2) + "\n")
    (root / "research/problems").mkdir(parents=True)
    (root / "research/problems/problem-001.json").write_text(
        json.dumps(
            {
                "problem_id": "problem-001",
                "title": "Repeated manual navigation",
                "target_users": ["operators"],
                "description": "Operators repeatedly lose position in long lists.",
                "current_workaround": "They scroll and manually remember positions.",
                "evidence_ids": ["signal-001", "signal-002"],
                "evidence": [
                    {
                        "evidence_id": "signal-001",
                        "source_type": "rss",
                        "url": "https://evidence.example/signal-001",
                        "summary": "Teams manually reconcile list positions.",
                    },
                    {
                        "evidence_id": "signal-002",
                        "source_type": "rss",
                        "url": "https://evidence.example/signal-002",
                        "summary": "Operators repeatedly return to the same list rows.",
                    },
                ],
                "frequency_score": 4,
                "severity_score": 4,
                "buildability_score": 7,
                "confidence": 0.7,
            }
        )
        + "\n"
    )


def _create_idea_payload() -> dict:
    return {
        "role": "researcher",
        "action_type": "create_idea_candidates",
        "title": "Create idea candidates",
        "summary": "Generate evidence-backed ideas for the active problem.",
        "rationale": "The active problem has validated evidence.",
        "risk_level": "low",
        "requires_approval": False,
        "evidence_ids": ["signal-001", "signal-002"],
        "idea_candidates": [
            _idea_candidate("idea-001", ["signal-001"]),
            _idea_candidate("idea-002", ["signal-001", "signal-002"]),
        ],
    }


def test_create_problem_candidate_applies_file_and_exact_state_transition(tmp_path: Path):
    signal_id = _initialize_runtime(tmp_path)
    initial = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    action_path = _write_action(tmp_path, _create_problem_payload(signal_id))
    action, changed = apply_validated_action(
        tmp_path,
        action_path,
        _write_preflight(tmp_path, signal_id),
        materialized_output_path=tmp_path / "runtime/materialized-action.json",
        applied_at=APPLIED_AT,
    )

    assert initial.lifecycle_stage == LifecycleStage.DISCOVERY
    assert changed
    assert action.action_type.value == "create_problem_candidate"
    assert ActionEnvelope.model_validate_json(action_path.read_text()).files == []
    assert MaterializedActionEnvelope.model_validate_json(
        (tmp_path / "runtime/materialized-action.json").read_text()
    ) == action
    problem_path = tmp_path / "research/problems/problem-001.json"
    assert problem_path.exists()
    problem = json.loads(problem_path.read_text())
    assert problem["evidence_ids"] == [signal_id]
    assert problem["evidence"][0]["evidence_id"] == signal_id
    assert problem["evidence"][0]["url"] == "https://evidence.example/signal-001"

    state = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.EVIDENCE_VALIDATION
    assert state.active_problem_id == "problem-001"
    assert state.selected_venture is None
    assert state.last_agent_run == APPLIED_AT
    unchanged = {
        key: value
        for key, value in state.model_dump(mode="json").items()
        if key not in {"lifecycle_stage", "last_agent_run", "active_problem_id"}
    }
    initial_unchanged = {
        key: value
        for key, value in initial.model_dump(mode="json").items()
        if key not in {"lifecycle_stage", "last_agent_run", "active_problem_id"}
    }
    assert unchanged == initial_unchanged


def test_validate_evidence_can_advance_from_validation_to_idea_evaluation(tmp_path: Path):
    signal_id = _initialize_runtime(tmp_path)
    create_path = _write_action(tmp_path, _create_problem_payload(signal_id))
    preflight_path = _write_preflight(tmp_path, signal_id)
    apply_validated_action(
        tmp_path,
        create_path,
        preflight_path,
        materialized_output_path=tmp_path / "runtime/materialized-create.json",
        applied_at=APPLIED_AT,
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
        materialized_output_path=tmp_path / "runtime/materialized-validate.json",
        applied_at=APPLIED_AT,
    )
    state = CompanyState.model_validate_json((tmp_path / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION
    assert state.selected_venture is None


def test_create_idea_candidates_applies_file_without_advancing_lifecycle(tmp_path: Path):
    _write_idea_evaluation_fixture(tmp_path)
    action_path = _write_action(tmp_path, _create_idea_payload())
    materialized_path = tmp_path / "runtime/materialized-action.json"

    action, changed = apply_validated_action(
        tmp_path,
        action_path,
        _write_preflight(tmp_path, None, "c", signal_ids=[]),
        materialized_output_path=materialized_path,
        applied_at=APPLIED_AT,
    )

    assert changed
    assert action.action_type.value == "create_idea_candidates"
    assert action.files is not None
    assert [file.path for file in action.files] == ["research/ideas/problem-001.json"]
    raw_action = ActionEnvelope.model_validate_json(action_path.read_text())
    assert raw_action.files == []
    assert raw_action.state_transition is None
    assert MaterializedActionEnvelope.model_validate_json(materialized_path.read_text()) == action
    ideas = json.loads((tmp_path / "research/ideas/problem-001.json").read_text())
    assert ideas["problem_id"] == "problem-001"
    assert [item["idea_id"] for item in ideas["idea_candidates"]] == [
        "idea-001",
        "idea-002",
    ]
    current_state = CompanyState.model_validate_json(
        (tmp_path / "company/state.json").read_text()
    )
    assert current_state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION
    assert current_state.active_problem_id == "problem-001"
    checkpoint = RepositoryCheckpoint.model_validate_json(
        (tmp_path / "company/checkpoints.json").read_text()
    )
    assert checkpoint.idempotency_keys == ["c" * 64]
    assert checkpoint.last_signal_ids == []
    assert checkpoint.last_metrics_hash is None
    assert checkpoint.updated_at == APPLIED_AT


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
    branch = step_index(lambda step: step.get("id") == "prepare_branch")
    apply = step_index(lambda step: step.get("id") == "apply")
    upload = step_index(lambda step: step.get("uses") == "actions/upload-artifact@v4")
    pytest_step = step_index(lambda step: step.get("run") == "python -m pytest")
    commit = step_index(lambda step: step.get("id") == "commit")
    assert checkout < branch < apply < upload < pytest_step < commit
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


def test_create_idea_candidates_materialize_commit_and_quality_flow(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_idea_evaluation_fixture(repo)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "company", "signals", "research"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", origin], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    control = tmp_path / "control"
    shutil.copytree(repo, control, ignore=shutil.ignore_patterns(".git", "runtime"))

    action_path = _write_action(repo, _create_idea_payload())
    preflight_path = _write_preflight(
        repo,
        None,
        "d",
        signal_ids=["signal-ee24e3790220b151"],
        metrics_hash="e" * 64,
    )
    materialized_path = repo / "runtime/materialized-action.json"
    branch = create_agent_branch(repo, action_path, "12345")
    action, changed = apply_validated_action(
        repo,
        action_path,
        preflight_path,
        materialized_output_path=materialized_path,
        applied_at=APPLIED_AT,
    )

    assert changed
    assert branch == "agent/12345-create-idea-candidates"
    assert ActionEnvelope.model_validate_json(action_path.read_text()).files == []
    assert MaterializedActionEnvelope.model_validate_json(materialized_path.read_text()) == action
    assert [change.path for change in action.files] == ["research/ideas/problem-001.json"]
    state = CompanyState.model_validate_json((repo / "company/state.json").read_text())
    assert state.lifecycle_stage == LifecycleStage.IDEA_EVALUATION

    changed, committed_branch, sha = commit_agent_changes(repo, materialized_path, "12345")

    assert changed
    assert committed_branch == branch
    assert len(sha) == 40
    changed_files = subprocess.run(
        ["git", "diff", "--name-only", "main..HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert changed_files == [
        "company/checkpoints.json",
        "research/ideas/problem-001.json",
    ]
    checkpoint = RepositoryCheckpoint.model_validate_json(
        (repo / "company/checkpoints.json").read_text()
    )
    assert checkpoint.idempotency_keys == ["d" * 64]
    assert checkpoint.last_signal_ids == []
    assert checkpoint.last_metrics_hash is None

    contract = validate_changed_file_contract(
        branch,
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {"filename": "research/ideas/problem-001.json", "status": "added"},
        ],
    )
    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=repo,
        contract=contract,
    )
    assert result.status == "valid"


def test_commit_rejects_materialized_idea_path_not_matching_active_problem(tmp_path: Path):
    _write_idea_evaluation_fixture(tmp_path)
    raw_action = ActionEnvelope.model_validate(_create_idea_payload())
    materialized = MaterializedActionEnvelope.from_model_action(
        raw_action,
        files=[FileChange(path="research/ideas/problem-other.json", content="{}\n")],
    )

    try:
        validate_materialized_action_for_commit(materialized, tmp_path)
    except ValueError as exc:
        assert "materialized file path is not allowed" in str(exc)
    else:
        raise AssertionError("unexpectedly accepted mismatched idea path")
