"""Read one description element or a structure outline. Never the whole document."""

from __future__ import annotations

from typing import Any

from ..harness.description import (
    DramaStructure,
    ExplainerStructure,
    TutorialStructure,
    VideoDescription,
)


def list_outline(description: VideoDescription | None) -> list[dict[str, Any]]:
    if description is None:
        return []
    structure = description.structure
    items: list[dict[str, Any]] = []
    if isinstance(structure, ExplainerStructure):
        for index, section in enumerate(structure.sections, start=1):
            items.append(
                {
                    "ref": f"section:{section.section_id}",
                    "kind": "section",
                    "order": index,
                    "title": section.goal,
                }
            )
        for beat in structure.beats:
            items.append(
                {
                    "ref": f"beat:{beat.planned_beat_id}",
                    "kind": "beat",
                    "order": beat.order,
                    "title": beat.semantic_goal,
                }
            )
        return items
    if isinstance(structure, DramaStructure):
        for scene in structure.scenes:
            items.append(
                {
                    "ref": f"scene:{scene.scene_id}",
                    "kind": "scene",
                    "order": scene.order,
                    "title": scene.purpose,
                }
            )
        for index, action in enumerate(structure.actions, start=1):
            items.append(
                {
                    "ref": f"action:{action.action_id}",
                    "kind": "action",
                    "order": index,
                    "title": action.description,
                }
            )
        return items
    if isinstance(structure, TutorialStructure):
        for step in structure.steps:
            items.append(
                {
                    "ref": f"step:{step.step_id}",
                    "kind": "step",
                    "order": step.order,
                    "title": step.instruction,
                }
            )
        for index, action in enumerate(structure.actions, start=1):
            items.append(
                {
                    "ref": f"action:{action.action_id}",
                    "kind": "action",
                    "order": index,
                    "title": action.description,
                }
            )
    return items


def get_element(
    description: VideoDescription | None, ref: str
) -> dict[str, Any] | None:
    if description is None or ":" not in ref:
        return None
    kind, _, ident = ref.partition(":")
    if not ident:
        return None
    payload = _lookup(description, kind, ident)
    if payload is None:
        return None
    return {"ref": ref, "kind": kind, "element": payload}


def _lookup(description: VideoDescription, kind: str, ident: str) -> Any:
    structure = description.structure
    if kind == "section" and isinstance(structure, ExplainerStructure):
        for section in structure.sections:
            if section.section_id == ident:
                return section.model_dump(mode="json")
    if kind == "beat" and isinstance(structure, ExplainerStructure):
        for beat in structure.beats:
            if beat.planned_beat_id == ident:
                return beat.model_dump(mode="json")
    if kind == "scene" and isinstance(structure, DramaStructure):
        for scene in structure.scenes:
            if scene.scene_id == ident:
                return scene.model_dump(mode="json")
    if kind == "step" and isinstance(structure, TutorialStructure):
        for step in structure.steps:
            if step.step_id == ident:
                return step.model_dump(mode="json")
    if kind == "action":
        actions = getattr(structure, "actions", ())
        for action in actions:
            if getattr(action, "action_id", None) == ident:
                return action.model_dump(mode="json")
    if kind == "utterance" and description.voice is not None:
        for utterance in description.voice.utterances:
            if utterance.utterance_id == ident:
                return utterance.model_dump(mode="json")
    if kind == "shot":
        for shot in description.shots:
            if shot.spec.shot_id == ident:
                return shot.model_dump(mode="json")
    if kind == "clip" and description.timeline is not None:
        for clip in description.timeline.clips:
            if clip.clip_id == ident:
                return clip.model_dump(mode="json")
    return None
