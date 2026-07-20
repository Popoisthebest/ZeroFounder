from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://api.github.com"
API_VERSION = "2022-11-28"
SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^(?:agent|dependency)/[A-Za-z0-9._/-]{1,180}$")
WRITE_PERMISSIONS = {"admin", "maintain", "write"}


class GitHubAPIError(RuntimeError):
    pass


class GitHubClient:
    def __init__(
        self,
        token: str,
        repository: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if repository.count("/") != 1:
            raise ValueError("repository must be owner/name")
        self.repository = repository
        self.client = httpx.Client(
            timeout=30,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "ZeroFounder/1.0",
            },
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, f"{API}{path}", **kwargs)
        if response.status_code >= 400:
            raise GitHubAPIError(f"GitHub API {response.status_code}: {response.text[:300]}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def repository_info(self) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repository}")

    def issue(self, number: int) -> dict[str, Any]:
        if number <= 0:
            raise ValueError("invalid issue number")
        return self._request("GET", f"/repos/{self.repository}/issues/{number}")

    def collaborator_permission(self, login: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
            return "none"
        data = self._request("GET", f"/repos/{self.repository}/collaborators/{login}/permission")
        return str(data.get("permission", "none"))

    def has_write_permission(self, login: str) -> bool:
        return self.collaborator_permission(login) in WRITE_PERMISSIONS

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repository}/issues",
            json={"title": title[:200], "body": body[:60_000], "labels": labels},
        )

    def comment(self, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repository}/issues/{number}/comments",
            json={"body": body[:60_000]},
        )

    def create_pull_request(self, *, title: str, body: str, head: str, base: str) -> dict[str, Any]:
        if not BRANCH.fullmatch(head):
            raise ValueError("invalid agent branch")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,180}", base):
            raise ValueError("invalid base branch")
        return self._request(
            "POST",
            f"/repos/{self.repository}/pulls",
            json={"title": title[:200], "body": body[:60_000], "head": head, "base": base},
        )

    def pull_request(self, number: int) -> dict[str, Any]:
        if number <= 0:
            raise ValueError("invalid PR number")
        return self._request("GET", f"/repos/{self.repository}/pulls/{number}")

    def update_pull_request_body(self, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "PATCH", f"/repos/{self.repository}/pulls/{number}", json={"body": body[:60_000]}
        )

    def dispatch_quality_check(
        self, *, pr_number: int, agent_branch: str, commit_sha: str, ref: str
    ) -> None:
        if pr_number <= 0 or not BRANCH.fullmatch(agent_branch) or not SHA.fullmatch(commit_sha):
            raise ValueError("invalid quality dispatch inputs")
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,180}", ref):
            raise ValueError("invalid workflow ref")
        self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/quality-check.yml/dispatches",
            json={
                "ref": ref,
                "inputs": {
                    "pr_number": str(pr_number),
                    "agent_branch": agent_branch,
                    "commit_sha": commit_sha,
                },
            },
        )

    def verify_pull_head(self, *, pr_number: int, branch: str, commit_sha: str) -> bool:
        if not BRANCH.fullmatch(branch) or not SHA.fullmatch(commit_sha):
            return False
        pull = self.pull_request(pr_number)
        return (
            pull.get("head", {}).get("ref") == branch
            and pull.get("head", {}).get("sha") == commit_sha
        )

    def ensure_labels(self, labels: dict[str, tuple[str, str]]) -> None:
        existing = self._request(
            "GET", f"/repos/{self.repository}/labels", params={"per_page": 100}
        )
        names = {item["name"] for item in existing}
        for name, (color, description) in labels.items():
            payload = {"name": name, "color": color, "description": description}
            if name in names:
                self._request("PATCH", f"/repos/{self.repository}/labels/{name}", json=payload)
            else:
                self._request("POST", f"/repos/{self.repository}/labels", json=payload)
