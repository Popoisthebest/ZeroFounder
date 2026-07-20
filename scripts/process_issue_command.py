from __future__ import annotations

import json
import os
from pathlib import Path

from agents.approval import parse_approval_command
from agents.github_client import GitHubClient
from agents.schemas import DependencyProposal

DEPENDENCY_MARKER = "<!-- zerofounder-dependency-proposal -->"


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
    command = parse_approval_command(str(comment.get("body", "")))
    labels = {item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)}
    valid_issue = not issue.get("pull_request") and bool(
        labels & {"founder-approval", "requires-approval"}
    )
    client = GitHubClient(os.environ["GITHUB_TOKEN"], os.environ["GITHUB_REPOSITORY"])
    dependency = (
        dependency_from_body(str(issue.get("body", ""))) if "tool-request" in labels else None
    )
    kind = "dependency" if dependency else "lifecycle"
    valid = bool(command and valid_issue and client.has_write_permission(actor))
    payload = {
        "valid": valid,
        "kind": kind if valid else None,
        "command": command if valid else None,
        "actor": actor if valid else None,
        "issue_number": issue.get("number") if valid else None,
        "comment_id": comment.get("id") if valid else None,
        "dependency_proposal": dependency.model_dump(mode="json") if valid and dependency else None,
    }
    Path("runtime").mkdir(exist_ok=True)
    Path("runtime/approval-command.json").write_text(json.dumps(payload) + "\n")
    if valid:
        client.comment(
            int(issue["number"]), f"ZeroFounder accepted `/{command}` for rule-based processing."
        )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"valid={str(valid).lower()}\n")
            handle.write(f"kind={kind if valid else 'none'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
