from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

API = "https://api.github.com"
API_VERSION = "2022-11-28"
SHA = re.compile(r"^[0-9a-f]{40}$")
BRANCH = re.compile(r"^(?:agent|dependency)/[A-Za-z0-9._/-]{1,180}$")
WRITE_PERMISSIONS = {"admin", "maintain", "write"}
MODEL_JOB_DISPLAY_NAMES = {"model", "AI 의사결정 실행"}
CONFIRM_MARKER = re.compile(r"\[inference-confirm-(\d+)\]$")
HTTP_FAILURE_MARKER = re.compile(r"\[inference-http-failed-(\d+)\]$")
VALIDATION_FAILURE_MARKER = re.compile(r"\[inference-validation-failed-(\d+)\]$")


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

    def open_issues_and_pulls(self) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            f"/repos/{self.repository}/issues",
            params={"state": "open", "per_page": 100},
        )
        return result if isinstance(result, list) else []

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

    def update_issue(self, number: int, *, title: str, body: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/repos/{self.repository}/issues/{number}",
            json={"title": title[:200], "body": body[:60_000]},
        )

    def add_labels(self, number: int, labels: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repository}/issues/{number}/labels",
            json={"labels": labels},
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

    def pull_request_files(self, number: int) -> list[dict[str, Any]]:
        if number <= 0:
            raise ValueError("invalid PR number")
        result = self._request(
            "GET",
            f"/repos/{self.repository}/pulls/{number}/files",
            params={"per_page": 100},
        )
        return result if isinstance(result, list) else []

    def update_pull_request_body(self, number: int, body: str) -> dict[str, Any]:
        return self._request(
            "PATCH", f"/repos/{self.repository}/pulls/{number}", json={"body": body[:60_000]}
        )

    def update_pull_request(self, number: int, *, title: str, body: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/repos/{self.repository}/pulls/{number}",
            json={"title": title[:200], "body": body[:60_000]},
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

    def model_usage_today(self) -> dict[str, int]:
        """Count only explicit successful inference markers from Actions job steps."""
        today = datetime.now(UTC).date().isoformat()
        runs = self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/agent.yml/runs",
            params={"created": f">={today}", "per_page": 100},
        ).get("workflow_runs", [])
        completed_calls = 0
        http_failed_calls = 0
        response_validation_failed_calls = 0
        active_reservations = 0
        skipped_runs = 0
        for run in runs:
            run_id = run.get("id")
            if not isinstance(run_id, int):
                continue
            jobs = self._request(
                "GET",
                f"/repos/{self.repository}/actions/runs/{run_id}/jobs",
                params={"per_page": 100},
            ).get("jobs", [])
            for job in jobs:
                if job.get("name") not in MODEL_JOB_DISPLAY_NAMES:
                    continue
                if job.get("conclusion") == "skipped":
                    skipped_runs += 1
                steps = job.get("steps")
                if not isinstance(steps, list):
                    continue
                confirmed_slots: set[str] = set()
                reserved_slots: set[str] = set()
                for step in steps:
                    if not isinstance(step, dict) or step.get("conclusion") != "success":
                        continue
                    name = str(step.get("name", ""))
                    if name.startswith("Confirm inference call "):
                        confirmed_slots.add(name.rsplit(" ", 1)[-1])
                    elif match := CONFIRM_MARKER.search(name):
                        confirmed_slots.add(match.group(1))
                    elif name.startswith("Reserve inference call "):
                        reserved_slots.add(name.rsplit(" ", 1)[-1])
                    elif name.startswith(
                        "Mark HTTP failed inference call "
                    ) or HTTP_FAILURE_MARKER.search(name):
                        http_failed_calls += 1
                    elif name.startswith(
                        "Mark response validation failed inference call "
                    ) or VALIDATION_FAILURE_MARKER.search(name):
                        response_validation_failed_calls += 1
                    elif name in {
                        "Mark inference run skipped",
                        "모델 호출 없는 실행 기록 [inference-skipped]",
                    }:
                        skipped_runs += 1
                completed_calls += len(confirmed_slots)
                if job.get("status") == "in_progress":
                    active_reservations += len(reserved_slots - confirmed_slots)
        return {
            "completed_inference_calls": completed_calls,
            "reserved_inference_calls": active_reservations,
            "failed_after_request_calls": min(
                http_failed_calls + response_validation_failed_calls,
                completed_calls,
            ),
            "http_failed_calls": min(http_failed_calls, completed_calls),
            "response_validation_failed_calls": min(
                response_validation_failed_calls, completed_calls
            ),
            "skipped_runs": skipped_runs,
        }
