from __future__ import annotations

import os

from agents.github_client import GitHubClient


def main() -> int:
    run_id = os.environ["GITHUB_RUN_ID"]
    repository = os.environ["GITHUB_REPOSITORY"]
    url = f"https://github.com/{repository}/actions/runs/{run_id}"
    GitHubClient(os.environ["GITHUB_TOKEN"], repository).create_issue(
        f"Deployment failed in run {run_id}",
        (
            f"GitHub Pages deployment failed. Review the immutable run log: {url}\n\n"
            "No automatic code repair was attempted."
        ),
        ["bug", "agent-generated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
