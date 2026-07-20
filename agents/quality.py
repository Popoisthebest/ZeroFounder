from __future__ import annotations

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
]
ValidationStatus = Literal[
    "passed",
    "failed",
    "invalid_pr",
    "branch_mismatch",
    "sha_mismatch",
    "repository_mismatch",
    "closed_pr",
    "quality_check_not_started",
]
ReviewStatus = Literal[
    "ready_for_human_review",
    "quality_check_failed",
    "invalid_pr",
    "branch_mismatch",
    "sha_mismatch",
    "repository_mismatch",
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


def candidate_change_paths_allowed(branch: str, files: list[dict[str, object]]) -> bool:
    if not files or len(files) > 100:
        return False
    dependency_branch = branch.startswith("dependency/")
    for record in files:
        raw = record.get("filename")
        if not isinstance(raw, str) or record.get("status") == "removed":
            return False
        path = PurePosixPath(raw)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            return False
        normalized = path.as_posix()
        if dependency_branch:
            if normalized not in DEPENDENCY_ALLOWED_EXACT:
                return False
        elif normalized not in AGENT_ALLOWED_EXACT and not normalized.startswith(
            AGENT_ALLOWED_PREFIXES
        ):
            return False
    return True


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
        return "invalid_pr", actual_sha if SHA.fullmatch(actual_sha) else ""
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
        "quality_check_not_started": "quality_check_not_started",
    }.get(validation_status, "quality_check_not_started")
