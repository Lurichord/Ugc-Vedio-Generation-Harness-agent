from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

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
    IdentityReferenceArtifact,
    IdentityReferenceRun,
    SeedreamIdentityImageProvider,
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

from .agent_trace import (
    agent_tool_log_path,
    clip_log_lines,
    injected_context,
    log_offset,
    merge_action_arguments,
    read_log_delta,
    received_task_message,
    run_opening_messages,
    skill_name,
)
from .repository import AppDataRepository
from .schemas import (
    CreateProjectRequest,
    RunActionView,
    RunLogRecord,
    RunStageRequest,
    StageName,
)


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
    def __init__(
        self,
        output_root: Path,
        repository: AppDataRepository | None = None,
    ) -> None:
        self.output_root = output_root
        self.repository = repository

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
        self._record_completed(
            project_dir,
            stage="narrative",
            source="studio",
            goal="生成脚本",
            agent="narrative_agent",
        )
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
        opened = self._open_run(project_dir, stage, task, feedback)
        log_at = log_offset(agent_tool_log_path())
        try:
            if stage == "narrative":
                self._narrative(project_dir, options, task, feedback)
            elif stage == "voice":
                self._voice(project_dir, options, task, feedback)
            elif stage == "editorial":
                self._editorial(project_dir, options, task, feedback)
            elif stage == "references":
                self._references(project_dir, options, task, feedback)
            elif stage == "asset":
                self._asset(project_dir, options, task, feedback)
            elif stage == "timeline":
                self._timeline(project_dir, options, task)
            elif stage == "render":
                self._render(project_dir, task)
        except Exception as exc:
            self._close_run(
                opened, "failed", project_dir, stage, error=str(exc), log_at=log_at
            )
            raise
        self._close_run(opened, "completed", project_dir, stage, log_at=log_at)

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

    def _repo(self) -> AppDataRepository:
        if self.repository is None:
            from .config import AppConfig

            self.repository = AppDataRepository(AppConfig.default().data_root)
        return self.repository

    def _open_run(
        self,
        project_dir: Path,
        stage: StageName,
        task: TaskEnvelope | None,
        feedback: str | None,
    ) -> tuple[RunLogRecord, float] | None:
        try:
            try:
                opened_state = self._state(project_dir)
                project_id = opened_state.context.project_id
            except (OSError, ValueError):
                opened_state = None
                project_id = project_dir.name
            record = RunLogRecord(
                run_id=f"run_{uuid4().hex}",
                project_id=project_id,
                source="intake" if task is not None else "studio",
                stage=stage,
                task_id=task.task_id if task is not None else None,
                agent=task.agent if task is not None else None,
                goal=task.goal if task is not None else f"执行 {stage}",
                instruction=feedback,
                status="running",
                allowed_tools=list(task.allowed_tools) if task is not None else [],
                target_refs=list(task.scope.target_refs) if task is not None else [],
                shot_ids=list(task.scope.shot_ids) if task is not None else [],
                received=(
                    received_task_message(task, feedback)
                    if task is not None
                    else (feedback or f"执行 {stage}")
                ),
                context=injected_context(
                    task,
                    instruction=feedback,
                    project_dir=project_dir,
                    stage=stage,
                    state=opened_state,
                ),
                messages=run_opening_messages(
                    task,
                    instruction=feedback,
                    project_dir=project_dir,
                    stage=stage,
                    state=opened_state,
                ),
            )
            self._repo().upsert_run(project_dir.name, record)
            return record, time.monotonic()
        except Exception:
            return None

    def _close_run(
        self,
        opened: tuple[RunLogRecord, float] | None,
        status: str,
        project_dir: Path,
        stage: StageName,
        *,
        error: str | None = None,
        log_at: int | None = None,
    ) -> None:
        if opened is None:
            return
        record, started = opened
        record.status = status
        record.error = error
        record.finished_at = datetime.now(timezone.utc).isoformat()
        record.duration_ms = int((time.monotonic() - started) * 1000)
        if log_at is not None:
            record.lines = read_log_delta(agent_tool_log_path(), log_at)
        self._enrich_run(record, project_dir, stage)
        record.lines = clip_log_lines(record.lines)
        try:
            self._repo().upsert_run(project_dir.name, record)
        except Exception:
            return

    def _record_completed(
        self,
        project_dir: Path,
        *,
        stage: StageName,
        source: str,
        goal: str,
        agent: str | None = None,
    ) -> None:
        try:
            try:
                project_id = self._state(project_dir).context.project_id
            except (OSError, ValueError):
                project_id = project_dir.name
            record = RunLogRecord(
                run_id=f"run_{uuid4().hex}",
                project_id=project_id,
                source=source,  # type: ignore[arg-type]
                stage=stage,
                agent=agent,
                goal=goal,
                status="completed",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            self._enrich_run(record, project_dir, stage)
            self._repo().upsert_run(project_dir.name, record)
        except Exception:
            return

    def _enrich_run(
        self,
        record: RunLogRecord,
        project_dir: Path,
        stage: StageName,
    ) -> None:
        match = None
        state = None
        try:
            state = self._state(project_dir)
        except (OSError, ValueError):
            state = None
        if state is not None:
            phase = state.trajectory.phases.get(stage)
            if phase is not None and phase.tasks:
                if record.task_id:
                    match = next(
                        (
                            item
                            for item in reversed(phase.tasks)
                            if item.task.task_id == record.task_id
                        ),
                        None,
                    )
                if match is None:
                    match = phase.tasks[-1]
                if not record.actions:
                    record.actions = [
                        RunActionView(
                            tool=item.tool,
                            result=item.result,
                            duration_ms=item.duration_ms,
                            reason=item.reason,
                            skill=skill_name(item.tool, None),
                        )
                        for item in match.agent_result.actions
                    ]
                if record.critic_passed is None:
                    record.critic_passed = match.evaluation.passed
                if not record.goal:
                    record.goal = match.task.goal
                if not record.agent:
                    record.agent = match.task.agent
                if not record.error:
                    record.error = match.agent_result.error
        if record.lines:
            record.actions = merge_action_arguments(record.actions, record.lines)
        record.context = injected_context(
            match.task if match is not None else None,
            instruction=record.instruction,
            project_dir=project_dir,
            stage=stage,
            existing=record.context,
            state=state,
        )
        if not record.messages:
            record.messages = run_opening_messages(
                match.task if match is not None else None,
                instruction=record.instruction,
                project_dir=project_dir,
                stage=stage,
                state=state,
                existing=record.context,
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
        self,
        project_dir: Path,
        options: RunStageRequest,
        task: TaskEnvelope | None,
        feedback: str | None,
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = self._prepare_stage(project_dir, self._state(project_dir), "voice")
        settings = TTSSettings.from_environment(None)
        override = options.voice_id
        if override:
            settings.voice_id = override
        with VolcengineTTS(settings) as provider:
            run = VoiceHarnessController.from_provider(
                provider, voice_id=override
            ).run(
                narrative,
                project_dir,
                state,
                task,
                instruction=feedback,
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
        self,
        project_dir: Path,
        options: RunStageRequest,
        task: TaskEnvelope | None,
        feedback: str | None,
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        state = self._prepare_stage(project_dir, self._state(project_dir), "references")
        generation = AssetGenerationSettings.from_environment(
            None, image_model=options.image_model
        )
        with SeedreamIdentityImageProvider(generation) as provider:
            artifact, run = ShotAssetHarnessController.from_provider(
                None,
                image_provider=provider,
            ).run(
                narrative,
                project_dir,
                state,
                task,
                instruction=feedback,
                identity_only=True,
            )
        identity_artifact = IdentityReferenceArtifact(
            project_id=artifact.project_id,
            identities=artifact.identities,
        )
        writer = ArtifactWriter(project_dir.parent)
        writer.write_identity_references(project_dir, identity_artifact)
        writer.write_identity_reference_run(
            project_dir,
            IdentityReferenceRun(
                task=run.task,
                actions=run.actions,
                committed_state_version=run.committed_state_version,
                project_state=run.project_state,
            ),
        )

    def _asset(
        self,
        project_dir: Path,
        options: RunStageRequest,
        task: TaskEnvelope | None,
        feedback: str | None,
    ) -> None:
        narrative = NarrativeArtifact.model_validate_json(
            (project_dir / "narrative_artifact.json").read_text(encoding="utf-8")
        )
        route = resolve_production_route(narrative)
        if route.asset_route == "shot_ai_video":
            identity_only = bool(
                task is not None
                and str(task.input_hash).startswith("repair-")
                and not task.scope.shot_ids
            )
            state = self._prepare_stage(project_dir, self._state(project_dir), "asset")
            generation = AssetGenerationSettings.from_environment(
                None,
                image_model=options.image_model,
                video_model=options.video_model,
            )
            with (
                SeedanceShotVideoProvider(generation) as video_provider,
                SeedreamIdentityImageProvider(generation) as image_provider,
            ):
                artifact, run = ShotAssetHarnessController.from_provider(
                    None if identity_only else video_provider,
                    image_provider=image_provider,
                ).run(
                    narrative,
                    project_dir,
                    state,
                    task,
                    instruction=feedback,
                    identity_only=identity_only,
                )
            writer = ArtifactWriter(project_dir.parent)
            if identity_only:
                writer.write_identity_references(
                    project_dir,
                    IdentityReferenceArtifact(
                        project_id=artifact.project_id,
                        identities=artifact.identities,
                    ),
                )
                writer.write_identity_reference_run(
                    project_dir,
                    IdentityReferenceRun(
                        task=run.task,
                        actions=run.actions,
                        committed_state_version=run.committed_state_version,
                        project_state=run.project_state,
                    ),
                )
                return
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
            voice_path = project_dir / "voice_artifact.json"
            voice = (
                VoiceArtifact.model_validate_json(
                    voice_path.read_text(encoding="utf-8")
                )
                if voice_path.is_file()
                else None
            )
            artifact, run = ShotRenderHarnessController().run(
                timeline, project_dir, state, voice=voice, narrative=narrative
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
