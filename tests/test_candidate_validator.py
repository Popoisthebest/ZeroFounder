import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from agents.bootstrap import initial_company_state
from agents.candidate_validator import (
    validate_create_idea_candidates_content,
    validate_create_problem_candidate_content,
    validate_validate_evidence_content,
    validate_write_report_content,
)
from agents.quality import ChangeValidation, validate_changed_file_contract
from agents.report_materializer import report_artifact_path, report_period
from agents.schemas import (
    LifecycleStage,
    ProblemCandidate,
    ProblemEvidenceReference,
    RepositoryCheckpoint,
    TriggerReason,
)

RUN_AT = datetime(2026, 7, 20, 15, 56, 5, tzinfo=UTC)
BRANCH = "agent/29757293892-create-problem-candidate"
VALIDATE_BRANCH = "agent/29757293893-validate-evidence"
IDEA_BRANCH = "agent/29757293894-create-idea-candidates"
REPORT_BRANCH = "agent/29757293895-write-report"
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


def _prepare_validate_evidence_candidate(
    tmp_path: Path,
) -> tuple[Path, Path, ChangeValidation]:
    control = tmp_path / "control"
    candidate = tmp_path / "candidate"
    (control / "company").mkdir(parents=True)
    state = initial_company_state().model_copy(
        update={
            "lifecycle_stage": LifecycleStage.EVIDENCE_VALIDATION,
            "active_problem_id": PROBLEM_ID,
            "last_agent_run": datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        }
    )
    (control / "company/state.json").write_text(
        state.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    checkpoint = RepositoryCheckpoint(idempotency_keys=["a" * 64])
    (control / "company/checkpoints.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    shutil.copytree(control, candidate)

    updated_state = state.model_copy(
        update={
            "lifecycle_stage": LifecycleStage.IDEA_EVALUATION,
            "last_agent_run": RUN_AT,
        }
    )
    (candidate / "company/state.json").write_text(
        updated_state.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    updated_checkpoint = checkpoint.model_copy(
        update={"idempotency_keys": ["a" * 64, "b" * 64], "updated_at": RUN_AT}
    )
    (candidate / "company/checkpoints.json").write_text(
        updated_checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    contract = validate_changed_file_contract(
        VALIDATE_BRANCH,
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {"filename": "company/state.json", "status": "modified"},
        ],
    )
    return control, candidate, contract


def _idea_candidate(idea_id: str, evidence_ids: list[str]) -> dict[str, object]:
    return {
        "idea_id": idea_id,
        "name": f"Candidate {idea_id}",
        "summary": "긴 목록 반복 탐색을 줄이는 후보입니다.",
        "target_users": ["operators"],
        "proposed_solution": "목록 위치 이동과 복귀를 단순하게 제공합니다.",
        "value_proposition": "반복 스크롤과 수동 위치 기억을 줄입니다.",
        "differentiation": "범용 검색 대신 반복 목록 탐색 문제만 다룹니다.",
        "revenue_model": "팀 공유 설정을 유료 기능으로 둘 수 있습니다.",
        "feasibility": "정적 브라우저 MVP로 검증할 수 있습니다.",
        "evidence_ids": evidence_ids,
        "risks": ["기존 방식에 머물 수 있습니다."],
        "evaluation_dimensions": ["반복 사용 가능성", "무료 MVP 구현성"],
    }


def _prepare_create_idea_candidate(
    tmp_path: Path,
) -> tuple[Path, Path, ChangeValidation]:
    control = tmp_path / "control"
    candidate = tmp_path / "candidate"
    (control / "company").mkdir(parents=True)
    (control / "research/problems").mkdir(parents=True)
    state = initial_company_state().model_copy(
        update={
            "lifecycle_stage": LifecycleStage.IDEA_EVALUATION,
            "active_problem_id": PROBLEM_ID,
        }
    )
    (control / "company/state.json").write_text(
        state.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    checkpoint = RepositoryCheckpoint(idempotency_keys=["a" * 64])
    (control / "company/checkpoints.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    problem = ProblemCandidate(
        problem_id=PROBLEM_ID,
        title="긴 목록 탐색 과정의 반복 낭비",
        target_users=["재고 관리자"],
        description="긴 목록에서 항목을 찾는 과정이 반복적으로 느립니다.",
        current_workaround="스크롤과 키보드 단축키를 반복 사용합니다.",
        evidence_ids=["signal-new", "signal-old"],
        evidence=[
            ProblemEvidenceReference(
                evidence_id="signal-new",
                source_type="github_issue",
                url="https://example.test/new",
                summary="반복 탐색이 느립니다.",
            ),
            ProblemEvidenceReference(
                evidence_id="signal-old",
                source_type="github_issue",
                url="https://example.test/old",
                summary="목록 이동이 번거롭습니다.",
            ),
        ],
        frequency_score=3,
        severity_score=4,
        buildability_score=7,
        confidence=0.6,
    )
    (control / f"research/problems/{PROBLEM_ID}.json").write_text(
        problem.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    shutil.copytree(control, candidate)
    updated_checkpoint = checkpoint.model_copy(
        update={"idempotency_keys": ["a" * 64, "c" * 64], "updated_at": RUN_AT}
    )
    (candidate / "company/checkpoints.json").write_text(
        updated_checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (candidate / "research/ideas").mkdir(parents=True)
    (candidate / f"research/ideas/{PROBLEM_ID}.json").write_text(
        json.dumps(
            {
                "problem_id": PROBLEM_ID,
                "lifecycle_stage": "IDEA_EVALUATION",
                "idea_candidates": [
                    _idea_candidate("idea-001", ["signal-new"]),
                    _idea_candidate("idea-002", ["signal-new", "signal-old"]),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    contract = validate_changed_file_contract(
        IDEA_BRANCH,
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {"filename": f"research/ideas/{PROBLEM_ID}.json", "status": "added"},
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


def test_validate_evidence_content_contract_is_valid_for_state_and_checkpoint_only(
    tmp_path: Path,
):
    control, candidate, contract = _prepare_validate_evidence_candidate(tmp_path)

    result = validate_validate_evidence_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )

    assert result.status == "valid"
    assert result.changed_files_count == 2
    assert result.allowed_files == ("company/checkpoints.json", "company/state.json")


def test_validate_evidence_rejects_active_problem_or_checkpoint_record_changes(
    tmp_path: Path,
):
    control, candidate, contract = _prepare_validate_evidence_candidate(tmp_path)
    state_path = candidate / "company/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["active_problem_id"] = "problem-other"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = validate_validate_evidence_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_state_change"
    assert result.rejected_files == ("company/state.json",)

    control, candidate, contract = _prepare_validate_evidence_candidate(tmp_path / "checkpoint")
    checkpoint_path = candidate / "company/checkpoints.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["processed_issue_ids"] = [123]
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    result = validate_validate_evidence_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_checkpoint_change"
    assert result.rejected_files == ("company/checkpoints.json",)


def test_create_idea_candidates_content_contract_is_valid(tmp_path: Path):
    control, candidate, contract = _prepare_create_idea_candidate(tmp_path)

    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )

    assert result.status == "valid"
    assert result.allowed_files == (
        "company/checkpoints.json",
        f"research/ideas/{PROBLEM_ID}.json",
    )


def test_create_idea_candidates_rejects_bad_evidence_or_state_change(tmp_path: Path):
    control, candidate, contract = _prepare_create_idea_candidate(tmp_path)
    idea_path = candidate / f"research/ideas/{PROBLEM_ID}.json"
    payload = json.loads(idea_path.read_text(encoding="utf-8"))
    payload["idea_candidates"][0]["evidence_ids"] = ["signal-invented"]
    idea_path.write_text(json.dumps(payload), encoding="utf-8")

    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_problem_path"
    assert result.rejected_files == (f"research/ideas/{PROBLEM_ID}.json",)

    control, candidate, contract = _prepare_create_idea_candidate(tmp_path / "state")
    state_path = candidate / "company/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["lifecycle_stage"] = "DISTRIBUTION_CHECK"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_state_change"


def test_create_idea_candidates_rejects_checkpoint_signal_or_metrics_mutation(
    tmp_path: Path,
):
    control, candidate, contract = _prepare_create_idea_candidate(tmp_path)
    checkpoint_path = candidate / "company/checkpoints.json"
    checkpoint = RepositoryCheckpoint.model_validate_json(
        checkpoint_path.read_text(encoding="utf-8")
    )
    checkpoint_path.write_text(
        checkpoint.model_copy(
            update={"last_signal_ids": ["signal-ee24e3790220b151"]}
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )

    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_checkpoint_change"
    assert result.rejected_files == ("company/checkpoints.json",)


def _prepare_write_report_candidate(
    tmp_path: Path,
    *,
    pdf_content: bytes,
) -> tuple[Path, Path, ChangeValidation]:
    control = tmp_path / "control"
    candidate = tmp_path / "candidate"
    (control / "company").mkdir(parents=True)
    (control / "company/state.json").write_text(
        initial_company_state()
        .model_copy(update={"lifecycle_stage": LifecycleStage.DISTRIBUTION_CHECK})
        .model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    (control / "company/strategy.json").write_text(
        json.dumps({"review": {"timezone": "Asia/Seoul"}}) + "\n",
        encoding="utf-8",
    )
    (control / "company/checkpoints.json").write_text(
        RepositoryCheckpoint(idempotency_keys=["a" * 64]).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    shutil.copytree(control, candidate)
    checkpoint = RepositoryCheckpoint(
        idempotency_keys=["a" * 64, "b" * 64],
        updated_at=RUN_AT,
    )
    (candidate / "company/checkpoints.json").write_text(
        checkpoint.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    period = report_period(control)
    path = report_artifact_path(period)
    report_path = candidate / path
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(pdf_content)
    contract = validate_changed_file_contract(
        REPORT_BRANCH,
        [
            {"filename": "company/checkpoints.json", "status": "modified"},
            {"filename": path, "status": "added"},
        ],
    )
    return control, candidate, contract


def test_write_report_candidate_requires_real_pdf_and_checkpoint_only(tmp_path: Path):
    control, candidate, contract = _prepare_write_report_candidate(
        tmp_path / "valid",
        pdf_content=b"%PDF-1.4\nbody body body\n%%EOF\n",
    )

    result = validate_write_report_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )

    assert result.status == "valid"
    assert result.report_type == "weekly"
    assert result.artifact_path == report_artifact_path(report_period(control))
    assert result.operation_key

    control, candidate, contract = _prepare_write_report_candidate(
        tmp_path / "fake",
        pdf_content=b"not a pdf",
    )
    result = validate_write_report_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_report_path"

    control, candidate, contract = _prepare_create_idea_candidate(tmp_path / "metrics")
    checkpoint_path = candidate / "company/checkpoints.json"
    checkpoint = RepositoryCheckpoint.model_validate_json(
        checkpoint_path.read_text(encoding="utf-8")
    )
    checkpoint_path.write_text(
        checkpoint.model_copy(update={"last_metrics_hash": "e" * 64}).model_dump_json(
            indent=2
        )
        + "\n",
        encoding="utf-8",
    )

    result = validate_create_idea_candidates_content(
        control_root=control,
        candidate_root=candidate,
        contract=contract,
    )
    assert result.status == "invalid_checkpoint_change"
    assert result.rejected_files == ("company/checkpoints.json",)
