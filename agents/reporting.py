from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from agents.schemas import CompanyState, UsageLedger


def _json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def render_daily_report(root: Path, today: date) -> str:
    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    venture = _json(root / "venture/venture.json")
    metrics = _json(root / "company/metrics.json")
    tasks = _json(root / "company/task-board.json")
    usage = UsageLedger.model_validate_json((root / "company/usage.json").read_text())
    usage_day = next((item for item in usage.days if item.date == today), None)
    raw_files = list((root / "signals/raw").glob(f"{today.isoformat()}*.jsonl"))
    signal_count = sum(
        len([line for line in path.read_text(errors="replace").splitlines() if line.strip()])
        for path in raw_files
    )
    selected = venture.get("name") or "선택되지 않음"
    model_usage = usage_day.inference_calls if usage_day else 0
    model_upper_bound = usage_day.inference_call_upper_bound if usage_day else 0
    bugs = metrics.get("bug_reports", "확인 불가")
    features = metrics.get("feature_requests", "확인 불가")
    visitors = metrics.get("visitor_count")
    visitor_text = visitors if visitors is not None else "분석 도구 미연결 — 확인 불가"
    return f"""# ZeroFounder 일일 경영 보고서 — {today.isoformat()}

## 현재 상태

- 스타트업 단계: `{state.lifecycle_stage.value}`
- 선택된 사업 아이템: {selected}
- 오늘의 핵심 목표: 현재 단계에서 근거가 있는 주요 행동 하나 수행

## 업무

- 최근 완료: {len(tasks.get("done", []))}건
- 진행 중: {len(tasks.get("in_progress", []))}건
- 새 시장 신호: {signal_count}건
- 사용자 요청: metrics에 확인된 열린 Issue {metrics.get("open_issues", "확인 불가")}건

## 주요 지표

- stars/forks: {metrics.get("stars", "확인 불가")}/{metrics.get("forks", "확인 불가")}
- 버그/기능 요청: {bugs}/{features}
- 방문자: {visitor_text}

## 실험·위험·다음 행동

- 진행 중인 실험: {state.active_experiment or "없음"}
- 최근 실패: 확인된 기록 없음
- 주요 위험: 근거·유통·검증 gate를 건너뛰지 않을 것
- 다음 추천 행동: preflight가 확인한 실제 변화에 따라 결정
- 인간 승인 필요: founder/tasks.md 및 열린 `requires-approval` Issue 확인
- 모델 사용량: 합산 확인 {model_usage}회, Actions 기반 보수적 상한 {model_upper_bound}회

확인할 수 없는 수치는 추정하지 않았습니다.
"""


def write_daily_report(root: Path, today: date | None = None) -> bool:
    today = today or date.today()
    target = root / "reports" / f"{today.isoformat()}.md"
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_daily_report(root, today))
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    changed = write_daily_report(args.root.resolve())
    print(json.dumps({"report_created": changed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
