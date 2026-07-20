from pathlib import Path

from agents.quality import (
    candidate_change_paths_allowed,
    classify_pull_target,
    finalize_validation_status,
    review_status,
    summarize_check_results,
)
from scripts.summarize_quality_checks import CHECKS, aggregate_quality_results
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
        "state": "open",
        "merged_at": None,
    }


def test_exact_pull_target_is_verified():
    status, verified_sha = classify_pull_target(
        pull(),
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert (status, verified_sha) == ("valid", "a" * 40)


def test_foreign_repository_pull_is_invalid():
    value = pull()
    value["head"]["repo"]["full_name"] = "fork/repo"
    status, _ = classify_pull_target(
        value,
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert status == "repository_mismatch"


def test_branch_sha_and_closed_pull_have_distinct_statuses():
    status, _ = classify_pull_target(
        pull(branch="agent/other"),
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert status == "branch_mismatch"
    status, _ = classify_pull_target(
        pull(sha="b" * 40),
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert status == "sha_mismatch"
    closed = pull()
    closed["state"] = "closed"
    status, _ = classify_pull_target(
        closed,
        repository="owner/repo",
        branch="agent/42-discovery",
        commit_sha="a" * 40,
    )
    assert status == "closed_pr"


def test_candidate_changed_paths_preserve_dependency_and_control_policy():
    assert candidate_change_paths_allowed(
        "agent/42-discovery",
        [
            {"filename": "research/problems/problem-001.json", "status": "added"},
            {"filename": "company/state.json", "status": "modified"},
        ],
    )
    assert not candidate_change_paths_allowed(
        "agent/42-discovery",
        [{"filename": "scripts/summarize_quality_checks.py", "status": "modified"}],
    )
    assert candidate_change_paths_allowed(
        "dependency/42-pyyaml",
        [{"filename": "requirements.txt", "status": "modified"}],
    )
    assert not candidate_change_paths_allowed(
        "dependency/42-pyyaml",
        [{"filename": "agents/safety.py", "status": "modified"}],
    )
    assert not candidate_change_paths_allowed(
        "agent/42-discovery",
        [{"filename": "research/old.json", "status": "removed"}],
    )


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
        verification_status="valid",
        quality_job_result="success",
        quality_status="passed",
        failed_check="",
    ) == ("passed", "")
    assert finalize_validation_status(
        verification_status="valid",
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
        verification_status="valid",
        quality_job_result="cancelled",
        quality_status="",
        failed_check="",
    )[0] == "quality_check_not_started"
    assert review_status("passed") == "ready_for_human_review"
    assert review_status("failed") == "quality_check_failed"
    assert review_status("branch_mismatch") == "branch_mismatch"
    assert review_status("repository_mismatch") == "repository_mismatch"


def test_trusted_aggregator_does_not_need_candidate_helper(tmp_path: Path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    assert not (candidate / "scripts/summarize_quality_checks.py").exists()
    outcomes = {variable: "success" for _, variable in CHECKS}
    result = aggregate_quality_results(
        results_dir=tmp_path / "runtime/quality-results",
        output=tmp_path / "runtime/quality-summary.json",
        verification_status="valid",
        verified_sha="a" * 40,
        quality_job_result="success",
        policy_job_result="success",
        run_url="https://github.com/owner/repo/actions/runs/1",
        outcomes=outcomes,
    )
    assert result["validation_status"] == "passed"
    assert result["verified_sha"] == "a" * 40
    assert len(list((tmp_path / "runtime/quality-results").glob("*.json"))) == len(CHECKS)


def test_trusted_aggregator_reports_failed_check(tmp_path: Path):
    outcomes = {variable: "success" for _, variable in CHECKS}
    outcomes["VITEST_RESULT"] = "failure"
    result = aggregate_quality_results(
        results_dir=tmp_path / "runtime/quality-results",
        output=tmp_path / "runtime/quality-summary.json",
        verification_status="valid",
        verified_sha="a" * 40,
        quality_job_result="success",
        policy_job_result="success",
        run_url="https://github.com/owner/repo/actions/runs/1",
        outcomes=outcomes,
    )
    assert result["validation_status"] == "failed"
    assert result["failed_check"] == "vitest"


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
