from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from ..harness.models import ActionRecord, AgentResult, TaskEnvelope
from ..tools.registry import ToolRegistry

T = TypeVar("T")


class BaseAgent(ABC, Generic[T]):
    name: str

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    def validate_task(self, task: TaskEnvelope) -> None:
        if task.agent != self.name:
            raise ValueError(f"task targets {task.agent}, not {self.name}")

    def invoke_tool(
        self,
        task: TaskEnvelope,
        actions: list[ActionRecord],
        name: str,
        **kwargs: object,
    ) -> object:
        if len(actions) >= task.budget.max_steps:
            raise RuntimeError("task step budget exhausted")
        started = time.perf_counter()
        try:
            value = self.tools.invoke(
                name,
                allowed_tools=task.allowed_tools,
                **kwargs,
            )
        except Exception as exc:
            actions.append(
                ActionRecord(
                    action_id=f"{task.task_id}:action:{len(actions) + 1}",
                    agent=self.name,
                    task_id=task.task_id,
                    tool=name,
                    result="failed",
                    reason=str(exc),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
            )
            raise
        actions.append(
            ActionRecord(
                action_id=f"{task.task_id}:action:{len(actions) + 1}",
                agent=self.name,
                task_id=task.task_id,
                tool=name,
                result="success",
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        )
        return value

    @abstractmethod
    def run(self, task: TaskEnvelope, **kwargs: object) -> T: ...


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
