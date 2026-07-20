from __future__ import annotations

import json
from pathlib import Path


def _read(path: Path, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(errors="replace")[-limit:]


def build_context(root: Path, max_chars: int = 50_000) -> str:
    sections: list[tuple[str, str]] = []
    fixed = [
        ("mission", root / "company/mission.md"),
        ("constitution", root / "company/constitution.md"),
        ("state", root / "company/state.json"),
        ("strategy", root / "company/strategy.json"),
        ("venture", root / "venture/venture.json"),
        ("metrics", root / "company/metrics.json"),
        ("tasks", root / "company/task-board.json"),
        ("usage", root / "company/usage.json"),
    ]
    for name, path in fixed:
        content = _read(path, 8_000)
        if content:
            sections.append((name, content))
    decisions_path = root / "company/decisions.jsonl"
    if decisions_path.exists():
        lines = decisions_path.read_text(errors="replace").splitlines()[-20:]
        sections.append(("recent_decisions", "\n".join(lines)))
    founder_results = _read(root / "founder/results.json", 8_000)
    if founder_results:
        sections.append(("human_only_founder_results", founder_results))
    payload = {name: content for name, content in sections}
    result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return result[:max_chars]
