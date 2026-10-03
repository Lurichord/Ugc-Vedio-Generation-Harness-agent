from __future__ import annotations

import json
from pathlib import Path

from app.backend.beat_projector import BeatProjector
from app.backend.intake_service import IntakeService
from app.backend.intake_store import IntakeStore
from app.backend.intake_workspace import IntakeWorkspaceStore, set_gate
from app.backend.jobs import JobManager
from app.backend.production_view import ProductionView
from app.backend.project_service import ProjectService
from app.backend.repository import AppDataRepository
from app.backend.review_service import ReviewService
from app.backend.schemas import PendingGate
from app.backend.stage_runner import StageRunner
from tests.test_intake import _write_demo_project
from ugc_harness.intake.agent import _log_tool
from ugc_harness.intake.host import append_message
from ugc_harness.intake.models import AgentYield


class _ToolLogAgent:
    def run_until_yield(self, session, runtime) -> AgentYield:
        _log_tool("call", "skill.activate", {"name": "inspect-graph"})
        _log_tool("done", "skill.activate", {"name": "inspect-graph"}, {"ok": True})
        _log_tool("call", "harness.list_graph", {})
        _log_tool("done", "harness.list_graph", {}, {"ok": True, "nodes": []})
        return AgentYield(session=session, message="图上看过了。", done=False)


class _ReplyAgent:
    def __init__(self, message: str = "记下了。") -> None:
        self.message = message
        self.seen: list[str] = []

    def run_until_yield(self, session, runtime) -> AgentYield:
        self.seen = [item.content for item in session.messages]
        return AgentYield(session=session, message=self.message, done=False)


class _StoreSpyAgent:
    def __init__(self, store: IntakeStore, session_id: str) -> None:
        self.store = store
        self.session_id = session_id
        self.persisted: list[str] = []

    def run_until_yield(self, session, runtime) -> AgentYield:
        loaded = self.store.load(self.session_id)
        self.persisted = [item.content for item in loaded.messages]
        return AgentYield(session=session, message="记下了。", done=False)


def _service(tmp_path: Path, agent=None) -> IntakeService:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    repository = AppDataRepository(tmp_path / "appdata")
    projects = ProjectService(output_root, repository)
    reviews = ReviewService(repository)
    beats = BeatProjector(projects, reviews)
    intake_root = tmp_path / "intake"
    return IntakeService(
        IntakeStore(intake_root),
        IntakeWorkspaceStore(intake_root),
        StageRunner(output_root),
        projects,
        reviews,
        beats,
        ProductionView(projects, beats),
        JobManager(),
        repository,
        agent=agent,
    )


def _seed_project(output_root: Path, name: str) -> Path:
    directory = output_root / name
    _write_demo_project(directory)
    (directory / "manifest.json").write_text(
        json.dumps({"project_name": name, "topic": name}),
        encoding="utf-8",
    )
    return directory


def test_handle_message_persists_user_text_before_agent_runs(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.create_session()
    spy = _StoreSpyAgent(service.store, created.session_id)
    service._agent = spy
    payload = service.handle_message(created.session_id, "想做咖啡教程")
    assert "想做咖啡教程" in spy.persisted
    assert any(item["content"] == "想做咖啡教程" for item in payload.messages)
    assert payload.reply == "记下了。"


def test_handle_message_records_intake_tools_and_terminal(tmp_path: Path) -> None:
    service = _service(tmp_path, agent=_ToolLogAgent())
    created = service.create_session()
    payload = service.handle_message(created.session_id, "先看看现在的图")
    turns = payload.run_log.intake_turns
    assert len(turns) == 1
    turn = turns[0]
    assert turn.received == "先看看现在的图"
    assert turn.reply == "图上看过了。"
    assert [item.tool for item in turn.tools] == ["skill.activate", "harness.list_graph"]
    assert turn.tools[0].skill == "inspect-graph"
    assert any("[intake] call skill.activate" in line for line in turn.lines)
    assert any("harness.list_graph" in line for line in turn.lines)
    assert turn.messages[0]["role"] == "system"
    assert "Skill 目录" in turn.messages[0]["content"]
    assert any(
        item["role"] == "user" and item["content"] == "先看看现在的图"
        for item in turn.messages
    )


def test_handle_message_sends_continue_phrases_to_the_host(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.create_session()
    spy = _StoreSpyAgent(service.store, created.session_id)
    service._agent = spy
    state = set_gate(
        service.workspace.load(created.session_id),
        PendingGate(kind="review", stage="narrative", question="可以继续做声音吗？"),
    )
    service.workspace.save(state)
    payload = service.handle_message(created.session_id, "可以")
    assert "可以" in spy.persisted
    assert payload.reply == "记下了。"
    assert any(item["content"] == "可以" for item in payload.messages)


def test_attach_project_switches_chat_and_workspace(tmp_path: Path) -> None:
    service = _service(tmp_path, agent=_ReplyAgent())
    _seed_project(tmp_path / "outputs", "alpha")
    _seed_project(tmp_path / "outputs", "beta")
    created = service.create_session()
    alpha = service.attach_project(created.session_id, "alpha")
    assert alpha.session_id == created.session_id
    assert alpha.project_key == "alpha"
    assert alpha.production.project_key == "alpha"

    session = append_message(
        service.store.load(alpha.session_id), "user", "这是 alpha 的对话"
    )
    service.store.save(session)

    beta = service.attach_project(alpha.session_id, "beta")
    assert beta.session_id != alpha.session_id
    assert beta.project_key == "beta"
    assert beta.production.project_key == "beta"
    assert not any("alpha 的对话" in item["content"] for item in beta.messages)
    assert any("已打开「beta」" in item["content"] for item in beta.messages)

    again = service.attach_project(beta.session_id, "alpha")
    assert again.session_id == alpha.session_id
    assert again.project_key == "alpha"
    assert any("alpha 的对话" in item["content"] for item in again.messages)


def test_continue_production_writes_start_message_into_intake_context(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.runner.run = lambda *args, **kwargs: None
    _seed_project(tmp_path / "outputs", "alpha")
    created = service.create_session()
    service.attach_project(created.session_id, "alpha")
    payload = service.continue_production(created.session_id, user_text="可以")
    texts = [item["content"] for item in payload.messages]
    assert "可以" in texts
    assert any(item.startswith("开始制作") for item in texts)
    assert payload.gate is not None
    assert payload.gate.kind == "running"
    stored = service.store.load(created.session_id)
    assert any(
        item.role == "agent" and item.content.startswith("开始制作")
        for item in stored.messages
    )


def test_handle_message_rejects_while_stage_is_running(tmp_path: Path) -> None:
    service = _service(tmp_path, agent=_ReplyAgent())
    created = service.create_session()
    state = set_gate(
        service.workspace.load(created.session_id),
        PendingGate(kind="running", stage="asset", question="素材正在生成。"),
    )
    service.workspace.save(state)
    try:
        service.handle_message(created.session_id, "顺便改一下旁白")
    except ValueError as exc:
        assert "还在制作" in str(exc)
    else:
        raise AssertionError("expected running stage to block chat")
    stored = service.store.load(created.session_id)
    assert not any(item.content == "顺便改一下旁白" for item in stored.messages)


def test_new_session_is_not_bound_to_previous_project(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _seed_project(tmp_path / "outputs", "alpha")
    first = service.create_session()
    service.attach_project(first.session_id, "alpha")
    second = service.create_session()
    payload = service.session_payload(second.session_id)
    assert payload.project_key is None
    assert payload.production.project_key is None
    assert payload.session_id != first.session_id
