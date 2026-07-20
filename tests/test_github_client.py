import httpx
import pytest

from agents.github_client import GitHubClient


def test_dispatch_payload_and_head_verification():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(204)
        return httpx.Response(200, json={"head": {"ref": "agent/1-test", "sha": "a" * 40}})

    client = GitHubClient("fake", "owner/repo", transport=httpx.MockTransport(handler))
    client.dispatch_quality_check(
        pr_number=1, agent_branch="agent/1-test", commit_sha="a" * 40, ref="main"
    )
    assert requests[0].url.path.endswith("/quality-check.yml/dispatches")
    assert client.verify_pull_head(pr_number=1, branch="agent/1-test", commit_sha="a" * 40)


def test_dispatch_rejects_untrusted_inputs():
    client = GitHubClient(
        "fake", "owner/repo", transport=httpx.MockTransport(lambda request: httpx.Response(204))
    )
    with pytest.raises(ValueError):
        client.dispatch_quality_check(
            pr_number=1, agent_branch="$(evil)", commit_sha="bad", ref="main"
        )
