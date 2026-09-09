from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .graph_history import (
    load_history_index,
    load_history_snapshot,
)
from ugc_harness.harness.models import ProductionState, ProjectState
from ugc_harness.intake.description_view import get_element
from ugc_harness.intake.graph_view import candidate_graph_refs, dump_node

from .project_service import ProjectService

_FIELD_LABELS = (
    ("name", "名称"),
    ("role", "角色"),
    ("description", "说明"),
    ("appearance", "外观"),
    ("instruction", "做法"),
    ("input_state", "开始时"),
    ("expected_result", "做成什么样"),
    ("common_mistakes", "常见失误"),
    ("safety_constraints", "注意"),
    ("semantic_goal", "这一段要讲什么"),
    ("target_effect", "看完之后"),
    ("goal", "目标"),
    ("purpose", "目的"),
    ("text", "口播"),
    ("action_description", "画面"),
    ("critical_detail", "关键细节"),
    ("camera_angle", "机位"),
    ("narration_text", "镜头旁白"),
    ("caption", "字幕"),
    ("playback_policy", "播放"),
    ("playback_modality", "画面类型"),
    ("quantity", "用量"),
)

_TITLE_KEYS = (
    "name",
    "instruction",
    "semantic_goal",
    "purpose",
    "goal",
    "text",
    "description",
    "caption",
)


class GraphProjector:
    """Studio view of the project dependency graph. No hashes."""

    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def view(self, project_dir: Path, *, version: int | None = None) -> dict[str, Any]:
        if version is None:
            payload = graph_view_from_state(self.projects.read_state(project_dir))
        else:
            payload = graph_view_from_snapshot(project_dir, version)
        payload["history"] = load_history_index(project_dir)["versions"]
        payload["history_version"] = version
        return payload


def graph_view_from_snapshot(project_dir: Path, version: int) -> dict[str, Any]:
    snapshot = load_history_snapshot(project_dir, version)
    state = ProjectState(
        dependency_graph=snapshot["dependency_graph"],
        production=ProductionState.model_validate(snapshot.get("production") or {}),
    )
    payload = graph_view_from_state(state)
    payload["recorded_at"] = snapshot.get("recorded_at") or ""
    return payload


def graph_view_from_state(state: Any) -> dict[str, Any]:
    graph = state.dependency_graph
    production = getattr(state, "production", None)
    nodes = []
    for ref in sorted(graph.nodes):
        payload = dump_node(graph.nodes[ref])
        payload.update(_content_for(production, payload["ref"], payload["kind"]))
        nodes.append(payload)
    kinds = Counter(str(node["kind"]) for node in nodes)
    return {
        "graph_version": graph.graph_version,
        "node_count": len(nodes),
        "kinds": dict(sorted(kinds.items())),
        "nodes": nodes,
    }


def _content_for(production: Any, ref: str, kind: str) -> dict[str, Any]:
    element = _lookup_element(production, ref, kind)
    if not element:
        ident = ref.partition(":")[2] or ref
        return {"title": ident, "summary": "", "details": []}
    fields = _flat_fields(kind, element)
    details = []
    seen: set[str] = set()
    for key, label in _FIELD_LABELS:
        text = _as_text(fields.get(key))
        if not text or text in seen:
            continue
        seen.add(text)
        details.append({"label": label, "text": text})
    title = ""
    for key in _TITLE_KEYS:
        title = _as_text(fields.get(key)) or ""
        if title:
            break
    if not title:
        title = ref.partition(":")[2] or ref
    summary = next((item["text"] for item in details if item["text"] != title), "")
    payload = {"title": title, "summary": summary, "details": details}
    order = fields.get("order")
    if isinstance(order, int):
        payload["order"] = order
    return payload


def _lookup_element(production: Any, ref: str, kind: str) -> dict[str, Any] | None:
    if production is None:
        return None
    for candidate in candidate_graph_refs(ref):
        found = get_element(production, candidate)
        if found and isinstance(found.get("element"), dict):
            return found["element"]
    ident = ref.partition(":")[2]
    if kind == "audio" and ident:
        found = get_element(production, f"utterance:{ident}")
        if found and isinstance(found.get("element"), dict):
            return found["element"]
    return None


def _flat_fields(kind: str, element: dict[str, Any]) -> dict[str, Any]:
    if kind == "shot" and isinstance(element.get("spec"), dict):
        spec = element["spec"]
        visual = spec.get("visual") if isinstance(spec.get("visual"), dict) else {}
        payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
        media = element.get("media") if isinstance(element.get("media"), dict) else {}
        return {**spec, **visual, **payload, **media}
    return dict(element)


def _as_text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return ""
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return "；".join(part for part in parts if part)
    return ""
