import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from agents.bootstrap import initial_company_state
from agents.candidate_validator import validate_create_problem_candidate_content
from agents.quality import ChangeValidation, validate_changed_file_contract
from agents.schemas import (
    LifecycleStage,
    ProblemCandidate,
    ProblemEvidenceReference,
    RepositoryCheckpoint,
    TriggerReason,
)

RUN_AT = datetime(2026, 7, 20, 15, 56, 5, tzinfo=UTC)
BRANCH = "agent/29757293892-create-problem-candidate"
PROBLEM_ID = "problem-navigation-inefficiency"


def _signal(signal_id: str) -> dict[str, object]:
    return {
        "signal_id": signal_id,
        "source_pack": "developer",
        "source_type": "github_issue",
        "url": f"https://github.com/example/project/issues/{signal_id[-1]}",
        "title": "List navigation is inefficient",
        "summary": "Users repeatedly navigate long lists with slow manual controls.",
        "collected_at": "2026-07-20T10:43:39Z",
        "published_at": None,
        "content_hash": "a" * 64,
    }


def _prepare_candidate(tmp_path: Path) -> tuple[Path, Path, ChangeValidation]:
    control = tmp_path / "control"
    candidate = tmp_path / "candidate"
    (control / "company").mkdir(parents=True)
    (control / "signals/raw").mkdir(parents=True)
    old_signal = _signal("signal-old")
    new_signal = _signal("signal-new")
    (control / "signals/raw/signals.jsonl").write_text(
        json.dumps(old_signal) + "\n" + json.dumps(new_signal) + "\n",
        encoding="utf-8",
    )
    (control / "company/state.json").write_text(
        initial_company_state().model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    old_checkpoint = RepositoryCheckpoint(
        last_signal_ids=["signal-old"],
        idempotency_keys=["a" * 64],
    )
    (control / "company/checkpoints.json").write_text(
        old_checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    shutil.copytree(control, candidate)

    metrics = b'{"features_registered":0}\n'
    (control / "company/metrics.json").write_bytes(metrics)
    (candidate / "company/metrics.json").write_bytes(metrics)
    metrics_hash = hashlib.sha256(metrics).hexdigest()
    reasons = [TriggerReason.METRICS_CHANGED, TriggerReason.MANUAL]
    material = {
        "signals": ["signal-new"],
        "issues": [],
        "comments": [],
        "product_sha": None,
        "metrics_hash": metrics_hash,
        "reasons": reasons,
    }
    idempotency_key = hashlib.sha256(
        json.dumps(
            material, sort_keys=True, default=str, separators=(",", ":")
        ).encode()
    ).hexdigest()

    state = initial_company_state().model_copy(
        update={
            "lifecycle_stage": LifecycleStage.EVIDENCE_VALIDATION,
            "active_problem_id": PROBLEM_ID,
            "last_agent_run": RUN_AT,
        }
    )
    (candidate / "company/state.json").write_text(
        state.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    checkpoint = old_checkpoint.model_copy(
        update={
            "last_signal_ids": ["signal-old", "signal-new"],
            "idempotency_keys": ["a" * 64, idempotency_key],
            "last_metrics_hash": metrics_hash,
            "updated_at": RUN_AT,
        }
    )
    (candidate / "company/checkpoints.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    record = new_signal
    problem = ProblemCandidate(
        problem_id=PROBLEM_ID,
        title="긴 목록 탐색 과정의 반복 낭비",
        target_users=["재고 관리자", "운영 담당자"],
        description="긴 목록에서 항목을 찾는 과정이 반복적으로 느리고 오류를 유발합니다.",
        current_workaround="스크롤과 키보드 단축키를 반복 사용합니다.",
        evidence_ids=["signal-new"],
        evidence=[
            ProblemEvidenceReference(
                evidence_id="signal-new",
                source_type=str(record["source_type"]),
                url=str(record["url"]),
                summary=str(record["summary"]),
            )
        ],
        frequency_score=3,
        severity_score=4,
        buildability_score=7,
        confidence=0.6,
    )
    problem_dir = candidate / "research/problems"
    problem_dir.mkdir(parents=True)
    (problem_dir / f"{PROBLEM_ID}.json").write_text(
        problem.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    contract = validate_changed_file_contract(
        BRANCH,
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {"filename": "company/state.json", "status": "modified"},
            {"filename": f"research/problems/{PROBLEM_ID}.json", "status": "added"},
        ],
    )
    return control, candidate, contract


def test_pr_one_candidate_content_contract_is_valid(tmp_path: Path):
    control, candidate, contract = _prepare_candidate(tmp_path)
    result = validate_create_problem_candidate_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "valid"
    assert result.changed_files_count == 3
    assert result.problem_id == PROBLEM_ID


def test_checkpoint_cannot_delete_existing_processing_records(tmp_path: Path):
    control, candidate, contract = _prepare_candidate(tmp_path)
    checkpoint = RepositoryCheckpoint.model_validate_json(
        (candidate / "company/checkpoints.json").read_text(encoding="utf-8")
    ).model_copy(
        update={"last_signal_ids": ["signal-new"]}
    )
    (candidate / "company/checkpoints.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    result = validate_create_problem_candidate_content(
        control_root=control, candidate_root=candidate, contract=contract
    )
    assert result.status == "invalid_checkpoint_change"
    assert result.rejected_files == ("company/checkpoints.json",)


def test_create_problem_candidate_rejects_unrelated_state_change(tmp_path: Path):
    control, candidate, contract = _prepare_candidate(tmp_path)
    state_path = candidate / "company/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["selected_venture"] = "invented-venture"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = validate_create_problem_candidate_content(
        control_root=control, candidate_root=candidate, contract=contract
    )
    assert result.status == "invalid_state_change"
    assert result.rejected_files == ("company/state.json",)


def test_create_problem_candidate_rejects_problem_id_or_evidence_forgery(tmp_path: Path):
    control, candidate, contract = _prepare_candidate(tmp_path)
    problem_path = candidate / f"research/problems/{PROBLEM_ID}.json"
    problem = json.loads(problem_path.read_text(encoding="utf-8"))
    problem["evidence_ids"] = ["signal-invented"]
    problem_path.write_text(json.dumps(problem), encoding="utf-8")
    result = validate_create_problem_candidate_content(
        control_root=control, candidate_root=candidate, contract=contract
    )
    assert result.status == "invalid_problem_path"
    assert result.rejected_files == (f"research/problems/{PROBLEM_ID}.json",)
