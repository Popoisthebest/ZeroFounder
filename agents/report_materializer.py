from __future__ import annotations

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
    }


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
) -> FileChange:
    if action.report is None:
        raise ValueError("write_report requires report")
    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    metadata = report_operation_metadata(
        root,
        state,
        report_type=action.report.report_type,
        now=now,
    )
    lines = [
        action.report.title,
        f"Report period: {metadata['report_period']}",
        action.report.summary,
        action.report.period_summary,
    ]
    for section in action.report.sections:
        lines.extend([section.heading, section.content])
    if action.evidence_ids or action.report.evidence_ids:
        evidence = ", ".join(sorted(set(action.evidence_ids + action.report.evidence_ids)))
        lines.append(f"Evidence IDs: {evidence}")
    content = _minimal_pdf(lines)
    return FileChange(path=str(metadata["artifact_path"]), content=content)
