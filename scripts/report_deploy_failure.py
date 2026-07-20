from __future__ import annotations

import os

from agents.github_client import GitHubClient


def main() -> int:
    run_id = os.environ["GITHUB_RUN_ID"]
    repository = os.environ["GITHUB_REPOSITORY"]
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    GitHubClient(os.environ["GITHUB_TOKEN"], repository).create_issue(
        f"[배포 실패] GitHub Pages 실행 {run_id}",
        (
            "## 요청 내용\n\nGitHub Pages 배포가 실패했습니다.\n\n"
            f"## 판단 근거\n\n변경되지 않는 실행 로그: {url}\n\n"
            "## 위험 요소\n\n자동 코드 수정은 시도하지 않았습니다."
        ),
        ["bug", "agent-generated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
