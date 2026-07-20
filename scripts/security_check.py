from __future__ import annotations

import argparse
import re
from pathlib import Path

from agents.safety import SECRET_PATTERNS

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml", ".md"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    forbidden = [
        re.compile(r"curl\s+.*\$\{\{\s*github\.event\.(issue|comment)", re.I),
        re.compile(r"rm\s+-rf"),
        re.compile(r"git\s+merge\s+--auto"),
    ]
    failures: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or any(
            part in {".git", ".venv", "node_modules", "dist", "tests"} for part in path.parts
        ):
            continue
        if path.is_symlink():
            failures.append(f"심볼릭 링크: {path.relative_to(root)}")
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"requirements.txt"}:
            continue
        text = path.read_text(errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            failures.append(f"비밀값 가능성: {path.relative_to(root)}")
        if any(pattern.search(text) for pattern in forbidden):
            failures.append(f"금지된 작업: {path.relative_to(root)}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("비밀값 및 금지 경로 검사가 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
