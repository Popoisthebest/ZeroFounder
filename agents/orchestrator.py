from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from agents.approval import AGENT_COMMANDS, is_bot_actor, parse_comment_command
from agents.context_builder import build_context_bundle
from agents.github_client import GitHubClient
from agents.github_models import (
    DEFAULT_MAX_MODEL_OUTPUT_TOKENS,
    GitHubModelsClient,
    PromptVariant,
    configured_model_input_budget,
    estimate_input_tokens,
    safe_no_op,
)
from agents.language import GLOBAL_MARKET_POLICY, korean_output_contract, operating_language
from agents.lifecycle import (
    action_allowed,
    allowed_actions,
    validate_action_transition,
)
from agents.preflight import build_preflight_decision, usage_allows_run
from agents.report_materializer import report_operation_metadata
from agents.safety import load_evidence_index, validate_evidence_references
from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    ActionType,
    CompactDiscoveryActionEnvelope,
    CompanyState,
    DiscoveryActionEnvelope,
    FailureStage,
    MarketSignal,
    ModelActionDiagnostic,
    ModelInferenceDiagnostic,
    ModelRunOutcome,
    PreflightDecision,
    RepositoryCheckpoint,
    TriggerReason,
)
from agents.usage_limiter import UsageLimiter, required_inference_calls

AGENT_PR_METADATA = re.compile(r"<!--\s*zerofounder-agent-pr\s*(\{.*?\})\s*-->", re.S)
OPERATION_METADATA = re.compile(r"<!--\s*zerofounder-operation:\s*(\{.*?\})\s*-->", re.S)
SCHEDULE_CRON = "17 */2 * * *"


def _load_company_state(root: Path) -> CompanyState:
    path = root / "company/state.json"
    if not path.exists():
        return CompanyState()
    return CompanyState.model_validate_json(path.read_text())


def _skip_detail(reason: str, *, state: CompanyState, pr_numbers: list[int] | None = None) -> str:
    problem = state.active_problem_id or "none"
    stage = state.lifecycle_stage.value
    details = {
        "bot_comment": (
            "봇이 작성한 댓글은 ZeroFounder 에이전트 실행 트리거로 사용하지 않습니다."
        ),
        "unrecognized_comment_command": "허용된 첫 줄 명령이 아니어서 일반 댓글로 처리했습니다.",
        "command_handled_by_approval_flow": (
            "승인 관련 명령은 AI 의사결정 workflow가 아닌 승인 반영 흐름에서 처리합니다."
        ),
        "unsupported_issue_context": (
            "댓글 대상 Issue/PR에 에이전트 실행을 허용하는 라벨 또는 문맥이 없습니다."
        ),
        "unauthorized_actor": "댓글 작성자가 ZeroFounder 에이전트 실행 권한을 갖고 있지 않습니다.",
        "concurrent_run_active": "다른 ZeroFounder 에이전트 실행이 모델 호출을 예약한 상태입니다.",
        "no_new_trigger": "마지막 실행 이후 새 signal, metric, 명령이 없습니다.",
        "idempotency_key_already_processed": (
            "현재 실행의 idempotency key가 이미 처리된 기록에 있습니다."
        ),
        "no_actionable_state": f"{stage} 단계에서 새로 수행할 material work가 없습니다.",
        "approval_required": "창업자 승인 대기 단계이므로 모델 의사결정을 새로 실행하지 않습니다.",
        "model_call_limit_reached": (
            "오늘 허용된 모델 호출 한도 또는 활성 예약 수 때문에 실행하지 않습니다."
        ),
        "unsupported_lifecycle_stage": f"{stage} 단계는 현재 자동 모델 action을 지원하지 않습니다.",
    }
    if reason == "open_agent_pr_exists":
        numbers = ", ".join(f"#{number}" for number in pr_numbers or [])
        return (
            f"{stage} 단계의 active_problem_id={problem} "
            f"agent PR {numbers}가 이미 열려 있습니다."
        )
    return details.get(reason, reason)


def _apply_skip(
    decision: PreflightDecision,
    reason: str,
    *,
    state: CompanyState,
    pr_numbers: list[int] | None = None,
    detail: str | None = None,
) -> None:
    decision.should_call_model = False
    decision.skip_reason = reason
    decision.blocked_reason = reason
    decision.skip_detail = detail or _skip_detail(reason, state=state, pr_numbers=pr_numbers)


def _agent_pr_metadata(body: object) -> dict[str, object]:
    if not isinstance(body, str):
        return {}
    match = AGENT_PR_METADATA.search(body)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _operation_metadata(body: object) -> dict[str, object]:
    if not isinstance(body, str):
        return {}
    match = OPERATION_METADATA.search(body)
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _matching_agent_pr_numbers(
    pulls: list[dict],
    *,
    lifecycle_stage: str,
    active_problem_id: str | None,
) -> list[int]:
    numbers: list[int] = []
    for pull in pulls:
        labels = {
            item.get("name")
            for item in pull.get("labels", [])
            if isinstance(item, dict)
        }
        if "agent-generated" not in labels:
            continue
        metadata = _agent_pr_metadata(pull.get("body"))
        if metadata.get("lifecycle_stage") != lifecycle_stage:
            continue
        if metadata.get("active_problem_id") != active_problem_id:
            continue
        number = pull.get("number")
        if isinstance(number, int):
            numbers.append(number)
    return sorted(numbers)


def _matching_operation_pr_numbers(
    pulls: list[dict],
    *,
    operation_key: str,
) -> list[int]:
    numbers: list[int] = []
    for pull in pulls:
        labels = {
            item.get("name")
            for item in pull.get("labels", [])
            if isinstance(item, dict)
        }
        if "agent-generated" not in labels:
            continue
        metadata = _operation_metadata(pull.get("body"))
        if metadata.get("operation_key") != operation_key:
            continue
        number = pull.get("number")
        if isinstance(number, int):
            numbers.append(number)
    return sorted(numbers)


def _idea_candidate_count(root: Path, problem_id: str | None) -> int:
    if not problem_id:
        return 0
    path = root / "research/ideas" / f"{problem_id}.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return 0
    candidates = data.get("idea_candidates") if isinstance(data, dict) else None
    return len(candidates) if isinstance(candidates, list) else 0


def _expected_action_type(root: Path, state: CompanyState) -> ActionType | None:
    if state.sleep_mode:
        return None
    permitted = [
        item for item in allowed_actions(state.lifecycle_stage) if item != ActionType.NO_OP
    ]
    if not permitted:
        return None
    if state.lifecycle_stage.value == "EVIDENCE_VALIDATION":
        return ActionType.VALIDATE_EVIDENCE
    if state.lifecycle_stage.value == "IDEA_EVALUATION":
        if _idea_candidate_count(root, state.active_problem_id) > 0:
            return ActionType.EVALUATE_IDEAS
        return ActionType.CREATE_IDEA_CANDIDATES
    if state.lifecycle_stage.value == "DISTRIBUTION_CHECK":
        return ActionType.WRITE_REPORT
    if state.lifecycle_stage.value == "FOUNDER_APPROVAL":
        return None
    return permitted[0]


def _signal_quality(root: Path) -> dict[str, float]:
    output: dict[str, float] = {}
    for path in (root / "signals/raw").glob("*.jsonl"):
        for line in path.read_text(errors="replace").splitlines():
            try:
                signal = MarketSignal.model_validate_json(line)
                output[signal.signal_id] = 0.5
            except ValueError:
                continue
    return output


def preflight(root: Path, event_path: Path | None, event_name: str) -> dict:
    checkpoint = RepositoryCheckpoint.model_validate_json(
        (root / "company/checkpoints.json").read_text()
    )
    state = _load_company_state(root)
    event: dict = {}
    if event_path and event_path.exists():
        loaded = json.loads(event_path.read_text())
        event = loaded if isinstance(loaded, dict) else {}
    issue = event.get("issue") if isinstance(event.get("issue"), dict) else {}
    comment = event.get("comment") if isinstance(event.get("comment"), dict) else {}
    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    github_client: GitHubClient | None = None
    if token and repository:
        try:
            github_client = GitHubClient(token, repository)
        except Exception:
            github_client = None
    decision: PreflightDecision
    if event_name == "issue_comment":
        actor = str(comment.get("user", {}).get("login", ""))
        author_type = str(comment.get("user", {}).get("type", ""))
        labels = {
            item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)
        }
        command = parse_comment_command(str(comment.get("body", "")))
        skip_reason = None
        if author_type == "Bot" or is_bot_actor(actor):
            skip_reason = "bot_comment"
        elif command is None:
            skip_reason = "unrecognized_comment_command"
        elif command not in AGENT_COMMANDS:
            skip_reason = "command_handled_by_approval_flow"
        elif not labels & {"agent-generated", "requires-approval", "founder-approval"}:
            skip_reason = "unsupported_issue_context"
        elif not (token and repository):
            skip_reason = "unauthorized_actor"
        else:
            try:
                if not (github_client and github_client.has_write_permission(actor)):
                    skip_reason = "unauthorized_actor"
            except Exception:
                skip_reason = "unauthorized_actor"
        if skip_reason:
            decision = PreflightDecision(
                should_call_model=False,
                idempotency_key=hashlib.sha256(
                    f"skip:{skip_reason}:{comment.get('id')}".encode()
                ).hexdigest(),
                blocked_reason=skip_reason,
                skip_reason=skip_reason,
                skip_detail=_skip_detail(skip_reason, state=state),
            )
        else:
            decision = build_preflight_decision(
                checkpoint=checkpoint,
                signal_quality={},
                issue_ids=[],
                comment_ids=[comment["id"]] if isinstance(comment.get("id"), int) else [],
                product_sha=None,
                metrics_hash=None,
                due_experiment=False,
                daily_review_due=False,
                weekly_review_due=False,
                manual=True,
                min_new_signals=999999,
                strong_evidence_threshold=2.0,
            )
    else:
        issue_ids = [issue["id"]] if isinstance(issue.get("id"), int) else []
        metrics = (root / "company/metrics.json").read_bytes()
        strategy = json.loads((root / "company/strategy.json").read_text())
        now = datetime.now(UTC)
        review = strategy["review"]
        daily_due = checkpoint.last_daily_review != now.date() and now.hour >= int(
            review["daily_hour"]
        )
        weekly_due = (
            now.isoweekday() == int(review["weekly_day"])
            and checkpoint.last_weekly_review != now.date()
            and now.hour >= int(review["weekly_hour"])
        )
        evidence = strategy["evidence"]
        decision = build_preflight_decision(
            checkpoint=checkpoint,
            signal_quality=_signal_quality(root),
            issue_ids=issue_ids,
            comment_ids=[],
            product_sha=os.getenv("PRODUCT_SHA"),
            metrics_hash=hashlib.sha256(metrics).hexdigest(),
            due_experiment=False,
            daily_review_due=daily_due,
            weekly_review_due=weekly_due,
            manual=event_name == "workflow_dispatch",
            min_new_signals=int(
                os.getenv(
                    "MIN_NEW_SIGNALS_FOR_ANALYSIS", evidence["min_new_signals_for_analysis"]
                )
            ),
            strong_evidence_threshold=float(
                os.getenv("STRONG_EVIDENCE_THRESHOLD", evidence["strong_evidence_threshold"])
            ),
        )
    expected_action_type = _expected_action_type(root, state)
    operation_metadata: dict[str, str | None] = {}
    if expected_action_type == ActionType.WRITE_REPORT:
        operation_metadata = report_operation_metadata(root, state)
        decision.idempotency_key = hashlib.sha256(
            str(operation_metadata["operation_key"]).encode()
        ).hexdigest()
    open_agent_pr_numbers: list[int] = []
    open_operation_pr_numbers: list[int] = []
    if github_client is not None:
        try:
            open_pulls = github_client.open_agent_pull_requests()
            if operation_metadata.get("operation_key"):
                open_operation_pr_numbers = _matching_operation_pr_numbers(
                    open_pulls,
                    operation_key=str(operation_metadata["operation_key"]),
                )
            open_agent_pr_numbers = _matching_agent_pr_numbers(
                open_pulls,
                lifecycle_stage=state.lifecycle_stage.value,
                active_problem_id=state.active_problem_id,
            )
        except Exception:
            open_agent_pr_numbers = []
    base_limit = int(os.getenv("DAILY_MODEL_CALL_LIMIT", "8"))
    diagnostic_mode = os.getenv("MODEL_DIAGNOSTIC_MODE", "false").lower() == "true"
    manual_diagnostic_allowance = (
        int(os.getenv("MANUAL_DIAGNOSTIC_CALL_ALLOWANCE", "1"))
        if diagnostic_mode and event_name == "workflow_dispatch"
        else 0
    )
    limit = base_limit + manual_diagnostic_allowance
    required_calls = (
        required_inference_calls(diagnostic_mode) if decision.should_call_model else 0
    )
    usage = {
        "completed_inference_calls": 0,
        "reserved_inference_calls": 0,
        "failed_after_request_calls": 0,
        "skipped_runs": 0,
    }
    if github_client is not None:
        try:
            usage = github_client.model_usage_today()
        except Exception:
            ledger = UsageLimiter.from_path(root / "company/usage.json", daily_limit=limit)
            day = ledger.today()
            usage = {
                "completed_inference_calls": day.inference_calls,
                "reserved_inference_calls": day.reserved_inference_calls,
                "failed_after_request_calls": day.failed_after_request_calls,
                "skipped_runs": day.skipped_runs,
            }
    completed = usage["completed_inference_calls"]
    active = usage["reserved_inference_calls"]
    allowed = usage_allows_run(
        completed_calls=completed,
        active_reservations=active,
        required_calls=required_calls,
        daily_limit=limit,
    )
    decision.completed_calls_today = completed
    decision.active_reservations = active
    decision.required_calls = required_calls
    decision.daily_limit = base_limit
    decision.manual_diagnostic_allowance = manual_diagnostic_allowance
    decision.effective_daily_limit = limit
    decision.usage_allowed = allowed
    decision.usage_calculation = f"{completed} + {active} + {required_calls} <= {limit}"
    decision.failed_after_request_calls_today = usage["failed_after_request_calls"]
    decision.skipped_runs_today = usage["skipped_runs"]
    decision.lifecycle_stage = state.lifecycle_stage
    decision.active_problem_id = state.active_problem_id
    decision.expected_action_type = expected_action_type
    duplicate_pr_numbers = open_operation_pr_numbers or open_agent_pr_numbers
    decision.open_agent_pr_count = len(duplicate_pr_numbers)
    decision.open_agent_pr_numbers = duplicate_pr_numbers
    artifact_path = operation_metadata.get("artifact_path")
    artifact_exists = bool(artifact_path and (root / str(artifact_path)).exists())
    decision.idempotency_key_seen = (
        decision.idempotency_key in checkpoint.idempotency_keys or artifact_exists
    )
    decision.concurrent_run_detected = active > 0
    decision.report_type = operation_metadata.get("report_type")
    decision.report_period = operation_metadata.get("report_period")
    decision.artifact_path = operation_metadata.get("artifact_path")
    decision.operation_key = operation_metadata.get("operation_key")
    if event_name == "schedule":
        decision.schedule_cron = SCHEDULE_CRON
        decision.next_schedule_note = "다음 실행은 GitHub 스케줄에 따라 진행됩니다."
    if decision.skip_reason in {
        "bot_comment",
        "unrecognized_comment_command",
        "command_handled_by_approval_flow",
        "unsupported_issue_context",
        "unauthorized_actor",
    }:
        pass
    elif decision.concurrent_run_detected:
        _apply_skip(decision, "concurrent_run_active", state=state)
    elif duplicate_pr_numbers:
        _apply_skip(
            decision,
            "open_agent_pr_exists",
            state=state,
            pr_numbers=duplicate_pr_numbers,
        )
    elif state.lifecycle_stage.value == "FOUNDER_APPROVAL":
        _apply_skip(decision, "approval_required", state=state)
    elif expected_action_type is None:
        _apply_skip(decision, "no_actionable_state", state=state)
    elif decision.idempotency_key_seen:
        detail = None
        if artifact_exists and artifact_path:
            detail = f"이번 보고서 산출물이 이미 존재합니다: {artifact_path}"
        _apply_skip(
            decision,
            "idempotency_key_already_processed",
            state=state,
            detail=detail,
        )
    elif not decision.should_call_model:
        _apply_skip(decision, decision.skip_reason or "no_new_trigger", state=state)
    elif not allowed:
        _apply_skip(decision, "model_call_limit_reached", state=state)
    return decision.model_dump(mode="json", by_alias=True)


def _count_records(directory: Path) -> int:
    count = 0
    if not directory.exists():
        return count
    for path in directory.rglob("*"):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        try:
            if path.suffix == ".jsonl":
                count += sum(1 for line in path.read_text().splitlines() if line.strip())
            else:
                value = json.loads(path.read_text())
                count += len(value) if isinstance(value, list) else int(isinstance(value, dict))
        except (OSError, json.JSONDecodeError):
            continue
    return count


def build_model_instruction(
    root: Path,
    state: CompanyState,
    decision: PreflightDecision | None,
    *,
    compact: bool = False,
) -> str:
    permitted = allowed_actions(state.lifecycle_stage)
    preferred = list(permitted)
    guidance = "Choose one allowed action that advances only the current lifecycle stage."
    transition_policy: dict[str, str] = {}
    counts = {
        "raw_signals": _count_records(root / "signals/raw"),
        "processed_evidence": _count_records(root / "signals/processed"),
        "problem_candidates": _count_records(root / "research/problems"),
    }
    reasons = decision.reasons if decision else []
    if state.lifecycle_stage.value == "DISCOVERY":
        strategy = json.loads((root / "company/strategy.json").read_text())
        minimum = int(
            os.getenv("MIN_UNIQUE_SIGNALS", strategy["evidence"]["min_unique_signals"])
        )
        has_strong_signal = TriggerReason.STRONG_SIGNAL in reasons
        enough_signals = counts["raw_signals"] >= minimum or has_strong_signal
        if enough_signals and counts["problem_candidates"] == 0:
            preferred = [
                ActionType.CREATE_PROBLEM_CANDIDATE,
                ActionType.VALIDATE_EVIDENCE,
                ActionType.WRITE_REPORT,
                ActionType.NO_OP,
                ActionType.COLLECT_SIGNALS,
            ]
            guidance = (
                "Raw signals are already sufficient. Do not collect them again. Create one "
                "problem candidate grounded in existing signal IDs; validate evidence instead "
                "only if a candidate can already be supported."
            )
        elif enough_signals:
            preferred = [
                ActionType.VALIDATE_EVIDENCE,
                ActionType.CREATE_PROBLEM_CANDIDATE,
                ActionType.WRITE_REPORT,
                ActionType.NO_OP,
                ActionType.COLLECT_SIGNALS,
            ]
            guidance = (
                "Raw signals and at least one problem candidate already exist. Validate the "
                "candidate's evidence, or create a distinct evidence-backed problem. Do not "
                "request duplicate signal collection."
            )
        else:
            preferred = [
                ActionType.COLLECT_SIGNALS,
                ActionType.CREATE_PROBLEM_CANDIDATE,
                ActionType.WRITE_REPORT,
                ActionType.NO_OP,
                ActionType.VALIDATE_EVIDENCE,
            ]
            guidance = (
                "Stored signals are below the configured discovery threshold. collect_signals "
                "is appropriate unless the supplied strong evidence supports a concrete problem."
            )
        transition_policy = {
            ActionType.COLLECT_SIGNALS.value: "omit state_transition",
            ActionType.CREATE_PROBLEM_CANDIDATE.value: (
                "omit it, keep DISCOVERY, or transition DISCOVERY to EVIDENCE_VALIDATION"
            ),
            ActionType.VALIDATE_EVIDENCE.value: (
                "omit it, keep DISCOVERY, or transition DISCOVERY to EVIDENCE_VALIDATION"
            ),
            ActionType.WRITE_REPORT.value: "omit state_transition",
            ActionType.NO_OP.value: "state_transition is forbidden",
        }
    elif state.lifecycle_stage.value == "EVIDENCE_VALIDATION":
        evidence_context = build_context_bundle(
            root,
            lifecycle_stage=state.lifecycle_stage,
            compact=compact,
            max_chars=1000,
            new_signal_ids=decision.new_signal_ids if decision else [],
        )
        preferred = [
            ActionType.VALIDATE_EVIDENCE,
            ActionType.WRITE_REPORT,
            ActionType.NO_OP,
        ]
        if evidence_context.resolved_evidence_count > 0:
            guidance = (
                "Validate the active problem candidate using the supplied stored evidence "
                "records. new_signal_ids are supplemental only; do not ignore existing "
                "candidate evidence when no new signals were collected."
            )
        else:
            guidance = (
                "Do not hide missing stored evidence as no_op. If evidence records cannot be "
                "resolved, return a clear validation outcome grounded in unresolved_evidence_ids."
            )
        transition_policy = {
            ActionType.VALIDATE_EVIDENCE.value: (
                "omit it, keep EVIDENCE_VALIDATION, or transition EVIDENCE_VALIDATION to "
                "IDEA_EVALUATION"
            ),
            ActionType.WRITE_REPORT.value: "omit state_transition",
            ActionType.NO_OP.value: "state_transition is forbidden",
        }
    elif state.lifecycle_stage.value == "IDEA_EVALUATION":
        idea_context = build_context_bundle(
            root,
            lifecycle_stage=state.lifecycle_stage,
            compact=compact,
            max_chars=1000,
        )
        if idea_context.idea_context_ready and idea_context.existing_idea_candidate_count == 0:
            preferred = [
                ActionType.CREATE_IDEA_CANDIDATES,
                ActionType.EVALUATE_IDEAS,
                ActionType.WRITE_REPORT,
                ActionType.NO_OP,
            ]
            guidance = (
                "The active problem and validated evidence are already available. "
                "Do not wait for new signals. Create idea candidates grounded in the "
                "active problem and included signal records."
            )
        elif idea_context.idea_context_ready:
            preferred = [
                ActionType.EVALUATE_IDEAS,
                ActionType.CREATE_IDEA_CANDIDATES,
                ActionType.WRITE_REPORT,
                ActionType.NO_OP,
            ]
            guidance = (
                "Existing idea candidates are available for this active problem. "
                "Evaluate them instead of creating duplicates; after evaluation, prepare "
                "selection evidence for the next lifecycle transition."
            )
        else:
            preferred = [ActionType.NO_OP, ActionType.WRITE_REPORT]
            guidance = (
                "Idea evaluation cannot proceed because the active problem context or "
                "validated evidence is missing. Do not invent a problem or evidence."
            )
        transition_policy = {
            ActionType.CREATE_IDEA_CANDIDATES.value: "omit state_transition",
            ActionType.EVALUATE_IDEAS.value: (
                "omit it, keep IDEA_EVALUATION, or transition IDEA_EVALUATION to "
                "DISTRIBUTION_CHECK only after evaluation is complete"
            ),
            ActionType.WRITE_REPORT.value: "omit state_transition",
            ActionType.NO_OP.value: "state_transition is forbidden",
        }
    elif state.lifecycle_stage.value == "DISTRIBUTION_CHECK":
        preferred = [
            ActionType.WRITE_REPORT,
            ActionType.CHECK_DISTRIBUTION,
            ActionType.NO_OP,
        ]
        guidance = (
            "Write a structured weekly operating report when a report is due. "
            "Do not provide files, filenames, artifact paths, or placeholder paths; "
            "trusted repository code will materialize the report."
        )
        transition_policy = {
            ActionType.WRITE_REPORT.value: "omit state_transition",
            ActionType.CHECK_DISTRIBUTION.value: (
                "omit it or transition DISTRIBUTION_CHECK only when distribution data exists"
            ),
            ActionType.NO_OP.value: "state_transition is forbidden",
        }
    payload = {
        "orchestration_policy": {
            "operating_language": operating_language(),
            "output_language_contract": korean_output_contract(),
            "market_policy": GLOBAL_MARKET_POLICY,
            "lifecycle_stage": state.lifecycle_stage.value,
            "allowed_action_types": [item.value for item in permitted],
            "preferred_action_types": [item.value for item in preferred],
            "trigger_reasons": [item.value for item in reasons],
            "new_signal_count": (
                0
                if state.lifecycle_stage.value == "IDEA_EVALUATION"
                else (len(decision.new_signal_ids) if decision else 0)
            ),
            "new_signal_ids": (
                []
                if state.lifecycle_stage.value in {"DISCOVERY", "IDEA_EVALUATION"}
                else (decision.new_signal_ids[: (6 if compact else 20)] if decision else [])
            ),
            "repository_counts": counts,
            "guidance": guidance,
            "state_transition_policy": transition_policy,
        }
    }
    if state.lifecycle_stage.value == "EVIDENCE_VALIDATION":
        payload["orchestration_policy"]["active_problem_id"] = evidence_context.active_problem_id
        payload["orchestration_policy"]["candidate_evidence_id_count"] = (
            evidence_context.candidate_evidence_id_count
        )
        payload["orchestration_policy"]["resolved_evidence_count"] = (
            evidence_context.resolved_evidence_count
        )
        payload["orchestration_policy"]["unresolved_evidence_ids"] = (
            evidence_context.unresolved_evidence_ids or []
        )
    if state.lifecycle_stage.value == "IDEA_EVALUATION":
        payload["orchestration_policy"]["active_problem_id"] = idea_context.active_problem_id
        payload["orchestration_policy"]["problem_loaded"] = idea_context.problem_loaded
        payload["orchestration_policy"]["problem_evidence_count"] = (
            idea_context.problem_evidence_count
        )
        payload["orchestration_policy"]["resolved_evidence_count"] = (
            idea_context.resolved_evidence_count
        )
        payload["orchestration_policy"]["existing_idea_candidate_count"] = (
            idea_context.existing_idea_candidate_count
        )
        payload["orchestration_policy"]["idea_context_ready"] = idea_context.idea_context_ready
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _rejected_outcome(
    state: CompanyState,
    *,
    code: ActionRejectionCode,
    reason: str,
    original_action_type: ActionType | None = None,
    inference: ModelInferenceDiagnostic | None = None,
    failure_stage: FailureStage | None = None,
) -> ModelRunOutcome:
    action = safe_no_op(reason)
    model_diagnostic = inference or ModelInferenceDiagnostic()
    if failure_stage is not None:
        model_diagnostic = model_diagnostic.model_copy(
            update={"failure_stage": failure_stage}
        )
    return ModelRunOutcome(
        action=action,
        diagnostic=ModelActionDiagnostic(
            lifecycle_stage=state.lifecycle_stage,
            allowed_action_types=list(allowed_actions(state.lifecycle_stage)),
            original_action_type=original_action_type,
            validated_action_type=ActionType.NO_OP,
            accepted=False,
            rejection_code=code,
            rejection_reason=reason,
            inference=model_diagnostic,
        ),
    )


def _collect_action_evidence_ids(action: ActionEnvelope) -> list[str]:
    values: list[str] = []
    values.extend(action.evidence_ids)
    if action.report is not None:
        values.extend(action.report.evidence_ids)
    if action.idea_candidates:
        for candidate in action.idea_candidates:
            values.extend(candidate.evidence_ids)
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _allowed_evidence_ids_for_state(root: Path, state: CompanyState) -> list[str]:
    if not state.active_problem_id:
        return []
    problem_path = root / f"research/problems/{state.active_problem_id}.json"
    if not problem_path.exists():
        return []
    try:
        payload = json.loads(problem_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    evidence_ids = payload.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in evidence_ids:
        if isinstance(value, str) and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def validate_model_action(
    root: Path,
    state: CompanyState,
    action: ActionEnvelope,
    inference: ModelInferenceDiagnostic | None = None,
) -> ModelRunOutcome:
    if not action_allowed(state.lifecycle_stage, action.action_type):
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.LIFECYCLE_ACTION_NOT_ALLOWED,
            reason=(
                f"{action.action_type.value} is not allowed during "
                f"{state.lifecycle_stage.value}"
            ),
            original_action_type=action.action_type,
            inference=inference,
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    if action.state_transition and action.state_transition.from_stage != state.lifecycle_stage:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.STATE_TRANSITION_SOURCE_MISMATCH,
            reason="state transition source does not match the current lifecycle stage",
            original_action_type=action.action_type,
            inference=inference,
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    try:
        validate_action_transition(
            state.lifecycle_stage,
            action.action_type,
            action.state_transition,
        )
    except ValueError:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.INVALID_STATE_TRANSITION,
            reason="action requested a state transition that is not allowed",
            original_action_type=action.action_type,
            inference=inference,
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    allowed_context_ids = _allowed_evidence_ids_for_state(root, state)
    returned_context_ids = _collect_action_evidence_ids(action)
    malformed_context_ids = [
        evidence_id
        for evidence_id in returned_context_ids
        if allowed_context_ids and evidence_id not in allowed_context_ids
    ]
    if inference is None:
        inference = ModelInferenceDiagnostic()
    inference = inference.model_copy(
        update={
            "allowed_evidence_ids": allowed_context_ids or inference.allowed_evidence_ids,
            "returned_evidence_ids": returned_context_ids,
            "malformed_evidence_ids": (
                malformed_context_ids or inference.malformed_evidence_ids
            ),
            "evidence_ids_preserved_during_compaction": True,
        }
    )
    if malformed_context_ids:
        if action.action_type == ActionType.CREATE_IDEA_CANDIDATES and action.idea_candidates:
            allowed_set = set(allowed_context_ids)
            rejected_count = sum(
                1
                for candidate in action.idea_candidates
                if not set(candidate.evidence_ids).issubset(allowed_set)
            )
            inference = inference.model_copy(
                update={
                    "generated_idea_candidate_count": len(action.idea_candidates),
                    "accepted_idea_candidate_count": 0,
                    "rejected_idea_candidate_count": rejected_count,
                    "idea_candidate_ids": [
                        candidate.idea_id for candidate in action.idea_candidates
                    ],
                }
            )
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED,
            reason=(
                "evidence_reference_rejected: evidence_ids must exactly match the "
                "active problem allowed evidence IDs; partial or prefix IDs are invalid: "
                f"{', '.join(malformed_context_ids)}"
            ),
            original_action_type=action.action_type,
            inference=inference,
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    try:
        validate_evidence_references(action, root)
    except ValueError:
        missing = [
            evidence_id
            for evidence_id in action.evidence_ids
            if evidence_id not in load_evidence_index(root)
        ]
        if missing and inference is None:
            inference = ModelInferenceDiagnostic()
        if missing and inference is not None:
            inference = inference.model_copy(
                update={"unresolved_evidence_ids": sorted(set(missing))}
            )
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED,
            reason=(
                "missing_evidence_record: one or more evidence_ids do not exist in "
                f"stored signal records: {', '.join(missing)}"
                if missing
                else "one or more evidence_ids do not exist in stored signal records"
            ),
            original_action_type=action.action_type,
            inference=inference,
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    if action.action_type == ActionType.CREATE_IDEA_CANDIDATES and action.idea_candidates:
        if inference is None:
            inference = ModelInferenceDiagnostic()
        ids = [item.idea_id for item in action.idea_candidates]
        inference = inference.model_copy(
            update={
                "generated_idea_candidate_count": len(action.idea_candidates),
                "accepted_idea_candidate_count": len(action.idea_candidates),
                "rejected_idea_candidate_count": 0,
                "idea_candidate_ids": ids,
            }
        )
        try:
            state_record = CompanyState.model_validate_json(
                (root / "company/state.json").read_text()
            )
            if not state_record.active_problem_id:
                raise ValueError("missing active problem")
            problem = json.loads(
                (root / f"research/problems/{state_record.active_problem_id}.json").read_text()
            )
            allowed_evidence = set(problem.get("evidence_ids", []))
        except (OSError, ValueError, json.JSONDecodeError):
            return _rejected_outcome(
                state,
                code=ActionRejectionCode.MISSING_PROBLEM_RECORD,
                reason="missing_problem_record: active problem file could not be loaded",
                original_action_type=action.action_type,
                inference=inference.model_copy(
                    update={
                        "accepted_idea_candidate_count": 0,
                        "rejected_idea_candidate_count": len(action.idea_candidates),
                    }
                ),
                failure_stage=FailureStage.LIFECYCLE_VALIDATION,
            )
        invalid = [
            item.idea_id
            for item in action.idea_candidates
            if not set(item.evidence_ids).issubset(allowed_evidence)
        ]
        if invalid:
            return _rejected_outcome(
                state,
                code=ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED,
                reason="idea candidate references evidence outside active problem",
                original_action_type=action.action_type,
                inference=inference.model_copy(
                    update={
                        "accepted_idea_candidate_count": 0,
                        "rejected_idea_candidate_count": len(invalid),
                    }
                ),
                failure_stage=FailureStage.LIFECYCLE_VALIDATION,
            )
    return ModelRunOutcome(
        action=action,
        diagnostic=ModelActionDiagnostic(
            lifecycle_stage=state.lifecycle_stage,
            allowed_action_types=list(allowed_actions(state.lifecycle_stage)),
            original_action_type=action.action_type,
            validated_action_type=action.action_type,
            accepted=True,
            inference=inference or ModelInferenceDiagnostic(),
        ),
    )


def run_model(
    root: Path,
    decision: PreflightDecision | None = None,
) -> ModelRunOutcome:
    state = CompanyState.model_validate_json((root / "company/state.json").read_text())
    if state.sleep_mode:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.SLEEP_MODE,
            reason="sleep mode is active",
            failure_stage=FailureStage.LIFECYCLE_VALIDATION,
        )
    diagnostic_mode = os.getenv("MODEL_DIAGNOSTIC_MODE", "false").lower() == "true"
    limiter = UsageLimiter.from_path(
        root / "company/usage.json",
        daily_limit=(
            decision.effective_daily_limit
            if decision and decision.effective_daily_limit
            else None
        ),
        max_run_calls=required_inference_calls(diagnostic_mode),
    )
    client = GitHubModelsClient(os.environ["GITHUB_TOKEN"], limiter)
    try:
        catalog = client.catalog()
    except Exception:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.MODEL_CATALOG_UNAVAILABLE,
            reason="GitHub Models catalog is unavailable",
            failure_stage=FailureStage.MODEL_SELECTION,
        )
    if diagnostic_mode:
        messages = [
            {
                "role": "system",
                "content": "Verify the response pipeline with the exact diagnostic JSON.",
            }
        ]
        response_model = ActionEnvelope
        compact_variant = None
        context_chars = 0
        included_signal_count = 0
        excluded_signal_count = 0
        prompt_estimated_tokens = estimate_input_tokens(
            messages,
            json.dumps(response_model.model_json_schema(), separators=(",", ":")),
            schema_in_messages=False,
        )
        required_output_tokens = 500
    else:
        prompt = (root / "agents/prompts/core.md").read_text()
        max_context_chars = int(os.getenv("MAX_INPUT_CHARS", "24000"))
        context = build_context_bundle(
            root,
            lifecycle_stage=state.lifecycle_stage,
            compact=False,
            max_chars=max_context_chars,
            new_signal_ids=decision.new_signal_ids if decision else [],
        )
        compact_context = build_context_bundle(
            root,
            lifecycle_stage=state.lifecycle_stage,
            compact=True,
            max_chars=max(1000, max_context_chars // 2),
            new_signal_ids=decision.new_signal_ids if decision else [],
        )
        if (
            state.lifecycle_stage.value == "EVIDENCE_VALIDATION"
            and context.candidate_evidence_id_count > 0
            and context.resolved_evidence_count == 0
        ):
            inference = ModelInferenceDiagnostic(
                active_problem_id=context.active_problem_id,
                candidate_evidence_id_count=context.candidate_evidence_id_count,
                resolved_evidence_count=context.resolved_evidence_count,
                unresolved_evidence_ids=context.unresolved_evidence_ids or [],
                new_signal_count=context.new_signal_count,
                included_signal_count=context.included_signal_count,
                excluded_signal_count=context.excluded_signal_count,
            )
            return _rejected_outcome(
                state,
                code=ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED,
                reason=(
                    "missing_evidence_record: active problem evidence_ids could not be "
                    "resolved from stored signal records"
                ),
                inference=inference,
                failure_stage=FailureStage.LIFECYCLE_VALIDATION,
            )
        if state.lifecycle_stage.value == "IDEA_EVALUATION":
            inference = ModelInferenceDiagnostic(
                active_problem_id=context.active_problem_id,
                problem_loaded=context.problem_loaded,
                problem_evidence_count=context.problem_evidence_count,
                resolved_evidence_count=context.resolved_evidence_count,
                existing_idea_candidate_count=context.existing_idea_candidate_count,
                included_signal_count=context.included_signal_count,
                idea_context_ready=context.idea_context_ready,
                unresolved_evidence_ids=context.unresolved_evidence_ids or [],
            )
            if not context.active_problem_id:
                return _rejected_outcome(
                    state,
                    code=ActionRejectionCode.MISSING_ACTIVE_PROBLEM,
                    reason="missing_active_problem: company/state.json has no active_problem_id",
                    inference=inference,
                    failure_stage=FailureStage.LIFECYCLE_VALIDATION,
                )
            if not context.problem_loaded:
                return _rejected_outcome(
                    state,
                    code=ActionRejectionCode.MISSING_PROBLEM_RECORD,
                    reason=(
                        "missing_problem_record: active problem file could not be loaded"
                    ),
                    inference=inference,
                    failure_stage=FailureStage.LIFECYCLE_VALIDATION,
                )
            if not context.idea_context_ready:
                return _rejected_outcome(
                    state,
                    code=ActionRejectionCode.INSUFFICIENT_VALIDATED_EVIDENCE,
                    reason=(
                        "insufficient_validated_evidence: active problem evidence could "
                        "not be fully resolved from stored signal records"
                    ),
                    inference=inference,
                    failure_stage=FailureStage.LIFECYCLE_VALIDATION,
                )
        instruction = build_model_instruction(root, state, decision)
        compact_instruction = build_model_instruction(
            root, state, decision, compact=True
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": instruction},
            {"role": "user", "content": context.content},
        ]
        compact_messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": compact_instruction},
            {"role": "user", "content": compact_context.content},
        ]
        if state.lifecycle_stage.value == "DISCOVERY":
            response_model = DiscoveryActionEnvelope
            compact_response_model = CompactDiscoveryActionEnvelope
        else:
            response_model = ActionEnvelope
            compact_response_model = ActionEnvelope
        compact_variant = PromptVariant(
            messages=compact_messages,
            response_model=compact_response_model,
            context_chars=len(compact_context.content),
            included_signal_count=compact_context.included_signal_count,
            excluded_signal_count=compact_context.excluded_signal_count,
            active_problem_id=compact_context.active_problem_id,
            candidate_evidence_id_count=compact_context.candidate_evidence_id_count,
            resolved_evidence_count=compact_context.resolved_evidence_count,
            unresolved_evidence_ids=compact_context.unresolved_evidence_ids or [],
            new_signal_count=compact_context.new_signal_count,
            problem_loaded=compact_context.problem_loaded,
            problem_evidence_count=compact_context.problem_evidence_count,
            existing_idea_candidate_count=compact_context.existing_idea_candidate_count,
            idea_context_ready=compact_context.idea_context_ready,
            allowed_evidence_ids=compact_context.allowed_evidence_ids or [],
        )
        context_chars = len(context.content)
        included_signal_count = context.included_signal_count
        excluded_signal_count = context.excluded_signal_count
        compact_schema = json.dumps(
            compact_response_model.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt_estimated_tokens = estimate_input_tokens(
            compact_messages,
            compact_schema,
            schema_in_messages=False,
        )
        required_output_tokens = DEFAULT_MAX_MODEL_OUTPUT_TOKENS
    selection_required_input_tokens = configured_model_input_budget()
    if hasattr(client, "select_chat_model_with_diagnostics"):
        selection, selection_inference, selection_rejection = (
            client.select_chat_model_with_diagnostics(
                catalog,
                required_input_tokens=selection_required_input_tokens,
                required_output_tokens=required_output_tokens,
            )
        )
    else:
        selection = client.select_chat_model(
            catalog,
            required_input_tokens=selection_required_input_tokens,
        )
        selection_rejection = (
            None if selection else ActionRejectionCode.NO_COMPATIBLE_MODEL
        )
        selection_inference = ModelInferenceDiagnostic(
            selected_model=selection.selected_model if selection else None,
            request_mode=selection.request_mode if selection else None,
            selected_model_max_input_tokens=selection.max_input_tokens if selection else 0,
            applied_input_budget=selection.applied_input_budget if selection else 0,
            required_input_tokens=selection_required_input_tokens,
            required_output_tokens=required_output_tokens,
        )
    selection_inference = selection_inference.model_copy(
        update={"estimated_input_tokens": prompt_estimated_tokens}
    )
    if not diagnostic_mode:
        selection_inference = selection_inference.model_copy(
            update={
                "active_problem_id": context.active_problem_id,
                "candidate_evidence_id_count": context.candidate_evidence_id_count,
                "resolved_evidence_count": context.resolved_evidence_count,
                "unresolved_evidence_ids": context.unresolved_evidence_ids or [],
                "allowed_evidence_ids": context.allowed_evidence_ids or [],
                "new_signal_count": context.new_signal_count,
                "problem_loaded": context.problem_loaded,
                "problem_evidence_count": context.problem_evidence_count,
                "existing_idea_candidate_count": context.existing_idea_candidate_count,
                "idea_context_ready": context.idea_context_ready,
                "included_signal_count": context.included_signal_count,
                "excluded_signal_count": context.excluded_signal_count,
            }
        )
    if not selection:
        reason = (
            "no model candidates were configured or discovered"
            if selection_rejection == ActionRejectionCode.NO_MODEL_CANDIDATES_CONFIGURED
            else "no compatible text model satisfies the required input budget"
        )
        return _rejected_outcome(
            state,
            code=selection_rejection or ActionRejectionCode.NO_COMPATIBLE_MODEL,
            reason=reason,
            inference=selection_inference,
            failure_stage=FailureStage.MODEL_SELECTION,
        )
    call = client.chat_action(
        model=selection.selected_model,
        request_mode=selection.request_mode,
        diagnostic_mode=diagnostic_mode,
        messages=messages,
        response_model=response_model,
        compact_variant=compact_variant,
        context_chars=context_chars,
        included_signal_count=included_signal_count,
        excluded_signal_count=excluded_signal_count,
        active_problem_id=context.active_problem_id if not diagnostic_mode else None,
        candidate_evidence_id_count=(
            context.candidate_evidence_id_count if not diagnostic_mode else 0
        ),
        resolved_evidence_count=context.resolved_evidence_count if not diagnostic_mode else 0,
        unresolved_evidence_ids=(
            (context.unresolved_evidence_ids or []) if not diagnostic_mode else []
        ),
        new_signal_count=context.new_signal_count if not diagnostic_mode else 0,
        problem_loaded=context.problem_loaded if not diagnostic_mode else False,
        problem_evidence_count=context.problem_evidence_count if not diagnostic_mode else 0,
        existing_idea_candidate_count=(
            context.existing_idea_candidate_count if not diagnostic_mode else 0
        ),
        idea_context_ready=context.idea_context_ready if not diagnostic_mode else False,
        allowed_evidence_ids=(context.allowed_evidence_ids or []) if not diagnostic_mode else [],
        model_max_input_tokens=selection.max_input_tokens,
        applied_input_budget=selection.applied_input_budget,
    )
    selection_diagnostic_fields = selection_inference.model_dump(
        include={
            "configured_model",
            "configured_fallback_models",
            "default_model_candidates",
            "evaluated_model_candidates",
            "required_input_tokens",
            "required_output_tokens",
            "selected_model_source",
        }
    )
    call = call.model_copy(
        update={
            "diagnostic": call.diagnostic.model_copy(
                update=selection_diagnostic_fields
            )
        }
    )
    if call.rejection_code is not None:
        return _rejected_outcome(
            state,
            code=call.rejection_code,
            reason=call.rejection_reason or "model response was rejected safely",
            original_action_type=call.original_action_type,
            inference=call.diagnostic,
        )
    return validate_model_action(root, state, call.action, call.diagnostic)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preflight", "model"])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "preflight":
        result = preflight(
            root,
            Path(os.environ["GITHUB_EVENT_PATH"]) if os.getenv("GITHUB_EVENT_PATH") else None,
            os.getenv("GITHUB_EVENT_NAME", "schedule"),
        )
    else:
        decision = None
        if args.preflight and args.preflight.exists():
            decision = PreflightDecision.model_validate_json(args.preflight.read_text())
        outcome = run_model(root, decision)
        result = outcome.action.model_dump(mode="json", by_alias=True)
        github_output = os.getenv("GITHUB_OUTPUT")
        if github_output:
            inference = outcome.diagnostic.inference
            with Path(github_output).open("a", encoding="utf-8") as handle:
                handle.write(
                    f"completed_inference_calls={inference.completed_inference_calls}\n"
                )
                handle.write(
                    "failed_after_request_calls="
                    f"{inference.failed_after_request_calls}\n"
                )
                handle.write(f"http_failed_calls={inference.http_failed_calls}\n")
                handle.write(
                    "response_validation_failed_calls="
                    f"{inference.response_validation_failed_calls}\n"
                )
        if args.diagnostics:
            args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
            args.diagnostics.write_text(
                outcome.diagnostic.model_dump_json(indent=2) + "\n"
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"mode": args.mode, "completed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
