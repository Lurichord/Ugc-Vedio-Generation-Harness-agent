"""Live intake component tests. Script the user; assert tools and working_brief."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tests.fixtures.intake_mcp import RecordingIntakeRuntime
from tests.fixtures.live import dump_json, live_settings
from tests.test_intake import _write_demo_project
from ugc_harness.harness.repair import load_project_state
from ugc_harness.intake.agent import IntentAgent, LlmIntentModel
from ugc_harness.intake.brief_sync import HeuristicBriefSync
from ugc_harness.intake.graph_view import candidate_graph_refs
from ugc_harness.intake.host import IntentHost, new_session
from ugc_harness.intake.models import Inbound, IntakeSession
from ugc_harness.intake.tools import (
    BRIEF_UPDATE,
    DESCRIPTION_GET_ELEMENT,
    DESCRIPTION_LIST_OUTLINE,
    HARNESS_INSPECT,
    HARNESS_LIST_GRAPH,
    HARNESS_REPAIR,
    IDENTITY_UPDATE,
    HARNESS_START_PROJECT,
    INTAKE_MCP_TOOLS,
    SKILL_ACTIVATE,
)
from ugc_harness.shared.llm import StructuredLLM

pytestmark = pytest.mark.live

ALLOWED_TOOLS = frozenset({SKILL_ACTIVATE, BRIEF_UPDATE, *INTAKE_MCP_TOOLS})
SKILL_BEFORE = {
    HARNESS_START_PROJECT: frozenset({"start-project"}),
    HARNESS_INSPECT: frozenset({"inspect-progress"}),
    HARNESS_LIST_GRAPH: frozenset({"inspect-graph", "revise-segment"}),
    HARNESS_REPAIR: frozenset({"inspect-graph", "revise-segment"}),
    DESCRIPTION_LIST_OUTLINE: frozenset({"inspect-graph", "revise-segment"}),
    DESCRIPTION_GET_ELEMENT: frozenset({"inspect-graph", "revise-segment"}),
    IDENTITY_UPDATE: frozenset({"inspect-graph", "revise-segment"}),
}


def _work_dir() -> Path:
    path = Path(".tmp") / "intake_live" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


class LiveIntake:
    def __init__(self) -> None:
        settings = live_settings()
        self.runtime = RecordingIntakeRuntime(_work_dir() / "session.json", dry_run=True)
        self.host = IntentHost(
            IntentAgent(LlmIntentModel(StructuredLLM(settings))),
            self.runtime,
            brief_sync=HeuristicBriefSync(),
        )
        self.session = new_session()

    def say(self, text: str) -> IntakeSession:
        self.session = self.host.respond(self.session, Inbound(text=text)).session
        return self.session

    def context(self) -> str:
        return dump_json(
            {
                "working_brief": self.session.working_brief.model_dump(mode="json"),
                "last_message": self.session.last_message,
                "tool_trace": self.runtime.tool_trace,
            }
        )


def _brief_parts(session: IntakeSession) -> dict[str, Any]:
    brief = session.working_brief
    return {
        "topic": brief.topic,
        "goal": brief.communication.goal if brief.communication else None,
        "audience": brief.audience.description if brief.audience else None,
        "platform": brief.target.platform if brief.target else None,
        "duration": brief.target.duration_target_ms if brief.target else None,
    }


def _filled(value: Any) -> bool:
    return value not in (None, "")


def _tool_names(intake: LiveIntake) -> list[str]:
    return [str(item["tool"]) for item in intake.runtime.tool_trace]


def _assert_catalog_and_skills(intake: LiveIntake) -> None:
    seen_skills: list[str] = []
    for item in intake.runtime.tool_trace:
        name = str(item["tool"])
        assert name in ALLOWED_TOOLS, f"编造了工具 {name}\n{intake.context()}"
        if name == SKILL_ACTIVATE:
            seen_skills.append(str(item["arguments"].get("name") or ""))
            continue
        required = SKILL_BEFORE.get(name)
        if required is None:
            continue
        assert any(skill in required for skill in seen_skills), (
            f"{name} 之前没有 skill.activate {sorted(required)}\n{intake.context()}"
        )


def _assert_brief_complete(intake: LiveIntake) -> None:
    parts = _brief_parts(intake.session)
    missing = [key for key, value in parts.items() if not _filled(value)]
    assert not missing, f"任务书仍缺 {missing}\n{intake.context()}"


def test_collects_brief_across_turns_without_starting() -> None:
    intake = LiveIntake()
    intake.say("做一条讲手冲咖啡的抖音")
    assert HARNESS_START_PROJECT not in _tool_names(intake), intake.context()
    intake.say("目标是让新手分清粉水比。")
    intake.say("给完全没冲过的人看。")
    intake.say("大概一分半。")
    _assert_catalog_and_skills(intake)
    assert HARNESS_START_PROJECT not in _tool_names(intake), intake.context()
    _assert_brief_complete(intake)
    parts = _brief_parts(intake.session)
    assert "手冲咖啡" in str(parts["topic"])
    assert parts["platform"] == "douyin"
    assert parts["duration"] == 90_000


def test_confirms_complete_brief_before_start() -> None:
    intake = LiveIntake()
    intake.say(
        "做一条讲手冲咖啡的抖音，目标是让新手分清粉水比。"
        "给完全没冲过的人看。大概一分半。"
    )
    assert HARNESS_START_PROJECT not in _tool_names(intake), intake.context()
    intake.say("可以，按这个开做")
    _assert_catalog_and_skills(intake)
    _assert_brief_complete(intake)
    names = _tool_names(intake)
    assert HARNESS_START_PROJECT in names, intake.context()
    start = next(
        item
        for item in intake.runtime.tool_trace
        if item["tool"] == HARNESS_START_PROJECT
    )
    assert not start["arguments"], intake.context()
    skills_before_start: list[str] = []
    for item in intake.runtime.tool_trace:
        if item["tool"] == SKILL_ACTIVATE:
            skills_before_start.append(str(item["arguments"].get("name") or ""))
        if item["tool"] == HARNESS_START_PROJECT:
            break
    assert "start-project" in skills_before_start, intake.context()
    assert start["result"].get("ok") is True, intake.context()
    assert start["result"].get("dry_run") is True, intake.context()


def test_topic_change_before_start_overwrites_brief() -> None:
    intake = LiveIntake()
    intake.say(
        "做一条讲手冲咖啡的抖音，目标是让新手分清粉水比。"
        "给完全没冲过的人看。大概一分半。"
    )
    intake.say("算了改成包粽子")
    _assert_catalog_and_skills(intake)
    assert HARNESS_START_PROJECT not in _tool_names(intake), intake.context()
    topic = intake.session.working_brief.topic or ""
    assert "包粽子" in topic, intake.context()
    assert "手冲咖啡" not in topic, intake.context()


def test_revision_uses_graph_tools_not_restart() -> None:
    intake = LiveIntake()
    project_dir = Path(".tmp") / "intake_live_revision" / uuid4().hex
    _write_demo_project(project_dir)
    intake.session.project_dir = str(project_dir)
    intake.say("第三段太长，收成两句")
    _assert_catalog_and_skills(intake)
    names = _tool_names(intake)
    assert HARNESS_START_PROJECT not in names, intake.context()
    skills = [
        str(item["arguments"].get("name") or "")
        for item in intake.runtime.tool_trace
        if item["tool"] == SKILL_ACTIVATE
    ]
    assert {"inspect-graph", "revise-segment"} & set(skills), intake.context()
    assert HARNESS_LIST_GRAPH in names or HARNESS_REPAIR in names, intake.context()
    graph_nodes = set(load_project_state(project_dir).dependency_graph.nodes)
    for item in intake.runtime.tool_trace:
        if item["tool"] != HARNESS_REPAIR:
            continue
        refs = item["arguments"].get("target_refs") or []
        instruction = str(item["arguments"].get("instruction") or "").strip()
        assert refs, intake.context()
        assert instruction, intake.context()
        for ref in refs:
            assert any(
                candidate in graph_nodes for candidate in candidate_graph_refs(str(ref))
            ), intake.context()


def test_vague_opening_does_not_invent_topic() -> None:
    intake = LiveIntake()
    intake.say("我想做个视频")
    _assert_catalog_and_skills(intake)
    assert not _filled(intake.session.working_brief.topic), intake.context()
    assert HARNESS_START_PROJECT not in _tool_names(intake), intake.context()
    message = intake.session.last_message or ""
    assert any(token in message for token in ("什么", "主题", "讲", "拍")), (
        intake.context()
    )
