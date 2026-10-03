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


def _pending_shot_prompt(shot: dict[str, Any] | None) -> str:
    if not shot:
        return "9:16 竖屏，按参考图生成。"
    visual = shot.get("visual") or {}
    action = (
        visual.get("action_description")
        or visual.get("critical_detail")
        or shot.get("purpose")
        or ""
    )
    return f"9:16 竖屏。{action}".strip()


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
        if self.item_key == "ref":
            list_tool = next(name for name in names if name.endswith(".list_identities"))
            generate = next(name for name in names if name.endswith("generate_identity"))
        elif self.item_key == "shot_id":
            list_tool = next(name for name in names if name.endswith(".list_shots"))
            generate = next(name for name in names if name.endswith("generate_shot"))
        else:
            generate = next(name for name in names if "generate" in name)
            list_tool = next(
                name
                for name in names
                if name.endswith(".list_shots") or name.endswith(".list_identities")
            )
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
                if generate.endswith("generate_identity"):
                    arguments["prompt"] = _pending_identity_prompt(payload, pending[0])
                current = next(
                    (
                        item
                        for item in payload.get("shots") or []
                        if item.get("shot_id") == pending[0]
                    ),
                    None,
                )
                if generate.endswith("generate_shot"):
                    arguments["prompt"] = _pending_shot_prompt(current)
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


class AssetStageToolModel:
    """Identity stills first, then scoped shots, then submit."""

    def __init__(self) -> None:
        self.selected: list[str] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        names = _tool_names(tools)
        submit = next(name for name in names if name.endswith("submit_candidate"))
        list_identities = next(
            (name for name in names if name.endswith(".list_identities")),
            None,
        )
        list_shots = next(
            (name for name in names if name.endswith(".list_shots")),
            None,
        )
        generate_identity = next(
            (name for name in names if name.endswith("generate_identity")),
            None,
        )
        generate_shot = next(
            (name for name in names if name.endswith("generate_shot")),
            None,
        )
        payload = _last_tool_result(messages)
        if payload is None:
            choice = list_identities or list_shots or submit
            self.selected.append(choice)
            return StepDecision(tool=choice)
        pending = payload.get("pending")
        if "identities" in payload:
            if pending and generate_identity is not None:
                ref = pending[0]
                self.selected.append(generate_identity)
                return StepDecision(
                    tool=generate_identity,
                    arguments={
                        "ref": ref,
                        "prompt": _pending_identity_prompt(payload, ref),
                    },
                )
            if list_shots is not None:
                self.selected.append(list_shots)
                return StepDecision(tool=list_shots)
            self.selected.append(submit)
            return StepDecision(tool=submit)
        if pending and generate_shot is not None:
            current = next(
                (
                    item
                    for item in payload.get("shots") or []
                    if item.get("shot_id") == pending[0]
                ),
                None,
            )
            arguments: dict[str, Any] = {
                "shot_id": pending[0],
                "prompt": _pending_shot_prompt(current),
            }
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
            self.selected.append(generate_shot)
            return StepDecision(tool=generate_shot, arguments=arguments)
        self.selected.append(submit)
        return StepDecision(tool=submit)


class ExplainerAssetToolModel:
    """口播镜先 list/generate，其余 visual 再 acquire，最后 submit。"""

    def __init__(self) -> None:
        self.selected: list[str] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> StepDecision:
        names = _tool_names(tools)
        submit = next(name for name in names if name.endswith("submit_candidate"))
        list_heads = next(
            (name for name in names if name.endswith("list_talking_heads")),
            None,
        )
        generate = next(
            (name for name in names if name.endswith("generate_talking_head")),
            None,
        )
        acquire = next(
            (name for name in names if name.endswith("acquire_requirement")),
            None,
        )
        payload = _last_tool_result(messages)
        if payload is None:
            choice = list_heads or acquire or submit
            self.selected.append(choice)
            return StepDecision(tool=choice)
        pending = payload.get("pending") or []
        talking = payload.get("talking_heads") or []
        current = next(
            (
                item
                for item in talking
                if item.get("visual_request_id") in pending
            ),
            None,
        )
        if current and generate is not None:
            arguments: dict[str, Any] = {
                "visual_request_id": str(current["visual_request_id"]),
                "prompt": (
                    "画面无任何字幕。9:16 竖屏中景。"
                    f"开口说{{{current.get('narration') or ''}}}，"
                    "口型与这句话同步，音色和节奏跟@音频1走。"
                ),
            }
            materials = current.get("materials") or []
            refs = [
                {
                    "id": item["id"],
                    "description": item.get("default_description") or item["id"],
                }
                for item in materials
                if item.get("id")
                and (
                    item.get("ready")
                    or str(item.get("id") or "").startswith("identity:")
                )
            ]
            if refs:
                arguments["references"] = refs[:9]
            self.selected.append(generate)
            return StepDecision(tool=generate, arguments=arguments)
        if pending and acquire is not None:
            self.selected.append(acquire)
            return StepDecision(tool=acquire)
        self.selected.append(submit)
        return StepDecision(tool=submit)


def _pending_identity_prompt(payload: dict[str, Any], ref: str) -> str:
    current = next(
        (
            item
            for item in payload.get("identities") or []
            if item.get("ref") == ref
        ),
        None,
    )
    last = str((current or {}).get("generation_prompt") or "").strip()
    if last:
        return last
    name = str((current or {}).get("name") or ref)
    return f"9:16 竖屏身份静帧。{name}，面部清楚，无文字无 logo。"
