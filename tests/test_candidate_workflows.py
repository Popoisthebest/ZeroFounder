from pathlib import Path

import pytest

from scripts.check_candidate_workflows import check_candidate_workflows


def write_workflow(root: Path, text: str) -> None:
    target = root / ".github/workflows/check.yml"
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")


def test_candidate_workflow_check_uses_control_helper_only(tmp_path: Path):
    write_workflow(
        tmp_path,
        """name: 후보 검사
on: workflow_dispatch
permissions:
  contents: read
jobs:
  check:
    name: 후보 검사
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
""",
    )
    assert not (tmp_path / "scripts/check_candidate_workflows.py").exists()
    check_candidate_workflows(tmp_path)


def test_candidate_workflow_rejects_pull_request_target(tmp_path: Path):
    write_workflow(
        tmp_path,
        """name: 후보 검사
on: pull_request_target
permissions:
  contents: read
jobs:
  check:
    name: 후보 검사
    runs-on: ubuntu-latest
    steps: []
""",
    )
    with pytest.raises(ValueError, match="pull_request_target"):
        check_candidate_workflows(tmp_path)


def test_candidate_workflow_rejects_unsafe_default_permissions(tmp_path: Path):
    write_workflow(
        tmp_path,
        """name: 후보 검사
on: workflow_dispatch
permissions:
  contents: write
jobs:
  check:
    name: 후보 검사
    runs-on: ubuntu-latest
    steps: []
""",
    )
    with pytest.raises(ValueError, match="기본 권한이 안전하지"):
        check_candidate_workflows(tmp_path)
