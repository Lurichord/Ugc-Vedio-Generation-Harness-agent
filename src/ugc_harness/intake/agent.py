"""Decide the next beat. MCP tools are available, never required."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Protocol

from .mcp_runtime import IntakeMcpRuntime
from .models import AgentDecision, AgentYield, IntakeMessage, IntakeSession
from .skills import list_skills

_AGENT_MD = (Path(__file__).parent / "agent.md").read_text(encoding="utf-8")
MAX_STEPS = 8
DIALOGUE_ROLES = {"user", "agent"}


class IntentModel(Protocol):
    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentDecision: ...


class LlmIntentModel:
    def __init__(self, generator: Any) -> None:
        self.generator = generator

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentDecision:
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
            "对用户问一句时可以把 tool 设为 null，只填 message。"
            "派制作、改某一段、查进度之前，必须先 skill.activate 对应 skill，再调 harness.* / description.*。"
            "改某一段时先读大纲和 harness.list_graph，再 repair；不要空着手直接 harness.start_project。"
            "不要编造工具名。\n\n"
            f"可用工具：{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"{_render_messages(messages)}"
        )
        return self.generator.generate(prompt, AgentDecision)


class IntentAgent:
    def __init__(
        self,
        model: IntentModel | Any,
        *,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.model = _adapt_model(model)
        self.instructions = _AGENT_MD
        self.max_steps = max_steps

    def run_until_yield(
        self,
        session: IntakeSession,
        runtime: IntakeMcpRuntime,
    ) -> AgentYield:
        return asyncio.run(self._run(session, runtime))

    async def _run(
        self,
        session: IntakeSession,
        runtime: IntakeMcpRuntime,
    ) -> AgentYield:
        async with runtime.connect(session) as bound:
            specs = bound.specs()
            messages = build_agent_messages(bound.session or session, self.instructions)
            last_text = ""
            for _ in range(self.max_steps):
                decision = self.model.decide(messages, specs)
                tool_name = (decision.tool or "").strip()
                if not tool_name:
                    message = (decision.message or last_text or "好的。").strip()
                    return AgentYield(
                        session=bound.session or session,
                        message=message,
                        done=decision.done,
                    )
                _log_tool("call", tool_name, decision.arguments)
                result = await bound.call(tool_name, decision.arguments)
                _log_tool("done", tool_name, result=result)
                last_text = _tool_text(tool_name, result)
                messages.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "tool": tool_name,
                                "arguments": decision.arguments,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                messages.append({"role": "user", "content": last_text})
            return AgentYield(
                session=bound.session or session,
                message=last_text or "这一拍步数用尽，先停在这里。",
                done=False,
            )


def build_agent_messages(
    session: IntakeSession,
    instructions: str,
) -> list[dict[str, Any]]:
    catalog = list_skills()
    memory = {
        "working_brief": session.working_brief.model_dump(mode="json"),
        "working_intent": session.working_intent.model_dump(mode="json"),
        "progress": session.progress.model_dump(mode="json"),
        "project_id": session.project_id,
        "project_dir": session.project_dir,
        "status": session.status,
    }
    system = (
        f"{instructions}\n\n"
        f"## Skill 目录\n{json.dumps(catalog, ensure_ascii=False, indent=2)}\n\n"
        f"## 当前 memory\n{json.dumps(memory, ensure_ascii=False, indent=2)}"
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for item in _dialogue(session.messages):
        role = "assistant" if item.role == "agent" else "user"
        messages.append({"role": role, "content": item.content})
    return messages


def _dialogue(messages: list[IntakeMessage]) -> list[IntakeMessage]:
    return [item for item in messages if item.role in DIALOGUE_ROLES]


def _adapt_model(model: Any) -> IntentModel:
    decide = getattr(model, "decide", None)
    if callable(decide):
        return model
    if callable(getattr(model, "generate", None)):
        return LlmIntentModel(model)
    raise TypeError("intent model must provide decide() or generate()")


def _render_messages(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in messages:
        role = item.get("role", "user")
        content = item.get("content") or ""
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _tool_text(name: str, result: dict[str, Any]) -> str:
    return json.dumps({"tool": name, "result": result}, ensure_ascii=False)


def _log_tool(
    event: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    parts = [f"[intake] {event} {name}"]
    if arguments:
        parts.append(json.dumps(arguments, ensure_ascii=False))
    if result is not None:
        if result.get("ok") is False and result.get("error"):
            parts.append(f"ok=false error={result['error']}")
        elif "ok" in result:
            parts.append(f"ok={result['ok']}")
    print(" ".join(parts), file=sys.stderr, flush=True)
