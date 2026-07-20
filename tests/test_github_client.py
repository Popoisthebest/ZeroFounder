import httpx

from agents.github_client import GitHubClient
from agents.quality import classify_pull_target


def test_pull_request_target_verification_uses_exact_repository_branch_and_sha():
    pull = {
        "number": 1,
        "head": {
            "ref": "agent/1-test",
            "sha": "a" * 40,
            "repo": {"full_name": "owner/repo"},
        },
        "base": {"repo": {"full_name": "owner/repo"}},
    }
    status, verified_sha = classify_pull_target(
        pull, repository="owner/repo", branch="agent/1-test", commit_sha="a" * 40
    )
    assert status == "verified"
    assert verified_sha == "a" * 40


def test_pull_request_target_rejects_branch_mismatch():
    pull = {
        "number": 1,
        "head": {
            "ref": "agent/other",
            "sha": "a" * 40,
            "repo": {"full_name": "owner/repo"},
        },
        "base": {"repo": {"full_name": "owner/repo"}},
    }
    status, verified_sha = classify_pull_target(
        pull, repository="owner/repo", branch="agent/1-test", commit_sha="a" * 40
    )
    assert status == "sha_mismatch"
    assert verified_sha == "a" * 40


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
                            "name": "AI 의사결정 실행",
                            "status": "completed",
                            "conclusion": "success",
                            "steps": [
                                {
                                    "name": "모델 호출 1 확인 [inference-confirm-1]",
                                    "conclusion": "success",
                                },
                                {
                                    "name": "모델 호출 2 확인 [inference-confirm-2]",
                                    "conclusion": "skipped",
                                },
                                    {
                                        "name": (
                                            "응답 검증 실패 호출 1 기록 "
                                            "[inference-validation-failed-1]"
                                        ),
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
