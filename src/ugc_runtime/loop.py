"""One decide / tool / append cycle. No project, no media calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Tool = Callable[[dict[str, Any]], dict[str, Any]]
Decide = Callable[[list[dict[str, Any]], list[str]], "Step"]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class LoopResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, Any]]
    steps: list[Step]
    stop: Literal["done", "budget"]


def run_loop(
    messages: list[dict[str, Any]],
    *,
    decide: Decide,
    tools: dict[str, Tool],
    max_steps: int = 8,
) -> LoopResult:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    transcript = list(messages)
    steps: list[Step] = []
    names = sorted(tools)
    for _ in range(max_steps):
        step = decide(transcript, names)
        steps.append(step)
        transcript.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    step.model_dump(),
                    ensure_ascii=False,
                ),
            }
        )
        tool_name = (step.tool or "").strip()
        if not tool_name:
            return LoopResult(messages=transcript, steps=steps, stop="done")
        transcript.append(
            {
                "role": "user",
                "content": json.dumps(
                    {"tool": tool_name, "result": _call(tools, tool_name, step.arguments)},
                    ensure_ascii=False,
                ),
            }
        )
    return LoopResult(messages=transcript, steps=steps, stop="budget")


def _call(tools: dict[str, Tool], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    tool = tools.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown_tool: {name}"}
    try:
        result = tool(arguments)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    if not isinstance(result, dict):
        return {"ok": False, "error": "tool_result_not_object"}
    return result
