from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

from agents.schemas import ActionEnvelope, ActionType

CODE_ALLOWED_PREFIXES = (
    "venture/product/",
    "venture/content/",
    "venture/public/",
    "research/",
    "experiments/",
    "reports/",
)
CODE_ALLOWED_EXACT = {
    "company/state.json",
    "company/strategy.json",
    "company/metrics.json",
    "company/task-board.json",
    "company/decisions.jsonl",
}
ALWAYS_PROTECTED = {
    "company/constitution.md",
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "founder/results.json",
}
PROTECTED_PREFIXES = (".github/", "agents/", "scripts/", "tests/security/")
SPEC_ALLOWED = {
    "venture/product-requirements.md",
    "venture/user-personas.md",
    "venture/user-flows.md",
    "venture/mvp-scope.md",
    "venture/launch-plan.md",
    "venture/metrics-plan.md",
    "venture/venture.json",
    "venture/infrastructure.json",
}
FOUNDER_CONTENT_ALLOWED = {
    "founder/tasks.md",
    "founder/outreach-plan.md",
    "founder/posting-pack.md",
}
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?:authorization|api[_-]?key|token)\s*[:=]\s*['\"][^'\"]{12,}", re.I),
)


class SafetyViolation(ValueError):
    pass


def normalize_repo_path(raw: str) -> str:
    if not raw or "\x00" in raw or "\\" in raw:
        raise SafetyViolation("invalid path encoding")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SafetyViolation("absolute and traversal paths are forbidden")
    normalized = path.as_posix()
    if normalized in ALWAYS_PROTECTED or normalized.startswith(PROTECTED_PREFIXES):
        raise SafetyViolation(f"protected path: {normalized}")
    return normalized


def path_allowed_for_action(path: str, action: ActionType) -> bool:
    if action == ActionType.CREATE_CODE_PATCH:
        return path in CODE_ALLOWED_EXACT or path.startswith(CODE_ALLOWED_PREFIXES)
    if action == ActionType.CREATE_PRODUCT_SPEC:
        return path in SPEC_ALLOWED
    if action == ActionType.CHECK_DISTRIBUTION:
        return path in FOUNDER_CONTENT_ALLOWED
    if action in {ActionType.WRITE_REPORT, ActionType.CREATE_CONTENT}:
        return path.startswith(("reports/", "research/", "venture/content/"))
    return not path.startswith(PROTECTED_PREFIXES) and path not in ALWAYS_PROTECTED


def validate_action_files(
    action: ActionEnvelope,
    *,
    workspace: Path,
    max_files: int | None = None,
    max_file_chars: int | None = None,
    max_total_chars: int | None = None,
) -> None:
    max_files = max_files or int(os.getenv("MAX_FILES_PER_ACTION", "12"))
    max_file_chars = max_file_chars or int(os.getenv("MAX_FILE_CHARS", "20000"))
    max_total_chars = max_total_chars or int(os.getenv("MAX_TOTAL_OUTPUT_CHARS", "60000"))
    if len(action.files) > max_files:
        raise SafetyViolation("file count limit exceeded")
    total = 0
    seen: set[str] = set()
    root = workspace.resolve()
    for change in action.files:
        normalized = normalize_repo_path(change.path)
        if normalized in seen:
            raise SafetyViolation("duplicate file path")
        seen.add(normalized)
        if not path_allowed_for_action(normalized, action.action_type):
            raise SafetyViolation(f"path not allowed for action: {normalized}")
        if len(change.content) > max_file_chars:
            raise SafetyViolation("per-file character limit exceeded")
        total += len(change.content)
        candidate = workspace / normalized
        if candidate.exists() and candidate.is_symlink():
            raise SafetyViolation("symlink targets are forbidden")
        resolved_parent = candidate.parent.resolve()
        if root != resolved_parent and root not in resolved_parent.parents:
            raise SafetyViolation("resolved path escapes workspace")
        assert_no_secrets(change.content)
    if total > max_total_chars:
        raise SafetyViolation("total output character limit exceeded")


def assert_no_secrets(text: str) -> None:
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise SafetyViolation("potential secret detected")


def load_evidence_index(root: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    for directory in (root / "signals/raw", root / "signals/processed"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
                continue
            try:
                if path.suffix == ".jsonl":
                    items = [
                        json.loads(line) for line in path.read_text().splitlines() if line.strip()
                    ]
                else:
                    loaded = json.loads(path.read_text())
                    items = loaded if isinstance(loaded, list) else [loaded]
            except (OSError, json.JSONDecodeError):
                continue
            for item in items:
                if isinstance(item, dict):
                    evidence_id = item.get("evidence_id") or item.get("signal_id")
                    if isinstance(evidence_id, str):
                        records[evidence_id] = item
    return records


def validate_evidence_references(action: ActionEnvelope, root: Path) -> dict[str, dict]:
    if not action.evidence_ids:
        return {}
    index = load_evidence_index(root)
    missing = [item for item in action.evidence_ids if item not in index]
    if missing:
        raise SafetyViolation(f"unknown evidence ids: {', '.join(missing)}")
    return {item: index[item] for item in action.evidence_ids}


def urls_from_evidence(evidence_ids: list[str], root: Path) -> list[str]:
    index = load_evidence_index(root)
    urls: list[str] = []
    for evidence_id in evidence_ids:
        record = index.get(evidence_id)
        if not record:
            raise SafetyViolation(f"unknown evidence id: {evidence_id}")
        url = record.get("url")
        if not isinstance(url, str):
            raise SafetyViolation(f"evidence has no URL: {evidence_id}")
        urls.append(url)
    return urls
