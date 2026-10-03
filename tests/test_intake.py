import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ugc_harness.content import (
    AudienceDelta,
    CommunicationSpec,
    EvidenceNeed,
    PlannedBeat,
    Section,
    TargetSpec,
    WorldClaim,
)
from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.description import (
    ExplainerStructure,
    VideoDescription,
    VideoIntent,
    VideoWorld,
)
from ugc_harness.harness.models import (
    DependencyEdge,
    DependencyGraphState,
    ProjectState,
    RuntimeContext,
    VideoState,
)
from ugc_harness.intake.actions import list_dependency_graph, repair_description
from ugc_harness.intake.agent import IntentAgent, build_agent_messages
from ugc_harness.intake.brief_sync import HeuristicBriefSync, apply_brief_patch
from ugc_harness.intake.cli import build_parser, format_status
from ugc_harness.intake.description_view import get_element, list_outline
from ugc_harness.intake.graph_view import list_graph
from ugc_harness.intake.host import IntentHost, new_session, record_user_text
from ugc_harness.intake.models import (
    AgentDecision,
    BriefDraft,
    BriefPatch,
    Inbound,
    IntakeMessage,
    TargetPatch,
)
from ugc_harness.intake.mcp_runtime import IntakeMcpRuntime
from ugc_harness.intake.skills import SKILL_NAME_ERROR, list_skills, load_skill, validate_skill_name
from ugc_harness.intake.tools import (
    HARNESS_LIST_GRAPH,
    INTAKE_MCP_TOOLS,
    activate_skill,
    mcp_tool_specs,
)
from ugc_harness.intake.view import (
    apply_state_to_session,
    intent_draft_from_video_intent,
    materialize_brief,
    progress_from_video,
)
from ugc_harness.mcp_servers import intake as intake_mcp
from ugc_harness.mcp_servers import narrative as narrative_mcp
from ugc_harness.profiles.models import VideoProfileDecision

from tests.fixtures.intake_mcp import RecordingIntakeRuntime, make_recording_runtime


class ScriptedIntentModel:
    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = list(decisions)
        self.calls: list[dict[str, Any]] = []

    def decide(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AgentDecision:
        self.calls.append({"messages": messages, "tools": [item["name"] for item in tools]})
        if not self.decisions:
            return AgentDecision(message="备用回复")
        return self.decisions.pop(0)


def _runtime(*, dry_run: bool = True) -> RecordingIntakeRuntime:
    return make_recording_runtime(dry_run=dry_run)


def _host(
    decisions: list[AgentDecision],
    runtime: RecordingIntakeRuntime | None = None,
) -> tuple[IntentHost, ScriptedIntentModel, RecordingIntakeRuntime]:
    model = ScriptedIntentModel(decisions)
    bound = runtime or _runtime()
    host = IntentHost(
        IntentAgent(model),
        bound,
        brief_sync=HeuristicBriefSync(),
    )
    return host, model, bound


def _complete_brief(**updates: Any) -> BriefDraft:
    payload = {
        "topic": "为什么测试很重要",
        "production_mode": "explainer",
        "target": TargetPatch(duration_target_ms=90_000),
        "communication": CommunicationSpec(goal="让观众理解测试能降低返工成本"),
    }
    payload.update(updates)
    return BriefDraft.model_validate(payload)


def _presentation() -> VideoProfileDecision:
    return VideoProfileDecision(
        requested="b_roll",
        resolved="b_roll",
        selection_source="user",
        rationale="不需要出镜",
        speaker_presence_ratio_min=0,
        speaker_presence_ratio_max=0,
        character_consistency_required=False,
    )


def _tiny_description() -> VideoDescription:
    intent = VideoIntent(
        format_id="explainer",
        topic="单元测试",
        one_sentence_thesis="先写测试能少返工",
        promise="听完愿意写下第一个测试",
        audience={"description": "业务开发", "knowledge_level": "general"},
        communication=CommunicationSpec(goal="愿意写测试"),
        target=TargetSpec(),
        presentation=_presentation(),
    )
    world = VideoWorld(
        claims=[
            WorldClaim(
                claim_id="c1",
                statement="测试能降返工",
                epistemic_status="given_by_brief",
                evidence_required=False,
            )
        ]
    )
    sections = [
        Section(
            section_id="sec_hook",
            role="hook",
            target_duration_ms=15_000,
            goal="抓住注意",
            attention_strategy="反常识",
        ),
        Section(
            section_id="sec_body",
            role="body",
            target_duration_ms=60_000,
            goal="讲清机制",
            attention_strategy="对照",
        ),
        Section(
            section_id="sec_close",
            role="close",
            target_duration_ms=15_000,
            goal="收束行动",
            attention_strategy="邀请",
        ),
    ]
    beat = PlannedBeat(
        planned_beat_id="b3",
        section_id="sec_body",
        order=3,
        semantic_goal="第三段说明返工成本",
        discourse_role="explanation",
        relation_to_previous="continuation",
        target_effect="听懂",
        target_duration_ms=8_000,
        audience_delta=AudienceDelta(emotion_target="清醒"),
        evidence_need=EvidenceNeed(required=False),
        visual_intent_hint="示意图",
    )
    return VideoDescription(
        intent=intent,
        world=world,
        structure=ExplainerStructure(
            narrative_pattern="problem-insight",
            sections=sections,
            beats=[beat],
        ),
    )


def _tiny_graph() -> DependencyGraphState:
    graph = DependencyGraph(DependencyGraphState())
    graph.commit_batch(
        task_id="t_narrative",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("cast:host", "cast", "host"),
            NodeCommit("beat:b3", "beat", "beat three"),
            NodeCommit(
                "utterance:ss3",
                "utterance",
                "line three",
                edges=(DependencyEdge(to="beat:b3", family="plot"),),
            ),
            NodeCommit(
                "shot:shot_b3",
                "shot",
                "shot three",
                edges=(DependencyEdge(to="beat:b3", family="plot"),),
            ),
        ],
    )
    graph.commit_batch(
        task_id="t_voice",
        produced_by="voice_agent",
        commits=[
            NodeCommit(
                "audio:ss3",
                "audio",
                "audio three",
                edges=(DependencyEdge(to="utterance:ss3", family="element"),),
            ),
        ],
    )
    return graph.state


def _write_demo_project(project_dir: Path, *, with_graph: bool = True) -> None:
    harness_dir = project_dir / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    state = ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id="demo", state_version=2, narrative_status="ready"),
        description=_tiny_description(),
        dependency_graph=_tiny_graph() if with_graph else DependencyGraphState(),
    )
    (harness_dir / "project_state.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )


def test_empty_brief_cannot_materialize() -> None:
    try:
        materialize_brief(BriefDraft())
    except ValueError as exc:
        assert "topic" in str(exc)
    else:
        raise AssertionError("expected materialize to fail")


def test_materialize_waits_for_goal() -> None:
    try:
        materialize_brief(BriefDraft(topic="只给了主题"))
    except ValueError as exc:
        assert "goal" in str(exc)
    else:
        raise AssertionError("expected materialize to fail")


def test_materialize_builds_creative_brief() -> None:
    brief = materialize_brief(_complete_brief())
    assert brief.topic == "为什么测试很重要"
    assert brief.production_mode == "explainer"
    assert brief.target.duration_target_ms == 90_000


def test_brief_patch_overlays_only_provided_fields() -> None:
    current = apply_brief_patch(BriefDraft(), BriefPatch(topic="短视频怎么开头"))
    current = apply_brief_patch(
        current, BriefPatch(production_mode="explainer")
    )
    assert current.topic == "短视频怎么开头"
    assert current.production_mode == "explainer"


def test_platform_only_does_not_fill_duration() -> None:
    host, _, _ = _host([AgentDecision(message="大概做多久？")])
    result = host.respond(new_session(), Inbound(text="抖音吧"))
    target = result.session.working_brief.target
    assert target is not None
    assert target.platform == "douyin"
    assert target.duration_target_ms is None


def test_agent_brief_update_writes_goal_from_confirmation() -> None:
    host, _, runtime = _host(
        [
            AgentDecision(
                tool="brief.update",
                arguments={
                    "communication": {
                        "goal": "让第一次包粽子的新手看完，能跟着步骤在家包出不散的粽子"
                    }
                },
            ),
            AgentDecision(message="打算发哪个平台？"),
        ]
    )
    session = new_session()
    session = session.model_copy(update={"working_brief": BriefDraft(topic="端午节")})
    result = host.respond(session, Inbound(text="可以的"))
    assert result.session.working_brief.topic == "端午节"
    assert result.session.working_brief.communication is not None
    assert "包粽子" in result.session.working_brief.communication.goal
    assert result.message == "打算发哪个平台？"
    assert runtime.mcp_calls == []
    assert [item["tool"] for item in runtime.tool_trace] == ["brief.update"]


def test_recording_runtime_accumulates_host_and_mcp_tools() -> None:
    runtime = _runtime(dry_run=True)
    host, _, runtime = _host(
        [
            AgentDecision(tool="skill.activate", arguments={"name": "start-project"}),
            AgentDecision(tool="brief.update", arguments={"topic": "包粽子"}),
            AgentDecision(tool="harness.start_project"),
            AgentDecision(message="dry-run 已收成任务书。"),
        ],
        runtime,
    )
    host.respond(
        new_session(),
        Inbound(
            text=(
                "主题是为什么测试很重要，目标是让观众理解测试能降低返工成本，"
                "做讲解，一分半。开做吧。"
            )
        ),
    )
    assert [item["tool"] for item in runtime.tool_trace] == [
        "skill.activate",
        "brief.update",
        "harness.start_project",
    ]
    assert runtime.mcp_calls == [("harness.start_project", {})]


def test_brief_update_then_start_project_sees_new_goal() -> None:
    runtime = _runtime(dry_run=True)
    host, _, runtime = _host(
        [
            AgentDecision(
                tool="brief.update",
                arguments={
                    "communication": {"goal": "让观众理解测试能降低返工成本"},
                    "audience": {"description": "业务开发"},
                    "target": {"platform": "douyin", "duration_target_ms": 90_000},
                    "production_mode": "explainer",
                },
            ),
            AgentDecision(tool="harness.start_project"),
            AgentDecision(message="已经按任务书开工。"),
        ],
        runtime,
    )
    result = host.respond(new_session(), Inbound(text="主题是为什么测试很重要"))
    assert runtime.mcp_calls[0][0] == "harness.start_project"
    assert result.session.project_id
    assert result.session.working_brief.communication is not None
    assert "降低返工" in result.session.working_brief.communication.goal
    assert result.message == "已经按任务书开工。"


def test_brief_update_rejects_invalid_arguments() -> None:
    host, _, _ = _host(
        [
            AgentDecision(tool="brief.update", arguments={"communication": "不是对象"}),
            AgentDecision(message="刚才没记下，再说一次目标？"),
        ]
    )
    result = host.respond(new_session(), Inbound(text="可以的"))
    assert result.session.working_brief.communication is None
    assert result.message == "刚才没记下，再说一次目标？"


def test_host_writes_brief_from_user_text_without_agent_tool() -> None:
    host, model, runtime = _host([AgentDecision(message="目标是什么？")])
    result = host.respond(
        new_session(),
        Inbound(text="做一条讲咖啡萃取的抖音，大概一分半"),
    )
    brief = result.session.working_brief
    assert brief.topic == "咖啡萃取"
    assert brief.target is not None
    assert brief.target.duration_target_ms == 90_000
    assert brief.target.platform == "douyin"
    assert result.message == "目标是什么？"
    assert result.session.messages[-2].content.startswith("做一条讲咖啡萃取")
    first_system = model.calls[0]["messages"][0]["content"]
    assert "咖啡萃取" in first_system


def test_vague_user_text_does_not_invent_topic() -> None:
    host, _, runtime = _host([AgentDecision(message="想做什么主题？")])
    result = host.respond(new_session(), Inbound(text="做个视频"))
    assert result.session.working_brief.topic is None
    assert result.session.messages[-1].role == "agent"


def test_agent_can_reply_with_zero_tools() -> None:
    host, model, runtime = _host([AgentDecision(message="先告诉我主题。")])
    result = host.respond(new_session(), Inbound(text="做个视频"))
    assert result.done is False
    assert result.session.status == "waiting_user"
    assert runtime.mcp_calls == []
    assert model.calls[0]["tools"]
    assert "description.list_outline" in model.calls[0]["tools"]
    assert "skill.activate" in model.calls[0]["tools"]
    assert "brief.update" in model.calls[0]["tools"]
    assert "narrative.explainer.plan_sections" not in model.calls[0]["tools"]


def test_host_does_not_duplicate_already_recorded_user_text() -> None:
    host, _, _ = _host([AgentDecision(message="主题是什么？")])
    session = record_user_text(new_session(), "做个视频")
    result = host.respond(session, Inbound(text="做个视频"))
    users = [item.content for item in result.session.messages if item.role == "user"]
    assert users == ["做个视频"]
    assert result.message == "主题是什么？"


def test_dialogue_is_kept_complete_across_turns() -> None:
    host, _, _ = _host(
        [
            AgentDecision(message="目标呢？"),
            AgentDecision(message="好，记下了。"),
        ]
    )
    session = new_session()
    first = host.respond(session, Inbound(text="做一条讲咖啡萃取的抖音"))
    second = host.respond(
        first.session,
        Inbound(text="目标是让新手分清粉水比。"),
    )
    texts = [item.content for item in second.session.messages if item.role == "user"]
    assert "做一条讲咖啡萃取的抖音" in texts
    assert "目标是让新手分清粉水比。" in texts
    assert second.session.working_brief.topic == "咖啡萃取"
    assert second.session.working_brief.communication is not None
    assert "粉水比" in second.session.working_brief.communication.goal


def test_agent_messages_include_full_dialogue() -> None:
    session = new_session()
    extra: list[IntakeMessage] = []
    for index in range(20):
        extra.append(IntakeMessage(role="user", content=f"用户第{index}句"))
        extra.append(IntakeMessage(role="agent", content=f"助手第{index}句"))
    session = session.model_copy(update={"messages": [*session.messages, *extra]})
    rendered = build_agent_messages(session, "说明书")
    contents = [item["content"] for item in rendered if item["role"] != "system"]
    assert contents[0] == session.messages[0].content
    assert "用户第0句" in contents
    assert "用户第19句" in contents
    assert contents[-1] == "助手第19句"
    assert len(contents) == len(session.messages)


def test_context_has_catalog_and_memory_not_project_state() -> None:
    session = new_session()
    session.working_brief = BriefDraft(topic="测试")
    messages = build_agent_messages(session, "意图解析层说明书")
    blob = json.dumps(messages, ensure_ascii=False)
    assert "意图解析层说明书" in blob
    assert "working_brief" in blob
    assert "Skill 目录" in blob
    assert "dependency_graph" not in blob
    assert "trajectory" not in blob


def test_progress_from_video_copies_status_enums() -> None:
    video = VideoState(
        project_id="p1",
        state_version=3,
        narrative_status="ready",
        voice_status="failed",
    )
    progress = progress_from_video(video)
    assert progress.narrative == "ready"
    assert progress.voice == "failed"


def test_apply_state_does_not_need_description() -> None:
    session = new_session()
    state = ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id="p2", state_version=1, narrative_status="ready"),
    )
    updated = apply_state_to_session(session, state)
    assert updated.progress.narrative == "ready"
    assert updated.working_intent.topic is None


def test_intent_draft_roundtrip_from_video_intent() -> None:
    intent = VideoIntent(
        format_id="explainer",
        topic="单元测试",
        one_sentence_thesis="先写测试能少返工",
        promise="听完愿意写下第一个测试",
        audience={"description": "业务开发", "knowledge_level": "general"},
        communication=CommunicationSpec(goal="愿意写测试"),
        target=TargetSpec(),
        presentation=_presentation(),
    )
    draft = intent_draft_from_video_intent(intent)
    assert draft.presentation is not None
    assert draft.presentation.resolved == "b_roll"


def test_host_start_project_dry_run() -> None:
    runtime = _runtime(dry_run=True)
    host, model, runtime = _host(
        [
            AgentDecision(tool="harness.start_project"),
            AgentDecision(message="已经收成任务书，dry-run 没有真正开工。"),
        ],
        runtime,
    )
    result = host.respond(
        new_session(),
        Inbound(
            text="主题是为什么测试很重要，目标是让观众理解测试能降低返工成本，做讲解，一分半。开做吧。"
        ),
    )
    assert runtime.mcp_calls[0][0] == "harness.start_project"
    assert result.session.project_id
    assert result.session.working_brief.topic == "为什么测试很重要"
    assert result.done is False
    system = model.calls[0]["messages"][0]["content"]
    assert "working_brief" in system
    assert "不要编造" in system or "字段说明" in system


def test_start_project_rejects_incomplete_brief() -> None:
    host, _, _ = _host(
        [
            AgentDecision(tool="harness.start_project"),
            AgentDecision(message="目标还没定，先别开工。"),
        ]
    )
    result = host.respond(
        new_session(),
        Inbound(text="主题是只给了主题，开做吧"),
    )
    assert result.session.project_id is None
    assert "目标" in result.message


def test_host_refreshes_progress_before_agent_sees_memory() -> None:
    project_dir = Path(".tmp") / "test_intake_inspect"
    harness_dir = project_dir / "harness"
    harness_dir.mkdir(parents=True, exist_ok=True)
    state = ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id="demo", state_version=4, narrative_status="ready"),
    )
    (harness_dir / "project_state.json").write_text(
        state.model_dump_json(), encoding="utf-8"
    )
    session = new_session()
    session.project_dir = str(project_dir)
    host, model, _ = _host([AgentDecision(message="narrative 已经好了。")])
    result = host.respond(session, Inbound(text="现在做到哪了"))
    assert result.session.progress.narrative == "ready"
    assert result.session.progress.state_version == 4
    memory = model.calls[0]["messages"][0]["content"]
    assert '"narrative": "ready"' in memory


def test_inspect_graph_then_repair() -> None:
    project_dir = Path(".tmp") / "test_intake_inspect_graph"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    runtime = _runtime()
    host, _, runtime = _host(
        [
            AgentDecision(tool="skill.activate", arguments={"name": "inspect-graph"}),
            AgentDecision(tool="harness.list_graph"),
            AgentDecision(
                tool="harness.list_graph",
                arguments={"around_ref": "beat:b3"},
            ),
            AgentDecision(
                tool="harness.repair",
                arguments={
                    "target_refs": ["beat:b3"],
                    "instruction": "把开场口播收成两句，保留钩子问题",
                },
            ),
            AgentDecision(message="已经按依赖图把开场那段派去改短。"),
        ],
        runtime,
    )
    before = (project_dir / "harness" / "project_state.json").read_text(encoding="utf-8")
    result = host.respond(session, Inbound(text="开场这段不对，改短一点"))
    assert (project_dir / "harness" / "project_state.json").read_text(
        encoding="utf-8"
    ) == before
    assert [name for name, _ in runtime.mcp_calls] == [
        "harness.list_graph",
        "harness.list_graph",
        "harness.repair",
    ]
    assert runtime.mcp_calls[0][1] == {}
    assert runtime.mcp_calls[1][1]["around_ref"] == "beat:b3"
    assert runtime.mcp_calls[-1][1]["target_refs"] == ["beat:b3"]
    assert result.message == "已经按依赖图把开场那段派去改短。"


def test_outline_list_graph_repair_then_reply() -> None:
    project_dir = Path(".tmp") / "test_intake_repair"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    runtime = _runtime()
    host, _, runtime = _host(
        [
            AgentDecision(tool="skill.activate", arguments={"name": "revise-segment"}),
            AgentDecision(tool="description.list_outline"),
            AgentDecision(tool="harness.list_graph"),
            AgentDecision(
                tool="harness.list_graph",
                arguments={"around_ref": "beat:b3"},
            ),
            AgentDecision(
                tool="harness.repair",
                arguments={
                    "target_refs": ["planned_beat:b3"],
                    "instruction": "收成两句",
                },
            ),
            AgentDecision(message="已经派去改第三段。"),
        ],
        runtime,
    )
    result = host.respond(session, Inbound(text="第三段太长，收成两句"))
    assert [name for name, _ in runtime.mcp_calls] == [
        "description.list_outline",
        "harness.list_graph",
        "harness.list_graph",
        "harness.repair",
    ]
    assert runtime.mcp_calls[2][1]["around_ref"] == "beat:b3"
    assert runtime.mcp_calls[-1][1]["target_refs"] == ["planned_beat:b3"]
    assert result.message == "已经派去改第三段。"
    assert result.session.messages[0].role == "agent"
    assert any(item.content == "第三段太长，收成两句" for item in result.session.messages)


def test_list_outline_and_get_element_do_not_dump_all_shots() -> None:
    description = _tiny_description()
    outline = list_outline(description)
    refs = [item["ref"] for item in outline]
    assert "section:sec_body" in refs
    assert "beat:b3" in refs
    payload = get_element(description, "beat:b3")
    assert payload is not None
    assert payload["element"]["planned_beat_id"] == "b3"
    assert get_element(description, "beat:missing") is None


def test_list_graph_returns_content_roots_without_hashes() -> None:
    overview = list_graph(_tiny_graph())
    assert overview["ok"] is True
    assert overview["scope"] == "roots"
    refs = [item["ref"] for item in overview["nodes"]]
    assert refs == ["beat:b3", "cast:host"]
    for node in overview["nodes"]:
        assert "content_hash" not in node
        assert "version" not in node
        assert "dependency_hashes" not in node
        assert {
            "ref",
            "kind",
            "produced_by",
            "status",
            "locked",
            "depends_on",
            "dependents",
        } <= set(node)
    assert "section:sec_body" not in refs
    assert "clip:clip_b3" not in refs
    assert "shot:shot_b3" not in refs


def test_list_graph_around_ref_includes_downstream_audio() -> None:
    around = list_graph(_tiny_graph(), around_ref="beat:b3")
    assert around["ok"] is True
    assert around["resolved_ref"] == "beat:b3"
    refs = {item["ref"] for item in around["nodes"]}
    assert refs == {
        "beat:b3",
        "utterance:ss3",
        "shot:shot_b3",
        "audio:ss3",
    }
    by_ref = {item["ref"]: item for item in around["nodes"]}
    assert by_ref["beat:b3"]["dependents_by_agent"]["narrative_agent"] == [
        "shot:shot_b3",
        "utterance:ss3",
    ]
    assert by_ref["utterance:ss3"]["dependents_by_agent"]["voice_agent"] == [
        "audio:ss3"
    ]
    assert "section:sec_body" not in refs
    assert "clip:clip_b3" not in refs
    missing = list_graph(_tiny_graph(), around_ref="beat:missing")
    assert missing["ok"] is False
    shot_around = list_graph(_tiny_graph(), around_ref="shot:shot_b3")
    around_refs = {item["ref"] for item in shot_around["nodes"]}
    assert around_refs == {"shot:shot_b3", "beat:b3"}
    assert "clip:clip_b3" not in around_refs
    assert "audio:ss3" not in around_refs


def test_list_graph_and_repair_require_a_project() -> None:
    session = new_session()
    listed = list_dependency_graph(session)
    assert listed["ok"] is False
    _, repaired = repair_description(session, ["beat:b3"], "收短")
    assert repaired["ok"] is False


def test_repair_resolves_description_ref_to_graph_node() -> None:
    project_dir = Path(".tmp") / "test_intake_repair_alias"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    before = (project_dir / "harness" / "project_state.json").read_text(encoding="utf-8")
    _, preview = repair_description(
        session, ["planned_beat:b3"], "收成两句", dry_run=True
    )
    assert preview["ok"] is True
    assert preview["dry_run"] is True
    assert preview["target_refs"] == ["beat:b3"]
    assert preview["shot_refs"] == ["shot:shot_b3"]
    assert set(preview["invalidated"]) == {"shot:shot_b3"}
    assert preview["planned_tasks"] >= 1
    assert (project_dir / "harness" / "project_state.json").read_text(
        encoding="utf-8"
    ) == before
    _, unwired = repair_description(session, ["beat:b3"], "收成两句")
    assert unwired["ok"] is False
    assert unwired["error"] == "harness.repair 执行器未接线"
    assert unwired["target_refs"] == ["beat:b3"]


def test_explainer_utterance_repair_does_not_expand_to_shots() -> None:
    project_dir = Path(".tmp") / "test_intake_repair_utterance"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    _, preview = repair_description(
        session, ["utterance:ss3"], "只重配这一句，不改词", dry_run=True
    )
    assert preview["ok"] is True
    assert preview["shot_refs"] == []
    assert "shot:shot_b3" not in preview["invalidated"]
    assert "audio:ss3" in preview["invalidated"]


def test_explainer_cast_repair_only_runs_narrative_on_shots() -> None:
    from ugc_harness.harness.repair import load_project_state, write_project_state
    from tests.test_repair import commit_repair_targets

    project_dir = Path(".tmp") / "test_intake_repair_cast"
    _write_demo_project(project_dir)
    state = load_project_state(project_dir)
    graph = DependencyGraph(state.dependency_graph)
    graph.commit_batch(
        task_id="attach_host_shot",
        produced_by="narrative_agent",
        commits=[
            NodeCommit(
                "shot:shot_b3",
                "shot",
                "shot three",
                edges=(
                    DependencyEdge(to="beat:b3", family="plot"),
                    DependencyEdge(to="cast:host", family="identity"),
                ),
            )
        ],
    )
    graph.commit_batch(
        task_id="attach_host_audio",
        produced_by="voice_agent",
        commits=[
            NodeCommit(
                "audio:ss3",
                "audio",
                "audio three",
                edges=(
                    DependencyEdge(to="utterance:ss3", family="element"),
                    DependencyEdge(to="cast:host", family="element"),
                ),
            )
        ],
    )
    write_project_state(project_dir, state)
    overview = list_graph(state)
    host = next(item for item in overview["nodes"] if item["ref"] == "cast:host")
    assert host["dependents_by_agent"]["narrative_agent"] == ["shot:shot_b3"]
    assert host["dependents_by_agent"]["voice_agent"] == ["audio:ss3"]
    session = new_session()
    session.project_dir = str(project_dir)
    _, preview = repair_description(
        session, ["cast:host"], "将主持人改为男性并重配", dry_run=True
    )
    assert preview["ok"] is True
    assert set(preview["invalidated"]) == {"shot:shot_b3"}
    ran: list[str] = []

    def run_task(task, instruction: str) -> None:
        ran.append(task.agent)
        assert instruction == "将主持人改为男性并重配"
        commit_repair_targets(project_dir, task)

    _, result = repair_description(
        session, ["cast:host"], "将主持人改为男性并重配", run_task=run_task
    )
    assert result["ok"] is True
    assert result["complete"] is True
    assert ran == ["narrative_agent"]
    final = load_project_state(project_dir)
    assert final.dependency_graph.nodes["shot:shot_b3"].status == "current"
    assert final.dependency_graph.nodes["audio:ss3"].status == "current"


def test_explainer_repair_rejects_mixed_shot_and_audio() -> None:
    project_dir = Path(".tmp") / "test_intake_repair_mixed"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    _, result = repair_description(
        session,
        ["shot:shot_b3", "audio:ss3"],
        "画面和配音一起改",
    )
    assert result["ok"] is False
    assert "一个制作 agent" in result["error"]


def test_repair_runs_injected_executor_until_current() -> None:
    from tests.test_repair import commit_repair_targets

    project_dir = Path(".tmp") / "test_intake_repair_run"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    ran: list[str] = []

    def run_task(task, instruction: str) -> None:
        ran.append(task.agent)
        assert instruction == "收成两句"
        commit_repair_targets(project_dir, task)

    _, result = repair_description(
        session, ["beat:b3"], "收成两句", run_task=run_task
    )
    assert result["ok"] is True
    assert result["complete"] is True
    assert result["dry_run"] is False
    assert result["shot_refs"] == ["shot:shot_b3"]
    assert ran == ["narrative_agent"]
    from ugc_harness.harness.repair import load_project_state

    state = load_project_state(project_dir)
    for ref in result["invalidated"]:
        assert state.dependency_graph.nodes[ref].status == "current"
    assert state.dependency_graph.nodes["beat:b3"].status == "current"
    assert state.dependency_graph.nodes["audio:ss3"].status == "current"


def test_skill_catalog_and_activate() -> None:
    names = {item["name"] for item in list_skills()}
    assert names >= {
        "clarify-brief",
        "user-revises",
        "start-project",
        "inspect-graph",
        "revise-segment",
        "revise-media",
        "inspect-progress",
    }
    body = load_skill("inspect-graph")
    assert "harness.list_graph" in body
    assert "harness.repair" in body
    assert "dependents_by_agent" in body
    assert "输出格式" in body
    assert '"target_refs"' in body
    assert "cast" in body and "utterance" in body and "shot" in body
    clarify = load_skill("clarify-brief")
    assert "制作粽子" in clarify
    assert "production_mode" in clarify
    start = load_skill("start-project")
    assert "production_mode" in start
    revise = load_skill("revise-segment")
    assert "harness.repair" in revise
    assert "harness.list_graph" in revise
    assert "inspect-graph" in revise
    media = load_skill("revise-media")
    assert "layer" in media
    assert "asset" in media
    assert "voice" in media
    from ugc_harness.intake.skills import SKILLS_ROOT

    instructions = (SKILLS_ROOT.parent / "agent.md").read_text(encoding="utf-8")
    assert "## 输出格式" in instructions
    assert '"tool"' in instructions
    assert '"arguments"' in instructions
    assert '"done"' in instructions
    assert "brief.update" in instructions
    for skill_name in names:
        assert "输出格式" in load_skill(skill_name)
    try:
        validate_skill_name("revise_segment")
    except ValueError as exc:
        assert str(exc) == SKILL_NAME_ERROR
    else:
        raise AssertionError("underscore skill names must be rejected")
    rejected = activate_skill("revise_segment")
    assert rejected["ok"] is False
    assert rejected["error"] == SKILL_NAME_ERROR


def test_intake_mcp_tools_advertise_descriptions_and_parameters() -> None:
    specs = {item["name"]: item for item in mcp_tool_specs()}
    assert list(specs) == list(INTAKE_MCP_TOOLS)
    for item in specs.values():
        assert item["description"], item["name"]
        assert item["parameters"].get("type") == "object", item["name"]
    assert "structure 大纲" in specs["description.list_outline"]["description"]
    ref = specs["description.get_element"]["parameters"]["properties"]["ref"]
    assert "beat:b3" in str(ref.get("description") or "")
    kind = specs["identity.create"]["parameters"]["properties"]["kind"]
    enum_values = kind.get("enum") or [
        option.get("const")
        for option in kind.get("anyOf") or []
        if isinstance(option, dict)
    ]
    assert "cast" in enum_values
    assert "prop" in enum_values
    patches = specs["identity.update"]["parameters"]["properties"]["patches"]
    assert patches.get("description")
    repair = specs["harness.repair"]["parameters"]["properties"]
    assert "身份" in str(repair["target_refs"].get("description") or "")
    assert repair["instruction"].get("description")
    assert "layer" in repair


def test_intake_mcp_tools_are_isolated_from_narrative() -> None:
    assert "narrative.explainer.plan_sections" not in INTAKE_MCP_TOOLS
    assert "narrative.submit_candidate" not in INTAKE_MCP_TOOLS
    assert hasattr(intake_mcp, "description_list_outline")
    assert hasattr(narrative_mcp, "CONFIGURE_TOOL")
    assert intake_mcp.CONFIGURE_TOOL == "intake.configure_session"
    assert "brief.update" not in INTAKE_MCP_TOOLS
    assert HARNESS_LIST_GRAPH in INTAKE_MCP_TOOLS
    assert hasattr(intake_mcp, "harness_list_graph")
    assert hasattr(intake_mcp, "identity_create")
    assert hasattr(intake_mcp, "shot_create")
    assert hasattr(intake_mcp, "shot_bind")
    assert hasattr(intake_mcp, "graph_remove")


def test_intake_stdio_mcp_exposes_only_intent_tools() -> None:
    import asyncio

    from ugc_harness.agents.generic import McpToolTransport
    from ugc_harness.intake.tools import CONFIGURE_SESSION
    from ugc_harness.tools.mcp import intake_stdio_server_config

    session = new_session()
    path = Path(".tmp") / "intake_mcp" / f"{uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.model_dump_json(), encoding="utf-8")

    async def _probe() -> tuple[set[str], dict[str, Any]]:
        transport = McpToolTransport(
            intake_stdio_server_config(),
            configure_tool=CONFIGURE_SESSION,
            configure_payload={"session_path": str(path), "dry_run": True},
        )
        async with transport.open() as channel:
            names = set(await channel.list_tools())
            started = await channel.call("harness.start_project", {})
            return names, started

    names, started = asyncio.run(_probe())
    assert CONFIGURE_SESSION in names
    assert set(INTAKE_MCP_TOOLS) <= names
    assert "narrative.explainer.plan_sections" not in names
    assert "brief.update" not in names
    assert started["ok"] is False
    assert "goal" in started["error"] or "CreativeBrief" in started["error"]


def test_cli_parser_and_status() -> None:
    args = build_parser().parse_args(["--dry-run", "--output-root", "outputs"])
    assert args.dry_run is True
    session = new_session()
    assert session.status in format_status(session)
    assert "topic" in format_status(session)


def test_new_session_starts_waiting_user() -> None:
    session = new_session()
    assert session.status == "waiting_user"
    assert session.last_message
    assert session.working_brief.topic is None
    assert session.working_intent.presentation is None


def test_asset_layer_does_not_invalidate_spec() -> None:
    from tests.test_shot_graph_production import (
        _plant_identity_images,
        _plant_shot_videos,
    )
    from tests.test_shot_repair import _write_tutorial_project
    from ugc_harness.harness.repair import load_project_state, write_project_state

    project_dir = Path(".tmp") / f"intake_asset_layer_{uuid4().hex}"
    state = _write_tutorial_project(project_dir)
    _plant_identity_images(state, project_dir)
    _plant_shot_videos(state, project_dir)
    state.evaluation.stages.references = "passed"
    state.evaluation.stages.asset = "passed"
    write_project_state(project_dir, state)
    shot_id = state.production.shots[0].spec.shot_id
    shot_ref = f"shot:{shot_id}"
    before = state.dependency_graph.nodes[shot_ref].status
    assert before == "current"
    session = new_session()
    session.project_dir = str(project_dir)
    ran: list[str] = []

    def run_task(task, instruction: str) -> None:
        ran.append(task.agent)
        assert task.input_hash == "repair-asset"
        assert instruction == "机位再近一点"

    _, result = repair_description(
        session,
        [shot_ref],
        "机位再近一点",
        layer="asset",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert result["layer"] == "asset"
    assert result["invalidated"] == []
    assert shot_id in result["regenerated_shot_ids"]
    assert ran == ["asset_stage"]
    final = load_project_state(project_dir)
    assert final.dependency_graph.nodes[shot_ref].status == "current"


def test_asset_layer_expands_bound_shots_and_rejects_audio() -> None:
    from tests.test_shot_graph_production import (
        _plant_identity_images,
        _plant_shot_videos,
    )
    from tests.test_shot_repair import _write_tutorial_project
    from ugc_harness.harness.repair import write_project_state
    from ugc_harness.harness.shot_repair import resolve_shot_refs

    project_dir = Path(".tmp") / f"intake_asset_expand_{uuid4().hex}"
    state = _write_tutorial_project(project_dir)
    _plant_identity_images(state, project_dir)
    _plant_shot_videos(state, project_dir)
    state.evaluation.stages.references = "passed"
    state.evaluation.stages.asset = "passed"
    write_project_state(project_dir, state)
    shot = next(item for item in state.production.shots if item.spec.binds)
    prop_ref = next(bind for bind in shot.spec.binds if bind.startswith("prop:"))
    dependent = [
        ref.partition(":")[2] for ref in resolve_shot_refs(state, [prop_ref])
    ]
    session = new_session()
    session.project_dir = str(project_dir)
    _, preview = repair_description(
        session, [prop_ref], "脸再清楚，衣服不要变", layer="asset", dry_run=True
    )
    assert preview["ok"] is True
    assert preview["invalidated"] == []
    assert preview["regenerated_identity_refs"] == [prop_ref]
    assert set(preview["regenerated_shot_ids"]) >= set(dependent)
    demo = Path(".tmp") / f"intake_asset_audio_{uuid4().hex}"
    _write_demo_project(demo)
    session.project_dir = str(demo)
    _, rejected = repair_description(
        session, ["audio:ss3"], "重出成片", layer="asset"
    )
    assert rejected["ok"] is False
    assert "不能圈配音" in rejected["error"]


def test_asset_layer_rejects_explainer_and_missing_realization() -> None:
    project_dir = Path(".tmp") / f"intake_asset_explainer_{uuid4().hex}"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    _, explainer = repair_description(
        session, ["shot:shot_b3"], "图不好看", layer="asset"
    )
    assert explainer["ok"] is False
    assert "短剧" in explainer["error"]
    from tests.test_shot_repair import _write_tutorial_project

    empty = Path(".tmp") / f"intake_asset_empty_{uuid4().hex}"
    state = _write_tutorial_project(empty)
    session.project_dir = str(empty)
    shot_ref = f"shot:{state.production.shots[0].spec.shot_id}"
    _, missing = repair_description(
        session, [shot_ref], "重出这一镜", layer="asset"
    )
    assert missing["ok"] is False
    assert "还没有参考图或成片" in missing["error"]


def test_voice_layer_does_not_invalidate_and_rejects_shots() -> None:
    project_dir = Path(".tmp") / f"intake_voice_layer_{uuid4().hex}"
    _write_demo_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    before = (
        (project_dir / "harness" / "project_state.json").read_text(encoding="utf-8")
    )
    _, preview = repair_description(
        session,
        ["audio:ss3"],
        "只重配这一句，不改音色",
        layer="voice",
        dry_run=True,
    )
    assert preview["ok"] is True
    assert preview["layer"] == "voice"
    assert preview["invalidated"] == []
    assert preview["audio_refs"] == ["audio:ss3"]
    assert (project_dir / "harness" / "project_state.json").read_text(
        encoding="utf-8"
    ) == before
    _, rejected = repair_description(
        session, ["shot:shot_b3"], "换个声音", layer="voice"
    )
    assert rejected["ok"] is False
    assert "不能圈 shot" in rejected["error"]
    _, bad_layer = repair_description(
        session, ["audio:ss3"], "换声", layer="image"
    )
    assert bad_layer["ok"] is False
    assert "layer 只能是" in bad_layer["error"]


def test_asset_layer_expands_continue_shots() -> None:
    from tests.test_description import _drama_artifact
    from tests.test_shot_graph_production import (
        _plant_identity_images,
        _plant_shot_videos,
    )
    from ugc_harness.harness.dependencies import DependencyGraph
    from ugc_harness.harness.dependency_builders import narrative_commits
    from ugc_harness.harness.description_builder import apply_narrative_document
    from ugc_harness.harness.models import ProjectContext
    from ugc_harness.harness.repair import write_project_state

    project_dir = Path(".tmp") / f"intake_asset_continue_{uuid4().hex}"
    artifact = _drama_artifact()
    state = ProjectState(
        context=ProjectContext(
            project_id=artifact.brief.project_id,
            state_version=1,
            constraints={"narrative_format": "drama"},
        )
    )
    apply_narrative_document(state, artifact)
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="seed",
        produced_by="narrative_agent",
        commits=narrative_commits(artifact),
    )
    project_dir.mkdir(parents=True, exist_ok=True)
    write_project_state(project_dir, state)
    (project_dir / "narrative_artifact.json").write_text(
        artifact.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _plant_identity_images(state, project_dir)
    _plant_shot_videos(state, project_dir)
    state.evaluation.stages.references = "passed"
    state.evaluation.stages.asset = "passed"
    write_project_state(project_dir, state)
    follow = next(
        item
        for item in state.production.shots
        if item.continuity.join == "continue_from_previous"
    )
    previous_id = follow.continuity.previous_shot_id
    assert previous_id
    session = new_session()
    session.project_dir = str(project_dir)
    _, preview = repair_description(
        session,
        [f"shot:{previous_id}"],
        "重出这一镜",
        layer="asset",
        dry_run=True,
    )
    assert preview["ok"] is True
    assert previous_id in preview["regenerated_shot_ids"]
    assert follow.spec.shot_id in preview["regenerated_shot_ids"]
    bridge = next(
        item
        for item in state.production.shots
        if item.continuity.join == "bridge_from_previous"
    )
    _, bridge_preview = repair_description(
        session,
        [f"shot:{bridge.spec.shot_id}"],
        "重出这一镜",
        layer="asset",
        dry_run=True,
    )
    assert bridge_preview["ok"] is True
    assert bridge.spec.shot_id in bridge_preview["regenerated_shot_ids"]
    for item in state.production.shots:
        if (
            item.continuity.join in {"continue_from_previous", "bridge_from_previous"}
            and item.continuity.previous_shot_id == bridge.spec.shot_id
        ):
            assert item.spec.shot_id in bridge_preview["regenerated_shot_ids"]


def test_list_graph_marks_realization_flags() -> None:
    from tests.test_shot_graph_production import (
        _plant_identity_images,
        _plant_shot_videos,
    )
    from tests.test_shot_repair import _write_tutorial_project
    from ugc_harness.harness.repair import write_project_state

    project_dir = Path(".tmp") / f"intake_graph_flags_{uuid4().hex}"
    state = _write_tutorial_project(project_dir)
    _plant_identity_images(state, project_dir)
    _plant_shot_videos(state, project_dir)
    write_project_state(project_dir, state)
    shot = state.production.shots[0]
    identity = next(iter(shot.spec.binds))
    around = list_graph(state, around_ref=f"shot:{shot.spec.shot_id}")
    by_ref = {item["ref"]: item for item in around["nodes"]}
    assert by_ref[f"shot:{shot.spec.shot_id}"]["has_media"] is True
    assert by_ref[identity]["has_reference_image"] is True


def test_duration_patch_keeps_existing_topic() -> None:
    current = BriefDraft(topic="咖啡")
    updated = apply_brief_patch(
        current, BriefPatch(target=TargetPatch(duration_target_ms=90_000))
    )
    assert updated.topic == "咖啡"
    assert updated.target is not None
    assert updated.target.duration_target_ms == 90_000
    assert updated.target.platform is None
