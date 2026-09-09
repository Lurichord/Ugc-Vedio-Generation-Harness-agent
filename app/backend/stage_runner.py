from __future__ import annotations

from pathlib import Path
from typing import Callable

from ugc_harness.agents.asset_agent.image_analysis import VolcengineImageAnalyzer
from ugc_harness.agents.asset_agent.models import AssetArtifact
from ugc_harness.agents.asset_agent.providers import RoutedAssetProvider
from ugc_harness.agents.editorial_agent.models import EditorialArtifact
from ugc_harness.agents.narrative_agent.brief import make_brief
from ugc_harness.agents.narrative_agent.models import (
    CreativeBrief,
    NarrativeArtifact,
)
from ugc_harness.agents.render_agent.capabilities import RenderCapabilities
from ugc_harness.agents.timeline_agent.models import TimelineArtifact
from ugc_harness.agents.timeline_agent.providers import VolcengineScreenAnimationProvider
from ugc_harness.agents.voice_agent.models import VoiceArtifact
from ugc_harness.agents.voice_agent.tts import VolcengineTTS
from ugc_harness.harness.asset_controller import AssetHarnessController
from ugc_harness.harness.controller import NarrativeHarnessController
from ugc_harness.harness.editorial_controller import EditorialHarnessController
from ugc_harness.harness.models import ProjectState, TaskEnvelope
from ugc_harness.harness.repair import write_project_state
from ugc_harness.harness.render_controller import RenderHarnessController
from ugc_harness.harness.production_routes import resolve_production_route
from ugc_harness.harness.shot_asset_controller import ShotAssetHarnessController
from ugc_harness.harness.shot_reference_controller import (
    SeedreamIdentityImageProvider,
    ShotReferenceHarnessController,
)
from ugc_harness.harness.shot_video_provider import SeedanceShotVideoProvider
from ugc_harness.harness.shot_asset_controller import ShotAssetArtifact
from ugc_harness.harness.shot_timeline_controller import ShotTimelineHarnessController
from ugc_harness.harness.shot_timeline_controller import ShotTimelineArtifact
from ugc_harness.harness.shot_render_controller import ShotRenderHarnessController
from ugc_harness.harness.timeline_controller import TimelineHarnessController
from ugc_harness.harness.voice_controller import VoiceHarnessController
from ugc_harness.shared.artifacts import ArtifactWriter
from ugc_harness.shared.llm import StructuredLLM
from ugc_harness.shared.settings import (
    AssetGenerationSettings,
    LLMSettings,
    TTSSettings,
)

from .schemas import CreateProjectRequest, RunStageRequest, StageName


class FeedbackAwareGenerator:
    """Add human guidance without changing the core LLM implementation."""

    def __init__(
        self,
        inner: StructuredLLM,
        instruction: str | None = None,
        *,
        baselines: dict[object, tuple[str, str]] | None = None,
        scope_note: str | None = None,
    ) -> None:
        self.inner = inner
        self.instruction = instruction
        # output_type -> (人类可读名称, 现有产物的紧凑 JSON)
        self.baselines = baselines or {}
        self.scope_note = scope_note

    def generate(self, prompt: str, output_type: object) -> object:
        if self.instruction:
            sections = [prompt]
            baseline = self.baselines.get(output_type)
            if baseline:
                label, payload = baseline
                sections.append(
                    f"这是一次局部修复任务。当前已批准的{label}基准如下（紧凑 JSON）：\n"
                    f"{payload}"
                )
            sections.append(
                f"用户针对当前局部产物的强制修改意见：\n{self.instruction}"
            )
            if self.scope_note:
                sections.append(self.scope_note)
            if baseline:
                sections.append(
                    "必须以上述基准为唯一出发点做最小修改，保持前后叙事一致性："
                    "除修复目标明确允许修改的部分外，其余所有 section、beat、segment、"
                    "world_state、video_profile 的 ID、数量、顺序和每个字段值都必须"
                    "与基准逐字相同，不得改写、增删或润色。"
                )
            sections.append("未被指定的 Beat 和字段必须保持不变。")
            prompt = "\n\n".join(sections)
        return self.inner.generate(prompt, output_type)


class StageRunner:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def create_project(self, request: CreateProjectRequest) -> Path:
        brief = make_brief(
            topic=request.topic,
            project_name=request.project_name,
            duration_seconds=request.duration_seconds,
            platform=request.platform,
            audience=request.audience,
            goal=request.goal,
            tone=request.tone or None,
            creator_persona=request.creator_persona,
            video_profile=request.video_profile,
        )
        return self.start_from_brief(brief)

    def start_from_brief(self, brief: CreativeBrief) -> Path:
        settings = LLMSettings.from_environment(None, None)
        run = NarrativeHarnessController.from_mcp(
            StructuredLLM(settings), settings.model
        ).run(brief)
        writer = ArtifactWriter(self.output_root)
        project_dir, _ = writer.write(run.artifact)
        writer.write_narrative_run(project_dir, run.record)
        return project_dir

    def run(
        self,
        stage: StageName,
        project_dir: Path,
        options: RunStageRequest,
        *,
        task: TaskEnvelope | None = None,
        feedback: str | None = None,
    ) -> None:
        if stage == "narrative":
            self._narrative(project_dir, options, task, feedback)
        elif stage == "voice":
            self._voice(project_dir, options, task)
        elif stage == "editorial":
            self._editorial(project_dir, options, task, feedback)
        elif stage == "references":
            self._references(project_dir, options, task)
        elif stage == "asset":
            self._asset(project_dir, options, task)
        elif stage == "timeline":
            self._timeline(project_dir, options, task)
        elif stage == "render":
            self._render(project_dir, task)

    @staticmethod
    def _prepare_stage(
        project_dir: Path,
        state: ProjectState,
        stage: str,
    ) -> ProjectState:
        current = str(getattr(state.evaluation.stages, stage))
        if current in {"ready", "needs_revision", "stale"}:
            return state
        next_state = state.model_copy(deep=True)
        setattr(next_state.evaluation.stages, stage, "ready")
        write_project_state(project_dir, next_state)
        return next_state

    @staticmethod
    def _state(project_dir: Path) -> ProjectState:
        return ProjectState.model_validate_json(
            (project_dir / "harness" / "project_state.json").read_text(encoding="utf-8")
        )

    def _narrative(
        self, project_dir: Path, options: RunStageRequest,
        task: TaskEnvelope | None, feedback: str | None,
    ) -> None:
        if task is not None and task.scope.target_refs:
            from ugc_harness.harness.shot_repair import run_shot_repair
            from ugc_harness.harness.shot_reference_controller import (
                SeedreamIdentityImageProvider,
            )

            state = self._state(project_dir)
            format_id = state.context.constraints.get(
                "narrative_format"
            ) or state.context.constraints.get("production_mode")
            if format_id in {"drama", "tutorial"}:
                generation = AssetGenerationSettings.from_environment(
                    None,
                    image_model=options.image_model,
                    video_model=options.video_model,
                )
                with SeedreamIdentityImageProvider(
                    generation
                ) as images, SeedanceShotVideoProvider(generation) as videos:
                    run_shot_repair(
                        project_dir,
                        task,
                        feedback or "",
                        image_provider=images,
                        video_provider=videos,
                    )
            else:
                run_shot_repair(project_dir, task, feedback or "")
            return
        current = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = self._state(project_dir)
        settings = LLMSettings.from_environment(None, options.model)
        generation_context = {
            "instruction": feedback,
            "scope_note": None,
            "baselines": {},
        }
        run = NarrativeHarnessController.from_mcp(
            StructuredLLM(settings),
            settings.model,
            state_version=state.context.state_version,
            generation_context=generation_context,
        ).run(current.brief, task=task, state=state)
        writer = ArtifactWriter(project_dir.parent)
        writer.write(run.artifact)
        writer.write_narrative_run(project_dir, run.record)

    def _voice(
        self, project_dir: Path, options: RunStageRequest, task: TaskEnvelope | None
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = self._state(project_dir)
        settings = TTSSettings.from_environment(None)
        if options.voice_id:
            settings.voice_id = options.voice_id
        with VolcengineTTS(settings) as provider:
            run = VoiceHarnessController.from_provider(provider, settings.voice_id).run(
                narrative, project_dir, state, task
            )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_voice(project_dir, run.artifact)
        writer.write_voice_run(project_dir, run.record)

    def _editorial(
        self, project_dir: Path, options: RunStageRequest,
        task: TaskEnvelope | None, feedback: str | None,
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json((project_dir / "narrative_artifact.json").read_text(encoding="utf-8"))
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        state = self._state(project_dir)
        current_path = project_dir / "editorial_artifact.json"
        current = EditorialArtifact.model_validate_json(current_path.read_text(encoding="utf-8")) if current_path.is_file() else None
        settings = LLMSettings.from_environment(None, options.model)
        generator = FeedbackAwareGenerator(StructuredLLM(settings), feedback)
        run = EditorialHarnessController.from_generator(generator, settings.model).run(
            narrative, voice, state, task=task, current_artifact=current,
            critic_problems=[feedback] if feedback else None,
        )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_editorial(project_dir, run.artifact)
        writer.write_editorial_run(project_dir, run.record)

    def _references(
        self, project_dir: Path, options: RunStageRequest, task: TaskEnvelope | None
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = self._prepare_stage(project_dir, self._state(project_dir), "references")
        generation = AssetGenerationSettings.from_environment(
            None, image_model=options.image_model
        )
        with SeedreamIdentityImageProvider(generation) as provider:
            artifact, run = ShotReferenceHarnessController.from_provider(provider).run(
                narrative, project_dir, state, task
            )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_identity_references(project_dir, artifact)
        writer.write_identity_reference_run(project_dir, run)

    def _asset(
        self, project_dir: Path, options: RunStageRequest, task: TaskEnvelope | None
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        route = resolve_production_route(narrative)
        if route.asset_route == "shot_ai_video":
            state = self._prepare_stage(project_dir, self._state(project_dir), "asset")
            generation = AssetGenerationSettings.from_environment(
                None, video_model=options.video_model
            )
            with SeedanceShotVideoProvider(generation) as provider:
                artifact, run = ShotAssetHarnessController.from_provider(provider).run(
                    narrative, project_dir, state, task
                )
            writer = ArtifactWriter(project_dir.parent)
            writer.write_shot_assets(project_dir, artifact)
            writer.write_shot_asset_run(project_dir, run)
            return
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        editorial = EditorialArtifact.model_validate_json((project_dir / "editorial_artifact.json").read_text(encoding="utf-8"))
        state = self._state(project_dir)
        current_path = project_dir / "asset_artifact.json"
        current = AssetArtifact.model_validate_json(current_path.read_text(encoding="utf-8")) if current_path.is_file() else None
        llm = LLMSettings.from_environment(None, options.model)
        generation = AssetGenerationSettings.from_environment(None, image_model=options.image_model, video_model=options.video_model)
        with RoutedAssetProvider(llm, generation) as provider, VolcengineImageAnalyzer(llm) as analyzer:
            run = AssetHarnessController.from_provider(provider, analyzer).run(
                editorial, voice, project_dir, state, task, current_artifact=current
            )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_assets(project_dir, run.artifact)
        writer.write_asset_run(project_dir, run.record)

    def _timeline(
        self, project_dir: Path, options: RunStageRequest, task: TaskEnvelope | None
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        if resolve_production_route(narrative).asset_route == "shot_ai_video":
            if task is not None:
                raise ValueError("direct Shot timeline does not support repair tasks yet")
            asset_path = project_dir / "shot_asset_artifact.json"
            if not asset_path.is_file():
                raise FileNotFoundError("短剧/教程要先完成参考图和出镜，再做时间线")
            assets = ShotAssetArtifact.model_validate_json(
                asset_path.read_text(encoding="utf-8")
            )
            state = self._prepare_stage(project_dir, self._state(project_dir), "timeline")
            artifact, run = ShotTimelineHarnessController().run(
                narrative, assets, state
            )
            writer = ArtifactWriter(project_dir.parent)
            writer.write_shot_timeline(project_dir, artifact)
            writer.write_shot_timeline_run(project_dir, run)
            return
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        editorial = EditorialArtifact.model_validate_json((project_dir / "editorial_artifact.json").read_text(encoding="utf-8"))
        assets = AssetArtifact.model_validate_json((project_dir / "asset_artifact.json").read_text(encoding="utf-8"))
        state = self._state(project_dir)
        current_path = project_dir / "timeline_artifact.json"
        current = TimelineArtifact.model_validate_json(current_path.read_text(encoding="utf-8")) if current_path.is_file() else None
        llm = LLMSettings.from_environment(None)
        generation = AssetGenerationSettings.from_environment(None, video_model=options.video_model)
        with VolcengineScreenAnimationProvider(llm, generation) as provider:
            run = TimelineHarnessController.from_provider(provider).run(
                voice, editorial, assets, project_dir, state,
                task=task, current_artifact=current,
            )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_timeline(project_dir, run.artifact)
        writer.write_timeline_run(project_dir, run.record)

    def _render(self, project_dir: Path, task: TaskEnvelope | None) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        if resolve_production_route(narrative).asset_route == "shot_ai_video":
            if task is not None:
                raise ValueError("direct Shot render does not support repair tasks yet")
            timeline_path = project_dir / "shot_timeline_artifact.json"
            if not timeline_path.is_file():
                raise FileNotFoundError("短剧/教程要先完成时间线，再渲染")
            timeline = ShotTimelineArtifact.model_validate_json(
                timeline_path.read_text(encoding="utf-8")
            )
            state = self._prepare_stage(project_dir, self._state(project_dir), "render")
            artifact, run = ShotRenderHarnessController().run(
                timeline, project_dir, state
            )
            writer = ArtifactWriter(project_dir.parent)
            writer.write_render(project_dir, artifact)
            writer.write_shot_render_run(project_dir, run)
            return
        voice = VoiceArtifact.model_validate_json((project_dir / "voice_artifact.json").read_text(encoding="utf-8"))
        timeline = TimelineArtifact.model_validate_json((project_dir / "timeline_artifact.json").read_text(encoding="utf-8"))
        state = self._state(project_dir)
        run = RenderHarnessController.from_renderer(RenderCapabilities().run).run(
            voice, timeline, project_dir, state, task
        )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_render(project_dir, run.artifact)
        writer.write_render_run(project_dir, run.record)
