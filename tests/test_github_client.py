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


def test_model_usage_counts_only_successful_inference_markers():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/agent.yml/runs"):
            return httpx.Response(200, json={"workflow_runs": [{"id": 1}, {"id": 2}]})
        if request.url.path.endswith("/actions/runs/1/jobs"):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "name": "model",
                            "status": "completed",
                            "conclusion": "success",
                            "steps": [
                                {"name": "Confirm inference call 1", "conclusion": "success"},
                                {"name": "Confirm inference call 2", "conclusion": "skipped"},
                                    {
                                        "name": "Mark response validation failed inference call 1",
                                        "conclusion": "success",
                                    },
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "name": "model",
                        "status": "completed",
                        "conclusion": "skipped",
                        "steps": [],
                    }
                ]
            },
        )

    client = GitHubClient("fake", "owner/repo", transport=httpx.MockTransport(handler))
    assert client.model_usage_today() == {
        "completed_inference_calls": 1,
        "reserved_inference_calls": 0,
        "failed_after_request_calls": 1,
        "http_failed_calls": 0,
        "response_validation_failed_calls": 1,
        "skipped_runs": 1,
    }
