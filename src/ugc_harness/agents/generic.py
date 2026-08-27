"""The unified agent: one LLM tool loop shared by every stage.

Stage differences are data, not code:

- ``TaskEnvelope`` carries the whitelist, budget, and injected instructions;
- a ``ToolTransport`` serves the stage's tools (stdio MCP subprocess, or an
  in-process registry until that stage grows its own MCP server);
- a ``CompletionSpec`` (built by the stage's controller, i.e. the harness)
  describes the artifact refs and StatePatch proposal of a completed run;
- the candidate submitted via the stage's ``*.submit_candidate`` tool is
  parsed with the stage's candidate schema.

The loop itself never varies: budget gate -> model chooses one tool ->
whitelist gate -> execute -> record ActionRecord -> refresh the execution
board view into messages -> repeat until the model submits. Critics and
commits stay outside, in the controllers.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field as dataclass_field
from typing import Any, AsyncIterator, Callable, Protocol

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict

from ..harness.models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    StatePatch,
    TaskEnvelope,
)
from ..harness.state_view import ExecutionBoard
from ..shared.llm import ModelToolCall
from ..tools.mcp import StdioMCPServerConfig
from ..tools.registry import ToolRegistry


class ToolChoosingModel(Protocol):
    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall: ...


class EnvironmentToolModel:
    """Builds the production tool-choosing model lazily from environment."""

    def __init__(self) -> None:
        self._model: Any = None

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        if self._model is None:
            from ..shared.llm import StructuredLLM
            from ..shared.settings import LLMSettings

            self._model = StructuredLLM(LLMSettings.from_environment(None, None))
        return self._model.choose_tool(messages=messages, tools=tools)


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
        async with stdio_client(self.server.server_parameters()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                channel = _McpChannel(session)
                if self.configure_tool is not None:
                    listing = await channel.list_tools()
                    if self.configure_tool not in listing:
                        raise RuntimeError(
                            f"MCP server is missing tool: {self.configure_tool}"
                        )
                    await channel.call(self.configure_tool, self.configure_payload)
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


# Unused after the unified loop: transports are concrete classes, not this protocol.
# class ToolTransport(Protocol):
#     def open(self) -> Any: ...


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
        tool_model: ToolChoosingModel,
        transport_factory: Callable[[TaskEnvelope, dict[str, Any]], Any],
        candidate_type: type[BaseModel],
        completion_builder: Callable[[TaskEnvelope, dict[str, Any]], CompletionSpec],
        context_builder: Callable[[TaskEnvelope, dict[str, Any]], dict[str, Any]]
        | None = None,
        capability_tools: ToolRegistry | None = None,
    ) -> None:
        self.tool_model = tool_model
        self.transport_factory = transport_factory
        self.candidate_type = candidate_type
        self.completion_builder = completion_builder
        self.context_builder = context_builder
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
        print(
            f"[{task.agent}] start task={task.task_id} format={task.format_id}",
            flush=True,
        )
        submit_tool = _submit_tool_for(task)
        context = (
            self.context_builder(task, kwargs)
            if self.context_builder is not None
            else {}
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": task.agent_instructions or task.goal,
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
                exposed: dict[str, str] = {}
                model_tools: list[dict[str, Any]] = []
                for allowed_name in task.allowed_tools:
                    spec = discovered[allowed_name]
                    safe_name = _model_tool_name(allowed_name)
                    exposed[safe_name] = allowed_name
                    exposed[allowed_name] = allowed_name
                    model_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": safe_name,
                                "description": spec.description,
                                "parameters": spec.parameters,
                            },
                        }
                    )
                board = ExecutionBoard(task)
                candidate: BaseModel | None = None
                while candidate is None:
                    if len(actions) >= task.budget.max_steps:
                        raise RuntimeError("task step budget exhausted")
                    print(
                        f"[{task.agent}] step={len(actions) + 1} choosing tool",
                        flush=True,
                    )
                    decision = self.tool_model.choose_tool(
                        messages=messages,
                        tools=model_tools,
                    )
                    active_name = exposed.get(decision.name)
                    if active_name is None:
                        raise PermissionError(
                            f"model selected unavailable tool: {decision.name}"
                        )
                    arguments = dict(decision.arguments)
                    print(f"[{task.agent}] call {active_name}", flush=True)
                    messages.append(decision.assistant_message())
                    started = time.perf_counter()
                    try:
                        value = await channel.call(active_name, arguments)
                    except Exception as exc:
                        actions.append(
                            _action(task, actions, active_name, "failed", str(exc), started)
                        )
                        print(
                            f"[{task.agent}] failed {active_name}: {exc}",
                            flush=True,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": decision.call_id,
                                "content": json.dumps(
                                    {"error": str(exc)},
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"工具 {active_name} 调用失败：{exc}。"
                                    "请根据错误选择下一步。"
                                ),
                            }
                        )
                        continue
                    actions.append(
                        _action(task, actions, active_name, "success", None, started)
                    )
                    print(f"[{task.agent}] success {active_name}", flush=True)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": decision.call_id,
                            "content": json.dumps(
                                value,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
                    if active_name == submit_tool:
                        candidate = self.candidate_type.model_validate(value)
                        _validate_candidate(task, candidate)
                        print(f"[{task.agent}] candidate submitted", flush=True)
                    else:
                        board.apply_tool_result(active_name, value)
                        messages.append(
                            {
                                "role": "user",
                                "content": board.progress_message(
                                    active_name,
                                    steps_used=len(actions),
                                ),
                            }
                        )
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


def _model_tool_name(mcp_name: str) -> str:
    """Map tool names to the conservative subset accepted by LLM APIs."""

    return re.sub(r"[^A-Za-z0-9_-]", "__", mcp_name)


def _mcp_result_text(result: object) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        value = getattr(item, "text", None)
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)
