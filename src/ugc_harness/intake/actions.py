"""Implementations used by the ugc-intake MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from ..agents.narrative_agent.models import CreativeBrief
from ..harness.repair import RepairScheduler
from .description_view import get_element, list_outline
from .graph_view import list_graph, resolve_graph_ref
from .models import IntakeSession
from .view import (
    apply_state_to_session,
    brief_draft_from_creative_brief,
    format_progress,
    load_project_state,
    materialize_brief,
    refresh_session_from_disk,
)

StartProject = Callable[[CreativeBrief], Path]

_BUSY_NARRATIVE = {"running", "ready", "passed", "locked"}


def inspect_progress(session: IntakeSession) -> tuple[IntakeSession, dict[str, Any]]:
    try:
        session = refresh_session_from_disk(session)
    except Exception as exc:
        return session, {"ok": False, "error": f"inspect 失败：{exc}"}
    return session, {
        "ok": True,
        "text": f"inspect：{format_progress(session.progress)}",
        "progress": session.progress.model_dump(mode="json"),
    }


def list_description_outline(session: IntakeSession) -> dict[str, Any]:
    description = _description_or_error(session)
    if isinstance(description, dict):
        return description
    return {"ok": True, "items": list_outline(description)}


def list_dependency_graph(
    session: IntakeSession,
    around_ref: str | None = None,
) -> dict[str, Any]:
    if not session.project_dir:
        return {"ok": False, "error": "还没有项目，dependency_graph 为空"}
    state = load_project_state(Path(session.project_dir))
    if state is None:
        return {"ok": False, "error": "找不到 project_state.json"}
    return list_graph(state, around_ref)


def get_description_element(session: IntakeSession, ref: str) -> dict[str, Any]:
    cleaned = ref.strip()
    if not cleaned:
        return {"ok": False, "error": "ref 不能为空"}
    description = _description_or_error(session)
    if isinstance(description, dict):
        return description
    payload = get_element(description, cleaned)
    if payload is None:
        return {"ok": False, "error": f"找不到 {cleaned}"}
    return {"ok": True, **payload}


def start_project(
    session: IntakeSession,
    *,
    start_project: StartProject | None = None,
    dry_run: bool = False,
) -> tuple[IntakeSession, dict[str, Any]]:
    if _already_started(session):
        return session, {
            "ok": False,
            "error": (
                "项目已开工。"
                f"当前 {format_progress(session.progress)}。"
                "不要重复 start_project。"
            ),
        }
    try:
        brief = materialize_brief(session.working_brief)
    except (ValidationError, ValueError) as exc:
        return session, {"ok": False, "error": f"CreativeBrief 校验失败，未开工：{exc}"}
    session = session.model_copy(
        update={
            "project_id": brief.project_id,
            "working_brief": brief_draft_from_creative_brief(brief),
        }
    )
    if dry_run:
        return session, {
            "ok": True,
            "dry_run": True,
            "project_id": brief.project_id,
            "text": (
                f"dry-run：已生成 CreativeBrief（project_id={brief.project_id}），"
                "未启动 harness。"
            ),
        }
    if start_project is None:
        return session, {"ok": False, "error": "start_project 尚未接线，未启动 harness。"}
    try:
        project_dir = start_project(brief)
    except Exception as exc:
        return session, {"ok": False, "error": f"harness 开工失败：{exc}"}
    session = session.model_copy(update={"project_dir": str(project_dir)})
    try:
        state = load_project_state(project_dir)
    except Exception as exc:
        return session, {
            "ok": True,
            "project_dir": str(project_dir),
            "text": f"start_project 已写入 {project_dir}，但读取 project_state 失败：{exc}",
        }
    if state is not None:
        session = apply_state_to_session(session, state)
    return session, {
        "ok": True,
        "project_id": session.project_id,
        "project_dir": str(project_dir),
        "text": f"start_project 完成。目录={project_dir}。{format_progress(session.progress)}",
        "progress": session.progress.model_dump(mode="json"),
    }


def repair_description(
    session: IntakeSession,
    target_refs: list[str],
    instruction: str,
) -> tuple[IntakeSession, dict[str, Any]]:
    refs = [item.strip() for item in target_refs if item and item.strip()]
    note = instruction.strip()
    if not refs:
        return session, {"ok": False, "error": "target_refs 不能为空"}
    if not note:
        return session, {"ok": False, "error": "instruction 不能为空"}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法 repair"}
    state = load_project_state(Path(session.project_dir))
    if state is None:
        return session, {"ok": False, "error": "找不到 project_state.json"}
    graph_refs: list[str] = []
    missing: list[dict[str, Any]] = []
    for ref in refs:
        resolved = resolve_graph_ref(state.dependency_graph.nodes, ref)
        if resolved is None:
            missing.append({"ref": ref, "reason": "missing target node"})
        elif resolved not in graph_refs:
            graph_refs.append(resolved)
    if missing:
        return session, {
            "ok": False,
            "error": "；".join(item["reason"] for item in missing),
            "blockers": missing,
        }
    plan = RepairScheduler().plan(state, graph_refs)
    if plan.blockers:
        return session, {
            "ok": False,
            "error": "；".join(item.reason for item in plan.blockers),
            "blockers": [item.model_dump(mode="json") for item in plan.blockers],
        }
    return session, {
        "ok": False,
        "error": "harness.repair 执行器未接线",
        "target_refs": graph_refs,
        "planned_tasks": len(plan.tasks),
        "complete": plan.complete,
    }


def _already_started(session: IntakeSession) -> bool:
    if not session.project_dir:
        return False
    return session.progress.narrative in _BUSY_NARRATIVE


def _description_or_error(session: IntakeSession) -> Any:
    if not session.project_dir:
        return {"ok": False, "error": "还没有项目，description 为空"}
    state = load_project_state(Path(session.project_dir))
    if state is None or state.description is None:
        return {"ok": False, "error": "还没有 VideoDescription"}
    return state.description


def execute_start_project(brief: CreativeBrief, output_root: Path) -> Path:
    from ..harness.controller import NarrativeHarnessController
    from ..shared.artifacts import ArtifactWriter
    from ..shared.llm import StructuredLLM
    from ..shared.settings import LLMSettings

    settings = LLMSettings.from_environment(None, None)
    run = NarrativeHarnessController.from_mcp(
        StructuredLLM(settings),
        settings.model,
    ).run(brief)
    writer = ArtifactWriter(output_root)
    project_dir, _ = writer.write(run.artifact)
    writer.write_narrative_run(project_dir, run.record)
    return project_dir
