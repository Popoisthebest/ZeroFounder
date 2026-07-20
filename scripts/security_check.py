from __future__ import annotations

import re
from pathlib import Path

from agents.safety import SECRET_PATTERNS

TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml", ".md"}


def main() -> int:
    root = Path(__file__).parents[1]
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
            failures.append(f"symlink: {path.relative_to(root)}")
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"requirements.txt"}:
            continue
        text = path.read_text(errors="replace")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            failures.append(f"potential secret: {path.relative_to(root)}")
        if any(pattern.search(text) for pattern in forbidden):
            failures.append(f"forbidden operation: {path.relative_to(root)}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("secret and forbidden-path scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
