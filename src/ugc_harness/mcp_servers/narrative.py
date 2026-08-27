from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from mcp.server import MCPServer

from ..content import (
    DramaAction,
    DramaCharacter,
    DramaScene,
    NarrativeFormatId,
    VideoWorldState,
)
from ..agents.narrative_agent.models import (
    BeatPlanArtifact,
    CreativeBrief,
    DramaActionPlanArtifact,
    DramaPlanningArtifact,
    DramaStoryArtifact,
    DramaWorldArtifact,
    NarrativeCandidate,
    NarrativePlanningArtifact,
    NarrativeScriptArtifact,
    PlanningArtifact,
    ScriptArtifact,
    SectionPlanArtifact,
    ShotPlanArtifact,
    TutorialDefinitionArtifact,
    TutorialPlanningArtifact,
    TutorialProcedureArtifact,
    TutorialScriptArtifact,
)
from ..agents.narrative_agent.prompts import (
    beats_prompt,
    beats_repair_prompt,
    drama_actions_prompt,
    drama_story_prompt,
    drama_world_prompt,
    script_prompt,
    script_quality_repair_prompt,
    sections_prompt,
    sections_repair_prompt,
    tutorial_definition_prompt,
    tutorial_explanations_prompt,
    tutorial_procedure_prompt,
)
from ..agents.narrative_agent.shots import (
    compile_drama_shots,
    compile_explainer_shots,
    compile_tutorial_shots,
)
from ..shared.llm import StructuredLLM
from ..shared.settings import LLMSettings


CONFIGURE_TOOL = "narrative.configure_task"
SUBMIT_TOOL = "narrative.submit_candidate"
SECTIONS_TOOL = "narrative.explainer.plan_sections"
BEATS_TOOL = "narrative.explainer.expand_beats"
SCRIPT_TOOL = "narrative.explainer.write_script"
SHOTS_TOOL = "narrative.explainer.compile_shots"
DRAMA_WORLD_TOOL = "narrative.drama.design_world"
DRAMA_STORY_TOOL = "narrative.drama.plan_story"
DRAMA_ACTIONS_TOOL = "narrative.drama.expand_scenes"
DRAMA_SHOTS_TOOL = "narrative.drama.compile_shots"
TUTORIAL_DEFINITION_TOOL = "narrative.tutorial.define_result"
TUTORIAL_STEPS_TOOL = "narrative.tutorial.plan_steps"
TUTORIAL_EXPLANATIONS_TOOL = "narrative.tutorial.plan_explanations"
TUTORIAL_SHOTS_TOOL = "narrative.tutorial.compile_shots"


@dataclass
class ExplainerFormatState:
    format_id: Literal["explainer"] = "explainer"
    section_plan: SectionPlanArtifact | None = None
    beat_plan: BeatPlanArtifact | None = None


@dataclass
class DramaFormatState:
    """Task-local staged drafts for the drama tool chain."""

    format_id: Literal["drama"] = "drama"
    world: DramaWorldArtifact | None = None
    story: DramaStoryArtifact | None = None
    action_plan: DramaActionPlanArtifact | None = None

    @property
    def characters(self) -> list[DramaCharacter]:
        return self.world.characters if self.world is not None else []

    @property
    def scenes(self) -> list[DramaScene]:
        return self.story.scenes if self.story is not None else []

    @property
    def actions(self) -> list[DramaAction]:
        return self.action_plan.actions if self.action_plan is not None else []


@dataclass
class TutorialFormatState:
    """Task-local drafts for a procedure-first tutorial."""

    format_id: Literal["tutorial"] = "tutorial"
    definition: TutorialDefinitionArtifact | None = None
    procedure: TutorialProcedureArtifact | None = None


FormatSessionState = (
    ExplainerFormatState | DramaFormatState | TutorialFormatState
)


@dataclass
class NarrativeTaskSession:
    brief: CreativeBrief
    generator: StructuredLLM
    format_id: NarrativeFormatId
    format_state: FormatSessionState
    generation_context: dict[str, Any] = field(default_factory=dict)
    world_state: VideoWorldState | None = None
    planning: NarrativePlanningArtifact | None = None
    script: NarrativeScriptArtifact | None = None
    shots: ShotPlanArtifact | None = None

    def __post_init__(self) -> None:
        if self.format_state.format_id != self.format_id:
            raise ValueError(
                "format_state.format_id must match NarrativeTaskSession.format_id"
            )


_session: NarrativeTaskSession | None = None


mcp = MCPServer(
    "ugc-narrative",
    version="2.0.0",
    instructions=(
        "Task-scoped explainer and drama narrative tools. Call configure_task "
        "once, then choose and repeat format-specific tools autonomously. Call "
        "submit_candidate when the candidate is ready. The server does not "
        "prescribe a workflow order and is intended to run over stdio."
    ),
)


def _active_session() -> NarrativeTaskSession:
    if _session is None:
        raise RuntimeError("narrative.configure_task must be called first")
    return _session


def _explainer_state() -> tuple[NarrativeTaskSession, ExplainerFormatState]:
    session = _active_session()
    state = session.format_state
    if not isinstance(state, ExplainerFormatState):
        raise RuntimeError(
            f"explainer tool cannot run for narrative format {session.format_id!r}"
        )
    return session, state


def _drama_state() -> tuple[NarrativeTaskSession, DramaFormatState]:
    session = _active_session()
    state = session.format_state
    if not isinstance(state, DramaFormatState):
        raise RuntimeError(
            f"drama tool cannot run for narrative format {session.format_id!r}"
        )
    return session, state


def _tutorial_state() -> tuple[NarrativeTaskSession, TutorialFormatState]:
    session = _active_session()
    state = session.format_state
    if not isinstance(state, TutorialFormatState):
        raise RuntimeError(
            f"tutorial tool cannot run for narrative format {session.format_id!r}"
        )
    return session, state


def _decorate_prompt(prompt: str, output_name: str) -> str:
    session = _active_session()
    context = session.generation_context
    sections = [prompt]
    baseline = (context.get("baselines") or {}).get(output_name)
    if baseline:
        label = baseline.get("label", output_name)
        sections.append(
            f"这是一次局部修复任务。当前已批准的{label}基准如下（紧凑 JSON）：\n"
            f"{baseline.get('payload', '')}"
        )
    instruction = context.get("instruction")
    if instruction:
        sections.append(f"用户针对当前局部产物的强制修改意见：\n{instruction}")
    scope_note = context.get("scope_note")
    if scope_note:
        sections.append(str(scope_note))
    if baseline:
        sections.append(
            "必须以上述基准为唯一出发点做最小修改；未被授权的结构、ID、"
            "顺序和字段必须保持不变。"
        )
    return "\n\n".join(sections)


@mcp.tool(name=CONFIGURE_TOOL, structured_output=True)
def configure_task(
    brief: dict[str, Any],
    model: str,
    generation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Initialize the isolated stdio server process for one narrative task."""

    global _session
    parsed_brief = CreativeBrief.model_validate(brief)
    if parsed_brief.production_mode not in {
        "auto", "explainer", "drama", "tutorial"
    }:
        raise ValueError(
            f"Narrative format {parsed_brief.production_mode!r} is not installed"
        )
    settings = LLMSettings.from_environment(None, model)
    format_id: NarrativeFormatId = (
        "explainer"
        if parsed_brief.production_mode == "auto"
        else parsed_brief.production_mode
    )
    format_state: FormatSessionState
    if format_id == "explainer":
        format_state = ExplainerFormatState()
    elif format_id == "drama":
        format_state = DramaFormatState()
    else:
        format_state = TutorialFormatState()
    _session = NarrativeTaskSession(
        brief=parsed_brief,
        generator=StructuredLLM(settings),
        format_id=format_id,
        format_state=format_state,
        generation_context=generation_context or {},
    )
    return {
        "project_id": parsed_brief.project_id,
        "model": settings.model,
        "format_id": format_id,
        "status": "configured",
    }


@mcp.tool(name=SUBMIT_TOOL, structured_output=True)
def submit_candidate() -> NarrativeCandidate:
    """Submit when the current format candidate is complete.

    Explainer needs section, beat, script, and shots. Drama needs world, story,
    actions, and shots. Tutorial needs result definition, procedure,
    explanations, and shots. Incomplete submissions return a repairable error.
    """

    session = _active_session()
    candidate = _candidate_for_submission(session)
    return candidate


@mcp.tool(name=DRAMA_WORLD_TOOL, structured_output=True)
def design_drama_world(
    problems: list[str] | None = None,
) -> DramaWorldArtifact:
    """Establish the authoritative drama world, cast, and visual identity."""

    session, state = _drama_state()
    state.world = session.generator.generate(
        _decorate_prompt(
            drama_world_prompt(session.brief, state.world, problems),
            "DramaWorldArtifact",
        ),
        DramaWorldArtifact,
    )
    session.world_state = state.world.world_state
    return state.world


@mcp.tool(name=DRAMA_STORY_TOOL, structured_output=True)
def plan_drama_story(
    problems: list[str] | None = None,
) -> DramaStoryArtifact:
    """Plan causal scenes from the approved world and cast."""

    session, state = _drama_state()
    state.story = session.generator.generate(
        _decorate_prompt(
            drama_story_prompt(
                session.brief,
                state.world,
                state.story,
                problems,
            ),
            "DramaStoryArtifact",
        ),
        DramaStoryArtifact,
    )
    return state.story


@mcp.tool(name=DRAMA_ACTIONS_TOOL, structured_output=True)
def expand_drama_scenes(
    problems: list[str] | None = None,
) -> DramaActionPlanArtifact:
    """Expand scenes into continuously generatable performance actions."""

    session, state = _drama_state()
    if state.story is None:
        raise RuntimeError("expand_scenes needs a story draft to expand")
    state.action_plan = session.generator.generate(
        _decorate_prompt(
            drama_actions_prompt(
                session.brief,
                state.world,
                state.story,
                state.action_plan,
                problems,
            ),
            "DramaActionPlanArtifact",
        ),
        DramaActionPlanArtifact,
    )
    if state.world is not None:
        session.planning = DramaPlanningArtifact.from_parts(
            state.world,
            state.story,
            state.action_plan,
        )
    return state.action_plan


@mcp.tool(name=DRAMA_SHOTS_TOOL, structured_output=True)
def compile_drama_production_shots(
    problems: list[str] | None = None,
) -> ShotPlanArtifact:
    """Compile approved drama actions into generated clips with embedded audio."""

    session, _ = _drama_state()
    planning = _assemble_drama_candidate(session)
    session.planning = planning
    session.shots = compile_drama_shots(planning)
    return session.shots


@mcp.tool(name=TUTORIAL_DEFINITION_TOOL, structured_output=True)
def define_tutorial_result(
    problems: list[str] | None = None,
) -> TutorialDefinitionArtifact:
    """Define the finished result, materials, tools, and evidence requirements."""

    session, state = _tutorial_state()
    state.definition = session.generator.generate(
        _decorate_prompt(
            tutorial_definition_prompt(session.brief, state.definition, problems),
            "TutorialDefinitionArtifact",
        ),
        TutorialDefinitionArtifact,
    )
    session.world_state = state.definition.world_state
    return state.definition


@mcp.tool(name=TUTORIAL_STEPS_TOOL, structured_output=True)
def plan_tutorial_steps(
    problems: list[str] | None = None,
) -> TutorialProcedureArtifact:
    """Plan observable procedure steps and actions."""

    session, state = _tutorial_state()
    state.procedure = session.generator.generate(
        _decorate_prompt(
            tutorial_procedure_prompt(
                session.brief,
                state.definition,
                state.procedure,
                problems,
            ),
            "TutorialProcedureArtifact",
        ),
        TutorialProcedureArtifact,
    )
    if state.definition is not None:
        session.planning = TutorialPlanningArtifact.from_parts(
            state.definition,
            state.procedure,
        )
    return state.procedure


@mcp.tool(name=TUTORIAL_EXPLANATIONS_TOOL, structured_output=True)
def plan_tutorial_explanations(
    problems: list[str] | None = None,
) -> TutorialScriptArtifact:
    """Write only the explanations that add value beyond visible actions."""

    session, state = _tutorial_state()
    planning = _assemble_tutorial_candidate(session)
    session.planning = planning
    current = (
        session.script
        if isinstance(session.script, TutorialScriptArtifact)
        else None
    )
    session.script = session.generator.generate(
        _decorate_prompt(
            tutorial_explanations_prompt(
                session.brief,
                planning,
                current,
                problems,
            ),
            "TutorialScriptArtifact",
        ),
        TutorialScriptArtifact,
    )
    return session.script


@mcp.tool(name=TUTORIAL_SHOTS_TOOL, structured_output=True)
def compile_tutorial_production_shots(
    problems: list[str] | None = None,
) -> ShotPlanArtifact:
    """Compile procedure actions into demonstration-led mixed-audio shots."""

    session, _ = _tutorial_state()
    planning = _assemble_tutorial_candidate(session)
    if not isinstance(session.script, TutorialScriptArtifact):
        raise RuntimeError("tutorial shots need an explanation draft")
    session.planning = planning
    session.shots = compile_tutorial_shots(planning, session.script)
    return session.shots


@mcp.tool(name=SECTIONS_TOOL, structured_output=True)
def plan_sections(problems: list[str] | None = None) -> SectionPlanArtifact:
    """Generate or repair world state, video profile, and the Section skeleton."""

    session, state = _explainer_state()
    if state.section_plan is not None and problems:
        prompt = sections_repair_prompt(
            session.brief,
            state.section_plan,
            "；".join(problems),
        )
    else:
        prompt = sections_prompt(session.brief)
    state.section_plan = session.generator.generate(
        _decorate_prompt(prompt, "PlanningArtifact"),
        SectionPlanArtifact,
    )
    session.world_state = state.section_plan.world_state
    return state.section_plan


@mcp.tool(name=BEATS_TOOL, structured_output=True)
def expand_beats(problems: list[str] | None = None) -> BeatPlanArtifact:
    """Expand the approved Section skeleton into Planned Beats."""

    session, state = _explainer_state()
    if state.section_plan is None:
        raise RuntimeError("narrative.explainer.plan_sections must succeed first")
    if state.beat_plan is not None and problems:
        prompt = beats_repair_prompt(
            session.brief,
            state.section_plan,
            state.beat_plan,
            "；".join(problems),
        )
    else:
        prompt = beats_prompt(session.brief, state.section_plan)
    state.beat_plan = session.generator.generate(
        _decorate_prompt(prompt, "PlanningArtifact"),
        BeatPlanArtifact,
    )
    # Cross-reference validation happens at assembly; failures surface as tool
    # errors so the model can repair the beats with the reported problems.
    session.planning = PlanningArtifact.from_parts(
        state.section_plan,
        state.beat_plan,
    )
    return state.beat_plan


@mcp.tool(name=SCRIPT_TOOL, structured_output=True)
def write_script(problems: list[str] | None = None) -> ScriptArtifact:
    """Write or repair the narration script for the assembled Beat plan."""

    session, _ = _explainer_state()
    if session.planning is None:
        raise RuntimeError("narrative.explainer.expand_beats must succeed first")
    if session.script is not None and problems:
        prompt = script_quality_repair_prompt(
            session.brief,
            session.planning,
            session.script,
            "；".join(problems),
        )
    else:
        prompt = script_prompt(session.brief, session.planning)
    session.script = session.generator.generate(
        _decorate_prompt(prompt, "ScriptArtifact"),
        ScriptArtifact,
    )
    return session.script


@mcp.tool(name=SHOTS_TOOL, structured_output=True)
def compile_shots(problems: list[str] | None = None) -> ShotPlanArtifact:
    """Deterministically compile the approved plan and script into ProductionShots."""

    session, _ = _explainer_state()
    if (
        not isinstance(session.planning, PlanningArtifact)
        or not isinstance(session.script, ScriptArtifact)
    ):
        raise RuntimeError("compile_shots needs a planning draft and script draft")
    session.shots = compile_explainer_shots(session.planning, session.script)
    return session.shots


def _assemble_drama_candidate(
    session: NarrativeTaskSession,
) -> DramaPlanningArtifact:
    state = session.format_state
    if not isinstance(state, DramaFormatState):
        raise RuntimeError("current task is not a drama task")
    missing = [
        name
        for name, value in (
            ("world draft", state.world),
            ("story draft", state.story),
            ("action draft", state.action_plan),
        )
        if value is None
    ]
    if missing:
        raise ValueError("candidate is incomplete: " + ", ".join(missing))
    assert state.world is not None
    assert state.story is not None
    assert state.action_plan is not None
    return DramaPlanningArtifact.from_parts(
        state.world,
        state.story,
        state.action_plan,
    )


def _assemble_tutorial_candidate(
    session: NarrativeTaskSession,
) -> TutorialPlanningArtifact:
    state = session.format_state
    if not isinstance(state, TutorialFormatState):
        raise RuntimeError("current task is not a tutorial task")
    missing = [
        name
        for name, value in (
            ("result definition", state.definition),
            ("procedure", state.procedure),
        )
        if value is None
    ]
    if missing:
        raise ValueError("candidate is incomplete: " + ", ".join(missing))
    assert state.definition is not None
    assert state.procedure is not None
    return TutorialPlanningArtifact.from_parts(
        state.definition,
        state.procedure,
    )


def _candidate_for_submission(
    session: NarrativeTaskSession,
) -> NarrativeCandidate:
    if isinstance(session.format_state, DramaFormatState):
        planning = _assemble_drama_candidate(session)
        if session.shots is None:
            raise ValueError("candidate is incomplete: shots")
        session.planning = planning
        return NarrativeCandidate(
            world_state=planning.world_state,
            planning=planning,
            shots=session.shots,
        )

    if isinstance(session.format_state, TutorialFormatState):
        planning = _assemble_tutorial_candidate(session)
        if not isinstance(session.script, TutorialScriptArtifact):
            raise ValueError("candidate is incomplete: tutorial script")
        if session.shots is None:
            raise ValueError("candidate is incomplete: shots")
        session.planning = planning
        return NarrativeCandidate(
            world_state=planning.world_state,
            planning=planning,
            script=session.script,
            shots=session.shots,
        )

    state = session.format_state
    if not isinstance(state, ExplainerFormatState):
        raise ValueError(f"format {session.format_id!r} cannot be submitted")
    missing = [
        name
        for name, value in (
            ("section draft", state.section_plan),
            ("beat draft", state.beat_plan),
            ("script", session.script),
            ("shots", session.shots),
        )
        if value is None
    ]
    if missing:
        raise ValueError("candidate is incomplete: " + ", ".join(missing))
    assert state.section_plan is not None
    assert state.beat_plan is not None
    assert isinstance(session.script, ScriptArtifact)
    assert session.shots is not None
    planning = PlanningArtifact.from_parts(
        state.section_plan,
        state.beat_plan,
    )
    session.planning = planning
    return NarrativeCandidate(
        world_state=planning.world_state,
        planning=planning,
        script=session.script,
        shots=session.shots,
    )
def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
