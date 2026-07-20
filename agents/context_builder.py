from __future__ import annotations

import json
from pathlib import Path


def _read(path: Path, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(errors="replace")[-limit:]


def _recent_json_records(directory: Path, *, limit: int, max_chars: int) -> str:
    if not directory.exists():
        return "[]"
    records: list[dict] = []
    for path in sorted(directory.rglob("*"), reverse=True):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix == ".jsonl":
                loaded = [json.loads(line) for line in path.read_text().splitlines() if line]
            else:
                value = json.loads(path.read_text())
                loaded = value if isinstance(value, list) else [value]
        except (OSError, json.JSONDecodeError):
            continue
        for item in reversed(loaded):
            if not isinstance(item, dict):
                continue
            records.append(item)
            if len(records) >= limit:
                break
        if len(records) >= limit:
            break
    serialized = json.dumps(list(reversed(records)), ensure_ascii=False, separators=(",", ":"))
    return serialized[:max_chars]


def _fit_context(payload: dict[str, str], max_chars: int) -> str:
    result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(result) > max_chars:
        name = max(payload, key=lambda key: len(payload[key]))
        current = payload[name]
        if len(current) <= 256:
            return result[:max_chars]
        payload[name] = current[: max(256, int(len(current) * 0.75))]
        result = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return result


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
    sections.extend(
        [
            (
                "recent_market_signals",
                _recent_json_records(root / "signals/raw", limit=60, max_chars=20_000),
            ),
            (
                "processed_evidence",
                _recent_json_records(root / "signals/processed", limit=40, max_chars=12_000),
            ),
            (
                "problem_candidates",
                _recent_json_records(root / "research/problems", limit=20, max_chars=10_000),
            ),
        ]
    )
    payload = {name: content for name, content in sections}
    return _fit_context(payload, max_chars)
