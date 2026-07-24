from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents.schemas import ActionEnvelope, CompanyState, FileChange

REPORT_PATH = re.compile(r"^reports/weekly_report_(?P<period>\d{4}-W\d{2})\.pdf$")


def operating_timezone(root: Path) -> ZoneInfo:
    try:
        strategy = json.loads((root / "company/strategy.json").read_text())
        name = str(strategy.get("review", {}).get("timezone") or "Asia/Seoul")
    except (OSError, json.JSONDecodeError):
        name = "Asia/Seoul"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Seoul")


def report_period(root: Path, *, now: datetime | None = None) -> str:
    clock = now or datetime.now(operating_timezone(root))
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=operating_timezone(root))
    localized = clock.astimezone(operating_timezone(root))
    iso_year, iso_week, _ = localized.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def report_artifact_path(period: str) -> str:
    if not re.fullmatch(r"\d{4}-W\d{2}", period):
        raise ValueError("invalid report period")
    return f"reports/weekly_report_{period}.pdf"


def stable_report_operation_key(
    *,
    lifecycle_stage: str,
    report_type: str,
    report_period_value: str,
    active_problem_id: str | None,
) -> str:
    problem = active_problem_id if active_problem_id is not None else "null"
    return f"{lifecycle_stage}|write_report|{report_type}|{report_period_value}|{problem}"


def stable_report_operation_key_hash(operation_key: str) -> str:
    return hashlib.sha256(operation_key.encode()).hexdigest()


def report_operation_metadata(
    root: Path,
    state: CompanyState,
    *,
    report_type: str = "weekly",
    now: datetime | None = None,
) -> dict[str, str | None]:
    period = report_period(root, now=now)
    path = report_artifact_path(period)
    key = stable_report_operation_key(
        lifecycle_stage=state.lifecycle_stage.value,
        report_type=report_type,
        report_period_value=period,
        active_problem_id=state.active_problem_id,
    )
    return {
        "lifecycle_stage": state.lifecycle_stage.value,
        "action_type": "write_report",
        "report_type": report_type,
        "report_period": period,
        "artifact_path": path,
        "active_problem_id": state.active_problem_id,
        "operation_key": key,
        "operation_key_hash": stable_report_operation_key_hash(key),
    }


def _problem_context(root: Path, problem_id: str | None) -> dict[str, object] | None:
    if not problem_id:
        return None
    path = root / f"research/problems/{problem_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _problem_domain(problem: dict[str, object] | None) -> str:
    if not problem:
        return "unknown"
    explicit = problem.get("domain")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    text = json.dumps(problem, ensure_ascii=False).lower()
    if any(term in text for term in ("재고", "inventory", "warehouse", "창고")):
        return "재고 관리 시스템"
    return "unknown"


def _problem_summary(problem: dict[str, object] | None, action: ActionEnvelope) -> str:
    if problem:
        for key in ("summary", "problem_statement", "description"):
            value = problem.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if action.report and action.report.summary:
        return action.report.summary
    return action.summary


def _pdf_escape(value: str) -> str:
    safe = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe[:1800]


def _minimal_pdf(lines: list[str]) -> str:
    stream_lines = ["BT", "/F1 10 Tf", "50 780 Td"]
    for line in lines[:45]:
        stream_lines.append(f"({_pdf_escape(line)}) Tj")
        stream_lines.append("0 -14 Td")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines)
    objects = [
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        (
            "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj"
        ),
        "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(stream.encode('utf-8'))} >> stream\n{stream}\nendstream endobj",
    ]
    content = "%PDF-1.4\n"
    offsets = [0]
    for item in objects:
        offsets.append(len(content.encode("utf-8")))
        content += item + "\n"
    xref_at = len(content.encode("utf-8"))
    content += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        content += f"{offset:010d} 00000 n \n"
    content += (
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    )
    return content


def materialize_report(
    action: ActionEnvelope,
    root: Path,
    *,
    now: datetime | None = None,
    metadata: dict[str, str | None] | None = None,
) -> FileChange:
    if action.report is None:
        raise ValueError("write_report requires report")
    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    if state.active_problem_id and action.report.problem_id != state.active_problem_id:
        raise ValueError("problem_context_mismatch")
    metadata = metadata or report_operation_metadata(
        root,
        state,
        report_type=action.report.report_type,
        now=now,
    )
    problem = _problem_context(root, state.active_problem_id)
    target_users = problem.get("target_users", []) if problem else []
    target_user_text = ", ".join(str(item) for item in target_users) if target_users else "unknown"
    problem_title = problem.get("title") if problem else None
    problem_title_text = str(problem_title) if problem_title else "unknown"
    problem_summary = _problem_summary(problem, action)
    problem_domain = _problem_domain(problem)
    lines = [
        f"Problem ID: {action.report.problem_id}",
        f"Problem title: {problem_title_text}",
        f"Problem domain: {problem_domain}",
        f"Target users: {target_user_text}",
        f"Problem description: {problem_summary}",
        f"Report period: {metadata['report_period']}",
        f"Report type: {action.report.report_type}",
        f"Period analysis: {action.report.period_summary}",
    ]
    for section in action.report.sections:
        lines.extend([section.heading, section.content])
    if action.report.findings:
        lines.append("Findings")
        lines.extend(action.report.findings)
    if action.report.recommendations:
        lines.append("Recommendations")
        lines.extend(action.report.recommendations)
    if action.evidence_ids or action.report.evidence_ids:
        evidence = ", ".join(sorted(set(action.evidence_ids + action.report.evidence_ids)))
        lines.append(f"Evidence IDs: {evidence}")
    content = _minimal_pdf(lines)
    return FileChange(path=str(metadata["artifact_path"]), content=content)
