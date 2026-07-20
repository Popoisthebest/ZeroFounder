from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agents.quality import finalize_validation_status, summarize_check_results

CHECKS = (
    ("python_dependencies", "PYTHON_DEPENDENCIES_RESULT"),
    ("pytest", "PYTEST_RESULT"),
    ("ruff", "RUFF_RESULT"),
    ("workflow_validation", "WORKFLOW_RESULT"),
    ("npm_ci", "NPM_CI_RESULT"),
    ("eslint", "ESLINT_RESULT"),
    ("typecheck", "TYPECHECK_RESULT"),
    ("vitest", "VITEST_RESULT"),
    ("production_build", "BUILD_RESULT"),
    ("python_dependency_audit", "PIP_AUDIT_RESULT"),
    ("npm_dependency_audit", "NPM_AUDIT_RESULT"),
    ("security_scan", "SECURITY_RESULT"),
)


def aggregate_quality_results(
    *,
    results_dir: Path,
    output: Path,
    verification_status: str,
    verified_sha: str,
    quality_job_result: str,
    policy_job_result: str,
    run_url: str,
    outcomes: dict[str, str],
) -> dict[str, str]:
    results_dir.mkdir(parents=True, exist_ok=True)
    checks = [(name, outcomes.get(variable, "skipped")) for name, variable in CHECKS]
    for name, outcome in checks:
        (results_dir / f"{name}.json").write_text(
            json.dumps({"check": name, "outcome": outcome}, indent=2) + "\n",
            encoding="utf-8",
        )
    quality_status, failed_check = summarize_check_results(checks)
    validation_status, final_failed_check = finalize_validation_status(
        verification_status=verification_status,
        quality_job_result=quality_job_result,
        policy_job_result=policy_job_result,
        quality_status=quality_status,
        failed_check=failed_check,
    )
    result = {
        "validation_status": validation_status,
        "verified_sha": verified_sha,
        "failed_check": final_failed_check,
        "quality_run_url": run_url,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification-status", required=True)
    parser.add_argument("--verified-sha", default="")
    parser.add_argument("--quality-job-result", default="")
    parser.add_argument("--policy-job-result", default="")
    parser.add_argument("--run-url", default="")
    args = parser.parse_args()
    result = aggregate_quality_results(
        results_dir=args.results_dir,
        output=args.output,
        verification_status=args.verification_status,
        verified_sha=args.verified_sha,
        quality_job_result=args.quality_job_result,
        policy_job_result=args.policy_job_result,
        run_url=args.run_url,
        outcomes=os.environ,
    )
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
