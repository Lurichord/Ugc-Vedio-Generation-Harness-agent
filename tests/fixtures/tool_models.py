"""Deterministic decide() fakes for exercising the unified agent loop."""

from __future__ import annotations

import json
from typing import Any

from ugc_harness.agents.generic import StepDecision


def _tool_names(tools: list[dict[str, Any]]) -> list[str]:
    return [str(tool["name"]) for tool in tools]


def _last_tool_result(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        raw = message.get("content")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            return result
    return None


class CyclingToolModel:
    """Replays a fixed sequence, wrapping around for repeated runs."""

    def __init__(self, sequence: list[str]) -> None:
        self.sequence = list(sequence)
        self.selected: list[str] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        del messages, tools
        name = self.sequence[len(self.selected) % len(self.sequence)]
        self.selected.append(name)
        return StepDecision(tool=name)


class QueueToolModel:
    """Works the queue-style stages: call the worker tool until the last tool
    result reports an empty pending list, then submit. Falls over to the next
    worker tool after an error (e.g. acquire vs prepare mode mismatch)."""

    def __init__(self) -> None:
        self.selected: list[str] = []
        self._last_worker: str | None = None

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        names = _tool_names(tools)
        submit = next(name for name in names if name.endswith("submit_candidate"))
        workers = [
            name
            for name in names
            if not name.endswith("submit_candidate") and name != "skill.activate"
        ]
        choice = workers[0]
        payload = _last_tool_result(messages)
        if payload is not None:
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
        return StepDecision(tool=choice)


class PendingItemToolModel:
    """List pending items, generate the first pending id, then submit."""

    def __init__(self, item_key: str) -> None:
        self.item_key = item_key
        self.selected: list[str] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        names = _tool_names(tools)
        submit = next(name for name in names if name.endswith("submit_candidate"))
        generate = next(name for name in names if "generate" in name)
        list_tool = next(name for name in names if "list" in name)
        payload = _last_tool_result(messages)
        choice = list_tool
        arguments: dict[str, Any] = {}
        if payload is not None:
            pending = payload.get("pending")
            if pending == []:
                choice = submit
            elif pending:
                choice = generate
                arguments = {self.item_key: pending[0]}
                current = next(
                    (
                        item
                        for item in payload.get("shots") or []
                        if item.get("shot_id") == pending[0]
                    ),
                    None,
                )
                materials = (current or {}).get("materials")
                if isinstance(materials, list) and materials:
                    arguments["references"] = [
                        {
                            "id": item["id"],
                            "description": item.get("default_description") or item["id"],
                        }
                        for item in materials
                        if item.get("ready") and item.get("id")
                    ][:9]
        self.selected.append(choice)
        return StepDecision(tool=choice, arguments=arguments)
