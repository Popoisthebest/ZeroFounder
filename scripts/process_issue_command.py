from __future__ import annotations

import json
import os
from pathlib import Path

from agents.approval import AGENT_COMMANDS, is_bot_actor, parse_comment_command
from agents.github_client import GitHubClient
from agents.schemas import DependencyProposal

DEPENDENCY_MARKER = "<!-- zerofounder-dependency-proposal -->"
COMMAND_LABELS = {"founder-approval", "requires-approval", "tool-request", "agent-generated"}


def dependency_from_body(body: str) -> DependencyProposal | None:
    if DEPENDENCY_MARKER not in body:
        return None
    tail = body.split(DEPENDENCY_MARKER, 1)[1].strip()
    if not tail.startswith("```json") or "```" not in tail[7:]:
        return None
    raw = tail[7:].split("```", 1)[0].strip()
    try:
        return DependencyProposal.model_validate_json(raw)
    except ValueError:
        return None


def main() -> int:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    issue = event.get("issue", {})
    comment = event.get("comment", {})
    actor = str(comment.get("user", {}).get("login", ""))
    command = parse_comment_command(str(comment.get("body", "")))
    labels = {item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)}
    author_type = str(comment.get("user", {}).get("type", ""))
    bot_comment = author_type == "Bot" or is_bot_actor(actor)
    valid_issue = not issue.get("pull_request") and bool(
        labels & {"founder-approval", "requires-approval"}
    )
    valid_command_context = bool(labels & COMMAND_LABELS)
    skip_reason = None
    if bot_comment:
        skip_reason = "bot_comment"
    elif command is None:
        skip_reason = "unrecognized_comment_command"
    elif not valid_command_context:
        skip_reason = "unsupported_issue_context"
    client = (
        None
        if skip_reason in {"bot_comment", "unrecognized_comment_command"}
        else GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    )
    has_write_permission = bool(client and client.has_write_permission(actor))
    if skip_reason is None and not has_write_permission:
        skip_reason = "unauthorized_actor"
    dependency = (
        dependency_from_body(str(issue.get("body", ""))) if "tool-request" in labels else None
    )
    kind = (
        "agent"
        if command in AGENT_COMMANDS
        else "dependency"
        if command == "approve" and dependency
        else "lifecycle"
    )
    valid = bool(
        command
        and has_write_permission
        and (
            (kind == "agent" and valid_command_context)
            or (kind == "dependency" and valid_issue)
            or (kind == "lifecycle" and valid_issue)
        )
    )
    if not valid and skip_reason is None:
        skip_reason = "unsupported_issue_context"
    payload = {
        "valid": valid,
        "kind": kind if valid else None,
        "command": command if valid else None,
        "actor": actor if valid else None,
        "issue_number": issue.get("number") if valid else None,
        "comment_id": comment.get("id") if valid else None,
        "dependency_proposal": dependency.model_dump(mode="json") if valid and dependency else None,
        "skipped": not valid,
        "skip_reason": skip_reason if not valid else None,
    }
    Path("runtime").mkdir(exist_ok=True)
    Path("runtime/approval-command.json").write_text(json.dumps(payload) + "\n")
    if valid and kind != "agent" and client:
        client.comment(
            int(issue["number"]),
            f"ZeroFounder가 `/{command}` 명령을 확인했으며 규칙 기반으로 처리합니다.",
        )
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(
                "## ZeroFounder Issue 댓글 명령\n\n"
                f"- skipped: `{str(not valid).lower()}`\n"
                f"- skip_reason: `{skip_reason if not valid else 'none'}`\n"
                f"- command: `{command or 'none'}`\n"
                f"- kind: `{kind if valid else 'none'}`\n"
            )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"valid={str(valid).lower()}\n")
            handle.write(f"kind={kind if valid else 'none'}\n")
            handle.write(f"skipped={str(not valid).lower()}\n")
            handle.write(f"skip_reason={skip_reason if not valid else 'none'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
