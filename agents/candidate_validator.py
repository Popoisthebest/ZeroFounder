from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

from agents.quality import ChangeValidation, VerificationStatus
from agents.safety import load_evidence_index
from agents.schemas import (
    CompanyState,
    LifecycleStage,
    ProblemCandidate,
    RepositoryCheckpoint,
    TriggerReason,
)

HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
URL = re.compile(r"https?://", re.I)


def _reject(
    contract: ChangeValidation,
    status: VerificationStatus,
    reason: str,
    files: list[str],
) -> ChangeValidation:
    return ChangeValidation(
        status=status,
        rejection_code=status,
        rejection_reason=reason,
        rejected_files=tuple(files),
        changed_files_count=contract.changed_files_count,
        action_type=contract.action_type,
        problem_id=contract.problem_id,
    )


def _monotonic_list(old: list[object], new: list[object]) -> bool:
    return set(old).issubset(set(new))


def _valid_optional_hash(value: str | None, pattern: re.Pattern[str]) -> bool:
    return value is None or bool(pattern.fullmatch(value))


def _checkpoint_fingerprint_is_valid(
    *,
    control_root: Path,
    candidate_root: Path,
    old: RepositoryCheckpoint,
    new: RepositoryCheckpoint,
) -> bool:
    try:
        metrics = (candidate_root / "company/metrics.json").read_bytes()
    except OSError:
        return False
    try:
        strategy = json.loads((control_root / "company/strategy.json").read_text())
        evidence = strategy["evidence"]
        min_new_signals = int(evidence["min_new_signals_for_analysis"])
        strong_threshold = float(evidence["strong_evidence_threshold"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        min_new_signals = 5
        strong_threshold = 0.85
    metrics_hash = hashlib.sha256(metrics).hexdigest()
    if new.last_metrics_hash != metrics_hash:
        return False

    signals = sorted(set(new.last_signal_ids) - set(old.last_signal_ids))
    issues = sorted(set(new.processed_issue_ids) - set(old.processed_issue_ids))
    comments = sorted(set(new.processed_comment_ids) - set(old.processed_comment_ids))
    if len(issues) > 1 or len(comments) > 1 or any(value <= 0 for value in issues + comments):
        return False
    reasons: list[TriggerReason] = []
    if len(signals) >= min_new_signals:
        reasons.append(TriggerReason.NEW_SIGNALS)
    elif signals and strong_threshold <= 0.5:
        reasons.append(TriggerReason.STRONG_SIGNAL)
    if issues:
        reasons.append(TriggerReason.NEW_ISSUE)
    if comments:
        reasons.append(TriggerReason.APPROVAL_COMMAND)
    product_sha = (
        new.last_product_sha
        if new.last_product_sha != old.last_product_sha
        else None
    )
    if product_sha:
        reasons.append(TriggerReason.PRODUCT_CHANGED)
    if new.last_metrics_hash != old.last_metrics_hash:
        reasons.append(TriggerReason.METRICS_CHANGED)
    if new.last_daily_review != old.last_daily_review:
        reasons.append(TriggerReason.DAILY_REVIEW)
    if new.last_weekly_review != old.last_weekly_review:
        reasons.append(TriggerReason.WEEKLY_REVIEW)

    if not new.idempotency_keys:
        return False
    target = new.idempotency_keys[-1]
    for manual in (False, True):
        candidate_reasons = [*reasons]
        if manual:
            candidate_reasons.append(TriggerReason.MANUAL)
        material = {
            "signals": signals,
            "issues": issues,
            "comments": comments,
            "product_sha": product_sha,
            "metrics_hash": metrics_hash,
            "reasons": candidate_reasons,
        }
        expected = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if target == expected:
            return True
    return False


def validate_create_problem_candidate_content(
    *,
    control_root: Path,
    candidate_root: Path,
    contract: ChangeValidation,
) -> ChangeValidation:
    if contract.action_type != "create_problem_candidate" or not contract.problem_id:
        return _reject(
            contract,
            "invalid_problem_path",
            "branch 행동과 문제 후보 경로를 연결할 수 없습니다.",
            [],
        )
    checkpoint_path = Path("company/checkpoints.json")
    state_path = Path("company/state.json")
    problem_path = Path(f"research/problems/{contract.problem_id}.json")
    required = [checkpoint_path, state_path, problem_path]
    if any((candidate_root / path).is_symlink() for path in required):
        return _reject(
            contract,
            "disallowed_file",
            "검증 대상 파일에 심볼릭 링크가 포함됐습니다.",
            [path.as_posix() for path in required if (candidate_root / path).is_symlink()],
        )
    try:
        old_checkpoint = RepositoryCheckpoint.model_validate_json(
            (control_root / checkpoint_path).read_text(encoding="utf-8")
        )
        new_checkpoint = RepositoryCheckpoint.model_validate_json(
            (candidate_root / checkpoint_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "checkpoint JSON 구조를 안전하게 검증할 수 없습니다.",
            [checkpoint_path.as_posix()],
        )

    checkpoint_lists_valid = all(
        (
            _monotonic_list(old_checkpoint.last_signal_ids, new_checkpoint.last_signal_ids),
            _monotonic_list(
                old_checkpoint.processed_issue_ids, new_checkpoint.processed_issue_ids
            ),
            _monotonic_list(
                old_checkpoint.processed_comment_ids, new_checkpoint.processed_comment_ids
            ),
            new_checkpoint.idempotency_keys[:-1] == old_checkpoint.idempotency_keys,
            len(new_checkpoint.idempotency_keys) == len(old_checkpoint.idempotency_keys) + 1,
            len(new_checkpoint.last_signal_ids) == len(set(new_checkpoint.last_signal_ids)),
            len(new_checkpoint.processed_issue_ids)
            == len(set(new_checkpoint.processed_issue_ids)),
            len(new_checkpoint.processed_comment_ids)
            == len(set(new_checkpoint.processed_comment_ids)),
            len(new_checkpoint.idempotency_keys) == len(set(new_checkpoint.idempotency_keys)),
        )
    )
    checkpoint_scalars_valid = all(
        (
            new_checkpoint.version == old_checkpoint.version,
            _valid_optional_hash(new_checkpoint.last_product_sha, HEX_40),
            _valid_optional_hash(new_checkpoint.last_metrics_hash, HEX_64),
            old_checkpoint.last_product_sha is None
            or new_checkpoint.last_product_sha is not None,
            old_checkpoint.last_metrics_hash is None
            or new_checkpoint.last_metrics_hash is not None,
            all(HEX_64.fullmatch(value) for value in new_checkpoint.idempotency_keys),
            new_checkpoint.updated_at is not None,
            old_checkpoint.updated_at is None
            or (
                new_checkpoint.updated_at is not None
                and new_checkpoint.updated_at >= old_checkpoint.updated_at
            ),
            old_checkpoint.last_daily_review is None
            or (
                new_checkpoint.last_daily_review is not None
                and new_checkpoint.last_daily_review >= old_checkpoint.last_daily_review
            ),
            old_checkpoint.last_weekly_review is None
            or (
                new_checkpoint.last_weekly_review is not None
                and new_checkpoint.last_weekly_review >= old_checkpoint.last_weekly_review
            ),
            new_checkpoint.last_daily_review == old_checkpoint.last_daily_review
            or (
                new_checkpoint.updated_at is not None
                and new_checkpoint.last_daily_review == new_checkpoint.updated_at.date()
            ),
            new_checkpoint.last_weekly_review == old_checkpoint.last_weekly_review
            or (
                new_checkpoint.updated_at is not None
                and new_checkpoint.last_weekly_review == new_checkpoint.updated_at.date()
            ),
        )
    )
    if not checkpoint_lists_valid or not checkpoint_scalars_valid:
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "checkpoint가 기존 기록 보존과 현재 실행 추가 규칙을 충족하지 않습니다.",
            [checkpoint_path.as_posix()],
        )
    if not _checkpoint_fingerprint_is_valid(
        control_root=control_root,
        candidate_root=candidate_root,
        old=old_checkpoint,
        new=new_checkpoint,
    ):
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "checkpoint 처리 기록이 저장소 입력과 현재 실행 fingerprint에 맞지 않습니다.",
            [checkpoint_path.as_posix()],
        )

    try:
        old_state = CompanyState.model_validate_json(
            (control_root / state_path).read_text(encoding="utf-8")
        )
        new_state = CompanyState.model_validate_json(
            (candidate_root / state_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return _reject(
            contract,
            "invalid_state_change",
            "회사 상태 JSON 구조를 안전하게 검증할 수 없습니다.",
            [state_path.as_posix()],
        )
    state_mutable_fields = {"lifecycle_stage", "last_agent_run", "active_problem_id"}
    old_unchanged = old_state.model_dump(mode="json", exclude=state_mutable_fields)
    new_unchanged = new_state.model_dump(mode="json", exclude=state_mutable_fields)
    if (
        old_state.lifecycle_stage != LifecycleStage.DISCOVERY
        or new_state.lifecycle_stage != LifecycleStage.EVIDENCE_VALIDATION
        or old_state.active_problem_id is not None
        or new_state.active_problem_id != contract.problem_id
        or new_state.last_agent_run is None
        or old_unchanged != new_unchanged
    ):
        return _reject(
            contract,
            "invalid_state_change",
            "create_problem_candidate에 허용된 상태 전환 범위를 벗어났습니다.",
            [state_path.as_posix()],
        )
    if (
        new_checkpoint.updated_at is not None
        and abs(new_checkpoint.updated_at - new_state.last_agent_run) > timedelta(minutes=5)
    ):
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "checkpoint와 상태 변경 시각이 동일 실행으로 보기 어렵습니다.",
            [checkpoint_path.as_posix(), state_path.as_posix()],
        )

    if (control_root / problem_path).exists():
        return _reject(
            contract,
            "invalid_problem_path",
            "create_problem_candidate가 기존 문제 파일을 덮어쓰려 합니다.",
            [problem_path.as_posix()],
        )
    try:
        problem = ProblemCandidate.model_validate_json(
            (candidate_root / problem_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return _reject(
            contract,
            "invalid_problem_path",
            "문제 후보 JSON 구조가 올바르지 않습니다.",
            [problem_path.as_posix()],
        )
    if problem.problem_id != contract.problem_id or len(problem.evidence_ids) != len(
        set(problem.evidence_ids)
    ):
        return _reject(
            contract,
            "invalid_problem_path",
            "문제 ID 또는 evidence ID 구조가 파일 경로와 일치하지 않습니다.",
            [problem_path.as_posix()],
        )
    human_fields = [
        problem.title,
        problem.description,
        problem.current_workaround,
        *problem.target_users,
    ]
    if any(URL.search(value) for value in human_fields):
        return _reject(
            contract,
            "invalid_problem_path",
            "근거 레코드 밖의 필드에 임의 URL이 포함됐습니다.",
            [problem_path.as_posix()],
        )
    evidence_index = load_evidence_index(candidate_root)
    added_signal_ids = set(new_checkpoint.last_signal_ids) - set(old_checkpoint.last_signal_ids)
    if any(evidence_id not in evidence_index for evidence_id in added_signal_ids):
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "checkpoint에 저장소에 없는 signal/evidence ID가 추가됐습니다.",
            [checkpoint_path.as_posix()],
        )
    if not problem.evidence_ids or any(
        evidence_id not in evidence_index for evidence_id in problem.evidence_ids
    ):
        return _reject(
            contract,
            "invalid_problem_path",
            "저장소에 존재하지 않는 signal/evidence ID가 참조됐습니다.",
            [problem_path.as_posix()],
        )
    if not set(problem.evidence_ids).issubset(set(new_checkpoint.last_signal_ids)):
        return _reject(
            contract,
            "invalid_checkpoint_change",
            "문제 후보 evidence ID가 checkpoint 처리 기록에 없습니다.",
            [checkpoint_path.as_posix(), problem_path.as_posix()],
        )
    if [item.evidence_id for item in problem.evidence] != problem.evidence_ids:
        return _reject(
            contract,
            "invalid_problem_path",
            "문제 후보의 evidence 상세와 evidence_ids가 일치하지 않습니다.",
            [problem_path.as_posix()],
        )
    for reference in problem.evidence:
        record = evidence_index[reference.evidence_id]
        expected_url = record.get("url") or record.get("source_url")
        expected_source = str(record.get("source_type") or "unknown")
        expected_summary = str(
            record.get("summary") or record.get("title") or "Evidence"
        )[:500].strip()
        if (
            reference.url != expected_url
            or reference.source_type != expected_source
            or reference.summary != expected_summary
        ):
            return _reject(
                contract,
                "invalid_problem_path",
                "문제 후보 근거가 저장된 evidence 레코드와 일치하지 않습니다.",
                [problem_path.as_posix()],
            )
    return contract
