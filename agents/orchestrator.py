from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agents.context_builder import build_context
from agents.github_client import GitHubClient
from agents.github_models import GitHubModelsClient, safe_no_op
from agents.lifecycle import (
    action_allowed,
    allowed_actions,
    validate_action_transition,
)
from agents.preflight import build_preflight_decision, usage_allows_run
from agents.safety import validate_evidence_references
from agents.schemas import (
    ActionEnvelope,
    ActionRejectionCode,
    ActionType,
    CompanyState,
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
    event: dict = {}
    if event_path and event_path.exists():
        loaded = json.loads(event_path.read_text())
        event = loaded if isinstance(loaded, dict) else {}
    issue = event.get("issue") if isinstance(event.get("issue"), dict) else {}
    comment = event.get("comment") if isinstance(event.get("comment"), dict) else {}
    issue_ids = [issue["id"]] if isinstance(issue.get("id"), int) else []
    comment_ids = [comment["id"]] if isinstance(comment.get("id"), int) else []
    metrics = (root / "company/metrics.json").read_bytes()
    strategy = json.loads((root / "company/strategy.json").read_text())
    now = datetime.now(UTC)
    review = strategy["review"]
    daily_due = checkpoint.last_daily_review != now.date() and now.hour >= int(review["daily_hour"])
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
        comment_ids=comment_ids,
        product_sha=os.getenv("PRODUCT_SHA"),
        metrics_hash=hashlib.sha256(metrics).hexdigest(),
        due_experiment=False,
        daily_review_due=daily_due,
        weekly_review_due=weekly_due,
        manual=event_name == "workflow_dispatch",
        min_new_signals=int(
            os.getenv("MIN_NEW_SIGNALS_FOR_ANALYSIS", evidence["min_new_signals_for_analysis"])
        ),
        strong_evidence_threshold=float(
            os.getenv("STRONG_EVIDENCE_THRESHOLD", evidence["strong_evidence_threshold"])
        ),
    )
    token = os.getenv("GITHUB_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
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
    if token and repository:
        try:
            usage = GitHubClient(token, repository).model_usage_today()
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
    if decision.should_call_model and not allowed:
        decision.should_call_model = False
        decision.blocked_reason = (
            "daily inference limit would be exceeded: "
            f"{completed} completed + {active} active reservations + "
            f"{required_calls} required > limit {limit}"
        )
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
    payload = {
        "orchestration_policy": {
            "lifecycle_stage": state.lifecycle_stage.value,
            "allowed_action_types": [item.value for item in permitted],
            "preferred_action_types": [item.value for item in preferred],
            "trigger_reasons": [item.value for item in reasons],
            "new_signal_ids": decision.new_signal_ids if decision else [],
            "repository_counts": counts,
            "guidance": guidance,
            "state_transition_policy": transition_policy,
        }
    }
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
    try:
        validate_evidence_references(action, root)
    except ValueError:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.EVIDENCE_REFERENCE_REJECTED,
            reason="one or more evidence_ids do not exist in stored signal records",
            original_action_type=action.action_type,
            inference=inference,
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
    selection = client.select_chat_model(catalog)
    if not selection:
        return _rejected_outcome(
            state,
            code=ActionRejectionCode.NO_COMPATIBLE_MODEL,
            reason="no compatible text model is available",
            failure_stage=FailureStage.MODEL_SELECTION,
        )
    if diagnostic_mode:
        messages = [
            {
                "role": "system",
                "content": "Verify the response pipeline with the exact diagnostic JSON.",
            }
        ]
    else:
        prompt = (root / "agents/prompts/core.md").read_text()
        context = build_context(root)
        instruction = build_model_instruction(root, state, decision)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "system", "content": instruction},
            {"role": "user", "content": context},
        ]
    call = client.chat_action(
        model=selection.selected_model,
        request_mode=selection.request_mode,
        diagnostic_mode=diagnostic_mode,
        messages=messages,
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
