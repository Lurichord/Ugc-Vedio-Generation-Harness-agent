from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from ..agents.narrative_agent.assemble import (
    assemble_candidate,
    fill_generation_prompt,
    resolve_video_profile,
    with_resolved_profile,
)
from ..agents.narrative_agent.memory import summarize_identities, summarize_shots
from ..agents.narrative_agent.models import (
    BeatPlanArtifact,
    CreativeBrief,
    DramaActionPlanArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    NarrativeCandidate,
    NarrativeIdentitiesArtifact,
    ScriptArtifact,
    SectionPlanArtifact,
    ShotPlanArtifact,
    TutorialDefinitionArtifact,
    TutorialProcedureArtifact,
    TutorialScriptArtifact,
)
from ..content import (
    NarrativeFormatId,
    PlannedBeat,
    ProductionShot,
    Section,
    VideoWorldState,
    identity_refs_for_world,
)


CONFIGURE_TOOL = "narrative.configure_task"
SUBMIT_TOOL = "narrative.submit_candidate"
WRITE_IDENTITIES_TOOL = "narrative.write_identities"
WRITE_SECTIONS_TOOL = "narrative.write_sections"
WRITE_BEATS_TOOL = "narrative.write_beats"
WRITE_SCRIPT_TOOL = "narrative.write_script"
WRITE_WORLD_TOOL = "narrative.write_world"
WRITE_STORY_TOOL = "narrative.write_story"
WRITE_ACTIONS_TOOL = "narrative.write_actions"
WRITE_DEFINITION_TOOL = "narrative.write_definition"
WRITE_PROCEDURE_TOOL = "narrative.write_procedure"
WRITE_EXPLANATIONS_TOOL = "narrative.write_explanations"
WRITE_SHOTS_TOOL = "narrative.write_shots"
LIST_STATE_TOOL = "narrative.list_state"


@dataclass
class NarrativeTaskSession:
    brief: CreativeBrief
    format_id: NarrativeFormatId
    identities: NarrativeIdentitiesArtifact | None = None
    section_plan: SectionPlanArtifact | None = None
    beat_plan: BeatPlanArtifact | None = None
    script: ScriptArtifact | TutorialScriptArtifact | None = None
    world: DramaWorldArtifact | None = None
    story: DramaStoryArtifact | None = None
    action_plan: DramaActionPlanArtifact | None = None
    definition: TutorialDefinitionArtifact | None = None
    procedure: TutorialProcedureArtifact | None = None
    shots: ShotPlanArtifact | None = None
    generation_context: dict[str, Any] = field(default_factory=dict)

    def layers(self) -> dict[str, Any]:
        return {
            "section_plan": self.section_plan,
            "beat_plan": self.beat_plan,
            "script": self.script,
            "world": self.world,
            "story": self.story,
            "action_plan": self.action_plan,
            "definition": self.definition,
            "procedure": self.procedure,
        }


_session: NarrativeTaskSession | None = None


mcp = MCPServer(
    "ugc-narrative",
    version="3.1.0",
    instructions=(
        "Narrative writer tools. First run writes one layer per call. "
        "Repair writes ProductionShot only. Then submit_candidate."
    ),
)


def _active_session() -> NarrativeTaskSession:
    if _session is None:
        raise RuntimeError("narrative.configure_task must be called first")
    return _session


@mcp.tool(
    name=CONFIGURE_TOOL,
    description="为本进程配置一次叙事任务。由 harness 调用，模型不要选这个工具。",
    structured_output=True,
)
def configure_task(
    brief: Annotated[dict[str, Any], Field(description="CreativeBrief JSON。")],
    model: Annotated[str, Field(description="本任务使用的模型名。")],
    generation_context: Annotated[
        dict[str, Any] | None,
        Field(description="跨轮种子：seed_identities / seed_shots / seed_scaffold / instruction。"),
    ] = None,
) -> dict[str, Any]:
    global _session
    parsed_brief = CreativeBrief.model_validate(brief)
    format_id: NarrativeFormatId = parsed_brief.production_mode
    context = dict(generation_context or {})
    session = NarrativeTaskSession(
        brief=parsed_brief,
        format_id=format_id,
        generation_context=context,
    )
    seed_identities = context.get("seed_identities")
    if isinstance(seed_identities, dict):
        session.identities = with_resolved_profile(
            NarrativeIdentitiesArtifact.model_validate(seed_identities),
            parsed_brief,
            format_id,
        )
    seed_shots = context.get("seed_shots")
    if isinstance(seed_shots, list) and seed_shots:
        session.shots = ShotPlanArtifact(
            shots=[
                fill_generation_prompt(ProductionShot.model_validate(item))
                for item in seed_shots
            ]
        )
    seed_scaffold = context.get("seed_scaffold")
    if isinstance(seed_scaffold, dict):
        _hydrate_scaffold(session, seed_scaffold)
    _session = session
    return {
        "project_id": parsed_brief.project_id,
        "model": model,
        "format_id": format_id,
        "status": "configured",
        "seeded_identities": session.identities is not None,
        "seeded_shots": session.shots is not None,
        "seeded_scaffold": any(item is not None for item in session.layers().values()),
    }


@mcp.tool(
    name=WRITE_IDENTITIES_TOOL,
    description="只改身份册、不改镜头和中间层。首跑不要单独调用。",
    structured_output=True,
)
def write_identities(
    identities: Annotated[dict[str, Any], Field(description="NarrativeIdentitiesArtifact JSON。")],
) -> dict[str, Any]:
    return summarize_identities(_store_identities(identities))


@mcp.tool(
    name=WRITE_SECTIONS_TOOL,
    description="讲解第一层：身份册 + Hook/Body/Close 三节。没有前置。",
    structured_output=True,
)
def write_sections(
    sections: Annotated[dict[str, Any], Field(description="SectionPlanArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("explainer", WRITE_SECTIONS_TOOL)
    plan = SectionPlanArtifact.model_validate(_with_profile(sections, "explainer"))
    session.section_plan = plan
    session.identities = NarrativeIdentitiesArtifact(
        one_sentence_thesis=plan.one_sentence_thesis,
        world_state=plan.world_state,
        video_profile=plan.video_profile,
    )
    return {
        "ok": True,
        "sections": [
            {"section_id": item.section_id, "role": item.role, "goal": item.goal}
            for item in plan.sections
        ],
        "identities": summarize_identities(session.identities),
    }


@mcp.tool(
    name=WRITE_BEATS_TOOL,
    description="讲解第二层：把已有三节扩成 PlannedBeat。前置：已 write_sections。",
    structured_output=True,
)
def write_beats(
    beats: Annotated[dict[str, Any], Field(description="BeatPlanArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("explainer", WRITE_BEATS_TOOL)
    if session.section_plan is None:
        raise ValueError("前置未满足：先 narrative.write_sections")
    plan = BeatPlanArtifact.model_validate(beats)
    session.beat_plan = plan
    return {
        "ok": True,
        "beats": [
            {
                "planned_beat_id": item.planned_beat_id,
                "section_id": item.section_id,
                "order": item.order,
            }
            for item in plan.beats
        ],
        "count": len(plan.beats),
    }


@mcp.tool(
    name=WRITE_SCRIPT_TOOL,
    description="讲解第三层：外部口播，覆盖每个 beat。前置：已 write_beats。",
    structured_output=True,
)
def write_script(
    script: Annotated[dict[str, Any], Field(description="ScriptArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("explainer", WRITE_SCRIPT_TOOL)
    if session.beat_plan is None:
        raise ValueError("前置未满足：先 narrative.write_beats")
    artifact = ScriptArtifact.model_validate(script)
    session.script = artifact
    return {
        "ok": True,
        "segments": len(artifact.segments),
        "title_options": list(artifact.title_options),
    }


@mcp.tool(
    name=WRITE_WORLD_TOOL,
    description="短剧第一层：世界、身份册、角色。没有前置。",
    structured_output=True,
)
def write_world(
    world: Annotated[dict[str, Any], Field(description="DramaWorldArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("drama", WRITE_WORLD_TOOL)
    artifact = DramaWorldArtifact.model_validate(_with_profile(world, "drama"))
    session.world = artifact
    session.identities = NarrativeIdentitiesArtifact(
        one_sentence_thesis=artifact.one_sentence_thesis,
        world_state=artifact.world_state,
        video_profile=artifact.video_profile,
        characters=list(artifact.characters),
    )
    return summarize_identities(session.identities)


@mcp.tool(
    name=WRITE_STORY_TOOL,
    description="短剧第二层：分场。前置：已 write_world。",
    structured_output=True,
)
def write_story(
    story: Annotated[dict[str, Any], Field(description="DramaStoryArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("drama", WRITE_STORY_TOOL)
    if session.world is None:
        raise ValueError("前置未满足：先 narrative.write_world")
    artifact = DramaStoryArtifact.model_validate(story)
    session.story = artifact
    return {
        "ok": True,
        "premise": artifact.premise,
        "scenes": [
            {"scene_id": item.scene_id, "order": item.order, "purpose": item.purpose}
            for item in artifact.scenes
        ],
    }


@mcp.tool(
    name=WRITE_ACTIONS_TOOL,
    description="短剧第三层：每场动作。前置：已 write_story。",
    structured_output=True,
)
def write_actions(
    actions: Annotated[dict[str, Any], Field(description="DramaActionPlanArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("drama", WRITE_ACTIONS_TOOL)
    if session.story is None:
        raise ValueError("前置未满足：先 narrative.write_story")
    artifact = DramaActionPlanArtifact.model_validate(actions)
    session.action_plan = artifact
    return {
        "ok": True,
        "actions": [
            {"action_id": item.action_id, "scene_id": item.scene_id, "order": item.order}
            for item in artifact.actions
        ],
        "count": len(artifact.actions),
    }


@mcp.tool(
    name=WRITE_DEFINITION_TOOL,
    description="教程第一层：成品、材料身份册。没有前置。",
    structured_output=True,
)
def write_definition(
    definition: Annotated[dict[str, Any], Field(description="TutorialDefinitionArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("tutorial", WRITE_DEFINITION_TOOL)
    artifact = TutorialDefinitionArtifact.model_validate(
        _with_profile(definition, "tutorial")
    )
    session.definition = artifact
    session.identities = NarrativeIdentitiesArtifact(
        one_sentence_thesis=artifact.one_sentence_thesis,
        world_state=artifact.world_state,
        video_profile=artifact.video_profile,
        objective=artifact.objective,
        materials=list(artifact.materials),
        tools=list(artifact.tools),
        coverage_requirements=list(artifact.coverage_requirements),
    )
    return summarize_identities(session.identities)


@mcp.tool(
    name=WRITE_PROCEDURE_TOOL,
    description="教程第二层：步骤和动作。前置：已 write_definition。",
    structured_output=True,
)
def write_procedure(
    procedure: Annotated[dict[str, Any], Field(description="TutorialProcedureArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("tutorial", WRITE_PROCEDURE_TOOL)
    if session.definition is None:
        raise ValueError("前置未满足：先 narrative.write_definition")
    artifact = TutorialProcedureArtifact.model_validate(procedure)
    session.procedure = artifact
    return {
        "ok": True,
        "steps": [
            {"step_id": item.step_id, "order": item.order, "instruction": item.instruction}
            for item in artifact.steps
        ],
        "actions": len(artifact.actions),
    }


@mcp.tool(
    name=WRITE_EXPLANATIONS_TOOL,
    description="教程第三层：可选讲解。前置：已 write_procedure。",
    structured_output=True,
)
def write_explanations(
    explanations: Annotated[dict[str, Any], Field(description="TutorialScriptArtifact JSON。")],
) -> dict[str, Any]:
    session = _require_format("tutorial", WRITE_EXPLANATIONS_TOOL)
    if session.procedure is None:
        raise ValueError("前置未满足：先 narrative.write_procedure")
    artifact = TutorialScriptArtifact.model_validate(explanations)
    session.script = artifact
    return {"ok": True, "segments": len(artifact.segments)}


@mcp.tool(
    name=WRITE_SHOTS_TOOL,
    description=(
        "写 ProductionShot。首跑前置：讲解已 write_script，短剧已 write_actions，"
        "教程已 write_procedure。修片段只交改过的镜，不要重写中间层。"
    ),
    structured_output=True,
)
def write_shots(
    shots: Annotated[
        list[dict[str, Any]],
        Field(description="ProductionShot JSON 列表。"),
    ],
    identities: Annotated[
        dict[str, Any] | None,
        Field(description="仅当还没有身份册时补交。"),
    ] = None,
    merge: Annotated[
        bool,
        Field(description="true 时按 shot_id 覆盖已有镜；false 整表替换。"),
    ] = True,
) -> dict[str, Any]:
    session = _active_session()
    if identities is not None:
        _store_identities(identities)
    if session.shots is None:
        _require_shot_precondition(session)
    if session.identities is None:
        raise ValueError("前置未满足：先写身份层，或在本调用带 identities")
    incoming = [
        fill_generation_prompt(ProductionShot.model_validate(item)) for item in shots
    ]
    if merge and session.shots is not None:
        by_id = {item.shot_id: item for item in session.shots.shots}
        for item in incoming:
            by_id[item.shot_id] = item
        ordered = sorted(by_id.values(), key=lambda item: item.order)
    else:
        ordered = incoming
    plan = ShotPlanArtifact(shots=ordered)
    _validate_binds(session.identities, plan)
    session.shots = plan
    return {
        "ok": True,
        "identities": summarize_identities(session.identities),
        "shots": summarize_shots(plan),
    }


def _store_identities(identities: dict[str, Any]) -> NarrativeIdentitiesArtifact:
    session = _active_session()
    book = with_resolved_profile(
        NarrativeIdentitiesArtifact.model_validate(identities),
        session.brief,
        session.format_id,
    )
    session.identities = book
    return book


@mcp.tool(
    name=LIST_STATE_TOOL,
    description="看当前各层是否已写、身份册和镜表骨架。",
    structured_output=True,
)
def list_state() -> dict[str, Any]:
    session = _active_session()
    identities = (
        summarize_identities(session.identities)
        if session.identities is not None
        else {"ok": True, "identities": [], "count": 0}
    )
    shots = (
        summarize_shots(session.shots)
        if session.shots is not None
        else {"ok": True, "spine": [], "count": 0}
    )
    return {
        "format_id": session.format_id,
        "identities": identities,
        "layers": {
            name: value is not None
            for name, value in session.layers().items()
        },
        "shots": shots,
        "instruction": session.generation_context.get("instruction"),
    }


@mcp.tool(
    name=SUBMIT_TOOL,
    description="身份册和全片镜头都齐了再交。有中间层就用中间层，缺的由镜头补。",
    structured_output=True,
)
def submit_candidate() -> NarrativeCandidate:
    session = _active_session()
    if session.identities is None:
        raise ValueError("candidate is incomplete: identities")
    if session.shots is None:
        raise ValueError("candidate is incomplete: shots")
    return assemble_candidate(
        brief=session.brief,
        format_id=session.format_id,
        identities=session.identities,
        shots=session.shots,
        layers=session.layers(),
    )


def _require_format(format_id: str, tool: str) -> NarrativeTaskSession:
    session = _active_session()
    if session.format_id != format_id:
        raise ValueError(f"{tool} 只用于 {format_id}，当前是 {session.format_id}")
    return session


def _require_shot_precondition(session: NarrativeTaskSession) -> None:
    if session.format_id == "explainer" and session.script is None:
        raise ValueError("前置未满足：先 narrative.write_script")
    if session.format_id == "drama" and session.action_plan is None:
        raise ValueError("前置未满足：先 narrative.write_actions")
    if session.format_id == "tutorial" and session.procedure is None:
        raise ValueError("前置未满足：先 narrative.write_procedure")


def _with_profile(payload: dict[str, Any], format_id: str) -> dict[str, Any]:
    session = _active_session()
    data = dict(payload)
    if data.get("video_profile"):
        return data
    world = VideoWorldState.model_validate(data.get("world_state") or {})
    data["video_profile"] = resolve_video_profile(
        session.brief, format_id, world, None
    ).model_dump(mode="json")
    return data


def _hydrate_scaffold(session: NarrativeTaskSession, data: dict[str, Any]) -> None:
    book = session.identities
    kind = data.get("kind")
    if kind == "explainer" or "sections" in data:
        if book is not None and data.get("sections"):
            session.section_plan = SectionPlanArtifact(
                narrative_pattern=str(
                    data.get("narrative_pattern")
                    or "Question→Evidence→Explanation→Implication"
                ),
                one_sentence_thesis=book.one_sentence_thesis,
                world_state=book.world_state,
                video_profile=book.video_profile
                or resolve_video_profile(
                    session.brief, "explainer", book.world_state, None
                ),
                sections=[Section.model_validate(item) for item in data["sections"]],
            )
        if data.get("beats"):
            session.beat_plan = BeatPlanArtifact(
                beats=[PlannedBeat.model_validate(item) for item in data["beats"]]
            )
    if kind == "drama" or "scenes" in data:
        if book is not None and book.video_profile is not None and book.characters:
            session.world = DramaWorldArtifact(
                one_sentence_thesis=book.one_sentence_thesis,
                world_state=book.world_state,
                video_profile=book.video_profile,
                characters=list(book.characters),
            )
        if data.get("scenes"):
            premise = data.get("premise")
            if not premise and book is not None:
                premise = book.one_sentence_thesis
            session.story = DramaStoryArtifact.model_validate(
                {"premise": premise or "", "scenes": data["scenes"]}
            )
        if data.get("actions"):
            session.action_plan = DramaActionPlanArtifact.model_validate(
                {"actions": data["actions"]}
            )
    if kind == "tutorial" or "steps" in data:
        if book is not None and book.video_profile is not None:
            session.definition = TutorialDefinitionArtifact(
                one_sentence_thesis=book.one_sentence_thesis,
                world_state=book.world_state,
                video_profile=book.video_profile,
                objective=book.objective or book.one_sentence_thesis,
                materials=list(book.materials),
                tools=list(book.tools),
                coverage_requirements=list(book.coverage_requirements),
            )
        if data.get("steps") and data.get("actions"):
            session.procedure = TutorialProcedureArtifact.model_validate(
                {"steps": data["steps"], "actions": data["actions"]}
            )


def _validate_binds(
    book: NarrativeIdentitiesArtifact, plan: ShotPlanArtifact
) -> None:
    roster = identity_refs_for_world(book.world_state)
    unknown = sorted(
        {
            ref
            for shot in plan.shots
            for ref in shot.binds
            if ref not in roster
        }
    )
    if unknown:
        raise ValueError(f"binds 不在身份册：{unknown}")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
