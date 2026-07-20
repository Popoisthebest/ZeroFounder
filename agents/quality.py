from __future__ import annotations

from typing import Literal

from agents.github_client import BRANCH, SHA

VerificationStatus = Literal["verified", "invalid_pr", "sha_mismatch"]
ValidationStatus = Literal[
    "passed",
    "failed",
    "sha_mismatch",
    "invalid_pr",
    "quality_check_not_started",
]
ReviewStatus = Literal[
    "ready_for_human_review",
    "quality_check_failed",
    "sha_mismatch",
    "quality_check_not_started",
]


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
        return "invalid_pr", actual_sha if SHA.fullmatch(actual_sha) else ""
    if head.get("ref") != branch or actual_sha != commit_sha:
        return "sha_mismatch", actual_sha if SHA.fullmatch(actual_sha) else ""
    return "verified", actual_sha


def summarize_check_results(results: list[tuple[str, str]]) -> tuple[str, str]:
    failed = [name for name, outcome in results if outcome != "success"]
    return ("failed", failed[0]) if failed else ("passed", "")


def finalize_validation_status(
    *,
    verification_status: str,
    quality_job_result: str,
    quality_status: str,
    failed_check: str,
) -> tuple[ValidationStatus, str]:
    if verification_status == "invalid_pr":
        return "invalid_pr", "pr_validation"
    if verification_status == "sha_mismatch":
        return "sha_mismatch", "pr_head_verification"
    if verification_status != "verified":
        return "quality_check_not_started", "pr_verification_not_started"
    if quality_job_result in {"cancelled", "skipped", ""}:
        return "quality_check_not_started", "quality_job_not_started"
    if quality_job_result != "success":
        return "failed", failed_check or "quality_job_setup"
    if quality_status == "passed":
        return "passed", ""
    if quality_status == "failed":
        return "failed", failed_check or "unknown_check"
    return "quality_check_not_started", "quality_result_missing"


def review_status(validation_status: str) -> ReviewStatus:
    return {
        "passed": "ready_for_human_review",
        "failed": "quality_check_failed",
        "sha_mismatch": "sha_mismatch",
        "invalid_pr": "quality_check_not_started",
        "quality_check_not_started": "quality_check_not_started",
    }.get(validation_status, "quality_check_not_started")
