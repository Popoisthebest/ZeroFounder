from __future__ import annotations

import argparse
import json
import os

from agents.github_client import GitHubAPIError, GitHubClient
from agents.quality import classify_pull_target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    try:
        pull = client.pull_request(args.pr)
    except (GitHubAPIError, ValueError):
        status, verified_sha = "invalid_pr", ""
        changed_files_count = 0
    else:
        status, verified_sha = classify_pull_target(
            pull,
            repository=client.repository,
            branch=args.branch,
            commit_sha=args.sha,
        )
        changed_files_count = int(pull.get("changed_files") or 0)
    reasons = {
        "invalid_pr": "Pull Request 정보를 검증할 수 없습니다.",
        "branch_mismatch": "전달된 branch와 실제 PR head branch가 다릅니다.",
        "sha_mismatch": "전달된 SHA와 실제 PR head SHA가 다릅니다.",
        "repository_mismatch": "PR head 또는 base 저장소가 현재 저장소와 다릅니다.",
        "closed_pr": "닫히거나 병합된 Pull Request는 검사할 수 없습니다.",
        "too_many_files": "변경 파일 수가 안전 한도를 초과했습니다.",
    }
    rejection_code = "" if status == "valid" else status
    rejection_reason = "" if status == "valid" else reasons.get(
        status, "Pull Request 검증에 실패했습니다."
    )
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"validation_status={status}\n")
            handle.write(f"sha={verified_sha}\n")
            handle.write(f"rejection_code={rejection_code}\n")
            handle.write(f"rejection_reason={rejection_reason}\n")
            handle.write("rejected_files=[]\n")
            handle.write(f"changed_files_count={changed_files_count}\n")
    print(
        json.dumps(
            {
                "validation_status": status,
                "rejection_code": rejection_code,
                "changed_files_count": changed_files_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
