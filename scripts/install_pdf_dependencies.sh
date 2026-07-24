#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  fonts-nanum \
  fontconfig \
  poppler-utils
sudo fc-cache -f -v

font_list="$(fc-list | grep -i "NanumGothic" || true)"
if [[ -z "$font_list" ]]; then
  echo "NanumGothic was not found by fc-list" >&2
  exit 41
fi
printf '%s\n' "$font_list" | head -n 5
fc-match "NanumGothic"

PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python - <<'PY'
from agents.pdf_report import detect_korean_font, find_korean_font

detection = detect_korean_font()
print(f"detected_font_path={detection.path or ''}")
print(f"detected_font_family={detection.family}")
print(f"detected_font_format={detection.format}")
print(f"font_file_exists={detection.exists}")
print(f"font_file_size={detection.size}")
print(f"find_korean_font={find_korean_font() or ''}")

if not detection.path:
    raise SystemExit("korean_font_not_installed")
if not detection.exists:
    raise SystemExit("korean_font_not_found")
if detection.format not in {"ttf", "ttc", "otf"}:
    raise SystemExit("korean_font_not_found")
if detection.size <= 0:
    raise SystemExit("korean_font_not_found")
PY
