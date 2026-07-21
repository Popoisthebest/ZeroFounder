from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from agents.github_client import BRANCH, SHA

VerificationStatus = Literal[
    "valid",
    "invalid_pr",
    "branch_mismatch",
    "sha_mismatch",
    "repository_mismatch",
    "closed_pr",
    "disallowed_file",
    "deleted_file",
    "too_many_files",
    "invalid_checkpoint_change",
    "invalid_state_change",
    "invalid_problem_path",
]
ValidationStatus = Literal[
    "passed",
    "failed",
    "invalid_pr",
    "branch_mismatch",
    "sha_mismatch",
    "repository_mismatch",
    "closed_pr",
    "disallowed_file",
    "deleted_file",
    "too_many_files",
    "invalid_checkpoint_change",
    "invalid_state_change",
    "invalid_problem_path",
    "quality_check_not_started",
]
ReviewStatus = Literal[
    "ready_for_human_review",
    "quality_check_failed",
    "invalid_pr",
    "branch_mismatch",
    "sha_mismatch",
    "repository_mismatch",
    "disallowed_file",
    "deleted_file",
    "too_many_files",
    "invalid_checkpoint_change",
    "invalid_state_change",
    "invalid_problem_path",
    "quality_check_not_started",
]

AGENT_ALLOWED_PREFIXES = (
    "venture/product/",
    "venture/content/",
    "venture/public/",
    "research/",
    "experiments/",
    "reports/",
    "ideas/",
    "signals/processed/",
)
AGENT_ALLOWED_EXACT = {
    "company/state.json",
    "company/strategy.json",
    "company/metrics.json",
    "company/task-board.json",
    "company/decisions.jsonl",
    "venture/product-requirements.md",
    "venture/user-personas.md",
    "venture/user-flows.md",
    "venture/mvp-scope.md",
    "venture/launch-plan.md",
    "venture/metrics-plan.md",
    "venture/venture.json",
    "venture/infrastructure.json",
    "founder/tasks.md",
    "founder/outreach-plan.md",
    "founder/posting-pack.md",
}
DEPENDENCY_ALLOWED_EXACT = {
    "requirements.txt",
    "package.json",
    "package-lock.json",
}

CREATE_PROBLEM_REQUIRED_EXACT = {
    "company/checkpoints.json",
    "company/state.json",
}
VALIDATE_EVIDENCE_ALLOWED_EXACT = (
    "company/checkpoints.json",
    "company/state.json",
)
CREATE_IDEA_REQUIRED_EXACT = {"company/checkpoints.json"}
PROBLEM_PATH = re.compile(
    r"^research/problems/(?P<problem_id>problem-[a-z0-9][a-z0-9._-]{0,100})\.json$"
)
IDEA_PATH = re.compile(
    r"^research/ideas/(?P<problem_id>problem-[a-z0-9][a-z0-9._-]{0,100})\.json$"
)
AGENT_ACTION_BRANCH = re.compile(
    r"^agent/(?P<run_id>[0-9]{1,30})-(?P<action>[a-z][a-z0-9-]{1,80})$"
)
SAFE_REPORTED_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,240}$")


@dataclass(frozen=True)
class ChangeValidation:
    status: VerificationStatus
    rejection_code: str
    rejection_reason: str
    rejected_files: tuple[str, ...]
    changed_files_count: int
    action_type: str | None = None
    problem_id: str | None = None
    allowed_files: tuple[str, ...] = ()


def _change_result(
    status: VerificationStatus,
    *,
    count: int,
    reason: str = "",
    files: list[str] | tuple[str, ...] = (),
    allowed_files: list[str] | tuple[str, ...] = (),
    action_type: str | None = None,
    problem_id: str | None = None,
) -> ChangeValidation:
    return ChangeValidation(
        status=status,
        rejection_code="" if status == "valid" else status,
        rejection_reason=reason,
        rejected_files=tuple(sorted(set(files))),
        changed_files_count=count,
        action_type=action_type,
        problem_id=problem_id,
        allowed_files=tuple(sorted(set(allowed_files))),
    )


def action_type_from_branch(branch: str) -> str | None:
    match = AGENT_ACTION_BRANCH.fullmatch(branch)
    return match.group("action").replace("-", "_") if match else None


def validate_changed_file_contract(
    branch: str, files: list[dict[str, object]]
) -> ChangeValidation:
    count = len(files)
    if not files:
        return _change_result(
            "disallowed_file", count=count, reason="변경 파일이 없습니다."
        )
    if count > 100:
        return _change_result(
            "too_many_files", count=count, reason="변경 파일 수가 안전 한도를 초과했습니다."
        )
    normalized_files: list[str] = []
    for record in files:
        raw = record.get("filename")
        if not isinstance(raw, str):
            return _change_result(
                "disallowed_file", count=count, reason="변경 파일 경로 형식이 잘못됐습니다."
            )
        if not SAFE_REPORTED_PATH.fullmatch(raw):
            return _change_result(
                "disallowed_file",
                count=count,
                reason="변경 파일 경로에 허용되지 않은 문자가 포함됐습니다.",
                files=["[invalid-path]"],
            )
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return _change_result(
                "disallowed_file",
                count=count,
                reason="절대경로나 경로 순회가 포함됐습니다.",
                files=[raw],
            )
        normalized = path.as_posix()
        normalized_files.append(normalized)
        if record.get("status") in {"removed", "renamed"}:
            return _change_result(
                "deleted_file",
                count=count,
                reason="파일 삭제 또는 이름 변경은 허용되지 않습니다.",
                files=[normalized],
            )

    if branch.startswith("dependency/"):
        rejected = [path for path in normalized_files if path not in DEPENDENCY_ALLOWED_EXACT]
        if rejected:
            return _change_result(
                "disallowed_file",
                count=count,
                reason="승인된 의존성 파일 외 변경이 포함됐습니다.",
                files=rejected,
                action_type="propose_dependency",
            )
        return _change_result("valid", count=count, action_type="propose_dependency")

    action_type = action_type_from_branch(branch)
    if action_type == "create_problem_candidate":
        problem_paths = [path for path in normalized_files if path.startswith("research/problems/")]
        valid_problem_paths = [path for path in problem_paths if PROBLEM_PATH.fullmatch(path)]
        if len(problem_paths) != 1 or len(valid_problem_paths) != 1:
            return _change_result(
                "invalid_problem_path",
                count=count,
                reason="검증 가능한 문제 후보 JSON 경로가 정확히 하나여야 합니다.",
                files=problem_paths,
                allowed_files=[
                    *CREATE_PROBLEM_REQUIRED_EXACT,
                    "research/problems/<problem_id>.json",
                ],
                action_type=action_type,
            )
        expected = CREATE_PROBLEM_REQUIRED_EXACT | {valid_problem_paths[0]}
        actual = set(normalized_files)
        if count > 3:
            return _change_result(
                "too_many_files",
                count=count,
                reason="문제 후보 생성은 정확히 세 파일만 변경할 수 있습니다.",
                files=sorted(actual - expected),
                allowed_files=sorted(expected),
                action_type=action_type,
            )
        if actual != expected:
            rejected = sorted(actual.symmetric_difference(expected))
            return _change_result(
                "disallowed_file",
                count=count,
                reason="문제 후보 생성 허용 목록과 변경 파일이 일치하지 않습니다.",
                files=rejected,
                allowed_files=sorted(expected),
                action_type=action_type,
            )
        match = PROBLEM_PATH.fullmatch(valid_problem_paths[0])
        return _change_result(
            "valid",
            count=count,
            action_type=action_type,
            problem_id=match.group("problem_id") if match else None,
            allowed_files=sorted(expected),
        )

    if action_type == "validate_evidence":
        expected = set(VALIDATE_EVIDENCE_ALLOWED_EXACT)
        actual = set(normalized_files)
        if actual != expected:
            rejected = sorted(actual - expected)
            missing = sorted(expected - actual)
            return _change_result(
                "disallowed_file",
                count=count,
                reason=(
                    "validate_evidence는 상태와 checkpoint 파일만 변경할 수 있습니다."
                ),
                files=[*rejected, *missing],
                allowed_files=VALIDATE_EVIDENCE_ALLOWED_EXACT,
                action_type=action_type,
            )
        return _change_result(
            "valid",
            count=count,
            action_type=action_type,
            allowed_files=VALIDATE_EVIDENCE_ALLOWED_EXACT,
        )

    if action_type == "create_idea_candidates":
        idea_paths = [path for path in normalized_files if path.startswith("research/ideas/")]
        valid_idea_paths = [path for path in idea_paths if IDEA_PATH.fullmatch(path)]
        if len(idea_paths) != 1 or len(valid_idea_paths) != 1:
            return _change_result(
                "disallowed_file",
                count=count,
                reason="아이디어 후보 JSON 경로가 정확히 하나여야 합니다.",
                files=idea_paths,
                allowed_files=[*CREATE_IDEA_REQUIRED_EXACT, "research/ideas/<problem_id>.json"],
                action_type=action_type,
            )
        expected = CREATE_IDEA_REQUIRED_EXACT | {valid_idea_paths[0]}
        actual = set(normalized_files)
        if actual != expected:
            return _change_result(
                "disallowed_file",
                count=count,
                reason="아이디어 후보 생성 허용 목록과 변경 파일이 일치하지 않습니다.",
                files=sorted(actual.symmetric_difference(expected)),
                allowed_files=sorted(expected),
                action_type=action_type,
            )
        match = IDEA_PATH.fullmatch(valid_idea_paths[0])
        return _change_result(
            "valid",
            count=count,
            action_type=action_type,
            problem_id=match.group("problem_id") if match else None,
            allowed_files=sorted(expected),
        )

    rejected = [
        path
        for path in normalized_files
        if path not in AGENT_ALLOWED_EXACT and not path.startswith(AGENT_ALLOWED_PREFIXES)
    ]
    if rejected:
        return _change_result(
            "disallowed_file",
            count=count,
            reason="현재 agent 행동에서 허용되지 않은 파일이 포함됐습니다.",
            files=rejected,
            action_type=action_type,
        )
    return _change_result("valid", count=count, action_type=action_type)


def candidate_change_paths_allowed(branch: str, files: list[dict[str, object]]) -> bool:
    return validate_changed_file_contract(branch, files).status == "valid"


def classify_pull_target(
    pull: object,
    *,
    repository: str,
    branch: str,
    commit_sha: str,
) -> tuple[VerificationStatus, str]:
    if (
        not isinstance(pull, dict)
        or not BRANCH.fullmatch(branch)
        or not SHA.fullmatch(commit_sha)
    ):
        return "invalid_pr", ""
    head = pull.get("head")
    base = pull.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return "invalid_pr", ""
    actual_sha = str(head.get("sha") or "")
    head_repo = head.get("repo")
    base_repo = base.get("repo")
    if (
        not isinstance(head_repo, dict)
        or not isinstance(base_repo, dict)
        or head_repo.get("full_name") != repository
        or base_repo.get("full_name") != repository
    ):
        return "repository_mismatch", actual_sha if SHA.fullmatch(actual_sha) else ""
    if pull.get("state") != "open" or pull.get("merged_at") is not None:
        return "closed_pr", actual_sha if SHA.fullmatch(actual_sha) else ""
    changed_files = pull.get("changed_files")
    if isinstance(changed_files, int) and changed_files > 100:
        return "too_many_files", actual_sha if SHA.fullmatch(actual_sha) else ""
    if head.get("ref") != branch:
        return "branch_mismatch", actual_sha if SHA.fullmatch(actual_sha) else ""
    if actual_sha != commit_sha:
        return "sha_mismatch", actual_sha if SHA.fullmatch(actual_sha) else ""
    return "valid", actual_sha


def summarize_check_results(results: list[tuple[str, str]]) -> tuple[str, str]:
    failed = [name for name, outcome in results if outcome != "success"]
    return ("failed", failed[0]) if failed else ("passed", "")


def finalize_validation_status(
    *,
    verification_status: str,
    quality_job_result: str,
    policy_job_result: str = "success",
    quality_status: str,
    failed_check: str,
) -> tuple[ValidationStatus, str]:
    verification_failures = {
        "invalid_pr": "pr_validation",
        "branch_mismatch": "pr_branch_verification",
        "sha_mismatch": "pr_head_verification",
        "repository_mismatch": "pr_repository_verification",
        "closed_pr": "pr_state_verification",
        "disallowed_file": "disallowed_file",
        "deleted_file": "deleted_file",
        "too_many_files": "too_many_files",
        "invalid_checkpoint_change": "invalid_checkpoint_change",
        "invalid_state_change": "invalid_state_change",
        "invalid_problem_path": "invalid_problem_path",
    }
    if verification_status in verification_failures:
        return verification_status, verification_failures[verification_status]  # type: ignore[return-value]
    if verification_status != "valid":
        return "quality_check_not_started", "pr_verification_not_started"
    if quality_job_result in {"cancelled", "skipped", ""} or policy_job_result in {
        "cancelled",
        "skipped",
        "",
    }:
        return "quality_check_not_started", "quality_job_not_started"
    if quality_job_result != "success":
        return "failed", "quality_job_setup"
    if policy_job_result != "success":
        return "failed", "policy_job_setup"
    if quality_status == "passed":
        return "passed", ""
    if quality_status == "failed":
        return "failed", failed_check or "unknown_check"
    return "quality_check_not_started", "quality_result_missing"


def review_status(validation_status: str) -> ReviewStatus:
    return {
        "passed": "ready_for_human_review",
        "failed": "quality_check_failed",
        "invalid_pr": "invalid_pr",
        "branch_mismatch": "branch_mismatch",
        "sha_mismatch": "sha_mismatch",
        "repository_mismatch": "repository_mismatch",
        "closed_pr": "invalid_pr",
        "disallowed_file": "disallowed_file",
        "deleted_file": "deleted_file",
        "too_many_files": "too_many_files",
        "invalid_checkpoint_change": "invalid_checkpoint_change",
        "invalid_state_change": "invalid_state_change",
        "invalid_problem_path": "invalid_problem_path",
        "quality_check_not_started": "quality_check_not_started",
    }.get(validation_status, "quality_check_not_started")
