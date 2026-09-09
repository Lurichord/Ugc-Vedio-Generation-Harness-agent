"""The unified agent: one intake-style decide loop shared by every stage.

Stage differences are data, not code:

- ``TaskEnvelope`` names the current step (``agent`` / narrative ``format_id``);
  GenericAgent loads that step's markdown as the system prompt;
- a ``ToolTransport`` serves the stage's tools (stdio MCP subprocess, or an
  in-process registry until that stage grows its own MCP server);
- a ``CompletionSpec`` (built by the stage's controller, i.e. the harness)
  describes the artifact refs and StatePatch proposal of a completed run;
- the model either calls a whitelisted tool or sets ``tool`` to null and puts
  the candidate JSON in ``message``. ``*.submit_candidate`` is still accepted.

The loop never varies: budget gate -> decide -> optional tool -> append
JSON exchange -> repeat until a candidate is accepted. Critics and commits
stay outside, in the controllers. ``skill.activate`` is built into the loop
so a stage can load a markdown playbook before it chooses tool arguments.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field

from ..harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from ..shared.llm import _extract_json
from ..shared.models import StrictModel
from ..tools.mcp import StdioMCPServerConfig, forward_child_stderr
from ..tools.registry import ToolRegistry
from .instructions import system_prompt_for_task
from .skills import (
    SKILL_ACTIVATE,
    activate_agent_skill,
    list_agent_skills,
    skill_activate_spec,
)


class StepDecision(StrictModel):
    """One loop step: optional tool, or a candidate JSON in ``message``."""

    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class StepModel(Protocol):
    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision: ...


class EnvironmentToolModel:
    """Production step model: JSON ``decide()``, same shape as intake."""

    def __init__(self, generator: Any | None = None) -> None:
        self._generator = generator

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        generator = self._generator
        if generator is None:
            from ..shared.llm import StructuredLLM
            from ..shared.settings import LLMSettings

            generator = StructuredLLM(LLMSettings.from_environment(None, None))
            self._generator = generator
        system = ""
        rendered = messages
        if messages and messages[0].get("role") == "system":
            system = str(messages[0].get("content") or "")
            rendered = messages[1:]
        catalog = [
            {
                "name": item["name"],
                "description": item.get("description") or "",
                "parameters": item.get("parameters") or {},
            }
            for item in tools
        ]
        prompt = (
            "根据当前上下文决定下一步。"
            "从可用工具里选一个；事情做完了再提交。"
            "不要编造工具名。\n\n"
            f"可用工具：{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"{_render_messages(rendered)}"
        )
        return generator.generate(prompt, StepDecision, system=system or None)


_LOG_LOCK = threading.Lock()


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
    raw = os.environ.get("AGENT_TOOL_LOG") or os.environ.get("INTAKE_TOOL_LOG")
    if not raw:
        return
    path = Path(raw)
    with _LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")


def _log_tool(
    agent: str,
    event: str,
    name: str,
    *,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    parts = [f"[{agent}] {event} {name}"]
    if arguments is not None:
        parts.append(json.dumps(arguments, ensure_ascii=False, separators=(",", ":")))
    if duration_ms is not None:
        parts.append(f"{duration_ms}ms")
    if error:
        parts.append(f"error={error}")
    elif result is not None:
        if result.get("ok") is False and result.get("error"):
            parts.append(f"ok=false error={result['error']}")
        elif "ok" in result:
            parts.append(f"ok={result['ok']}")
        else:
            keys = [str(key) for key in result]
            if keys:
                shown = ",".join(keys[:8])
                extra = len(keys) - 8
                parts.append(f"keys={shown}" + (f"+{extra}" if extra > 0 else ""))
    _log(" ".join(parts))


def failed_result(
    task: TaskEnvelope,
    actions: list[ActionRecord],
    error: Exception,
) -> AgentResult:
    status = (
        "budget_exhausted"
        if "budget exhausted" in str(error).lower()
        else "failed"
    )
    return AgentResult(
        task_id=task.task_id,
        status=status,
        state_version_used=task.based_on_state_version,
        input_hash=task.input_hash,
        actions=actions,
        error=str(error),
    )


# ---------------------------------------------------------------------------
# Tool transports: where the tools live is invisible to the loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


class ToolChannel(Protocol):
    async def list_tools(self) -> dict[str, ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class McpToolTransport:
    """Tools served by a stdio MCP subprocess; configure runs on open."""

    def __init__(
        self,
        server: StdioMCPServerConfig,
        *,
        configure_tool: str | None = None,
        configure_payload: dict[str, Any] | None = None,
    ) -> None:
        self.server = server
        self.configure_tool = configure_tool
        self.configure_payload = configure_payload or {}

    @asynccontextmanager
    async def open(self) -> AsyncIterator["_McpChannel"]:
        with forward_child_stderr() as errlog:
            async with stdio_client(
                self.server.server_parameters(),
                errlog=errlog,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    channel = _McpChannel(session)
                    if self.configure_tool is not None:
                        listing = await channel.list_tools()
                        if self.configure_tool not in listing:
                            raise RuntimeError(
                                f"MCP server is missing tool: {self.configure_tool}"
                            )
                        await channel.call(
                            self.configure_tool, self.configure_payload
                        )
                    yield channel


class _McpChannel:
    def __init__(self, session: ClientSession) -> None:
        self.session = session
        self._specs: dict[str, ToolSpec] | None = None

    async def list_tools(self) -> dict[str, ToolSpec]:
        if self._specs is None:
            listing = await self.session.list_tools()
            specs: dict[str, ToolSpec] = {}
            for tool in listing.tools:
                input_schema = getattr(tool, "input_schema", None)
                if not isinstance(input_schema, dict):
                    raise RuntimeError(f"MCP tool {tool.name} has no schema")
                specs[tool.name] = ToolSpec(
                    name=tool.name,
                    description=getattr(tool, "description", None) or tool.name,
                    parameters=input_schema,
                )
            self._specs = specs
        return self._specs

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.session.call_tool(name, arguments=arguments)
        if getattr(result, "is_error", False):
            reason = _mcp_result_text(result) or f"MCP tool failed: {name}"
            raise RuntimeError(reason)
        value = getattr(result, "structured_content", None)
        if not isinstance(value, dict):
            text = _mcp_result_text(result)
            value = json.loads(text) if text else None
        if isinstance(value, dict) and set(value) == {"result"}:
            value = value["result"]
        if not isinstance(value, dict):
            raise RuntimeError(f"MCP tool {name} returned no structured object")
        return value


_NO_ARGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "problems": {
            "type": "array",
            "items": {"type": "string"},
            "description": "上一轮产物需要修复的问题；首次调用留空。",
        }
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RegistryTool:
    """An in-process tool exposed to the unified loop.

    Bridge for stages whose capabilities have not moved into their own MCP
    server yet; the model sees exactly the same tool surface either way.
    """

    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict[str, Any] = dataclass_field(
        default_factory=lambda: dict(_NO_ARGS_SCHEMA)
    )


class RegistryToolTransport:
    def __init__(self, tools: list[RegistryTool]) -> None:
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("registry tool names must be unique")

    @asynccontextmanager
    async def open(self) -> AsyncIterator["_RegistryChannel"]:
        yield _RegistryChannel(self.tools)


class _RegistryChannel:
    def __init__(self, tools: dict[str, RegistryTool]) -> None:
        self.tools = tools

    async def list_tools(self) -> dict[str, ToolSpec]:
        return {
            name: ToolSpec(
                name=name,
                description=tool.description,
                parameters=tool.parameters,
            )
            for name, tool in self.tools.items()
        }

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise RuntimeError(f"unknown registry tool: {name}")
        value = tool.handler(**arguments)
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        raise RuntimeError(f"registry tool {name} returned no structured object")


# ---------------------------------------------------------------------------
# Stage wiring: harness-owned data describing one stage's contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompletionSpec:
    """What a completed AgentResult claims; built by the stage controller."""

    artifact_refs: list[ArtifactRef]
    state_patch: StatePatch
    evaluation_target: str


class GenericAgentExecution(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    candidate: Any | None = None
    result: AgentResult


class GenericAgent:
    """One agent for every stage; stage identity comes from the TaskEnvelope."""

    def __init__(
        self,
        *,
        tool_model: Any,
        transport_factory: Callable[[TaskEnvelope, dict[str, Any]], Any],
        candidate_type: type[BaseModel],
        completion_builder: Callable[[TaskEnvelope, dict[str, Any]], CompletionSpec],
        context_builder: Callable[[TaskEnvelope, dict[str, Any]], dict[str, Any]]
        | None = None,
        candidate_validator: Callable[[TaskEnvelope, BaseModel], None] | None = None,
        capability_tools: ToolRegistry | None = None,
    ) -> None:
        self.tool_model = _adapt_tool_model(tool_model)
        self.transport_factory = transport_factory
        self.candidate_type = candidate_type
        self.completion_builder = completion_builder
        self.context_builder = context_builder
        self.candidate_validator = candidate_validator
        # Inventory only: controllers copy these names into ProjectState.
        # The loop never executes this registry; tools run through the transport.
        self.tools = capability_tools or ToolRegistry()

    def run(self, task: TaskEnvelope, **kwargs: Any) -> GenericAgentExecution:
        return asyncio.run(self._run(task, kwargs))

    async def _run(
        self,
        task: TaskEnvelope,
        kwargs: dict[str, Any],
    ) -> GenericAgentExecution:
        actions: list[ActionRecord] = []
        _log(f"[{task.agent}] start task={task.task_id} format={task.format_id}")
        submit_tool = _submit_tool_for(task)
        context = (
            self.context_builder(task, kwargs)
            if self.context_builder is not None
            else {}
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt_for_task(task),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"task": task.model_dump(mode="json"), **context},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        try:
            transport = self.transport_factory(task, kwargs)
            async with transport.open() as channel:
                discovered = await channel.list_tools()
                missing = set(task.allowed_tools) - set(discovered)
                if missing:
                    raise RuntimeError(
                        f"tool transport is missing tools: {sorted(missing)}"
                    )
                catalog = [
                    {
                        "name": name,
                        "description": discovered[name].description,
                        "parameters": discovered[name].parameters,
                    }
                    for name in task.allowed_tools
                ]
                allowed = set(task.allowed_tools)
                if list_agent_skills(task.agent):
                    catalog.insert(0, skill_activate_spec(task.agent))
                    allowed.add(SKILL_ACTIVATE)
                candidate: BaseModel | None = None
                steps = 0
                while candidate is None:
                    if steps >= task.budget.max_steps:
                        raise RuntimeError("task step budget exhausted")
                    steps += 1
                    _log(f"[{task.agent}] step={steps} deciding")
                    decision = self.tool_model.decide(messages, catalog)
                    tool_name = (decision.tool or "").strip()
                    if not tool_name:
                        candidate = self._accept_message_candidate(
                            task, decision.message, messages
                        )
                        continue
                    if tool_name not in allowed:
                        raise PermissionError(
                            f"model selected unavailable tool: {tool_name}"
                        )
                    arguments = dict(decision.arguments or {})
                    _log_tool(task.agent, "call", tool_name, arguments=arguments)
                    started = time.perf_counter()
                    try:
                        if tool_name == SKILL_ACTIVATE:
                            value = activate_agent_skill(
                                task.agent, str(arguments.get("name") or "")
                            )
                        else:
                            value = await channel.call(tool_name, arguments)
                        if tool_name == submit_tool:
                            parsed = self.candidate_type.model_validate(value)
                            _validate_candidate(task, parsed)
                            if self.candidate_validator is not None:
                                self.candidate_validator(task, parsed)
                            candidate = parsed
                    except Exception as exc:
                        _feed_tool_failure(
                            task=task,
                            actions=actions,
                            messages=messages,
                            tool_name=tool_name,
                            arguments=arguments,
                            started=started,
                            exc=exc,
                        )
                        continue
                    actions.append(
                        _action(task, actions, tool_name, "success", None, started)
                    )
                    _log_tool(
                        task.agent,
                        "done",
                        tool_name,
                        arguments=arguments,
                        result=value,
                        duration_ms=round(
                            (time.perf_counter() - started) * 1000
                        ),
                    )
                    _append_exchange(messages, tool_name, arguments, value)
                    if tool_name == submit_tool:
                        _log(f"[{task.agent}] candidate submitted")
        except Exception as exc:
            return GenericAgentExecution(
                result=failed_result(task, actions, _unwrap_exception(exc)),
            )
        completion = self.completion_builder(task, kwargs)
        return GenericAgentExecution(
            candidate=candidate,
            result=AgentResult(
                task_id=task.task_id,
                status="completed",
                state_version_used=task.based_on_state_version,
                input_hash=task.input_hash,
                actions=actions,
                artifact_refs=completion.artifact_refs,
                state_patch=completion.state_patch,
                evaluation_target=completion.evaluation_target,
            ),
        )

    def _accept_message_candidate(
        self,
        task: TaskEnvelope,
        message: str,
        messages: list[dict[str, Any]],
    ) -> BaseModel | None:
        try:
            parsed = self.candidate_type.model_validate(_extract_json(message))
            _validate_candidate(task, parsed)
            if self.candidate_validator is not None:
                self.candidate_validator(task, parsed)
        except Exception as exc:
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"tool": None, "message": message},
                        ensure_ascii=False,
                    ),
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": json.dumps(
                        {"error": str(exc)},
                        ensure_ascii=False,
                    ),
                }
            )
            return None
        _log(f"[{task.agent}] candidate accepted from message")
        return parsed


def _feed_tool_failure(
    *,
    task: TaskEnvelope,
    actions: list[ActionRecord],
    messages: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    started: float,
    exc: BaseException,
) -> None:
    actions.append(
        _action(task, actions, tool_name, "failed", str(exc), started)
    )
    _log_tool(
        task.agent,
        "failed",
        tool_name,
        arguments=arguments,
        error=str(exc),
        duration_ms=round((time.perf_counter() - started) * 1000),
    )
    _append_exchange(messages, tool_name, arguments, {"error": str(exc)})


def _action(
    task: TaskEnvelope,
    actions: list[ActionRecord],
    tool: str,
    result: str,
    reason: str | None,
    started: float,
) -> ActionRecord:
    return ActionRecord(
        action_id=f"{task.task_id}:action:{len(actions) + 1}",
        agent=task.agent,
        task_id=task.task_id,
        tool=tool,
        result=result,  # type: ignore[arg-type]
        reason=reason,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


def _submit_tool_for(task: TaskEnvelope) -> str:
    candidates = [
        name for name in task.allowed_tools if name.endswith(".submit_candidate")
    ]
    if len(candidates) != 1:
        raise ValueError(
            "TaskEnvelope.allowed_tools must contain exactly one "
            f"*.submit_candidate tool, found: {candidates}"
        )
    return candidates[0]


def _validate_candidate(task: TaskEnvelope, candidate: BaseModel) -> None:
    missing = [
        name
        for name in task.required_outputs
        if hasattr(candidate, name) and getattr(candidate, name) is None
    ]
    if missing:
        raise ValueError(f"submitted candidate is missing outputs: {missing}")


def _unwrap_exception(exc: Exception) -> Exception:
    """Surface the root cause when anyio wraps errors in an ExceptionGroup."""

    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        inner = exc.exceptions[0]
        if not isinstance(inner, Exception):
            break
        exc = inner
    return exc


def _append_exchange(
    messages: list[dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> None:
    messages.append(
        {
            "role": "assistant",
            "content": json.dumps(
                {"tool": tool_name, "arguments": arguments},
                ensure_ascii=False,
            ),
        }
    )
    messages.append(
        {
            "role": "user",
            "content": json.dumps(
                {"tool": tool_name, "result": result},
                ensure_ascii=False,
            ),
        }
    )


def _adapt_tool_model(model: Any) -> StepModel:
    if callable(getattr(model, "decide", None)):
        return model
    if callable(getattr(model, "generate", None)):
        return EnvironmentToolModel(generator=model)
    raise TypeError(
        "tool_model must provide decide(messages, tools) or generate(prompt, type)"
    )


def _render_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in messages:
        role = item.get("role", "user")
        content = item.get("content") or ""
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _mcp_result_text(result: object) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        value = getattr(item, "text", None)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)
