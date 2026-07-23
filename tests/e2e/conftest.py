from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agents.bootstrap import initial_company_state
from agents.preflight import build_preflight_decision
from agents.quality import ChangeValidation
from agents.schemas import (
    ActionEnvelope,
    CompanyState,
    MaterializedActionEnvelope,
    PreflightDecision,
    RepositoryCheckpoint,
    TriggerReason,
)
from scripts.apply_agent_action import apply_validated_action
from scripts.commit_agent_changes import commit_agent_changes
from scripts.create_agent_branch import create_agent_branch
from scripts.quality_gate import main as quality_gate_main
from scripts.summarize_quality_checks import CHECKS, aggregate_quality_results
from scripts.validate_candidate_change import validate_candidate

APPLIED_AT = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
PROBLEM_ID = "problem-navigation-inefficiency"
SIGNAL_IDS = ("signal-001", "signal-002")


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def signal_record(signal_id: str, index: int) -> dict[str, object]:
    return {
        "signal_id": signal_id,
        "source_pack": "operators",
        "source_type": "rss",
        "url": f"https://evidence.example/{signal_id}",
        "title": f"Repeated list navigation {index}",
        "summary": "Operators repeatedly return to the same long-list position by hand.",
        "collected_at": "2026-07-20T00:00:00Z",
        "published_at": "2026-07-19T00:00:00Z",
        "content_hash": hashlib.sha256(signal_id.encode()).hexdigest(),
    }


def strategy_payload() -> dict[str, object]:
    return {
        "evidence": {
            "min_unique_signals": 2,
            "min_new_signals_for_analysis": 2,
            "strong_evidence_threshold": 0.85,
        },
        "review": {
            "daily_hour": 0,
            "weekly_day": 1,
            "weekly_hour": 0,
        },
    }


def idea_candidate(idea_id: str, evidence_ids: list[str]) -> dict[str, object]:
    return {
        "idea_id": idea_id,
        "name": f"Navigation Helper {idea_id[-1]}",
        "summary": "A focused workflow tool that reduces repeated long-list navigation.",
        "target_users": ["operators"],
        "proposed_solution": "Store safe return points and provide one-click jumps in long lists.",
        "value_proposition": "Teams spend less attention remembering manual list positions.",
        "differentiation": "It targets repeated list-position recovery instead of broad search.",
        "revenue_model": "Team presets and shared workflows can be paid features.",
        "feasibility": "A static browser MVP can validate saved positions and jump controls.",
        "evidence_ids": evidence_ids,
        "risks": ["Users may keep existing navigation habits."],
        "evaluation_dimensions": ["repeat usage", "free MVP feasibility"],
    }


def active_problem_payload() -> dict[str, object]:
    return {
        "problem_id": PROBLEM_ID,
        "title": "Repeated manual navigation",
        "target_users": ["operators"],
        "description": "Operators repeatedly lose position in long operational lists.",
        "current_workaround": "They scroll, search, and manually remember positions.",
        "evidence_ids": list(SIGNAL_IDS),
        "evidence": [
            {
                "evidence_id": signal_id,
                "source_type": "rss",
                "url": f"https://evidence.example/{signal_id}",
                "summary": "Operators repeatedly return to the same long-list position by hand.",
            }
            for signal_id in SIGNAL_IDS
        ],
        "frequency_score": 5,
        "severity_score": 5,
        "buildability_score": 8,
        "confidence": 0.72,
    }


def unique_key(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def metrics_hash(root: Path) -> str:
    return hashlib.sha256((root / "company/metrics.json").read_bytes()).hexdigest()


@dataclass(frozen=True)
class StepResult:
    run_id: str
    branch: str
    sha: str
    action: MaterializedActionEnvelope
    raw_action: ActionEnvelope
    changed_files: list[str]
    validation: ChangeValidation
    old_state: CompanyState
    new_state: CompanyState
    old_checkpoint: RepositoryCheckpoint
    new_checkpoint: RepositoryCheckpoint
    materialized_path: Path
    quality_result: dict[str, object]


class FakePullRequestClient:
    def __init__(
        self,
        *,
        repository: str,
        branch: str,
        sha: str,
        files: list[dict[str, object]],
        pull_overrides: dict[str, object] | None = None,
    ) -> None:
        self.repository = repository
        self.branch = branch
        self.sha = sha
        self.files = files
        self.pull_overrides = pull_overrides or {}

    def pull_request(self, number: int) -> dict[str, object]:
        assert number == 1
        pull: dict[str, object] = {
            "number": 1,
            "head": {
                "ref": self.branch,
                "sha": self.sha,
                "repo": {"full_name": self.repository},
            },
            "base": {"repo": {"full_name": self.repository}},
            "state": "open",
            "merged_at": None,
            "changed_files": len(self.files),
        }
        pull.update(self.pull_overrides)
        return pull

    def pull_request_files(self, number: int) -> list[dict[str, object]]:
        assert number == 1
        return self.files


class E2EHarness:
    def __init__(self, repo: Path, monkeypatch: pytest.MonkeyPatch, push_log: Path) -> None:
        self.repo = repo
        self.monkeypatch = monkeypatch
        self.push_log = push_log

    def read_state(self) -> CompanyState:
        return CompanyState.model_validate_json(
            (self.repo / "company/state.json").read_text(encoding="utf-8")
        )

    def write_state(self, state: CompanyState) -> None:
        (self.repo / "company/state.json").write_text(
            state.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def read_checkpoint(self) -> RepositoryCheckpoint:
        return RepositoryCheckpoint.model_validate_json(
            (self.repo / "company/checkpoints.json").read_text(encoding="utf-8")
        )

    def write_checkpoint(self, checkpoint: RepositoryCheckpoint) -> None:
        (self.repo / "company/checkpoints.json").write_text(
            checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    def write_model_action(self, action: ActionEnvelope, run_id: str) -> Path:
        path = self.repo / f"runtime/model-action-{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
        return path

    def write_raw_action(self, payload: dict[str, object], run_id: str) -> Path:
        return self.write_model_action(ActionEnvelope.model_validate(payload), run_id)

    def write_preflight(self, decision: PreflightDecision, run_id: str) -> Path:
        path = self.repo / f"runtime/preflight-{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def discovery_decision(self) -> PreflightDecision:
        checkpoint = self.read_checkpoint()
        return build_preflight_decision(
            checkpoint=checkpoint,
            signal_quality={signal_id: 0.9 for signal_id in SIGNAL_IDS},
            issue_ids=[],
            comment_ids=[],
            product_sha=None,
            metrics_hash=metrics_hash(self.repo),
            due_experiment=False,
            daily_review_due=False,
            weekly_review_due=False,
            manual=False,
            min_new_signals=2,
            strong_evidence_threshold=0.85,
        )

    def manual_decision(self, run_id: str) -> PreflightDecision:
        return PreflightDecision(
            should_call_model=True,
            reasons=[TriggerReason.MANUAL],
            new_signal_ids=[],
            issue_ids=[],
            comment_ids=[],
            idempotency_key=unique_key(f"manual-{run_id}"),
        )

    def copy_control(self, label: str) -> Path:
        destination = self.repo.parent / label
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            self.repo,
            destination,
            ignore=shutil.ignore_patterns(".git", "runtime"),
        )
        return destination

    def changed_files_against_main(self) -> list[dict[str, object]]:
        status_map = {
            "A": "added",
            "M": "modified",
            "D": "removed",
            "R": "renamed",
        }
        output = run_git(self.repo, "diff", "--name-status", "main..HEAD")
        files: list[dict[str, object]] = []
        for line in output.splitlines():
            parts = line.split("\t")
            status_code = parts[0][0]
            filename = parts[-1]
            files.append(
                {
                    "filename": filename,
                    "status": status_map.get(status_code, "modified"),
                }
            )
        return files

    def apply_commit_validate(
        self,
        *,
        action_path: Path,
        preflight_path: Path,
        run_id: str,
        minute_offset: int = 0,
    ) -> StepResult:
        control_root = self.copy_control(f"control-{run_id}")
        old_state = CompanyState.model_validate_json(
            (control_root / "company/state.json").read_text(encoding="utf-8")
        )
        old_checkpoint = RepositoryCheckpoint.model_validate_json(
            (control_root / "company/checkpoints.json").read_text(encoding="utf-8")
        )
        raw_action = ActionEnvelope.model_validate_json(action_path.read_text(encoding="utf-8"))
        branch = create_agent_branch(self.repo, action_path, run_id)
        materialized_path = self.repo / f"runtime/materialized-action-{run_id}.json"
        materialized, changed = apply_validated_action(
            self.repo,
            action_path,
            preflight_path,
            materialized_output_path=materialized_path,
            applied_at=APPLIED_AT + timedelta(minutes=minute_offset),
        )
        assert changed
        trusted = MaterializedActionEnvelope.model_validate_json(
            materialized_path.read_text(encoding="utf-8")
        )
        assert trusted == materialized

        committed, committed_branch, sha = commit_agent_changes(
            self.repo, materialized_path, run_id
        )
        assert committed
        assert committed_branch == branch
        assert len(sha) == 40
        files = self.changed_files_against_main()
        changed_files = sorted(str(item["filename"]) for item in files)
        expected = {change.path for change in materialized.files}
        if materialized.state_transition:
            expected.add("company/state.json")
        expected.add("company/checkpoints.json")
        assert changed_files == sorted(expected)

        client = FakePullRequestClient(
            repository="owner/repo",
            branch=branch,
            sha=sha,
            files=files,
        )
        validation, verified_sha = validate_candidate(
            client=client,  # type: ignore[arg-type]
            pull_request_number=1,
            branch=branch,
            commit_sha=sha,
            control_root=control_root,
            candidate_root=self.repo,
        )
        assert verified_sha == sha
        assert validation.status == "valid"

        outcomes = {variable: "success" for _, variable in CHECKS}
        quality_result = aggregate_quality_results(
            results_dir=self.repo / f"runtime/quality-results-{run_id}",
            output=self.repo / f"runtime/quality-summary-{run_id}.json",
            verification_status=validation.status,
            verified_sha=sha,
            quality_job_result="success",
            policy_job_result="success",
            run_url="https://github.com/owner/repo/actions/runs/1",
            outcomes=outcomes,
            changed_files_count=validation.changed_files_count,
            allowed_files=list(validation.allowed_files),
            report_type=validation.report_type or "",
            report_period=validation.report_period or "",
            artifact_path=validation.artifact_path or "",
            operation_key=validation.operation_key or "",
        )
        assert quality_result["validation_status"] == "passed"
        self.monkeypatch.setenv("VALIDATION_STATUS", "passed")
        self.monkeypatch.setenv("RECORD_RESULT", "success")
        assert quality_gate_main() == 0

        push_records = [
            json.loads(line)
            for line in self.push_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert push_records[-1]["args"][-1] == branch

        new_state = self.read_state()
        new_checkpoint = self.read_checkpoint()
        run_git(self.repo, "switch", "main")
        run_git(self.repo, "merge", "--ff-only", branch)
        return StepResult(
            run_id=run_id,
            branch=branch,
            sha=sha,
            action=materialized,
            raw_action=raw_action,
            changed_files=changed_files,
            validation=validation,
            old_state=old_state,
            new_state=new_state,
            old_checkpoint=old_checkpoint,
            new_checkpoint=new_checkpoint,
            materialized_path=materialized_path,
            quality_result=quality_result,
        )

    def push_records(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.push_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def initialize_repo(root: Path) -> None:
    for directory in (
        "agents/prompts",
        "company",
        "founder",
        "research/problems",
        "research/ideas",
        "signals/raw",
        "signals/processed",
        "venture/product/src",
        "venture/content",
        "experiments",
        "ideas/evaluations",
        "ideas/selected",
        "reports",
        "runtime",
    ):
        (root / directory).mkdir(parents=True, exist_ok=True)

    (root / "agents/prompts/core.md").write_text("Return exactly one JSON action.\n")
    (root / "company/mission.md").write_text("Find small evidence-backed software ventures.\n")
    (root / "company/constitution.md").write_text("Stay safe and preserve protected files.\n")
    (root / "company/state.json").write_text(
        initial_company_state().model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "company/checkpoints.json").write_text(
        RepositoryCheckpoint().model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    write_json(root / "company/strategy.json", strategy_payload())
    write_json(root / "company/metrics.json", {"visits": 0, "feedback_items": 0})
    write_json(root / "company/usage.json", {"days": []})
    write_json(root / "company/task-board.json", {"tasks": []})
    (root / "company/decisions.jsonl").write_text("", encoding="utf-8")
    write_json(root / "signals/sources.json", {"enabled_packs": [], "packs": []})
    write_json(root / "venture/venture.json", {"selected": None})
    write_json(root / "venture/infrastructure.json", {"provider": "unselected"})
    (root / "founder/tasks.md").write_text("# Founder Tasks\n", encoding="utf-8")
    (root / "founder/outreach-plan.md").write_text("# Outreach\n", encoding="utf-8")
    (root / "founder/posting-pack.md").write_text("# Posting\n", encoding="utf-8")
    signal_lines = [
        json.dumps(signal_record(signal_id, index))
        for index, signal_id in enumerate(SIGNAL_IDS, 1)
    ]
    (root / "signals/raw/signals.jsonl").write_text(
        "\n".join(signal_lines) + "\n",
        encoding="utf-8",
    )

    run_git(root, "init", "-b", "main")
    run_git(root, "config", "user.name", "Test User")
    run_git(root, "config", "user.email", "test@example.com")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "initial")


@pytest.fixture
def e2e_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> E2EHarness:
    repo = tmp_path / "repo"
    repo.mkdir()
    initialize_repo(repo)

    real_git = shutil.which("git")
    assert real_git is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    push_log = tmp_path / "git-push.jsonl"
    push_log.write_text("", encoding="utf-8")
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env python3
import json
import os
import subprocess
import sys

if len(sys.argv) > 1 and sys.argv[1] == "push":
    with open(os.environ["FAKE_GIT_PUSH_LOG"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"args": sys.argv[1:]}, separators=(",", ":")) + "\\n")
    sys.exit(0)

completed = subprocess.run([os.environ["REAL_GIT"], *sys.argv[1:]], check=False)
sys.exit(completed.returncode)
""",
        encoding="utf-8",
    )
    fake_git.chmod(fake_git.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("REAL_GIT", real_git)
    monkeypatch.setenv("FAKE_GIT_PUSH_LOG", str(push_log))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    return E2EHarness(repo, monkeypatch, push_log)
