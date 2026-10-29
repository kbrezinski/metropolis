"""Pinned Stirrup agent orchestration over benchmark-owned Docker tools."""

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


def engine_metadata(provider) -> dict:
    return {
        "name": "stirrup",
        "version": version("stirrup"),
        "config": {
            "tools": ["read_file", "write_file", "run_command", "finish"],
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


class _ReadFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str


class _WriteFile(_ReadFile):
    content: str


class _RunCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str
    timeout: float | None = None


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
        self.engine.event("engine_message", message=message.model_dump(mode="json"))

    def tool_result(self, message):
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
    """Stirrup owns the loop; callbacks retain Docker execution and evidence limits."""

    def __init__(self, provider, dispatch, result, event, clean, deadline):
        self.provider = provider
        self.dispatch = dispatch
        self.result = result
        self.event = event
        self.clean = clean
        self.deadline = deadline
        self.active_boundary = "provider"

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
            reply = await asyncio.wait_for(
                self.provider.generate(messages, tools), timeout=self.remaining()
            )
        except TimeoutError:
            self.event("model_error", error="Attempt deadline exceeded")
            raise AttemptLimit(
                "Attempt wall-clock budget exhausted during provider request"
            ) from None
        except (AttemptLimit, InfrastructureError) as exc:
            usage = getattr(self.provider, "last_usage", {})
            self.record_usage(usage)
            self.event("model_error", error=str(exc), usage=usage)
            raise
        self.remaining()  # A late response must never dispatch tools.
        reply = AssistantMessage.model_validate_json(self.clean(reply.model_dump_json()))
        usage = reply.metadata.get("usage", reply.token_usage.model_dump())
        self.record_usage(usage)
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
            tool(
                "run_command",
                "Run a shell command in /workspace with a bounded timeout.",
                _RunCommand,
            ),
        ]
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
            finish, _, _ = await session.run(prompt)
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
