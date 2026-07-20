from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from agents.schemas import DependencyProposal


class DependencyChangeError(RuntimeError):
    pass


def proposal_path(root: Path, proposal_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", proposal_id):
        raise ValueError("invalid proposal id")
    return root / "company/dependency-proposals" / f"{proposal_id}.json"


def save_proposal(root: Path, proposal: DependencyProposal) -> Path:
    path = proposal_path(root, proposal.proposal_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise DependencyChangeError("dependency proposal already exists")
    path.write_text(proposal.model_dump_json(indent=2) + "\n")
    return path


def apply_approved_dependency(
    root: Path,
    proposal: DependencyProposal,
    *,
    approved_by: str,
    has_write_permission: bool,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    if not has_write_permission or re.search(r"(?:\[bot\]|bot$|agent)", approved_by, re.I):
        raise DependencyChangeError("dependency approval requires a verified human")
    if proposal.ecosystem == "npm":
        package = f"{proposal.package_name}@{proposal.exact_version}"
        command = ["npm", "install", "--ignore-scripts", "--save-exact"]
        if proposal.dependency_type == "development":
            command.append("--save-dev")
        command.append(package)
        result = runner(command, cwd=root, text=True, check=False)
    else:
        requirements = root / "requirements.txt"
        original = requirements.read_text().splitlines()
        prefix = re.compile(rf"^{re.escape(proposal.package_name)}==", re.I)
        retained = [line for line in original if not prefix.match(line)]
        retained.append(f"{proposal.package_name}=={proposal.exact_version}")
        requirements.write_text("\n".join(retained) + "\n")
        result = runner(
            ["python", "-m", "pip", "install", "--only-binary=:all:", "-r", "requirements.txt"],
            cwd=root,
            text=True,
            check=False,
        )
    if result.returncode:
        raise DependencyChangeError("approved dependency installation failed")


def load_proposal(root: Path, proposal_id: str) -> DependencyProposal:
    return DependencyProposal.model_validate(
        json.loads(proposal_path(root, proposal_id).read_text())
    )
