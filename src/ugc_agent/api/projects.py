from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..agent import invoke_turn, resume_turn
from ..context import AgentContext
from ..state import ProjectState
from .views import (
    interrupt_payload,
    last_assistant_text,
    project_chat,
    project_run,
    snapshot_interrupt,
)

router = APIRouter()


class MessageIn(BaseModel):
    content: str = Field(min_length=1)


class DecisionIn(BaseModel):
    type: Literal["approve", "edit", "reject"]
    message: str | None = None
    edited_action: dict[str, Any] | None = None


class ResumeIn(BaseModel):
    interrupt_id: str
    decision: DecisionIn


def _resources(request: Request):
    return request.app.state.resources


def _project_id(project_id: str) -> str:
    if (
        not project_id
        or project_id in {".", ".."}
        or "/" in project_id
        or "\\" in project_id
    ):
        raise HTTPException(status_code=400, detail="invalid project_id")
    return project_id


def _load_project(request: Request, project_id: str) -> ProjectState:
    store = _resources(request).store
    project_id = _project_id(project_id)
    if not store.exists(project_id):
        raise HTTPException(status_code=404, detail=f"unknown project: {project_id}")
    return store.load(project_id)


def _summary(state: ProjectState) -> dict:
    return {
        "project_id": state.project_id,
        "revision": state.revision,
        "topic": state.outline.topic if state.outline else None,
        "has_deliverable": state.deliverable is not None,
    }


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


def _snapshot(request: Request, project_id: str):
    return _resources(request).agent.get_state(_thread_config(project_id))


def _messages(request: Request, project_id: str) -> list:
    values = _snapshot(request, project_id).values or {}
    if isinstance(values, dict):
        return list(values.get("messages") or [])
    return []


def _resume_payload(body: ResumeIn) -> dict:
    decision = body.decision
    if decision.type == "approve":
        payload: dict[str, Any] = {"type": "approve"}
    elif decision.type == "reject":
        payload = {"type": "reject"}
        if decision.message:
            payload["message"] = decision.message
    else:
        payload = {"type": "edit", "edited_action": decision.edited_action}
    return {body.interrupt_id: payload}


def _turn_reply(result: dict, turn_id: str, revision: int):
    interrupt = interrupt_payload(result)
    if interrupt:
        return JSONResponse(
            status_code=202,
            content={
                "turn_id": turn_id,
                "interrupt": interrupt,
                "revision": revision,
            },
        )
    content = last_assistant_text(result.get("messages") or [])
    if content is None:
        raise HTTPException(status_code=500, detail="no assistant reply")
    return {
        "turn_id": turn_id,
        "message": {"role": "assistant", "content": content},
        "revision": revision,
    }


@router.get("/api/projects")
def list_projects(request: Request) -> dict:
    store = _resources(request).store
    projects = []
    for project_id in store.list_ids():
        try:
            projects.append(_summary(store.load(project_id)))
        except Exception:
            continue
    return {"projects": projects}


@router.post("/api/projects", status_code=201)
def create_project(request: Request) -> dict:
    store = _resources(request).store
    project_id = uuid.uuid4().hex
    return _summary(store.create(project_id))


@router.get("/api/projects/{project_id}")
def get_project(request: Request, project_id: str) -> dict:
    return _summary(_load_project(request, project_id))


@router.post("/api/projects/{project_id}/messages")
def post_message(request: Request, project_id: str, body: MessageIn):
    _load_project(request, project_id)
    resources = _resources(request)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    try:
        result = invoke_turn(
            resources.agent,
            body.content,
            thread_id=project_id,
            context=AgentContext(project_id, turn_id, resources.store),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _turn_reply(
        result,
        turn_id,
        resources.store.load(project_id).revision,
    )


@router.post("/api/projects/{project_id}/messages/resume")
def resume_message(request: Request, project_id: str, body: ResumeIn):
    _load_project(request, project_id)
    resources = _resources(request)
    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    try:
        result = resume_turn(
            resources.agent,
            _resume_payload(body),
            thread_id=project_id,
            context=AgentContext(project_id, turn_id, resources.store),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _turn_reply(
        result,
        turn_id,
        resources.store.load(project_id).revision,
    )


@router.get("/api/projects/{project_id}/messages")
def get_messages(request: Request, project_id: str) -> dict:
    _load_project(request, project_id)
    return {
        "messages": project_chat(_messages(request, project_id)),
        "interrupt": snapshot_interrupt(_snapshot(request, project_id)),
    }


@router.get("/api/projects/{project_id}/state")
def get_state(request: Request, project_id: str) -> dict:
    return _load_project(request, project_id).model_dump(mode="json")


@router.get("/api/projects/{project_id}/run")
def get_run(request: Request, project_id: str) -> dict:
    _load_project(request, project_id)
    return {"turns": project_run(_messages(request, project_id))}


@router.get("/api/projects/{project_id}/media/{media_id}")
def get_media(request: Request, project_id: str, media_id: str) -> FileResponse:
    state = _load_project(request, project_id)
    asset = state.media.get(media_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"unknown media_id: {media_id}")
    project_dir = (_resources(request).store.root / project_id).resolve()
    path = Path(asset.path)
    if not path.is_absolute():
        path = project_dir / path
    path = path.resolve()
    if not path.is_relative_to(project_dir) or not path.is_file():
        raise HTTPException(status_code=404, detail="media not found")
    kwargs = {"media_type": asset.mime_type} if asset.mime_type else {}
    return FileResponse(path, **kwargs)
