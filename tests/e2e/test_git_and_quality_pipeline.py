from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import scripts.create_agent_pr as create_agent_pr
from agents.quality import classify_pull_target
from agents.schemas import (
    ActionType,
    AgentRole,
    FileChange,
    MaterializedActionEnvelope,
    RiskLevel,
)
from scripts.quality_gate import main as quality_gate_main
from scripts.validate_candidate_change import validate_candidate
from tests.e2e.conftest import E2EHarness, FakePullRequestClient

ROOT = Path(__file__).parents[2]


def _pull(branch: str, sha: str, repository: str = "owner/repo") -> dict[str, object]:
    return {
        "number": 1,
        "head": {
            "ref": branch,
            "sha": sha,
            "repo": {"full_name": repository},
        },
        "base": {"repo": {"full_name": repository}},
        "state": "open",
        "merged_at": None,
        "changed_files": 2,
    }


def test_pull_request_target_requires_exact_branch_sha_and_repository() -> None:
    branch = "agent/1001-create-idea-candidates"
    sha = "a" * 40
    assert classify_pull_target(
        _pull(branch, sha),
        repository="owner/repo",
        branch=branch,
        commit_sha=sha,
    ) == ("valid", sha)
    assert classify_pull_target(
        _pull(branch, "b" * 40),
        repository="owner/repo",
        branch=branch,
        commit_sha=sha,
    )[0] == "sha_mismatch"
    assert classify_pull_target(
        _pull(branch, sha, repository="other/repo"),
        repository="owner/repo",
        branch=branch,
        commit_sha=sha,
    )[0] == "repository_mismatch"
    assert classify_pull_target(
        {"head": "bad"},
        repository="owner/repo",
        branch=branch,
        commit_sha=sha,
    )[0] == "invalid_pr"


def test_validate_candidate_uses_fake_github_api_boundary(
    e2e_harness: E2EHarness,
) -> None:
    branch = "agent/1002-validate-evidence"
    sha = "a" * 40
    files = [
        {"filename": "company/checkpoints.json", "status": "modified"},
        {"filename": "company/state.json", "status": "modified"},
    ]
    client = FakePullRequestClient(
        repository="owner/repo",
        branch=branch,
        sha=sha,
        files=files,
        pull_overrides={"changed_files": len(files)},
    )

    result, verified_sha = validate_candidate(
        client=client,  # type: ignore[arg-type]
        pull_request_number=1,
        branch=branch,
        commit_sha=sha,
        control_root=e2e_harness.repo,
        candidate_root=e2e_harness.repo,
    )

    assert verified_sha == sha
    assert result.status == "invalid_state_change"
    assert result.changed_files_count == 2


def test_create_agent_pr_uses_branch_sha_and_quality_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeGitHubClient:
        def __init__(self, token: str, repository: str) -> None:
            captured["token"] = token
            captured["repository"] = repository

        def repository_info(self) -> dict[str, object]:
            return {"default_branch": "main"}

        def create_pull_request(
            self,
            *,
            title: str,
            body: str,
            head: str,
            base: str,
        ) -> dict[str, object]:
            captured["pull"] = {
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            }
            return {"number": 7}

        def add_labels(self, number: int, labels: list[str]) -> dict[str, object]:
            captured["labels"] = {"number": number, "labels": labels}
            return {}

    action = MaterializedActionEnvelope(
        source="trusted_materializer",
        role=AgentRole.RESEARCHER,
        action_type=ActionType.WRITE_REPORT,
        title="보고서 작성",
        summary="생애주기 검토 보고서를 작성합니다.",
        rationale="이번 실행에는 사람이 검토할 운영 산출물이 필요합니다.",
        risk_level=RiskLevel.LOW,
        requires_approval=False,
        files=[FileChange(path="reports/e2e.md", content="# E2E\n")],
    )
    action_path = tmp_path / "materialized-action.json"
    action_path.write_text(action.model_dump_json(indent=2) + "\n", encoding="utf-8")
    output_path = tmp_path / "github-output.txt"
    monkeypatch.setattr(create_agent_pr, "GitHubClient", FakeGitHubClient)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_agent_pr",
            "--action",
            str(action_path),
            "--branch",
            "agent/1003-write-report",
            "--sha",
            "c" * 40,
        ],
    )

    assert create_agent_pr.main() == 0

    pull = captured["pull"]
    assert pull["head"] == "agent/1003-write-report"
    assert pull["base"] == "main"
    assert "생애주기 검토 보고서" in pull["body"]
    assert "이번 실행에는" in pull["body"]
    assert "`" + "c" * 40 + "`" in pull["body"]
    assert "quality_check_not_started" in pull["body"]
    assert captured["labels"] == {"number": 7, "labels": ["agent-generated"]}
    assert "pr_number=7" in output_path.read_text(encoding="utf-8")


def test_workflow_artifacts_and_quality_check_inputs_are_separated() -> None:
    agent = yaml.safe_load((ROOT / ".github/workflows/agent.yml").read_text())
    create_branch_steps = agent["jobs"]["create-branch"]["steps"]
    model_uploads = [
        step
        for step in agent["jobs"]["model"]["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    ]
    materialized_upload = next(
        step
        for step in create_branch_steps
        if step.get("with", {}).get("name") == "materialized-action"
    )
    assert model_uploads[0]["with"]["name"] == "validated-action"
    assert "runtime/model-action.json" in model_uploads[0]["with"]["path"]
    assert "runtime/model-diagnostic.json" in model_uploads[0]["with"]["path"]
    assert materialized_upload["with"]["path"] == "runtime/materialized-action.json"
    commit_step = next(step for step in create_branch_steps if step.get("id") == "commit")
    assert "runtime/materialized-action.json" in commit_step["run"]

    quality_call = agent["jobs"]["quality-check"]
    assert quality_call["uses"] == "./.github/workflows/quality-check.yml"
    assert quality_call["with"]["agent_branch"] == (
        "${{ needs.create-branch.outputs.branch || needs.dependency-branch.outputs.branch }}"
    )
    assert quality_call["with"]["commit_sha"] == (
        "${{ needs.create-branch.outputs.sha || needs.dependency-branch.outputs.sha }}"
    )
    assert quality_call["with"]["called_by_agent"] is True

    quality = yaml.safe_load((ROOT / ".github/workflows/quality-check.yml").read_text())
    trigger = quality.get("on", quality.get(True))
    inputs = trigger["workflow_call"]["inputs"]
    assert set(inputs) >= {"pull_request_number", "agent_branch", "commit_sha"}


def test_quality_gate_passes_only_after_validation_and_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VALIDATION_STATUS", "passed")
    monkeypatch.setenv("RECORD_RESULT", "success")
    assert quality_gate_main() == 0

    monkeypatch.setenv("VALIDATION_STATUS", "failed")
    with pytest.raises(SystemExit, match="failed"):
        quality_gate_main()
