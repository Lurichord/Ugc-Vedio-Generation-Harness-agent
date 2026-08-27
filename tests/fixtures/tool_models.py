"""Deterministic tool-choosing fakes for exercising the unified agent loop."""

from __future__ import annotations

import json
from typing import Any

from ugc_harness.shared.llm import ModelToolCall


class CyclingToolModel:
    """Replays a fixed sequence, wrapping around for repeated runs."""

    def __init__(self, sequence: list[str]) -> None:
        self.sequence = list(sequence)
        self.selected: list[str] = []

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        name = self.sequence[len(self.selected) % len(self.sequence)]
        self.selected.append(name)
        return ModelToolCall(
            call_id=f"call_{len(self.selected)}",
            name=name,
            arguments={},
        )


class QueueToolModel:
    """Works the queue-style stages: call the worker tool until the last tool
    result reports an empty pending list, then submit. Falls over to the next
    worker tool after an error (e.g. acquire vs prepare mode mismatch)."""

    def __init__(self) -> None:
        self.selected: list[str] = []
        self._last_worker: str | None = None

    def choose_tool(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> ModelToolCall:
        names = [tool["function"]["name"] for tool in tools]
        submit = next(name for name in names if name.endswith("submit_candidate"))
        workers = [name for name in names if not name.endswith("submit_candidate")]
        choice = workers[0]
        last_tool_message = next(
            (
                message
                for message in reversed(messages)
                if message.get("role") == "tool"
            ),
            None,
        )
        if last_tool_message is not None:
            try:
                payload = json.loads(last_tool_message.get("content") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                if payload.get("pending") == []:
                    choice = submit
                elif "error" in payload and self._last_worker in workers:
                    remaining = [
                        name for name in workers if name != self._last_worker
                    ]
                    if remaining:
                        choice = remaining[0]
        if choice != submit:
            self._last_worker = choice
        self.selected.append(choice)
        return ModelToolCall(
            call_id=f"call_{len(self.selected)}",
            name=choice,
            arguments={},
        )
