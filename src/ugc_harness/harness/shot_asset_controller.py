from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from ..agents.generic import (
    CompletionSpec,
    EnvironmentToolModel,
    GenericAgent,
    RegistryTool,
    RegistryToolTransport,
)
from ..agents.narrative_agent.models import NarrativeArtifact
from ..content import DramaVisualSpec, ProductionShot, TutorialVisualSpec
from ..shared.media_duration import probe_duration_ms
from ..shared.models import StrictModel
from ..shared.video_generation import last_frame_bytes_from_job
from .dependencies import DependencyGraph
from .dependency_builders import content_commits, has_content_graph
from .description import ShotGenerationReference
from .description_builder import refresh_shot_continuity
from .description_realization import apply_shot_media
from .models import (
    ActionRecord,
    ArtifactRef,
    ProjectState,
    StatePatch,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
)
from .production_routes import resolve_production_route, tutorial_needs_voice
from .shot_reference_controller import (
    IdentityImageProvider,
    IdentityReferenceArtifact,
    IdentityReferenceItem,
    apply_identity_references,
    write_identity_image,
)
from .world_identities import (
    bound_identity_refs,
    document_from_state,
    identity_record,
    readable_reference,
)


def _shot_step_budget(item_count: int) -> int:
    """Cover skill.activate, list, one generate per item, submit, and retries."""

    return min(128, max(8, item_count * 2 + 6))


LIST_TOOL = "asset.list_shots"
LIST_IDENTITIES_TOOL = "asset.list_identities"
GRAPH_TOOL = "asset.list_graph"
GENERATE_TOOL = "asset.generate_shot"
GENERATE_IDENTITY_TOOL = "asset.generate_identity"
SUBMIT_TOOL = "asset.submit_candidate"
MAX_REFERENCE_IMAGES = 9


@dataclass(frozen=True)
class ShotReferenceImage:
    path: Path
    description: str
    material_id: str = ""
    kind: str = ""
    name: str = ""


class GeneratedShotVideo(StrictModel):
    content: bytes
    mime_type: str = "video/mp4"
    model: str
    prompt: str
    job_id: str | None = None
    duration_ms: int = Field(gt=0)
    cost_usd: float | None = Field(default=None, ge=0)
    last_frame: bytes | None = None


class ShotVideoProvider(Protocol):
    def generate(
        self,
        shot: ProductionShot,
        *,
        progress_path: Path,
        reference_images: tuple[ShotReferenceImage, ...] | list[ShotReferenceImage] = (),
        prompt: str | None = None,
        replace_existing_job: bool = False,
    ) -> GeneratedShotVideo: ...


class ShotVideoAsset(StrictModel):
    asset_id: str
    shot_id: str
    beat_id: str = ""
    modality: Literal["ai_video"] = "ai_video"
    local_path: str
    mime_type: str
    sha256: str
    generator_model: str
    generation_prompt: str
    generation_references: list[ShotGenerationReference] = Field(default_factory=list)
    generation_job_id: str | None = None
    duration_ms: int = Field(gt=0)
    generation_cost_usd: float | None = Field(default=None, ge=0)
    audio_mode: Literal["embedded_in_video", "mixed"]
    preserve_source_audio: bool
    generated_media_disclosure_required: Literal[True] = True

    @model_validator(mode="after")
    def unify_beat_id(self) -> "ShotVideoAsset":
        if not self.beat_id:
            self.beat_id = self.shot_id
        return self


class ShotAssetQuality(StrictModel):
    passed: bool
    shot_count: int = Field(ge=0)
    ai_video_count: int = Field(ge=0)
    missing_shot_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ShotAssetArtifact(StrictModel):
    schema_version: str = "shot-assets.v1"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str
    format_id: Literal["drama", "tutorial"]
    source_narrative: str = "narrative_artifact.json"
    assets: list[ShotVideoAsset] = Field(default_factory=list)
    identities: list[IdentityReferenceItem] = Field(default_factory=list)
    quality: ShotAssetQuality


class ShotAssetRun(StrictModel):
    task: TaskEnvelope
    actions: list[ActionRecord]
    committed_state_version: int = Field(ge=1)
    project_state: ProjectState


class ShotAssetHarnessController:
    """GenericAgent host: generate one AI video per scoped ProductionShot."""

    def __init__(
        self,
        provider: ShotVideoProvider | None,
        tool_model: object | None = None,
        image_provider: IdentityImageProvider | None = None,
    ) -> None:
        self.provider = provider
        self.image_provider = image_provider
        self.agent = self._make_agent(provider, image_provider, tool_model)

    @classmethod
    def from_provider(
        cls,
        provider: ShotVideoProvider | None,
        tool_model: object | None = None,
        image_provider: IdentityImageProvider | None = None,
    ) -> "ShotAssetHarnessController":
        return cls(provider, tool_model, image_provider)

    @staticmethod
    def _make_agent(
        provider: ShotVideoProvider | None,
        image_provider: IdentityImageProvider | None,
        tool_model: object | None,
    ) -> GenericAgent:
        from ..tools.registry import ToolRegistry

        tools = ToolRegistry()
        for name in (
            LIST_IDENTITIES_TOOL,
            LIST_TOOL,
            GRAPH_TOOL,
            GENERATE_IDENTITY_TOOL,
            GENERATE_TOOL,
            SUBMIT_TOOL,
        ):
            tools.register(name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            state: ProjectState = kwargs["state"]
            narrative: NarrativeArtifact = kwargs["narrative"]
            project_dir = Path(kwargs["project_dir"])
            scoped = _scoped_shot_ids(narrative, task)
            force_shots = set(kwargs.get("regenerate_shot_ids") or [])
            if not force_shots and task.input_hash == "repair-asset":
                force_shots = set(task.scope.shot_ids)
            force_identities = set(kwargs.get("regenerate_identity_refs") or [])
            if not force_identities and task.input_hash == "repair-asset":
                force_identities = {
                    ref
                    for ref in task.scope.target_refs
                    if ref.startswith(("cast:", "prop:", "place:"))
                }
            identity_only = bool(kwargs.get("identity_only"))
            scoped_identities = _scoped_identity_refs(state, task, scoped)
            session: dict[str, ShotVideoAsset] = {
                asset.shot_id: asset
                for asset in _existing_assets(project_dir, state, scoped)
                if asset.shot_id not in force_shots
            }
            images: dict[str, str] = {
                ref: path
                for ref, path in _existing_identity_images(
                    state, project_dir, scoped_identities
                ).items()
                if ref not in force_identities
            }

            def list_identities(problems: list[str] | None = None) -> dict[str, Any]:
                del problems
                return _list_identities(
                    state,
                    project_dir,
                    scoped_identities,
                    images,
                    force_identities,
                )

            def list_shots(problems: list[str] | None = None) -> dict[str, Any]:
                del problems
                return _list_shots(
                    state, project_dir, scoped, session, force_shots
                )

            def list_shot_graph(
                around_ref: str | None = None,
                problems: list[str] | None = None,
            ) -> dict[str, Any]:
                del problems
                from ..intake.graph_view import list_graph

                return list_graph(state, around_ref)

            def generate_identity(
                ref: str,
                prompt: str,
                problems: list[str] | None = None,
            ) -> dict[str, Any]:
                del problems
                text = prompt.strip()
                if not text:
                    raise RuntimeError("generate_identity 必须自己写 prompt")
                if image_provider is None:
                    raise RuntimeError("本任务没有图像生成器")
                token = ref.strip()
                write_identity_image(
                    state, project_dir, token, image_provider, prompt=text
                )
                from .world_identities import identity_image_path

                images[token] = identity_image_path(
                    project_dir, token
                ).relative_to(project_dir).as_posix()
                force_identities.discard(token)
                return _list_identities(
                    state,
                    project_dir,
                    scoped_identities,
                    images,
                    force_identities,
                )

            def generate_shot(
                shot_id: str,
                prompt: str,
                references: list[dict[str, str]] | None = None,
                problems: list[str] | None = None,
            ) -> dict[str, Any]:
                del problems
                text = prompt.strip()
                if not text:
                    raise RuntimeError("generate_shot 必须自己写 prompt，先读 list_shots / list_graph")
                if provider is None:
                    raise RuntimeError("本任务没有视频生成器")
                asset = generate_shot_video(
                    state,
                    project_dir,
                    shot_id.strip(),
                    provider,
                    references=references,
                    prompt=text,
                    replace_existing_job=shot_id.strip() in force_shots,
                )
                session[asset.shot_id] = asset
                force_shots.discard(asset.shot_id)
                return _list_shots(
                    state, project_dir, scoped, session, force_shots
                )

            def submit(problems: list[str] | None = None) -> ShotAssetArtifact:
                del problems
                identity_payload = _list_identities(
                    state,
                    project_dir,
                    scoped_identities,
                    images,
                    force_identities,
                )
                if identity_payload["pending"]:
                    raise RuntimeError(
                        "candidate is incomplete: missing identity images for "
                        + ", ".join(identity_payload["pending"])
                    )
                items = [
                    IdentityReferenceItem(
                        ref=item["ref"],
                        kind=item["kind"],
                        name=item["name"],
                        local_path=item["path"],
                        generation_prompt=item.get("generation_prompt"),
                    )
                    for item in identity_payload["identities"]
                    if item.get("path")
                ]
                route = resolve_production_route(narrative)
                if identity_only:
                    if not items:
                        raise RuntimeError("no bound identities to submit")
                    return ShotAssetArtifact(
                        project_id=narrative.brief.project_id,
                        format_id=route.format_id,  # type: ignore[arg-type]
                        identities=items,
                        quality=ShotAssetQuality(
                            passed=True,
                            shot_count=0,
                            ai_video_count=0,
                        ),
                    )
                for asset in _existing_assets(project_dir, state, scoped):
                    if asset.shot_id not in force_shots:
                        session.setdefault(asset.shot_id, asset)
                payload = _list_shots(
                    state, project_dir, scoped, session, force_shots
                )
                missing = [shot_id for shot_id in scoped if shot_id not in session]
                if payload["pending"] or missing:
                    raise RuntimeError(
                        "candidate is incomplete: missing ai_video for "
                        + ", ".join(payload["pending"] or missing)
                    )
                assets = [session[shot_id] for shot_id in scoped]
                return ShotAssetArtifact(
                    project_id=narrative.brief.project_id,
                    format_id=route.format_id,  # type: ignore[arg-type]
                    assets=assets,
                    identities=items,
                    quality=ShotAssetQuality(
                        passed=True,
                        shot_count=len(assets),
                        ai_video_count=len(assets),
                    ),
                )

            registered = [
                RegistryTool(
                    name=LIST_IDENTITIES_TOOL,
                    description="列出范围内身份、是否已有参考图、上次 generation_prompt。",
                    handler=list_identities,
                ),
                RegistryTool(
                    name=GENERATE_IDENTITY_TOOL,
                    description="为指定身份生成参考图。必须自己写 prompt。",
                    handler=generate_identity,
                    parameters={
                        "type": "object",
                        "properties": {
                            "ref": {"type": "string"},
                            "prompt": {"type": "string"},
                            "problems": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["ref", "prompt"],
                        "additionalProperties": False,
                    },
                ),
                RegistryTool(
                    name=LIST_TOOL,
                    description="列出范围内各镜正文、是否已有成片、上次导演句和配图，以及 materials。",
                    handler=list_shots,
                ),
                RegistryTool(
                    name=GRAPH_TOOL,
                    description="读精简依赖图。无参看内容根；around_ref 用 shot: / cast: / prop: / place: 或当前名字。",
                    handler=list_shot_graph,
                    parameters={
                        "type": "object",
                        "properties": {
                            "around_ref": {
                                "type": "string",
                                "description": "焦点节点，如 shot:shot_a1、cast:character_azhou，或身份当前名字。",
                            },
                            "problems": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "additionalProperties": False,
                    },
                ),
                RegistryTool(
                    name=GENERATE_TOOL,
                    description="生成一镜 AI 视频。必须自己写 prompt；references 的数组顺序就是图片1、图片2。",
                    handler=generate_shot,
                    parameters={
                        "type": "object",
                        "properties": {
                            "shot_id": {"type": "string"},
                            "prompt": {
                                "type": "string",
                                "description": "发给 Seedance 的完整导演句。用图片N指代 references 数组顺序。",
                            },
                            "references": {
                                "type": "array",
                                "description": "本镜参考图。id 来自 list_shots.materials；description 只写职务。",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["id", "description"],
                                    "additionalProperties": False,
                                },
                            },
                            "problems": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["shot_id", "prompt"],
                        "additionalProperties": False,
                    },
                ),
                RegistryTool(
                    name=SUBMIT_TOOL,
                    description="范围内该齐的参考图和成片都齐后提交。",
                    handler=submit,
                ),
            ]
            allowed = set(task.allowed_tools)
            return RegistryToolTransport(
                [item for item in registered if item.name in allowed]
            )

        def context_builder(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> dict[str, Any]:
            state: ProjectState = kwargs["state"]
            project_dir = Path(kwargs["project_dir"])
            narrative = kwargs["narrative"]
            scoped = _scoped_shot_ids(narrative, task)
            scoped_identities = _scoped_identity_refs(state, task, scoped)
            return {
                "memory": _asset_memory(state, project_dir, scoped_identities, scoped),
                "turn": {
                    "instruction": kwargs.get("instruction") or "",
                    "regenerate_identity_refs": list(
                        kwargs.get("regenerate_identity_refs") or []
                    ),
                    "regenerate_shot_ids": list(
                        kwargs.get("regenerate_shot_ids") or scoped
                    ),
                    "identity_only": bool(kwargs.get("identity_only")),
                },
            }

        return GenericAgent(
            tool_model=tool_model or EnvironmentToolModel(),
            transport_factory=transport_factory,
            candidate_type=ShotAssetArtifact,
            completion_builder=_asset_completion,
            context_builder=context_builder,
            capability_tools=tools,
        )

    def create_task(
        self,
        narrative: NarrativeArtifact,
        state: ProjectState,
        *,
        shot_ids: list[str] | None = None,
    ) -> TaskEnvelope:
        route = resolve_production_route(narrative)
        if route.asset_route != "shot_ai_video" or narrative.shots is None:
            raise ValueError("Shot Asset route only supports drama/tutorial AI video")
        ids = shot_ids or [shot.shot_id for shot in narrative.shots.shots]
        graph = DependencyGraph(state.dependency_graph)
        identity_refs = sorted(
            {
                bind
                for shot in state.production.shots
                if shot.spec.shot_id in set(ids)
                for bind in shot.spec.binds
                if bind in state.dependency_graph.nodes
            }
        )
        snapshot_refs = [
            ref for ref in identity_refs
            if state.dependency_graph.nodes[ref].status == "current"
        ]
        return TaskEnvelope(
            task_id=f"task_shot_asset_{narrative.brief.project_id}_v{state.context.state_version}",
            agent="asset_stage",
            goal="Generate exactly one AI video asset for every scoped ProductionShot.",
            scope=TaskScope(
                project_id=narrative.brief.project_id,
                shot_ids=ids,
            ),
            based_on_state_version=state.context.state_version,
            format_id=route.format_id,
            allowed_tools=[
                LIST_IDENTITIES_TOOL,
                LIST_TOOL,
                GRAPH_TOOL,
                GENERATE_IDENTITY_TOOL,
                GENERATE_TOOL,
                SUBMIT_TOOL,
            ],
            required_outputs=["assets"],
            forbidden_actions=[
                "search_web_assets",
                "generate_images",
                "read_editorial_artifact",
                "read_voice_artifact",
                "modify_narrative",
            ],
            acceptance_criteria=[
                "Every scoped Shot has exactly one ai_video asset.",
                "Drama keeps embedded clip audio; tutorial keeps source operation audio.",
            ],
            budget=TaskBudget(
                max_steps=_shot_step_budget(len(ids)),
                max_retries=1,
            ),
            input_hash=hashlib.sha256(narrative.model_dump_json().encode()).hexdigest(),
            dependency_snapshot=(
                graph.snapshot(snapshot_refs) if snapshot_refs else []
            ),
        )

    def create_identity_task(
        self,
        narrative: NarrativeArtifact,
        state: ProjectState,
    ) -> TaskEnvelope:
        route = resolve_production_route(narrative)
        refs = bound_identity_refs(state)
        graph = DependencyGraph(state.dependency_graph)
        snapshot_refs = [
            ref for ref in refs if ref in state.dependency_graph.nodes
            and state.dependency_graph.nodes[ref].status == "current"
        ]
        return TaskEnvelope(
            task_id=(
                f"task_shot_identity_{narrative.brief.project_id}_v"
                f"{state.context.state_version}"
            ),
            agent="asset_stage",
            goal="为镜头绑定的身份各生成一张参考图，写回身份节点。",
            scope=TaskScope(
                project_id=narrative.brief.project_id,
                target_refs=refs,
            ),
            based_on_state_version=state.context.state_version,
            format_id=route.format_id,
            allowed_tools=[
                LIST_IDENTITIES_TOOL,
                GRAPH_TOOL,
                GENERATE_IDENTITY_TOOL,
                SUBMIT_TOOL,
            ],
            required_outputs=["identities"],
            budget=TaskBudget(
                max_steps=_shot_step_budget(len(refs)),
                max_retries=1,
            ),
            input_hash="identity-only",
            dependency_snapshot=(
                graph.snapshot(snapshot_refs) if snapshot_refs else []
            ),
        )

    def run(
        self,
        narrative: NarrativeArtifact,
        project_dir: str | Path,
        state: ProjectState,
        task: TaskEnvelope | None = None,
        *,
        instruction: str | None = None,
        regenerate_identity_refs: list[str] | None = None,
        regenerate_shot_ids: list[str] | None = None,
        identity_only: bool = False,
    ) -> tuple[ShotAssetArtifact, ShotAssetRun]:
        route = resolve_production_route(narrative)
        if route.asset_route != "shot_ai_video" or narrative.shots is None:
            raise ValueError("Shot Asset route only supports drama/tutorial AI video")
        if state.context.project_id != narrative.brief.project_id:
            raise ValueError("asset inputs have different project_id values")
        refresh_shot_continuity(state)
        is_repair = bool(
            regenerate_identity_refs
            or regenerate_shot_ids
            or (task is not None and str(task.input_hash).startswith("repair-"))
        )
        if identity_only:
            allowed = {
                "ready",
                "pending",
                "blocked",
                "not_required",
                "needs_revision",
                "stale",
                "passed",
            }
            if state.evaluation.stages.references not in allowed:
                raise ValueError("shot asset identity pass is not ready in project state")
        else:
            allowed = {
                "ready",
                "pending",
                "blocked",
                "not_required",
                "needs_revision",
                "stale",
            }
            if is_repair:
                allowed.add("passed")
            if state.evaluation.stages.asset not in allowed:
                raise ValueError("shot asset stage is not ready in project state")
        if not has_content_graph(state.dependency_graph.nodes):
            raise ValueError("shot asset stage requires a content graph")
        envelope = task
        if envelope is None:
            envelope = (
                self.create_identity_task(narrative, state)
                if identity_only
                else self.create_task(
                    narrative,
                    state,
                    shot_ids=regenerate_shot_ids,
                )
            )
        if (
            identity_only
            and envelope is not None
            and GENERATE_IDENTITY_TOOL not in envelope.allowed_tools
        ):
            envelope = self.create_identity_task(narrative, state)
        if envelope.based_on_state_version != state.context.state_version:
            raise ValueError("STALE_RESULT: shot asset task uses an obsolete state")
        execution = self.agent.run(
            envelope,
            narrative=narrative,
            project_dir=project_dir,
            state=state,
            instruction=instruction,
            regenerate_identity_refs=regenerate_identity_refs or [],
            regenerate_shot_ids=regenerate_shot_ids or [],
            identity_only=identity_only,
        )
        artifact = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            artifact, ShotAssetArtifact
        ):
            raise RuntimeError(execution.result.error or "shot asset agent did not complete")
        next_state = state.model_copy(deep=True)
        next_state.context.state_version += 1
        if artifact.identities:
            apply_identity_references(
                next_state,
                IdentityReferenceArtifact(
                    project_id=narrative.brief.project_id,
                    identities=artifact.identities,
                ),
            )
            next_state.evaluation.stages.references = "passed"
        if identity_only:
            if str(state.evaluation.stages.asset) not in {
                "passed",
                "needs_revision",
                "stale",
                "locked",
            }:
                next_state.evaluation.stages.asset = "ready"
        else:
            merged = _merge_shot_asset_artifact(project_dir, artifact)
            apply_shot_media(next_state, merged)
            commit_shot_media(
                next_state, [asset.shot_id for asset in artifact.assets]
            )
            next_state.evaluation.stages.asset = "passed"
            needs_voice = tutorial_needs_voice(narrative)
            voice_status = str(next_state.evaluation.stages.voice)
            if needs_voice and voice_status not in {
                "passed",
                "needs_revision",
                "stale",
                "locked",
            }:
                next_state.evaluation.stages.voice = "ready"
                next_state.evaluation.stages.timeline = "pending"
            elif str(state.evaluation.stages.timeline) in {
                "passed",
                "needs_revision",
                "stale",
                "locked",
            }:
                next_state.evaluation.stages.timeline = "pending"
            else:
                next_state.evaluation.stages.timeline = "ready"
            if str(state.evaluation.stages.render) in {
                "passed",
                "needs_revision",
                "stale",
                "locked",
            }:
                next_state.evaluation.stages.render = "pending"
            artifact = merged
        return artifact, ShotAssetRun(
            task=envelope,
            actions=execution.result.actions,
            committed_state_version=next_state.context.state_version,
            project_state=next_state,
        )


def generate_shot_video(
    state: ProjectState,
    project_dir: str | Path,
    shot_id: str,
    provider: ShotVideoProvider,
    *,
    references: list[dict[str, Any]] | None = None,
    prompt: str | None = None,
    replace_existing_job: bool = False,
) -> ShotVideoAsset:
    root = Path(project_dir)
    refresh_shot_continuity(state)
    description = _shot_description(state, shot_id)
    if description is None:
        raise ValueError(f"production 里没有镜头 {shot_id}")
    shot = description.spec
    labeled = resolve_shot_references(state, root, shot_id, references)
    progress = root / "harness" / "seedance_jobs" / f"{shot.shot_id}.json"
    generated = provider.generate(
        shot,
        progress_path=progress,
        reference_images=labeled,
        prompt=prompt,
        replace_existing_job=replace_existing_job,
    )
    folder = root / "assets" / "generated_shots"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"asset_{shot.shot_id}.mp4"
    output.write_bytes(generated.content)
    duration_ms = probe_duration_ms(output) or generated.duration_ms
    if generated.last_frame:
        write_shot_last_frame(root, shot.shot_id, generated.last_frame)
    preserve_source = (
        shot.audio.audio_mode == "embedded_in_video"
        or bool(getattr(shot.audio, "preserve_source_audio", False))
    )
    asset = ShotVideoAsset(
        asset_id=f"asset_{shot.shot_id}",
        shot_id=shot.shot_id,
        beat_id=shot.shot_id,
        local_path=output.relative_to(root).as_posix(),
        mime_type=generated.mime_type,
        sha256=hashlib.sha256(generated.content).hexdigest(),
        generator_model=generated.model,
        generation_prompt=generated.prompt,
        generation_references=[
            ShotGenerationReference(
                id=item.material_id or item.path.name,
                description=item.description,
            )
            for item in labeled
        ],
        generation_job_id=generated.job_id,
        duration_ms=duration_ms,
        generation_cost_usd=generated.cost_usd,
        audio_mode=shot.audio.audio_mode,
        preserve_source_audio=preserve_source,
    )
    return asset


def resolve_shot_references(
    state: ProjectState,
    project_dir: Path,
    shot_id: str,
    requested: list[dict[str, Any]] | None = None,
) -> list[ShotReferenceImage]:
    materials = shot_materials(state, project_dir, shot_id)
    by_id = {item["id"]: item for item in materials}
    if requested is None:
        selected = _default_shot_references(state, project_dir, shot_id, materials)
    else:
        selected = []
        for item in requested:
            ident = str(item.get("id") or "").strip()
            description = str(item.get("description") or "").strip()
            if not ident or not description:
                raise RuntimeError("references 每项都要有 id 和 description")
            material = by_id.get(ident)
            if material is None:
                raise RuntimeError(f"本镜没有参考图 {ident}")
            if not material.get("ready"):
                raise RuntimeError(_material_not_ready(material))
            selected.append((material, description))
    if not selected:
        raise RuntimeError("至少要选一张参考图")
    if len(selected) > MAX_REFERENCE_IMAGES:
        raise RuntimeError(f"参考图最多 {MAX_REFERENCE_IMAGES} 张")
    labeled: list[ShotReferenceImage] = []
    for material, description in selected:
        path = project_dir / str(material["path"])
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(_material_not_ready(material))
        labeled.append(
            ShotReferenceImage(
                path=path,
                description=description,
                material_id=str(material["id"]),
                kind=str(material.get("kind") or ""),
                name=str(material.get("name") or ""),
            )
        )
    return labeled


def shot_materials(
    state: ProjectState,
    project_dir: Path,
    shot_id: str,
) -> list[dict[str, Any]]:
    description = _shot_description(state, shot_id)
    if description is None:
        raise ValueError(f"production 里没有镜头 {shot_id}")
    identities: list[dict[str, Any]] = []
    for bind in description.spec.binds:
        item = identity_record(state, bind)
        name = str(getattr(item, "name", "") or bind) if item is not None else bind
        relative = getattr(item, "reference_image", None) if item is not None else None
        ready = readable_reference(project_dir, relative)
        kind = bind.partition(":")[0]
        label = {"cast": "人物", "prop": "道具", "place": "场景"}.get(kind, "身份")
        identities.append(
            {
                "id": bind,
                "kind": "identity",
                "role": kind if kind in {"cast", "prop", "place"} else "identity",
                "name": name,
                "path": str(relative) if ready else None,
                "ready": ready,
                "default_description": f"{label}{name}",
            }
        )
    tail = _last_frame_material(project_dir, shot_id, description)
    join = description.continuity.join
    if join == "continue_from_previous" and tail is not None:
        return [tail, *identities]
    if join == "bridge_from_previous" and tail is not None:
        return [*identities, tail]
    return identities


def _last_frame_material(
    project_dir: Path,
    shot_id: str,
    description: Any,
) -> dict[str, Any] | None:
    if not description.continuity.uses_previous_tail():
        return None
    previous_id = description.continuity.previous_shot_id
    if not previous_id:
        raise RuntimeError(f"{shot_id} 声称接上一镜，但没有 previous_shot_id")
    frame = load_shot_last_frame(project_dir, previous_id)
    join = description.continuity.join
    kind = (
        "bridge_last_frame"
        if join == "bridge_from_previous"
        else "previous_last_frame"
    )
    role = "bridge" if join == "bridge_from_previous" else "continue"
    return {
        "id": f"last_frame:{previous_id}",
        "kind": kind,
        "role": role,
        "name": "上一镜尾帧",
        "shot_id": previous_id,
        "path": (
            frame.relative_to(project_dir).as_posix() if frame is not None else None
        ),
        "ready": frame is not None,
        "default_description": "上一镜尾帧",
    }


def _default_shot_references(
    state: ProjectState,
    project_dir: Path,
    shot_id: str,
    materials: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    description = _shot_description(state, shot_id)
    if description is None:
        raise ValueError(f"production 里没有镜头 {shot_id}")
    if description.continuity.uses_previous_tail():
        previous_id = description.continuity.previous_shot_id or "?"
        previous = _shot_description(state, previous_id)
        if _shot_video_path(project_dir, previous) is None:
            raise RuntimeError(
                f"{shot_id} 需要上一镜 {previous_id} 的成片，请按 order 先生成"
            )
        last = next(
            (
                item
                for item in materials
                if str(item.get("id") or "").startswith("last_frame:")
            ),
            None,
        )
        if last is None or not last.get("ready"):
            raise RuntimeError(
                f"{shot_id} 需要上一镜 {previous_id} 的 Seedance 尾帧，请按 order 先生成"
            )
    missing = [
        item["id"]
        for item in materials
        if item["kind"] == "identity" and not item.get("ready")
    ]
    if missing:
        raise RuntimeError("身份参考图缺失，先跑 references：" + ", ".join(missing))
    last: dict[str, Any] | None = None
    identities: list[tuple[dict[str, Any], str]] = []
    for item in materials:
        if not item.get("ready"):
            continue
        if str(item.get("id") or "").startswith("last_frame:"):
            last = item
            continue
        identities.append((item, str(item["default_description"])))
    budget = MAX_REFERENCE_IMAGES - (1 if last is not None else 0)
    picked = identities[:budget]
    if last is not None:
        pair = (last, str(last["default_description"]))
        if description.continuity.join == "continue_from_previous":
            picked.insert(0, pair)
        else:
            picked.append(pair)
    return picked


def _material_not_ready(material: dict[str, Any]) -> str:
    ident = str(material.get("id") or "")
    if ident.startswith("last_frame:") or material.get("kind") in {
        "previous_last_frame",
        "bridge_last_frame",
    }:
        previous_id = material.get("shot_id") or ident.partition(":")[2]
        return f"需要上一镜 {previous_id} 的 Seedance 尾帧，请按 order 先生成"
    return f"身份参考图缺失，先跑 references：{ident}"


def commit_shot_media(state: ProjectState, shot_ids: list[str]) -> None:
    document = document_from_state(state)
    allowed = {f"shot:{shot_id}" for shot_id in shot_ids}
    commits = [item for item in content_commits(document) if item.ref in allowed]
    missing = allowed - {item.ref for item in commits}
    if missing:
        raise ValueError(f"无法提交这些镜头节点：{sorted(missing)}")
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="shot_asset_submit",
        produced_by="asset_stage",
        commits=commits,
    )


def _scoped_shot_ids(narrative: NarrativeArtifact, task: TaskEnvelope) -> list[str]:
    if narrative.shots is None:
        return list(task.scope.shot_ids)
    ordered = [shot.shot_id for shot in narrative.shots.shots]
    wanted = set(task.scope.shot_ids) if task.scope.shot_ids else set(ordered)
    return [shot_id for shot_id in ordered if shot_id in wanted]


def _shot_view(state: ProjectState, description: Any) -> dict[str, Any]:
    spec = description.spec
    identities: list[dict[str, str]] = []
    for bind in spec.binds:
        item = identity_record(state, bind)
        identities.append(
            {
                "id": bind,
                "role": bind.partition(":")[0],
                "name": str(getattr(item, "name", "") or bind) if item is not None else bind,
                "description": (
                    str(getattr(item, "description", "") or "") if item is not None else ""
                ),
            }
        )
    visual = spec.visual
    payload: dict[str, Any] = {
        "shot_kind": spec.shot_kind,
        "purpose": spec.purpose,
        "source_refs": list(spec.source_refs),
        "identities": identities,
        "continuity": {
            "join": description.continuity.join,
            "previous_shot_id": description.continuity.previous_shot_id,
        },
    }
    if isinstance(visual, DramaVisualSpec):
        payload["visual"] = {
            "action_description": visual.action_description,
            "camera_instruction": visual.camera_instruction,
            "continuity_constraints": list(visual.continuity_constraints),
        }
    elif isinstance(visual, TutorialVisualSpec):
        subject = visual.subject_ref
        subject_name = next(
            (
                item["name"]
                for item in identities
                if item["id"] in {subject, f"prop:{subject}"}
                or item["id"].partition(":")[2] == subject
            ),
            "",
        )
        payload["visual"] = {
            "subject_ref": visual.subject_ref,
            "subject_name": subject_name,
            "hands_required": visual.hands_required,
            "camera_angle": visual.camera_angle,
            "critical_detail": visual.critical_detail,
        }
    audio = spec.audio
    payload["audio"] = {
        "dialogue_lines": list(getattr(audio, "dialogue_lines", []) or []),
        "narration_text": getattr(spec.payload, "narration_text", None),
        "ambient_audio": getattr(audio, "ambient_audio", None),
        "source_audio_types": list(getattr(audio, "source_audio_types", []) or []),
    }
    return payload


def _list_shots(
    state: ProjectState,
    project_dir: Path,
    scoped: list[str],
    session: dict[str, ShotVideoAsset],
    force_shots: set[str] | None = None,
) -> dict[str, Any]:
    shots: list[dict[str, Any]] = []
    pending: list[str] = []
    forced = force_shots or set()
    ordered = sorted(state.production.shots, key=lambda item: item.spec.order)
    for description in ordered:
        shot_id = description.spec.shot_id
        if shot_id not in scoped:
            continue
        has_media = shot_id in session or (
            shot_id not in forced
            and _shot_video_path(project_dir, description) is not None
        )
        previous_id = description.continuity.previous_shot_id
        media = description.media
        last_refs = []
        if media is not None:
            last_refs = [
                item.model_dump(mode="json")
                if hasattr(item, "model_dump")
                else item
                for item in (media.generation_references or [])
            ]
        shots.append(
            {
                "shot_id": shot_id,
                "order": description.spec.order,
                "has_media": has_media,
                "previous_shot_id": previous_id,
                "binds": list(description.spec.binds),
                "materials": shot_materials(state, project_dir, shot_id),
                "generation_prompt": (
                    session[shot_id].generation_prompt
                    if shot_id in session
                    else (media.generation_prompt if media is not None else None)
                ),
                "generation_references": (
                    [
                        item.model_dump(mode="json")
                        for item in session[shot_id].generation_references
                    ]
                    if shot_id in session
                    else last_refs
                ),
                **_shot_view(state, description),
            }
        )
        if not has_media:
            pending.append(shot_id)
    return {"shots": shots, "pending": pending}


def _existing_assets(
    project_dir: Path,
    state: ProjectState,
    scoped: list[str],
) -> list[ShotVideoAsset]:
    found: list[ShotVideoAsset] = []
    for shot_id in scoped:
        description = _shot_description(state, shot_id)
        path = _shot_video_path(project_dir, description)
        if path is None or description is None:
            continue
        media = description.media
        found.append(
            ShotVideoAsset(
                asset_id=f"asset_{shot_id}",
                shot_id=shot_id,
                beat_id=shot_id,
                local_path=(
                    media.local_path
                    if media is not None and media.local_path
                    else path.relative_to(project_dir).as_posix()
                ),
                mime_type="video/mp4",
                sha256=(
                    media.sha256
                    if media is not None and media.sha256
                    else hashlib.sha256(path.read_bytes()).hexdigest()
                ),
                generator_model=(
                    media.generator_model if media is not None else "unknown"
                ),
                generation_prompt=(
                    media.generation_prompt if media is not None else ""
                ),
                generation_references=list(
                    getattr(media, "generation_references", []) or []
                )
                if media is not None
                else [],
                generation_job_id=(
                    media.generation_job_id if media is not None else None
                ),
                duration_ms=(
                    probe_duration_ms(path)
                    or (media.duration_ms if media is not None and media.duration_ms else 4000)
                ),
                generation_cost_usd=(
                    media.generation_cost_usd if media is not None else None
                ),
                audio_mode=description.spec.audio.audio_mode,
                preserve_source_audio=True,
            )
        )
    return found


def _shot_description(state: ProjectState, shot_id: str):
    for shot in state.production.shots:
        if shot.spec.shot_id == shot_id:
            return shot
    return None


def shot_last_frame_path(project_dir: Path, shot_id: str) -> Path:
    return project_dir / "assets" / "shot_frames" / f"{shot_id}_last.png"


def write_shot_last_frame(project_dir: Path, shot_id: str, payload: bytes) -> Path:
    path = shot_last_frame_path(project_dir, shot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def load_shot_last_frame(project_dir: Path, shot_id: str) -> Path | None:
    path = shot_last_frame_path(project_dir, shot_id)
    if path.is_file() and path.stat().st_size:
        return path
    payload = last_frame_bytes_from_job(
        project_dir / "harness" / "seedance_jobs" / f"{shot_id}.json"
    )
    if not payload:
        return None
    return write_shot_last_frame(project_dir, shot_id, payload)


def _shot_video_path(project_dir: Path, description: Any) -> Path | None:
    if description is not None and description.media is not None and description.media.local_path:
        path = project_dir / description.media.local_path
        if path.is_file() and path.stat().st_size:
            return path
    if description is not None:
        fallback = project_dir / "assets" / "generated_shots" / f"asset_{description.spec.shot_id}.mp4"
        if fallback.is_file() and fallback.stat().st_size:
            return fallback
    return None


def _scoped_identity_refs(
    state: ProjectState,
        task: TaskEnvelope,
    scoped_shots: list[str],
) -> list[str]:
    wanted = [
        ref
        for ref in task.scope.target_refs
        if ref.startswith(("cast:", "prop:", "place:"))
    ]
    if wanted:
        return list(dict.fromkeys(wanted))
    found: list[str] = []
    wanted_shots = set(scoped_shots)
    for shot in state.production.shots:
        if shot.spec.shot_id not in wanted_shots:
            continue
        for bind in shot.spec.binds:
            if bind.startswith(("cast:", "prop:", "place:")) and bind not in found:
                found.append(bind)
    return found or bound_identity_refs(state)


def _existing_identity_images(
    state: ProjectState,
    project_dir: Path,
    scoped_identities: list[str],
) -> dict[str, str]:
    found: dict[str, str] = {}
    for ref in scoped_identities:
        item = identity_record(state, ref)
        path = getattr(item, "reference_image", None) if item is not None else None
        if readable_reference(project_dir, path):
            found[ref] = str(path)
    return found


def _list_identities(
    state: ProjectState,
    project_dir: Path,
    scoped_identities: list[str],
    images: dict[str, str],
    force_identities: set[str] | None = None,
) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    pending: list[str] = []
    forced = force_identities or set()
    for ref in scoped_identities:
        item = identity_record(state, ref)
        name = str(getattr(item, "name", "") or ref) if item is not None else ref
        path = images.get(ref)
        if not readable_reference(project_dir, path):
            path = getattr(item, "reference_image", None) if item is not None else None
        has_image = ref not in forced and readable_reference(project_dir, path)
        identities.append(
            {
                "ref": ref,
                "kind": ref.partition(":")[0],
                "name": name,
                "description": (
                    str(getattr(item, "description", "") or "")
                    if item is not None
                    else ""
                ),
                "path": path if has_image else None,
                "has_image": has_image,
                "generation_prompt": (
                    getattr(item, "generation_prompt", None)
                    if item is not None
                    else None
                ),
            }
        )
        if not has_image:
            pending.append(ref)
    return {"identities": identities, "pending": pending}


def _asset_memory(
    state: ProjectState,
    project_dir: Path,
    scoped_identities: list[str],
    scoped_shots: list[str],
) -> dict[str, Any]:
    identities: list[dict[str, Any]] = []
    for ref in scoped_identities:
        item = identity_record(state, ref)
        path = getattr(item, "reference_image", None) if item is not None else None
        has_image = readable_reference(project_dir, path)
        identities.append(
            {
                "ref": ref,
                "kind": ref.partition(":")[0],
                "name": str(getattr(item, "name", "") or ref) if item is not None else ref,
                "generation_prompt": (
                    getattr(item, "generation_prompt", None)
                    if item is not None
                    else None
                ),
                "has_reference_image": has_image,
                "path": str(path) if has_image else None,
            }
        )
    shots: list[dict[str, Any]] = []
    for shot_id in scoped_shots:
        description = _shot_description(state, shot_id)
        if description is None:
            continue
        media = description.media
        refs = []
        if media is not None:
            refs = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in (media.generation_references or [])
            ]
        shots.append(
            {
                "shot_id": shot_id,
                "generation_prompt": (
                    media.generation_prompt if media is not None else None
                ),
                "generation_references": refs,
                "has_media": _shot_video_path(project_dir, description) is not None,
            }
        )
    return {"identities": identities, "shots": shots}


def _merge_shot_asset_artifact(
    project_dir: Path,
    artifact: ShotAssetArtifact,
) -> ShotAssetArtifact:
    path = Path(project_dir) / "shot_asset_artifact.json"
    if not path.is_file():
        return artifact
    try:
        existing = ShotAssetArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return artifact
    assets_by_id = {item.shot_id: item for item in existing.assets}
    for item in artifact.assets:
        assets_by_id[item.shot_id] = item
    ordered: list[ShotVideoAsset] = []
    seen: set[str] = set()
    for item in existing.assets:
        current = assets_by_id.get(item.shot_id)
        if current is None or item.shot_id in seen:
            continue
        ordered.append(current)
        seen.add(item.shot_id)
    for item in artifact.assets:
        if item.shot_id in seen:
            continue
        ordered.append(item)
        seen.add(item.shot_id)
    identities_by_ref = {item.ref: item for item in existing.identities}
    for item in artifact.identities:
        identities_by_ref[item.ref] = item
    return artifact.model_copy(
        update={
            "assets": ordered,
            "identities": list(identities_by_ref.values()),
            "quality": ShotAssetQuality(
                passed=True,
                shot_count=len(ordered),
                ai_video_count=len(ordered),
            ),
        }
    )


def _asset_completion(
    task: TaskEnvelope,
    kwargs: dict[str, Any],
) -> CompletionSpec:
    narrative = kwargs["narrative"]
    assert isinstance(narrative, NarrativeArtifact)
    return CompletionSpec(
        artifact_refs=[ArtifactRef(kind="shot_assets", id=narrative.brief.project_id)],
        state_patch=StatePatch(
            set={"video.asset_status": "ready"},
            invalidate=["timeline:all", "render:final"],
        ),
        evaluation_target=(
            f"asset:{narrative.brief.project_id}@{task.based_on_state_version + 1}"
        ),
        )
