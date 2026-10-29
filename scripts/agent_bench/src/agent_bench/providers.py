"""Stirrup model clients; API credentials remain on the controller."""

import json
import math
import os
from pathlib import Path
import tomllib
import urllib.parse

import httpx
from openai import APIError
from stirrup.clients.chat_completions_client import ChatCompletionsClient
from stirrup.core.exceptions import (
    CompactionRequiredError,
    ContextOverflowError,
    OutputTokenLimitError,
)
from stirrup.core.models import AssistantMessage, TextBlock, TokenUsage, ToolCall

from .contracts import AttemptLimit, InfrastructureError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
POLICY_CODES = {"content_filter", "content_policy_violation", "safety_violation"}


def _token_usage(data: dict, prefix: str = "") -> dict:
    result = {}
    for key, value in data.items():
        name = prefix + str(key)
        if isinstance(value, dict):
            result.update(_token_usage(value, name + "."))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(value) and value >= 0:
                result[name] = value
    return result


def _validate_token_limits(config: dict) -> None:
    for name in ("max_tokens", "context_window_tokens"):
        value = config.get(name)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be an explicit positive integer")
    if config["max_tokens"] > config["context_window_tokens"]:
        raise ValueError("max_tokens must not exceed context_window_tokens")


def load_models(path: Path) -> dict:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    models = data.get("models", {})
    if not models:
        raise ValueError("Configuration must contain at least one [models.ALIAS] table")
    for alias, config in models.items():
        if not isinstance(config, dict):
            raise ValueError(f"{alias}: expected a model configuration table")
        if not alias or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in alias
        ):
            raise ValueError(
                "Model aliases may contain only letters, digits, underscores and hyphens"
            )
        kind = config.get("provider", "openai-compatible")
        if kind == "canned":
            cassette = Path(config["cassette"])
            config["cassette"] = str((path.parent / cassette).resolve())
            config.setdefault("id", "canned-not-a-model")
            config.setdefault("endpoint", "canned://local")
        elif kind == "openai-compatible":
            endpoint = config.get("endpoint", "")
            parsed = urllib.parse.urlsplit(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{alias}: provide an explicit HTTP(S) API endpoint")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Do not embed credentials, queries, or fragments in API endpoints")
            if not config.get("id") or not config.get("api_key_env"):
                raise ValueError(f"{alias}: model id and api_key_env are required")
            _validate_token_limits(config)
        else:
            raise ValueError(f"Unsupported provider: {kind}")
        config["alias"] = alias
    return models


class CompatibleProvider:
    """Native Stirrup client preserving transport limits and refusal evidence."""

    def __init__(self, config: dict):
        _validate_token_limits(config)
        self.config = config
        self.secret = os.environ.get(config["api_key_env"], "")
        if not self.secret:
            raise ValueError(f"Set the environment variable {config['api_key_env']} first")
        self._client = None
        self.start("")

    @property
    def model_slug(self):
        return self.config["id"]

    @property
    def context_window_tokens(self):
        return self.config["context_window_tokens"]

    def start(self, task_id: str) -> None:
        self.last_refusal = False
        self.last_usage = {}
        self._wire = {}
        self._response_error = None

    async def _capture_response(self, response):
        chunks = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                self._response_error = "Provider response exceeded the response-size limit"
                raise InfrastructureError(self._response_error)
            chunks.append(chunk)
        raw = b"".join(chunks)
        # A response hook must retain the bounded body for the SDK's normal parser.
        response._content = raw
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if isinstance(data, dict):
            self._wire = data
            usage = data.get("usage") or {}
            self.last_usage = _token_usage(usage) if isinstance(usage, dict) else {}

    async def _native_client(self):
        if self._client is None:
            client = ChatCompletionsClient(
                model=self.model_slug,
                base_url=self.config["endpoint"],
                api_key=self.secret,
                max_tokens=self.config["max_tokens"],
                context_window_tokens=self.context_window_tokens,
                reasoning_effort=self.config.get("reasoning"),
                timeout=600,
                max_retries=0,
            )
            # Stirrup's pinned constructor has no HTTP-client parameter. Preserve
            # its native generation/conversion and replace only the SDK transport.
            sdk = client._client
            await sdk.close()
            client._client = sdk.with_options(
                http_client=httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(),
                    follow_redirects=False,
                    trust_env=False,
                    event_hooks={"response": [self._capture_response]},
                )
            )
            self._client = client
        return self._client

    async def generate(self, messages, tools):
        self.start("")
        client = await self._native_client()
        try:
            message = await client.generate(messages, tools)
        except (ContextOverflowError, CompactionRequiredError):
            raise AttemptLimit("Model context limit exhausted") from None
        except OutputTokenLimitError:
            raise AttemptLimit("Model output token limit exhausted") from None
        except APIError as exc:
            error = self._wire.get("error", {})
            if isinstance(error, dict) and error.get("code") in POLICY_CODES:
                message = AssistantMessage(
                    blocks=[TextBlock(text="Provider explicitly blocked this request.")]
                )
                self.last_refusal = True
            else:
                detail = self._response_error or (
                    f"Provider HTTP {exc.status_code}"
                    if hasattr(exc, "status_code")
                    else "Provider request failed or timed out"
                )
                raise InfrastructureError(detail) from None
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise InfrastructureError("Provider returned an unsupported response shape") from None
        choices = self._wire.get("choices", [])
        if choices and isinstance(choices[0], dict):
            first = choices[0]
            content = first.get("message") or {}
            refusal = content.get("refusal") if isinstance(content, dict) else None
            self.last_refusal |= bool(refusal) or first.get("finish_reason") == "content_filter"
            if self.last_refusal and not message.blocks:
                message.blocks = [
                    TextBlock(text=refusal or "Provider explicitly blocked this request.")
                ]
        usage = self._wire.get("usage") or {}
        self.last_usage = _token_usage(usage) if isinstance(usage, dict) else {}
        message.metadata.update(provider_refusal=self.last_refusal, usage=self.last_usage)
        return message

    async def aclose(self):
        if self._client is not None:
            await self._client._client.close()
            self._client = None


class CannedProvider:
    """Replay fixtures through the real Stirrup agent loop, without model APIs."""

    secret = ""
    context_window_tokens = 128_000

    def __init__(self, config: dict):
        self.config = config
        self.data = json.loads(Path(config["cassette"]).read_text(encoding="utf-8"))
        self.replies = iter(())
        self._round = 0

    @property
    def model_slug(self):
        return self.config.get("id", "canned-not-a-model")

    def start(self, task_id: str) -> None:
        try:
            self.replies = iter(self.data["tasks"][task_id])
            self._round = 0
        except KeyError:
            raise InfrastructureError(f"No canned replies for {task_id}") from None

    async def generate(self, messages, tools):
        self._round += 1
        item = next(self.replies, {"content": "Canned attempt complete."})
        content = item.get("content", "")
        blocks = [TextBlock(text=content)] if content else []
        calls = item.get("tool_calls") or []
        if not calls:
            calls = [{"name": "finish", "arguments": {"reason": content, "paths": []}}]
        for index, call in enumerate(calls):
            arguments = call.get("arguments", {})
            blocks.append(
                ToolCall(
                    name=call["name"],
                    arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
                    tool_call_id=call.get("id", f"canned-{self._round}-{index}"),
                )
            )
        self.last_refusal = bool(item.get("provider_refusal", False))
        self.last_usage = _token_usage(item.get("usage") or {})
        prompt = int(self.last_usage.get("prompt_tokens", 0))
        completion = int(self.last_usage.get("completion_tokens", 0))
        reasoning = int(self.last_usage.get("completion_tokens_details.reasoning_tokens", 0))
        return AssistantMessage(
            blocks=blocks,
            token_usage=TokenUsage(
                input=prompt, answer=max(0, completion - reasoning), reasoning=reasoning
            ),
            metadata={"provider_refusal": self.last_refusal, "usage": self.last_usage},
        )

    async def aclose(self):
        pass


def make_provider(config: dict):
    kind = config.get("provider", "openai-compatible")
    if kind == "canned":
        return CannedProvider(config)
    if kind != "openai-compatible":
        raise ValueError(f"Unsupported provider: {kind}")
    return CompatibleProvider(config)
