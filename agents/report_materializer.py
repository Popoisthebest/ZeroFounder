from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents.pdf_report import ReportPdfSource, build_report_pdf, validate_report_pdf_bytes
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
        action.report.problem_id,
        problem_title_text,
        problem_domain,
        target_user_text,
        problem_summary,
        str(metadata["report_period"]),
        action.report.period_summary,
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
        lines.append(evidence)
    source = ReportPdfSource(
        title="ZeroFounder 주간 운영 보고서",
        problem_id=action.report.problem_id,
        problem_title=problem_title_text,
        problem_domain=problem_domain,
        target_users=target_user_text,
        problem_summary=problem_summary,
        report_period=str(metadata["report_period"]),
        report_type=action.report.report_type,
        period_summary=action.report.period_summary,
        sections=[(section.heading, section.content) for section in action.report.sections],
        findings=action.report.findings,
        recommendations=action.report.recommendations,
        evidence_ids=sorted(set(action.evidence_ids + action.report.evidence_ids)),
        operation_key=str(metadata.get("operation_key") or ""),
        operation_key_hash=str(metadata.get("operation_key_hash") or ""),
        required_text=[
            action.report.problem_id,
            str(metadata["report_period"]),
            *sorted(set(action.evidence_ids + action.report.evidence_ids)),
            *[
                sample
                for sample in [
                    "재고 관리 시스템",
                    "재고 관리자",
                    "창고 운영자",
                    "목록 탐색 비효율성",
                    "5 또는 10줄 단위로 이동",
                ]
                if sample in " ".join(lines)
            ],
        ],
    )
    pdf_bytes = build_report_pdf(source)
    validation = validate_report_pdf_bytes(pdf_bytes, required_text=source.required_text)
    if validation.status != "valid":
        raise ValueError(validation.rejection_code or "invalid_pdf_encoding")
    return FileChange(
        path=str(metadata["artifact_path"]),
        content=base64.b64encode(pdf_bytes).decode("ascii"),
        encoding="base64",
    )
