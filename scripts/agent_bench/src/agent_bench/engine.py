"""Pinned Stirrup orchestration with file-only authoring tools."""

import asyncio
from importlib.metadata import version
import json
import math
import time

from pydantic import BaseModel, ConfigDict
from stirrup import Agent, Tool, ToolResult
from stirrup.core.models import AssistantMessage, joined_text, tool_call_blocks
from stirrup.utils.logging import AgentLoggerBase

from .contracts import AttemptLimit, InfrastructureError


def engine_metadata(provider, *, staged: bool = False) -> dict:
    metadata = {
        "name": "stirrup",
        "version": version("stirrup"),
        "config": {
            "tools": [
                "read_file",
                "write_file",
                *(["submit_stage"] if staged else []),
                "finish",
            ],
            "tool_execution": "sequential",
            "default_tools": False,
            "subagents": False,
            "fallback": False,
            "api_retries": 0,
            "context_summarization": "disabled",
            "recover_from_context_overflow": False,
            "block_successive_assistant_messages": True,
            "turns_remaining_warning_threshold": 5,
            "resume": False,
            "max_tokens": provider.config.get("max_tokens", 8192),
            "context_window_tokens": provider.context_window_tokens,
            "dependencies": {name: version(name) for name in ("openai", "httpx", "pydantic")},
        },
    }
    if provider.config.get("endpoint", "").startswith(("http://", "https://")):
        metadata["config"]["provider"] = provider.config.get("provider", "openai-compatible")
        if provider.config.get("upstream_provider"):
            metadata["config"]["upstream_provider"] = provider.config["upstream_provider"]
        if provider.config.get("provider") == "openrouter":
            metadata["config"]["routing"] = {
                "only": [provider.config["upstream_provider"]],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
    return metadata


class _ReadFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str


class _WriteFile(_ReadFile):
    content: str


class _SubmitStage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ProviderRefusal(Exception):
    """Stop before executing tool calls attached to a structured refusal."""


class _TraceLogger(AgentLoggerBase):
    depth = 0

    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def _ignore(self, *args, **kwargs):
        pass

    on_step = assistant_message = task_message = _ignore
    context_summarization_start = context_summarization_complete = _ignore
    debug = info = warning = error = _ignore

    def user_message(self, message):
        self.engine.history.append(message)
        self.engine.event("engine_message", message=message.model_dump(mode="json"))

    def tool_result(self, message):
        self.engine.history.append(message)
        result = self.engine.result
        result["tool_calls"] += 1
        if message.name != "finish":
            result["generated"] = True
        self.engine.event(
            "tool_result",
            call_id=message.tool_call_id,
            name=message.name,
            result=message.content,
            success=message.success,
            arguments_valid=message.args_was_valid,
            duration_seconds=message.tool_duration,
        )


class AgentEngine:
    """Stirrup owns the loop; callbacks provide bounded file operations."""

    def __init__(self, provider, dispatch, result, event, clean, deadline, *, staged=False):
        self.provider = provider
        self.dispatch = dispatch
        self.result = result
        self.event = event
        self.clean = clean
        self.deadline = deadline
        self.active_boundary = "provider"
        self.staged = staged
        self.history = []

    @property
    def model_slug(self):
        return self.provider.model_slug

    @property
    def context_window_tokens(self):
        return self.provider.context_window_tokens

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AttemptLimit("Attempt wall-clock budget exhausted")
        return remaining

    def record_usage(self, usage):
        for key, value in usage.items():
            self.result["usage"][key] = self.result["usage"].get(key, 0) + value

    async def generate(self, messages, tools):
        self.active_boundary = "provider"
        self.history = list(messages)
        context = [message.model_dump(mode="json") for message in messages]
        if not self.result["rounds_used"]:
            self.event("initial_context", messages=context, task_hash=self.result["task_hash"])
        self.event(
            "model_request",
            messages=context,
            tools=[
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters.model_json_schema(),
                }
                for tool in tools.values()
            ],
        )
        started = time.monotonic()
        self.result["rounds_used"] += 1
        try:
            remaining = self.remaining()
            reply = await asyncio.wait_for(
                self.provider.generate(messages, tools), timeout=remaining
            )
        except TimeoutError:
            usage = getattr(self.provider, "last_usage", {})
            metadata = getattr(self.provider, "last_response_metadata", None)
            self.record_usage(usage)
            self.record_provider_response(metadata)
            self.event(
                "model_error",
                error="Attempt deadline exceeded",
                usage=usage,
                provider_response=metadata,
            )
            raise AttemptLimit(
                "Attempt wall-clock budget exhausted during provider request"
            ) from None
        except (AttemptLimit, InfrastructureError) as exc:
            usage = getattr(self.provider, "last_usage", {})
            self.record_usage(usage)
            self.record_provider_response(getattr(self.provider, "last_response_metadata", None))
            if getattr(self.provider, "fatal_error", None):
                self.result["model_error"] = self.clean(self.provider.fatal_error)
            self.event("model_error", error=str(exc), usage=usage)
            raise
        reply = AssistantMessage.model_validate_json(self.clean(reply.model_dump_json()))
        self.record_provider_response(reply.metadata.get("provider_response"))
        usage = reply.metadata.get("usage", reply.token_usage.model_dump())
        self.record_usage(usage)
        try:
            self.remaining()  # A late response must never dispatch tools.
        except AttemptLimit:
            self.event(
                "model_error",
                error="Provider response arrived after the attempt deadline",
                usage=usage,
                provider_response=reply.metadata.get("provider_response"),
            )
            raise
        self.history.append(reply)
        refusal = bool(reply.metadata.get("provider_refusal"))
        self.result["provider_refusal"] |= refusal
        self.result["final_response"] = joined_text(reply.blocks)
        self.event(
            "model_response",
            message=reply.model_dump(mode="json"),
            content=joined_text(reply.blocks),
            tool_calls=[call.model_dump(mode="json") for call in tool_call_blocks(reply.blocks)],
            provider_refusal=refusal,
            usage=usage,
            duration_seconds=time.monotonic() - started,
        )
        if refusal:
            raise _ProviderRefusal
        return reply

    def record_provider_response(self, metadata):
        if metadata:
            self.result.setdefault("provider_responses", []).append(
                json.loads(self.clean(json.dumps(metadata)))
            )

    async def _run(self, prompt, system_prompt, rounds):
        def tool(name, description, parameters):
            async def execute(params):
                self.active_boundary = "runtime"
                arguments = params.model_dump(exclude_none=True)
                output = self.dispatch(name, json.dumps(arguments), self.remaining())
                return ToolResult(content=json.dumps(output), success="tool_error" not in output)

            return Tool(name=name, description=description, parameters=parameters, executor=execute)

        tools = [
            tool("read_file", "Read a UTF-8 file relative to /workspace.", _ReadFile),
            tool("write_file", "Write a UTF-8 file relative to /workspace.", _WriteFile),
        ]
        if self.staged:
            tools.append(
                tool(
                    "submit_stage",
                    "Submit the current solution.sh and receive the next observation.",
                    _SubmitStage,
                )
            )
        agent = Agent(
            client=self,
            name="industrial_agent",
            max_turns=rounds,
            system_prompt=system_prompt,
            tools=tools,
            context_summarization_cutoff=math.inf,
            turns_remaining_warning_threshold=5,
            recover_from_context_overflow=False,
            block_successive_assistant_messages=True,
            logger=_TraceLogger(self),
        )
        # No framework cache, file collector, skills or execution backend is used.
        # The benchmark snapshots every artifact itself, including on failed runs.
        async with agent.session(
            resume=False, cache_on_interrupt=False, clear_cache_on_success=False
        ) as session:
            finish, history, _ = await session.run(prompt)
            if history:
                self.history = list(history[-1])
        if finish is None:
            self.result["stop_reason"] = "round_limit"
        else:
            self.result["final_response"] = self.clean(finish.reason)

    def run(self, prompt, system_prompt, rounds):
        async def execute():
            try:
                await self._run(prompt, system_prompt, rounds)
            except _ProviderRefusal:
                self.result["stop_reason"] = "provider_refusal"
            finally:
                await self.provider.aclose()

        asyncio.run(execute())
