import subprocess
from pathlib import Path

import pytest

from agents.dependencies import DependencyChangeError, apply_approved_dependency
from agents.schemas import DependencyProposal


def proposal() -> DependencyProposal:
    return DependencyProposal(
        proposal_id="dep-001",
        ecosystem="npm",
        package_name="example-package",
        exact_version="1.2.3",
        dependency_type="development",
        reason="Needed for a specific approved test capability",
        standard_library_alternative="No equivalent browser standard is available",
        license="MIT",
        security_risk="Low after audit",
        bundle_or_maintenance_impact="Development-only package",
        requested_by_action="create_code_patch",
    )


def test_dependency_requires_human_and_uses_exact_version(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    apply_approved_dependency(
        tmp_path, proposal(), approved_by="founder", has_write_permission=True, runner=runner
    )
    assert calls[0][-1] == "example-package@1.2.3"
    assert "--ignore-scripts" in calls[0]


def test_dependency_bot_approval_rejected(tmp_path: Path):
    with pytest.raises(DependencyChangeError):
        apply_approved_dependency(
            tmp_path, proposal(), approved_by="agent[bot]", has_write_permission=True
        )
