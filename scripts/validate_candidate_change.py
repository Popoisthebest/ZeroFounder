from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agents.candidate_validator import (
    validate_create_idea_candidates_content,
    validate_create_problem_candidate_content,
    validate_validate_evidence_content,
    validate_write_report_content,
)
from agents.github_client import GitHubAPIError, GitHubClient
from agents.quality import (
    ChangeValidation,
    VerificationStatus,
    classify_pull_target,
    validate_changed_file_contract,
)


def _metadata_failure(
    status: VerificationStatus, changed_files_count: int = 0
) -> ChangeValidation:
    reasons = {
        "invalid_pr": "Pull Request 정보를 검증할 수 없습니다.",
        "branch_mismatch": "전달된 branch와 실제 PR head branch가 다릅니다.",
        "sha_mismatch": "전달된 SHA와 실제 PR head SHA가 다릅니다.",
        "repository_mismatch": "PR head 또는 base 저장소가 현재 저장소와 다릅니다.",
        "closed_pr": "닫히거나 병합된 Pull Request는 검사할 수 없습니다.",
    }
    return ChangeValidation(
        status=status,
        rejection_code=status,
        rejection_reason=reasons.get(status, "Pull Request 검증에 실패했습니다."),
        rejected_files=(),
        changed_files_count=changed_files_count,
    )


def validate_candidate(
    *,
    client: GitHubClient,
    pull_request_number: int,
    branch: str,
    commit_sha: str,
    control_root: Path,
    candidate_root: Path,
) -> tuple[ChangeValidation, str]:
    try:
        pull = client.pull_request(pull_request_number)
    except (GitHubAPIError, ValueError):
        return _metadata_failure("invalid_pr"), ""
    status, verified_sha = classify_pull_target(
        pull,
        repository=client.repository,
        branch=branch,
        commit_sha=commit_sha,
    )
    changed_count = int(pull.get("changed_files") or 0)
    if status != "valid":
        return _metadata_failure(status, changed_count), verified_sha
    try:
        files = client.pull_request_files(pull_request_number)
    except (GitHubAPIError, ValueError):
        return _metadata_failure("invalid_pr", changed_count), verified_sha
    if changed_count != len(files):
        return _metadata_failure("invalid_pr", changed_count), verified_sha
    contract = validate_changed_file_contract(branch, files)
    if contract.status != "valid":
        return contract, verified_sha
    if contract.action_type == "create_problem_candidate":
        contract = validate_create_problem_candidate_content(
            control_root=control_root,
            candidate_root=candidate_root,
            contract=contract,
        )
    elif contract.action_type == "validate_evidence":
        contract = validate_validate_evidence_content(
            control_root=control_root,
            candidate_root=candidate_root,
            contract=contract,
        )
    elif contract.action_type == "create_idea_candidates":
        contract = validate_create_idea_candidates_content(
            control_root=control_root,
            candidate_root=candidate_root,
            contract=contract,
        )
    elif contract.action_type == "write_report":
        contract = validate_write_report_content(
            control_root=control_root,
            candidate_root=candidate_root,
            contract=contract,
        )
    return contract, verified_sha


def write_result(result: ChangeValidation, verified_sha: str, output_path: Path) -> None:
    payload = {
        "validation_status": result.status,
        "verified_sha": verified_sha,
        "rejection_code": result.rejection_code,
        "rejection_reason": result.rejection_reason,
        "rejected_files": list(result.rejected_files),
        "allowed_files": list(result.allowed_files),
        "changed_files_count": result.changed_files_count,
        "action_type": result.action_type,
        "problem_id": result.problem_id,
        "report_type": result.report_type,
        "report_period": result.report_period,
        "artifact_path": result.artifact_path,
        "operation_key": result.operation_key,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            for key in (
                "validation_status",
                "verified_sha",
                "rejection_code",
                "rejection_reason",
                "changed_files_count",
                "report_type",
                "report_period",
                "artifact_path",
                "operation_key",
            ):
                handle.write(f"{key}={payload[key] or ''}\n")
            handle.write(
                "rejected_files="
                + json.dumps(payload["rejected_files"], ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
            handle.write(
                "allowed_files="
                + json.dumps(payload["allowed_files"], ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    result, verified_sha = validate_candidate(
        client=client,
        pull_request_number=args.pr,
        branch=args.branch,
        commit_sha=args.sha,
        control_root=args.control_root.resolve(),
        candidate_root=args.candidate_root.resolve(),
    )
    write_result(result, verified_sha, args.output.resolve())
    print(
        json.dumps(
            {
                "validation_status": result.status,
                "rejection_code": result.rejection_code,
                "changed_files_count": result.changed_files_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
