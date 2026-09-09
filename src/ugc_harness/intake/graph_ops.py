"""Intake wrappers for adding shots, appending binds, and removing nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..harness.graph_edit import bind_identity, prepare_shot_create, remove_graph_node
from ..harness.models import TaskEnvelope
from ..harness.repair import load_project_state, write_project_state
from ..harness.shot_repair import _sync_narrative_artifact_shots
from .identity import _sync_narrative_artifact_identity
from .models import IntakeSession
from .view import format_progress, refresh_session_from_disk

RunTask = Callable[[TaskEnvelope, str], None]


def create_shot(
    session: IntakeSession,
    *,
    anchor_ref: str,
    binds: list[str] | None = None,
    after_ref: str | None = None,
    instruction: str,
    dry_run: bool = False,
    run_task: RunTask | None = None,
) -> tuple[IntakeSession, dict[str, Any]]:
    note = instruction.strip()
    if not note:
        return session, {"ok": False, "error": "instruction 不能为空"}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法加镜头"}
    project_dir = Path(session.project_dir)
    state = load_project_state(project_dir)
    if state is None or state.production.structure is None:
        return session, {"ok": False, "error": "还没有 production"}
    try:
        task = prepare_shot_create(
            state,
            anchor_ref=anchor_ref,
            binds=list(binds or []),
            after_ref=after_ref,
            instruction=note,
        )
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc)}
    spec = task.create_shot
    assert spec is not None
    payload = {
        "shot_ref": f"shot:{spec.shot_id}",
        "source_refs": spec.source_refs,
        "binds": spec.binds,
        "order": spec.order,
    }
    if dry_run:
        return session, {
            "ok": True,
            "dry_run": True,
            **payload,
            "text": f"dry-run：将派 generic 写 {payload['shot_ref']}",
        }
    if run_task is None:
        return session, {"ok": False, "error": "shot.create 执行器未接线", **payload}
    before = {shot.spec.shot_id for shot in state.production.shots}
    try:
        run_task(task, note)
    except Exception as exc:
        session = refresh_session_from_disk(session)
        after = load_project_state(project_dir)
        leftover = (
            {shot.spec.shot_id for shot in after.production.shots} - before
            if after is not None
            else set()
        )
        return session, {
            "ok": False,
            "error": f"shot.create 失败：{exc}",
            **payload,
            "leftover_shots": sorted(leftover),
        }
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        **payload,
        "text": (
            f"已新增镜头 {payload['shot_ref']}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def bind_shot(
    session: IntakeSession,
    shot_refs: list[str] | None = None,
    identity_ref: str = "",
) -> tuple[IntakeSession, dict[str, Any]]:
    refs = [item.strip() for item in (shot_refs or []) if item and item.strip()]
    token = identity_ref.strip()
    if not refs:
        return session, {"ok": False, "error": "shot_refs 不能为空"}
    if not token:
        return session, {"ok": False, "error": "identity_ref 不能为空"}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法绑镜头"}
    project_dir = Path(session.project_dir)
    state = load_project_state(project_dir)
    if state is None or state.production.structure is None:
        return session, {"ok": False, "error": "还没有 production"}
    try:
        changed = bind_identity(state, refs, token)
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc)}
    write_project_state(project_dir, state)
    _sync_narrative_artifact_shots(project_dir, state)
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "shot_refs": changed,
        "changed_refs": changed,
        "text": (
            f"已把身份接到 {', '.join(changed)}，接着 harness.repair 改画面。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }


def remove_node(
    session: IntakeSession,
    ref: str,
) -> tuple[IntakeSession, dict[str, Any]]:
    token = ref.strip()
    if not token:
        return session, {"ok": False, "error": "ref 不能为空"}
    if not session.project_dir:
        return session, {"ok": False, "error": "还没有项目目录，无法删节点"}
    project_dir = Path(session.project_dir)
    state = load_project_state(project_dir)
    if state is None or state.production.structure is None:
        return session, {"ok": False, "error": "还没有 production"}
    try:
        removed = remove_graph_node(state, token)
    except ValueError as exc:
        return session, {"ok": False, "error": str(exc)}
    write_project_state(project_dir, state)
    _sync_narrative_artifact_shots(project_dir, state)
    _sync_narrative_artifact_identity(project_dir, state)
    session = refresh_session_from_disk(session)
    return session, {
        "ok": True,
        "ref": removed,
        "text": (
            f"已删除 {removed}。"
            f"{format_progress(session.progress)}"
        ),
        "progress": session.progress.model_dump(mode="json"),
    }
