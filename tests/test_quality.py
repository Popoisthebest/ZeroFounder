from agents.quality import (
    classify_pull_target,
    finalize_validation_status,
    review_status,
    summarize_check_results,
)
from scripts.update_pr_status import render_status_body


def pull(branch: str = "agent/42-discovery", sha: str = "a" * 40) -> dict:
    return {
        "number": 42,
        "head": {
            "ref": branch,
            "sha": sha,
            "repo": {"full_name": "owner/repo"},
        },
        "base": {"repo": {"full_name": "owner/repo"}},
    }


def test_exact_pull_target_is_verified():
    status, verified_sha = classify_pull_target(
        pull(),
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert (status, verified_sha) == ("verified", "a" * 40)


def test_foreign_repository_pull_is_invalid():
    value = pull()
    value["head"]["repo"]["full_name"] = "fork/repo"
    status, _ = classify_pull_target(
        value,
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert status == "invalid_pr"


def test_quality_success_and_failure_outputs():
    assert summarize_check_results([("pytest", "success"), ("ruff", "success")]) == (
        "passed",
        "",
    )
    assert summarize_check_results([("pytest", "success"), ("ruff", "failure")]) == (
        "failed",
        "ruff",
    )
    assert summarize_check_results([("pytest", "success"), ("ruff", "skipped")]) == (
        "failed",
        "ruff",
    )
    assert finalize_validation_status(
        verification_status="verified",
        quality_job_result="success",
        quality_status="passed",
        failed_check="",
    ) == ("passed", "")
    assert finalize_validation_status(
        verification_status="verified",
        quality_job_result="success",
        quality_status="failed",
        failed_check="vitest",
    ) == ("failed", "vitest")


def test_unstarted_and_sha_mismatch_are_not_success():
    assert finalize_validation_status(
        verification_status="sha_mismatch",
        quality_job_result="skipped",
        quality_status="",
        failed_check="",
    ) == ("sha_mismatch", "pr_head_verification")
    assert finalize_validation_status(
        verification_status="verified",
        quality_job_result="cancelled",
        quality_status="",
        failed_check="",
    )[0] == "quality_check_not_started"
    assert review_status("passed") == "ready_for_human_review"
    assert review_status("failed") == "quality_check_failed"


def test_quality_status_body_is_korean_and_keeps_machine_status():
    body = render_status_body(
        "## 기존 본문\n",
        status="quality_check_failed",
        verified_sha="a" * 40,
        failed_check="pytest",
        run_url="https://github.com/owner/repo/actions/runs/1",
    )
    assert "## 품질검사 상태" in body
    assert "품질검사 실패" in body
    assert "`quality_check_failed`" in body
    assert "pytest" in body
