"""Native Stirrup client integration at the HTTP boundary; no paid API calls."""

import asyncio
import json

import httpx
from pydantic import BaseModel
import pytest
from stirrup.core.models import TextBlock, Tool, ToolResult, UserMessage, tool_call_blocks

from agent_bench.contracts import AttemptLimit, InfrastructureError
from agent_bench.providers import CannedProvider, CompatibleProvider, load_models


def model_config():
    return {
        "endpoint": "https://model.example/v1",
        "id": "version-a",
        "api_key_env": "PILOT_KEY",
        "max_tokens": 8192,
        "context_window_tokens": 128000,
    }


def install_transport(monkeypatch, handler):
    monkeypatch.setenv("PILOT_KEY", "credential-not-to-be-persisted")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))


def test_native_client_preserves_tool_calls_usage_and_structured_refusal(monkeypatch):
    requests = []
    payloads = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "edit",
                                    "type": "function",
                                    "function": {
                                        "name": "write_file",
                                        "arguments": '{"path":"answer.py","content":"pass"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 7,
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
            },
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "refusal": "I decline this task."},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
    )

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=next(payloads))

    install_transport(monkeypatch, handler)

    class WriteParams(BaseModel):
        path: str
        content: str

    tools = {
        "write_file": Tool(
            name="write_file",
            description="Write a file",
            parameters=WriteParams,
            executor=lambda _: ToolResult(content="written"),
        )
    }

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            reply = await provider.generate([UserMessage(content="task")], tools)
            assert tool_call_blocks(reply.blocks)[0].name == "write_file"
            assert reply.metadata["usage"] == {
                "prompt_tokens": 13,
                "completion_tokens": 7,
                "completion_tokens_details.reasoning_tokens": 4,
            }
            assert reply.token_usage.answer == 3
            assert reply.token_usage.reasoning == 4
            refused = await provider.generate([UserMessage(content="task")], tools)
            assert refused.metadata["provider_refusal"]
            assert refused.blocks == [TextBlock(text="I decline this task.")]
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    sent = json.loads(requests[0].content)
    assert sent["model"] == "version-a"
    assert sent["max_completion_tokens"] == 8192
    assert sent["tools"][0]["function"]["name"] == "write_file"
    assert requests[0].headers["authorization"] == "Bearer credential-not-to-be-persisted"


@pytest.mark.parametrize(
    ("status", "payload", "refusal"),
    [
        (400, {"error": {"code": "content_policy_violation"}}, True),
        (503, {"error": {"message": "credential-not-to-be-persisted"}}, False),
    ],
)
def test_policy_blocks_are_distinct_from_provider_errors_and_never_retried(
    monkeypatch, status, payload, refusal
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    install_transport(monkeypatch, handler)

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            if refusal:
                message = await provider.generate([UserMessage(content="task")], {})
                assert message.metadata["provider_refusal"]
            else:
                with pytest.raises(InfrastructureError, match=f"Provider HTTP {status}") as error:
                    await provider.generate([UserMessage(content="task")], {})
                assert "credential-not-to-be-persisted" not in str(error.value)
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status", "payload", "limit"),
    [
        (400, {"error": {"code": "context_length_exceeded"}}, "context"),
        (
            200,
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Incomplete answer"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 13, "completion_tokens": 7},
            },
            "output token",
        ),
    ],
)
def test_native_token_exhaustion_is_a_model_limit_without_retry(
    monkeypatch, status, payload, limit
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    install_transport(monkeypatch, handler)

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            with pytest.raises(AttemptLimit, match=f"Model {limit} limit exhausted"):
                await provider.generate([UserMessage(content="task")], {})
            assert not provider.last_refusal
            assert provider.last_usage == payload.get("usage", {})
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert len(requests) == 1


def test_redirect_does_not_forward_credentials(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://other.example/v1"})

    install_transport(monkeypatch, handler)

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            with pytest.raises(InfrastructureError, match="Provider HTTP 307"):
                await provider.generate([UserMessage(content="task")], {})
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert len(requests) == 1
    assert requests[0].url.host == "model.example"


def test_transport_failure_is_not_refusal_or_secret_disclosure(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ConnectError("network unavailable credential-not-to-be-persisted")

    install_transport(monkeypatch, handler)

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            with pytest.raises(InfrastructureError, match="failed or timed out") as error:
                await provider.generate([UserMessage(content="task")], {})
            assert "credential-not-to-be-persisted" not in str(error.value)
            assert not provider.last_refusal
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert len(requests) == 1


def test_large_response_stops_before_reading_entire_body(monkeypatch):
    chunks_read = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(8):
                chunks_read.append(index)
                yield b"x" * (1024 * 1024)

    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=Body()))

    async def scenario():
        provider = CompatibleProvider(model_config())
        try:
            with pytest.raises(InfrastructureError, match="response-size limit"):
                await provider.generate([UserMessage(content="task")], {})
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert chunks_read == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "extra",
    [
        'endpoint="https://user:password@api.example/v1"',
        "max_tokens=0\ncontext_window_tokens=128000",
        "max_tokens=9000\ncontext_window_tokens=8000",
    ],
)
def test_model_config_rejects_credentials_and_invalid_token_budgets(tmp_path, extra):
    path = tmp_path / "models.toml"
    endpoint = "" if extra.startswith("endpoint=") else 'endpoint="https://api.example/v1"\n'
    path.write_text('[models.a]\nid="v1"\napi_key_env="KEY"\n' + endpoint + extra + "\n")
    with pytest.raises(ValueError):
        load_models(path)


def test_canned_terminal_reply_uses_native_finish_and_keeps_refusal_metadata(tmp_path):
    cassette = tmp_path / "cassette.json"
    cassette.write_text(
        json.dumps({"tasks": {"task": [{"content": "Declined.", "provider_refusal": True}]}})
    )
    provider = CannedProvider({"cassette": str(cassette)})
    provider.start("task")
    message = asyncio.run(provider.generate([], {}))
    assert message.metadata["provider_refusal"]
    finish = tool_call_blocks(message.blocks)[0]
    assert finish.name == "finish"
    assert json.loads(finish.arguments) == {"reason": "Declined.", "paths": []}
