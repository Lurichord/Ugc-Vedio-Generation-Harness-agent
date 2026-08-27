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
    VideoWorldState,
    WorldClaim,
    WorldEntity,
)
from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.description import (
    ExplainerStructure,
    VideoDescription,
    VideoIntent,
    VideoWorld,
)
from ugc_harness.harness.models import (
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
from ugc_harness.intake.host import IntentHost, new_session
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
from ugc_harness.intake.tools import HARNESS_LIST_GRAPH, INTAKE_MCP_TOOLS, activate_skill
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
        "target": TargetSpec(duration_target_ms=90_000),
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
        state=VideoWorldState(
            topic_frame="测试",
            entities=[
                WorldEntity(
                    entity_id="e1",
                    name="测试",
                    kind="concept",
                    narrative_role="主题",
                    description="软件测试",
                )
            ],
            claims=[
                WorldClaim(
                    claim_id="c1",
                    statement="测试能降返工",
                    epistemic_status="given_by_brief",
                    evidence_required=False,
                )
            ],
        )
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
            NodeCommit("input:brief", "brief", "brief"),
            NodeCommit(
                "artifact:narrative",
                "narrative_artifact",
                "narrative",
                ("input:brief",),
            ),
            NodeCommit(
                "planned_beat:b3",
                "planned_beat",
                "beat three",
                ("artifact:narrative",),
            ),
        ],
    )
    graph.commit_batch(
        task_id="t_voice",
        produced_by="voice_agent",
        commits=[
            NodeCommit(
                "artifact:voice",
                "voice_artifact",
                "voice",
                ("artifact:narrative",),
            ),
            NodeCommit(
                "realized_beat:b3",
                "realized_beat",
                "realized three",
                ("artifact:voice", "planned_beat:b3"),
            ),
        ],
    )
    graph.commit_batch(
        task_id="t_timeline",
        produced_by="timeline_agent",
        commits=[
            NodeCommit(
                "timeline_clip:b3",
                "timeline_clip",
                "clip",
                ("realized_beat:b3",),
            ),
            NodeCommit(
                "artifact:timeline",
                "timeline_artifact",
                "timeline",
                ("timeline_clip:b3", "artifact:voice"),
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
    assert "narrative.explainer.plan_sections" not in model.calls[0]["tools"]


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


def test_list_graph_returns_artifact_overview_without_hashes() -> None:
    overview = list_graph(_tiny_graph())
    assert overview["ok"] is True
    assert overview["scope"] == "artifacts"
    refs = [item["ref"] for item in overview["nodes"]]
    assert refs == [
        "artifact:narrative",
        "artifact:timeline",
        "artifact:voice",
    ]
    for node in overview["nodes"]:
        assert "content_hash" not in node
        assert "version" not in node
        assert "dependency_hashes" not in node
        assert set(node) == {
            "ref",
            "kind",
            "produced_by",
            "status",
            "locked",
            "depends_on",
            "dependents",
        }
        assert all(edge.startswith("artifact:") for edge in node["depends_on"])
        assert all(edge.startswith("artifact:") for edge in node["dependents"])
    narrative = next(
        item for item in overview["nodes"] if item["ref"] == "artifact:narrative"
    )
    assert "planned_beat:b3" not in narrative["dependents"]
    assert "timeline_clip:b3" not in refs


def test_list_graph_around_ref_is_one_hop_and_resolves_beat_alias() -> None:
    around = list_graph(_tiny_graph(), around_ref="beat:b3")
    assert around["ok"] is True
    assert around["resolved_ref"] == "planned_beat:b3"
    refs = {item["ref"] for item in around["nodes"]}
    assert refs == {
        "planned_beat:b3",
        "artifact:narrative",
        "realized_beat:b3",
    }
    assert "timeline_clip:b3" not in refs
    assert "artifact:timeline" not in refs
    missing = list_graph(_tiny_graph(), around_ref="beat:missing")
    assert missing["ok"] is False
    artifact_around = list_graph(_tiny_graph(), around_ref="artifact:timeline")
    around_refs = {item["ref"] for item in artifact_around["nodes"]}
    assert "timeline_clip:b3" not in around_refs
    assert artifact_around.get("omitted_edges", 0) >= 1


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
    _, result = repair_description(session, ["beat:b3"], "收成两句")
    assert result["target_refs"] == ["planned_beat:b3"]
    assert result["error"] == "harness.repair 执行器未接线"


def test_skill_catalog_and_activate() -> None:
    names = {item["name"] for item in list_skills()}
    assert names >= {
        "clarify-brief",
        "user-revises",
        "start-project",
        "revise-segment",
        "inspect-progress",
    }
    body = load_skill("revise-segment")
    assert "harness.repair" in body
    assert "harness.list_graph" in body
    assert "输出格式" in body
    assert '"target_refs"' in body
    from ugc_harness.intake.skills import SKILLS_ROOT

    instructions = (SKILLS_ROOT.parent / "agent.md").read_text(encoding="utf-8")
    assert "## 输出格式" in instructions
    assert '"tool"' in instructions
    assert '"arguments"' in instructions
    assert '"done"' in instructions
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


def test_intake_mcp_tools_are_isolated_from_narrative() -> None:
    assert "narrative.explainer.plan_sections" not in INTAKE_MCP_TOOLS
    assert "narrative.submit_candidate" not in INTAKE_MCP_TOOLS
    assert hasattr(intake_mcp, "description_list_outline")
    assert hasattr(narrative_mcp, "CONFIGURE_TOOL")
    assert intake_mcp.CONFIGURE_TOOL == "intake.configure_session"
    assert "brief.update" not in INTAKE_MCP_TOOLS
    assert HARNESS_LIST_GRAPH in INTAKE_MCP_TOOLS
    assert hasattr(intake_mcp, "harness_list_graph")


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


def test_duration_patch_keeps_existing_topic() -> None:
    current = BriefDraft(topic="咖啡")
    updated = apply_brief_patch(
        current, BriefPatch(target=TargetPatch(duration_target_ms=90_000))
    )
    assert updated.topic == "咖啡"
    assert updated.target is not None
    assert updated.target.duration_target_ms == 90_000
