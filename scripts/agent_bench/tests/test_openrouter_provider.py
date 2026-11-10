"""OpenRouter's routing and multi-turn wire contracts, with no API calls."""

import asyncio
import json

import httpx
from pydantic import BaseModel
import pytest
from stirrup.core.models import Tool, ToolMessage, ToolResult, UserMessage, tool_call_blocks

from agent_bench.contracts import AttemptLimit, InfrastructureError
from agent_bench.providers import CompatibleProvider, load_models, make_provider


def config(**changes):
    return {
        "provider": "openrouter",
        "id": "vendor/model-version",
        "upstream_provider": "host/region",
        "context_window_tokens": 128000,
        "max_tokens": 8192,
        **changes,
    }


def transport(monkeypatch, payloads):
    requests = []
    replies = iter(payloads)

    def handler(request):
        requests.append(request)
        status, payload = next(replies)
        return httpx.Response(status, json=payload)

    monkeypatch.setenv("OPENROUTER_API_KEY", "private-openrouter-key")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda: httpx.MockTransport(handler))
    return requests


def test_pinned_openrouter_preserves_tool_continuation_identity_and_usage(monkeypatch):
    reasoning = [{"type": "reasoning.encrypted", "data": "opaque", "id": "r1"}]
    requests = transport(
        monkeypatch,
        [
            (
                200,
                {
                    "id": "gen-first",
                    "model": "vendor/model-version",
                    "provider": "Host",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "reasoning_details": reasoning,
                                "tool_calls": [
                                    {
                                        "id": "call-read",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"observation.txt"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                            "native_finish_reason": "tool_use",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "completion_tokens_details": {"reasoning_tokens": 10},
                        "cost": 0.002,
                    },
                },
            ),
            (
                200,
                {
                    "id": "gen-second",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Finished."},
                            "finish_reason": "stop",
                        }
                    ],
                },
            ),
        ],
    )

    class Read(BaseModel):
        path: str

    tools = {
        "read_file": Tool(
            name="read_file",
            description="Read the observation",
            parameters=Read,
            executor=lambda _: ToolResult(content="observation"),
        )
    }

    async def scenario():
        provider = make_provider(config())
        try:
            messages = [UserMessage(content="Read the observation.")]
            first = await provider.generate(messages, tools)
            assert first.metadata["provider_response"] == {
                "response_id": "gen-first",
                "resolved_model": "vendor/model-version",
                "upstream_provider": "Host",
                "finish_reason": "tool_calls",
                "native_finish_reason": "tool_use",
                "error_type": None,
            }
            assert first.metadata["usage"]["cost"] == 0.002
            assert first.metadata["usage"]["completion_tokens_details.reasoning_tokens"] == 10
            call = tool_call_blocks(first.blocks)[0]
            messages += [
                first,
                ToolMessage(name=call.name, tool_call_id=call.tool_call_id, content="observation"),
            ]
            second = await provider.generate(messages, tools)
            assert second.metadata["provider_response"]["resolved_model"] is None
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    for request in requests:
        sent = json.loads(request.content)
        assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
        assert sent["model"] == "vendor/model-version"
        assert sent["provider"] == {
            "only": ["host/region"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert sent["max_completion_tokens"] == 8192
        assert "models" not in sent
        assert request.headers["authorization"] == "Bearer private-openrouter-key"
    continued = json.loads(requests[1].content)["messages"][1]
    assert continued["reasoning_details"] == reasoning
    assert continued["tool_calls"][0]["function"]["name"] == "read_file"
    assert "provider_response" not in continued


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
def test_plain_reasoning_continuation_uses_the_returned_field(monkeypatch, field):
    requests = transport(
        monkeypatch,
        [
            (
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": "Ready", field: "context"}}
                    ]
                },
            ),
            (200, {"choices": [{"message": {"role": "assistant", "content": "Done"}}]}),
        ],
    )

    async def scenario():
        provider = CompatibleProvider(config())
        try:
            initial = UserMessage(content="task")
            first = await provider.generate([initial], {})
            await provider.generate([initial, first, UserMessage(content="continue")], {})
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assistant = json.loads(requests[1].content)["messages"][1]
    assert assistant[field] == "context"
    assert {key for key in assistant if key.startswith("reasoning")} == {field}


@pytest.mark.parametrize(
    ("status", "envelope", "error_type", "refusal", "fatal"),
    [
        (403, "root", "content_policy_violation", True, False),
        (200, "choice", "refusal", True, False),
        (403, "root", None, False, False),
        (403, "root", "permission_denied", False, True),
        (402, "root", "payment_required", False, True),
        (401, "root", "authentication", False, True),
        (400, "root", "invalid_request", False, True),
        (200, "root", "rate_limit_exceeded", False, False),
        (200, "choice", "provider_unavailable", False, False),
        (200, "empty-choice", None, False, False),
    ],
)
def test_error_envelopes_do_not_turn_infrastructure_into_model_outcomes(
    monkeypatch, status, envelope, error_type, refusal, fatal
):
    error = {
        "code": status if status != 200 else 503,
        "message": "private-openrouter-key must not be echoed",
        "metadata": {"error_type": error_type, "provider_name": "Host"},
    }
    payload = (
        {"error": error}
        if envelope == "root"
        else {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Partial output"},
                    "finish_reason": "error",
                    "error": error,
                }
            ]
        }
    )
    payload["usage"] = {"prompt_tokens": 20, "cost": 0.001}
    if envelope == "empty-choice":
        del payload["choices"][0]["error"]
    requests = transport(monkeypatch, [(status, payload)])

    async def scenario():
        provider = CompatibleProvider(config())
        try:
            if refusal:
                reply = await provider.generate([UserMessage(content="task")], {})
                assert reply.metadata["provider_refusal"]
                assert not tool_call_blocks(reply.blocks)
            else:
                with pytest.raises(InfrastructureError) as caught:
                    await provider.generate([UserMessage(content="task")], {})
                assert "private-openrouter-key" not in str(caught.value)
                assert not provider.last_refusal
            assert bool(provider.fatal_error) == fatal
            assert provider.last_usage == {"prompt_tokens": 20, "cost": 0.001}
            assert provider.last_response_metadata["error_type"] == error_type
            provider.start("next-task")
            assert provider.fatal_error is None
        finally:
            await provider.aclose()

    asyncio.run(scenario())
    assert len(requests) == 1


def test_openrouter_typed_context_limit_is_not_infrastructure(monkeypatch):
    transport(
        monkeypatch,
        [(400, {"error": {"code": 400, "metadata": {"error_type": "context_length_exceeded"}}})],
    )

    async def scenario():
        provider = CompatibleProvider(config())
        try:
            with pytest.raises(AttemptLimit, match="context limit"):
                await provider.generate([UserMessage(content="task")], {})
            assert provider.fatal_error is None
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "openrouter/auto"},
        {"id": "openrouter/auto:nitro"},
        {"id": "~vendor/latest"},
        {"id": ["vendor/a", "vendor/b"]},
        {"upstream_provider": ["host-a", "host-b"]},
        {"upstream_provider": ""},
        {"allow_fallbacks": True},
        {"endpoint": "https://other.example/v1"},
    ],
)
def test_openrouter_configuration_requires_one_model_and_host(tmp_path, changes):
    # Direct construction follows the same public configuration gate as TOML loading.
    with pytest.raises(ValueError):
        CompatibleProvider(config(**changes))


def test_openrouter_toml_defaults_and_custom_environment_variable(tmp_path, monkeypatch):
    path = tmp_path / "models.toml"
    path.write_text(
        '[models.mine]\nprovider="openrouter"\nid="vendor/model-version"\n'
        'upstream_provider="host/region"\nmax_tokens=8192\ncontext_window_tokens=128000\n'
    )
    models = load_models(path)
    assert models["mine"]["endpoint"] == "https://openrouter.ai/api/v1"
    assert models["mine"]["api_key_env"] == "OPENROUTER_API_KEY"
    monkeypatch.setenv("CUSTOM_ROUTER_KEY", "custom-key")
    provider = make_provider(config(api_key_env="CUSTOM_ROUTER_KEY"))
    assert provider.secret == "custom-key"


def test_prose_refusal_remains_unconfirmed(monkeypatch):
    transport(
        monkeypatch,
        [(200, {"choices": [{"message": {"role": "assistant", "content": "I decline."}}]})],
    )

    async def scenario():
        provider = CompatibleProvider(config())
        try:
            reply = await provider.generate([UserMessage(content="task")], {})
            assert not reply.metadata["provider_refusal"]
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_content_filter_without_message_is_still_a_structured_refusal(monkeypatch):
    transport(monkeypatch, [(200, {"choices": [{"finish_reason": "content_filter"}]})])

    async def scenario():
        provider = CompatibleProvider(config())
        try:
            reply = await provider.generate([UserMessage(content="task")], {})
            assert reply.metadata["provider_refusal"]
            assert provider.fatal_error is None
        finally:
            await provider.aclose()

    asyncio.run(scenario())
