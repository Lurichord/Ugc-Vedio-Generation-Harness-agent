"""Implementations used by the ugc-intake MCP server."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..harness.dependencies import DependencyGraph
from ..harness.models import TaskBudget, TaskEnvelope, TaskScope
from ..harness.realization_scope import expand_asset_regenerate, expand_voice_refs
from ..harness.repair import _TOOLS, repair_input_hash
from ..harness.shot_repair import REPAIR_SUBMIT_TOOL, _stage_produced, resolve_shot_refs

from pydantic import ValidationError

from ..agents.narrative_agent.models import CreativeBrief
from ..harness.repair import (
    RepairScheduler,
    drive_repair,
    invalidate_subgraph,
    load_project_state as load_repair_state,
    write_project_state,
)
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
_VOICE_PREFIXES = ("utterance:", "audio:", "script_segment:")
_MIXED_REPAIR_ERROR = (
    "一次 harness.repair 只派一个制作 agent。"
    "画面圈 shot:（或身份 changed_refs），配音圈 audio:，分开两次。"
)
_NO_SHOT_REPAIR_ERROR = (
    "画面修要圈 shot: 或身份；配音圈 audio:。这次 target_refs 扩不出 shot。"
)
_LAYER_ERROR = "layer 只能是 spec、asset 或 voice"
_ASSET_TARGET_ERROR = "layer=asset 只能圈 shot: 或身份，不能圈配音。"
_VOICE_TARGET_ERROR = "layer=voice 只能圈 audio: 或说话人，不能圈 shot。"
_NO_ASSET_YET = "还没有参考图或成片，不能只改实现。先出片，或去掉 layer 走修规格。"
_NO_VOICE_YET = "还没有配音节点。演绎换声音请用 layer=asset 圈 shot:。"


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


def repair_lane_seeds(state, graph_refs: list[str]) -> tuple[list[str], list[str], list[str], bool]:
    """One repair call seeds one production agent.

    Voice refs stay on audio. Identity / beat / shot expand to shots only.
    Mixing the two lanes in one target_refs list is rejected so intake
    must dispatch narrative and voice separately after inspect-graph.
    """

    if _mixed_repair_lanes(graph_refs):
        raise ValueError(_MIXED_REPAIR_ERROR)
    format_id = state.context.constraints.get(
        "narrative_format"
    ) or state.context.constraints.get("production_mode")
    voice_only = bool(graph_refs) and all(
        item.startswith(_VOICE_PREFIXES) for item in graph_refs
    )
    identity_refs = [
        item for item in graph_refs if item.startswith(("cast:", "prop:", "place:"))
    ]
    if voice_only:
        return _audio_repair_refs(state, graph_refs) or list(graph_refs), [], identity_refs, False
    shot_refs = resolve_shot_refs(state, graph_refs)
    direct_shots = format_id in {"drama", "tutorial"}
    if direct_shots:
        seeds = list(dict.fromkeys([*identity_refs, *shot_refs]))
        if not seeds:
            raise ValueError(_NO_SHOT_REPAIR_ERROR)
        return seeds, shot_refs, identity_refs, True
    if not shot_refs:
        raise ValueError(_NO_SHOT_REPAIR_ERROR)
    return shot_refs, shot_refs, identity_refs, False


def _mixed_repair_lanes(graph_refs: list[str]) -> bool:
    has_voice = any(item.startswith(_VOICE_PREFIXES) for item in graph_refs)
    has_picture = any(not item.startswith(_VOICE_PREFIXES) for item in graph_refs)
    return has_voice and has_picture


def repair_description(
    session: IntakeSession,
    target_refs: list[str],
    instruction: str,
    *,
    layer: str | None = None,
    dry_run: bool = False,
    run_task: Callable[[TaskEnvelope, str], None] | None = None,
) -> tuple[IntakeSession, dict[str, Any]]:
    refs = [item.strip() for item in target_refs if item and item.strip()]
    note = instruction.strip()
    mode = (layer or "spec").strip() or "spec"
    if not refs:
        return session, {"ok": False, "error": "target_refs 不能为空"}
    if not note:
        return session, {"ok": False, "error": "instruction 不能为空"}
    if mode not in {"spec", "asset", "voice"}:
        return session, {"ok": False, "error": _LAYER_ERROR}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法 repair"}
    project_dir = Path(session.project_dir)
    state = load_project_state(project_dir)
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
    if mode == "asset":
        return _repair_assets(
            session,
            project_dir,
            state,
            graph_refs=graph_refs,
            instruction=note,
            dry_run=dry_run,
            run_task=run_task,
        )
    if mode == "voice":
        return _repair_voice_media(
            session,
            project_dir,
            state,
            graph_refs=graph_refs,
            instruction=note,
            dry_run=dry_run,
            run_task=run_task,
        )
    try:
        seeds, shot_refs, identity_refs, direct_shots = repair_lane_seeds(
            state, graph_refs
        )
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc), "target_refs": graph_refs}
    try:
        working = state.model_copy(deep=True)
        invalidated = invalidate_subgraph(working, seeds)
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc), "target_refs": graph_refs}
    if direct_shots:
        return _repair_direct_shots(
            session,
            project_dir,
            state,
            graph_refs=graph_refs,
            identity_refs=identity_refs,
            shot_refs=shot_refs,
            invalidated=invalidated,
            instruction=note,
            dry_run=dry_run,
            run_task=run_task,
        )
    preview = RepairScheduler().plan(working, invalidated)
    if preview.blockers:
        return session, {
            "ok": False,
            "error": "；".join(item.reason for item in preview.blockers),
            "blockers": [item.model_dump(mode="json") for item in preview.blockers],
            "target_refs": graph_refs,
            "invalidated": invalidated,
        }
    preview_payload = {
        "target_refs": graph_refs,
        "shot_refs": shot_refs,
        "invalidated": invalidated,
        "planned_tasks": len(preview.tasks),
        "complete": preview.complete,
    }
    if dry_run:
        circled = ", ".join(shot_refs or graph_refs)
        return session, {
            "ok": True,
            "dry_run": True,
            **preview_payload,
            "text": (
                f"dry-run：将把 {circled} 交给 generic agent，"
                f"弄脏 {len(invalidated)} 个节点，首轮 {len(preview.tasks)} 个任务。"
            ),
        }
    if run_task is None:
        return session, {
            "ok": False,
            "error": "harness.repair 执行器未接线",
            **preview_payload,
        }
    invalidate_subgraph(state, seeds)
    write_project_state(project_dir, state)
    try:
        result = drive_repair(
            desired_refs=invalidated,
            instruction=note,
            load_state=lambda: load_repair_state(project_dir),
            run_task=run_task,
        )
    except Exception as exc:
        session = refresh_session_from_disk(session)
        return session, {
            "ok": False,
            "error": f"repair 执行失败：{exc}",
            **preview_payload,
        }
    session = refresh_session_from_disk(session)
    payload = result.model_dump(mode="json")
    payload["target_refs"] = graph_refs
    payload["shot_refs"] = shot_refs
    payload["invalidated"] = invalidated
    if result.ok:
        circled = ", ".join(shot_refs or graph_refs)
        payload["text"] = (
            f"repair 完成。已按指令改 {circled}，"
            f"跑了 {len(result.tasks_run)} 个任务。"
            f"{format_progress(session.progress)}"
        )
        payload["progress"] = session.progress.model_dump(mode="json")
    else:
        payload["error"] = result.error or "repair 未完成"
    return session, payload



def _repair_direct_shots(
    session: IntakeSession,
    project_dir: Path,
    state,
    *,
    graph_refs: list[str],
    identity_refs: list[str],
    shot_refs: list[str],
    invalidated: list[str],
    instruction: str,
    dry_run: bool,
    run_task: Callable[[TaskEnvelope, str], None] | None,
) -> tuple[IntakeSession, dict[str, Any]]:
    circled = identity_refs + shot_refs
    preview_payload = {
        "target_refs": graph_refs,
        "shot_refs": shot_refs,
        "identity_refs": identity_refs,
        "invalidated": invalidated,
        "planned_tasks": 1,
        "complete": False,
    }
    if dry_run:
        return session, {
            "ok": True,
            "dry_run": True,
            **preview_payload,
            "text": (
                f"dry-run：将按图改 {', '.join(circled)}，"
                f"弄脏 {len(invalidated)} 个节点，然后重出"
                f"{'参考图和成片' if _stage_produced(state, 'asset') else '参考图'}。"
            ),
        }
    if run_task is None:
        return session, {
            "ok": False,
            "error": "harness.repair 执行器未接线",
            **preview_payload,
        }
    invalidate_subgraph(state, circled)
    write_project_state(project_dir, state)
    task = TaskEnvelope(
        task_id=(
            f"task_repair_direct_{state.context.project_id}_v"
            f"{state.context.state_version}"
        ),
        agent="narrative_agent",
        goal="按依赖图修镜头规格，并重出圈中身份参考图与成片",
        scope=TaskScope(
            project_id=state.context.project_id,
            shot_ids=[ref.partition(":")[2] for ref in shot_refs],
            target_refs=circled,
        ),
        based_on_state_version=state.context.state_version,
        allowed_tools=[REPAIR_SUBMIT_TOOL],
        required_outputs=["patches"],
        budget=TaskBudget(max_steps=6, max_retries=1),
        input_hash="repair-direct",
    )
    try:
        run_task(task, instruction)
    except Exception as exc:
        session = refresh_session_from_disk(session)
        return session, {
            "ok": False,
            "error": f"repair 执行失败：{exc}",
            **preview_payload,
        }
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "complete": True,
        "dry_run": False,
        **preview_payload,
        "text": (
            f"repair 完成。已按指令改 {', '.join(circled)}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def _repair_assets(
    session: IntakeSession,
    project_dir: Path,
    state,
    *,
    graph_refs: list[str],
    instruction: str,
    dry_run: bool,
    run_task: Callable[[TaskEnvelope, str], None] | None,
) -> tuple[IntakeSession, dict[str, Any]]:
    if any(item.startswith(_VOICE_PREFIXES) for item in graph_refs):
        return session, {
            "ok": False,
            "error": _ASSET_TARGET_ERROR,
            "target_refs": graph_refs,
        }
    identity_refs, shot_ids = expand_asset_regenerate(state, graph_refs)
    if not identity_refs and not shot_ids:
        return session, {
            "ok": False,
            "error": "layer=asset 要圈 shot: 或身份。",
            "target_refs": graph_refs,
        }
    format_id = state.context.constraints.get(
        "narrative_format"
    ) or state.context.constraints.get("production_mode")
    if format_id not in {"drama", "tutorial"}:
        return session, {
            "ok": False,
            "error": "只有短剧/教程能按 layer=asset 重出参考图或成片。",
            "target_refs": graph_refs,
        }
    if not _asset_realization_ready(state, identity_refs, shot_ids):
        return session, {
            "ok": False,
            "error": _NO_ASSET_YET,
            "target_refs": graph_refs,
        }
    shot_refs = [f"shot:{shot_id}" for shot_id in shot_ids]
    preview = {
        "layer": "asset",
        "target_refs": graph_refs,
        "identity_refs": identity_refs,
        "shot_refs": shot_refs,
        "regenerated_identity_refs": identity_refs,
        "regenerated_shot_ids": shot_ids,
        "invalidated": [],
        "planned_tasks": 1,
        "complete": False,
    }
    circled = ", ".join(identity_refs + shot_refs) or ", ".join(graph_refs)
    if dry_run:
        return session, {
            "ok": True,
            "dry_run": True,
            **preview,
            "text": f"dry-run：将按实现层重做 {circled}，不改规格。",
        }
    if run_task is None:
        return session, {
            "ok": False,
            "error": "harness.repair 执行器未接线",
            **preview,
        }
    from ..harness.shot_asset_controller import (
        GENERATE_IDENTITY_TOOL,
        GENERATE_TOOL,
        GRAPH_TOOL,
        LIST_IDENTITIES_TOOL,
        LIST_TOOL,
        SUBMIT_TOOL,
    )

    task = TaskEnvelope(
        task_id=(
            f"task_repair_asset_{state.context.project_id}_v"
            f"{state.context.state_version}"
        ),
        agent="asset_stage",
        goal="按 instruction 重做圈中参考图与成片，不改规格",
        scope=TaskScope(
            project_id=state.context.project_id,
            shot_ids=shot_ids,
            target_refs=list(dict.fromkeys([*identity_refs, *shot_refs])),
        ),
        based_on_state_version=state.context.state_version,
        format_id=format_id,
        allowed_tools=[
            LIST_IDENTITIES_TOOL,
            LIST_TOOL,
            GRAPH_TOOL,
            GENERATE_IDENTITY_TOOL,
            GENERATE_TOOL,
            SUBMIT_TOOL,
        ],
        required_outputs=["assets"] if shot_ids else ["identities"],
        budget=TaskBudget(
            max_steps=min(128, max(8, (len(identity_refs) + len(shot_ids)) * 2 + 6)),
            max_retries=1,
        ),
        input_hash="repair-asset",
    )
    try:
        run_task(task, instruction)
    except Exception as exc:
        session = refresh_session_from_disk(session)
        return session, {
            "ok": False,
            "error": f"repair 执行失败：{exc}",
            **preview,
        }
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "complete": True,
        "dry_run": False,
        **preview,
        "text": (
            f"repair 完成。已按实现层重做 {circled}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def _repair_voice_media(
    session: IntakeSession,
    project_dir: Path,
    state,
    *,
    graph_refs: list[str],
    instruction: str,
    dry_run: bool,
    run_task: Callable[[TaskEnvelope, str], None] | None,
) -> tuple[IntakeSession, dict[str, Any]]:
    if any(item.startswith("shot:") for item in graph_refs):
        return session, {
            "ok": False,
            "error": _VOICE_TARGET_ERROR,
            "target_refs": graph_refs,
        }
    audio_refs = expand_voice_refs(state, graph_refs, instruction)
    if not audio_refs:
        return session, {
            "ok": False,
            "error": _NO_VOICE_YET,
            "target_refs": graph_refs,
        }
    preview = {
        "layer": "voice",
        "target_refs": graph_refs,
        "shot_refs": [],
        "invalidated": [],
        "audio_refs": audio_refs,
        "planned_tasks": 1,
        "complete": False,
    }
    circled = ", ".join(audio_refs)
    if dry_run:
        return session, {
            "ok": True,
            "dry_run": True,
            **preview,
            "text": f"dry-run：将按实现层重配 {circled}，不改词。",
        }
    if run_task is None:
        return session, {
            "ok": False,
            "error": "harness.repair 执行器未接线",
            **preview,
        }
    graph = DependencyGraph(state.dependency_graph)
    required = sorted(
        {
            dep
            for ref in audio_refs
            for dep in state.dependency_graph.nodes[ref].depends_on
        }
    )
    snapshots = graph.snapshot(required) if required else []
    task = TaskEnvelope(
        task_id=(
            f"task_repair_voice_{state.context.project_id}_v"
            f"{state.context.state_version}"
        ),
        agent="voice_agent",
        goal="按 instruction 重配圈中音频，不改词",
        scope=TaskScope(
            project_id=state.context.project_id,
            target_refs=audio_refs,
        ),
        based_on_state_version=state.context.state_version,
        allowed_tools=_TOOLS["voice_agent"],
        required_outputs=["timed_audio"],
        budget=TaskBudget(max_steps=8, max_retries=1),
        input_hash=repair_input_hash(snapshots, audio_refs),
        dependency_snapshot=snapshots,
    )
    try:
        run_task(task, instruction)
    except Exception as exc:
        session = refresh_session_from_disk(session)
        return session, {
            "ok": False,
            "error": f"repair 执行失败：{exc}",
            **preview,
        }
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "complete": True,
        "dry_run": False,
        **preview,
        "text": (
            f"repair 完成。已按实现层重配 {circled}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def _asset_realization_ready(state, identity_refs: list[str], shot_ids: list[str]) -> bool:
    if _stage_produced(state, "asset") or _stage_produced(state, "references"):
        return True
    from ..harness.world_identities import identity_record

    for ref in identity_refs:
        item = identity_record(state, ref)
        if item is not None and getattr(item, "reference_image", None):
            return True
    wanted = set(shot_ids)
    return any(
        item.spec.shot_id in wanted and item.media is not None
        for item in state.production.shots
    )


def _audio_repair_refs(state, graph_refs: list[str]) -> list[str]:
    found: list[str] = []
    nodes = state.dependency_graph.nodes
    for ref in graph_refs:
        if ref.startswith("audio:") and ref not in found:
            found.append(ref)
            continue
        node = nodes.get(ref)
        if node is None:
            continue
        for dep in node.dependents:
            if dep.startswith("audio:") and dep not in found:
                found.append(dep)
        for other in nodes.values():
            if other.ref.startswith("audio:") and ref in other.depends_on:
                if other.ref not in found:
                    found.append(other.ref)
    return found


def _already_started(session: IntakeSession) -> bool:
    if not session.project_dir:
        return False
    return session.progress.narrative in _BUSY_NARRATIVE


def _description_or_error(session: IntakeSession) -> Any:
    if not session.project_dir:
        return {"ok": False, "error": "还没有项目，description 为空"}
    state = load_project_state(Path(session.project_dir))
    if state is None or state.production.structure is None:
        return {"ok": False, "error": "还没有 production"}
    return state.production


def execute_start_project(brief: CreativeBrief, output_root: Path) -> Path:
    import sys

    from ..harness.controller import NarrativeHarnessController
    from ..shared.artifacts import ArtifactWriter
    from ..shared.llm import StructuredLLM
    from ..shared.settings import LLMSettings

    print(
        f"start_project: 正在跑叙事 harness → {output_root}（通常要几分钟，会先出 [narrative] 日志）",
        file=sys.stderr,
        flush=True,
    )
    settings = LLMSettings.from_environment(None, None)
    run = NarrativeHarnessController.from_mcp(
        StructuredLLM(settings),
        settings.model,
    ).run(brief)
    writer = ArtifactWriter(output_root)
    project_dir, _ = writer.write(run.artifact)
    writer.write_narrative_run(project_dir, run.record)
    print(
        f"start_project: 完成 {project_dir}",
        file=sys.stderr,
        flush=True,
    )
    return project_dir
