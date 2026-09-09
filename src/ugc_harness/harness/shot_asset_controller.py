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
from ..content import ProductionShot
from ..shared.media_duration import probe_duration_ms
from ..shared.models import StrictModel
from ..shared.video_generation import last_frame_bytes_from_job
from .dependencies import DependencyGraph
from .dependency_builders import content_commits, has_content_graph
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
from .production_routes import resolve_production_route
from .world_identities import (
    document_from_state,
    identity_record,
    readable_reference,
)

LIST_TOOL = "asset.list_shots"
GENERATE_TOOL = "asset.generate_shot"
SUBMIT_TOOL = "asset.submit_candidate"
MAX_REFERENCE_IMAGES = 9


@dataclass(frozen=True)
class ShotReferenceImage:
    path: Path
    description: str


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
    shot_count: int = Field(ge=1)
    ai_video_count: int = Field(ge=1)
    missing_shot_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class ShotAssetArtifact(StrictModel):
    schema_version: str = "shot-assets.v1"
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str
    format_id: Literal["drama", "tutorial"]
    source_narrative: str = "narrative_artifact.json"
    assets: list[ShotVideoAsset] = Field(min_length=1)
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
        provider: ShotVideoProvider,
        tool_model: object | None = None,
    ) -> None:
        self.provider = provider
        self.agent = self._make_agent(provider, tool_model)

    @classmethod
    def from_provider(
        cls,
        provider: ShotVideoProvider,
        tool_model: object | None = None,
    ) -> "ShotAssetHarnessController":
        return cls(provider, tool_model)

    @staticmethod
    def _make_agent(
        provider: ShotVideoProvider,
        tool_model: object | None,
    ) -> GenericAgent:
        from ..tools.registry import ToolRegistry

        tools = ToolRegistry()
        for name in (LIST_TOOL, GENERATE_TOOL, SUBMIT_TOOL):
            tools.register(name, lambda: None)

        def transport_factory(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> RegistryToolTransport:
            state: ProjectState = kwargs["state"]
            narrative: NarrativeArtifact = kwargs["narrative"]
            project_dir = Path(kwargs["project_dir"])
            scoped = _scoped_shot_ids(narrative, task)
            session: dict[str, ShotVideoAsset] = {
                asset.shot_id: asset
                for asset in _existing_assets(project_dir, state, scoped)
            }

            def list_shots(problems: list[str] | None = None) -> dict[str, Any]:
                del problems
                return _list_shots(state, project_dir, scoped, session)

            def generate_shot(
                shot_id: str,
                references: list[dict[str, str]] | None = None,
                problems: list[str] | None = None,
            ) -> dict[str, Any]:
                del problems
                asset = generate_shot_video(
                    state,
                    project_dir,
                    shot_id.strip(),
                    provider,
                    references=references,
                )
                session[asset.shot_id] = asset
                return _list_shots(state, project_dir, scoped, session)

            def submit(problems: list[str] | None = None) -> ShotAssetArtifact:
                del problems
                for asset in _existing_assets(project_dir, state, scoped):
                    session.setdefault(asset.shot_id, asset)
                payload = _list_shots(state, project_dir, scoped, session)
                missing = [shot_id for shot_id in scoped if shot_id not in session]
                if payload["pending"] or missing:
                    raise RuntimeError(
                        "candidate is incomplete: missing ai_video for "
                        + ", ".join(payload["pending"] or missing)
                    )
                assets = [session[shot_id] for shot_id in scoped]
                route = resolve_production_route(narrative)
                return ShotAssetArtifact(
                    project_id=narrative.brief.project_id,
                    format_id=route.format_id,  # type: ignore[arg-type]
                    assets=assets,
                    quality=ShotAssetQuality(
                        passed=True,
                        shot_count=len(assets),
                        ai_video_count=len(assets),
                    ),
                )

            return RegistryToolTransport(
                [
                    RegistryTool(
                        name=LIST_TOOL,
                        description="列出范围内各镜是否已有成片、上一镜、以及可用参考图 materials。",
                        handler=list_shots,
                    ),
                    RegistryTool(
                        name=GENERATE_TOOL,
                        description="生成一镜 AI 视频。传 shot_id 和 references（materials 的 id + 说明这张图是尾帧还是身份参考）。",
                        handler=generate_shot,
                        parameters={
                            "type": "object",
                            "properties": {
                                "shot_id": {"type": "string"},
                                "references": {
                                    "type": "array",
                                    "description": "本镜参考图。id 来自 list_shots.materials；description 写进 prompt。",
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
                            "required": ["shot_id"],
                            "additionalProperties": False,
                        },
                    ),
                    RegistryTool(
                        name=SUBMIT_TOOL,
                        description="范围内每镜都有成片后提交。",
                        handler=submit,
                    ),
                ]
            )

        def context_builder(
            task: TaskEnvelope,
            kwargs: dict[str, Any],
        ) -> dict[str, Any]:
            narrative = kwargs["narrative"]
            assert isinstance(narrative, NarrativeArtifact)
            shots = narrative.shots.shots if narrative.shots is not None else []
            return {
                "asset_context": {
                    "project_id": narrative.brief.project_id,
                    "shot_count": len(shots),
                    "scoped_shot_ids": _scoped_shot_ids(narrative, task),
                }
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
            allowed_tools=[LIST_TOOL, GENERATE_TOOL, SUBMIT_TOOL],
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
                max_steps=min(128, max(6, len(ids) + 4)),
                max_retries=1,
            ),
            input_hash=hashlib.sha256(narrative.model_dump_json().encode()).hexdigest(),
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
    ) -> tuple[ShotAssetArtifact, ShotAssetRun]:
        route = resolve_production_route(narrative)
        if route.asset_route != "shot_ai_video" or narrative.shots is None:
            raise ValueError("Shot Asset route only supports drama/tutorial AI video")
        if state.context.project_id != narrative.brief.project_id:
            raise ValueError("asset inputs have different project_id values")
        if state.evaluation.stages.asset not in {
            "ready",
            "pending",
            "blocked",
            "not_required",
            "needs_revision",
            "stale",
        }:
            raise ValueError("shot asset stage is not ready in project state")
        if not has_content_graph(state.dependency_graph.nodes):
            raise ValueError("shot asset stage requires a content graph")
        envelope = task or self.create_task(narrative, state)
        if envelope.based_on_state_version != state.context.state_version:
            raise ValueError("STALE_RESULT: shot asset task uses an obsolete state")
        execution = self.agent.run(
            envelope,
            narrative=narrative,
            project_dir=project_dir,
            state=state,
        )
        artifact = execution.candidate
        if execution.result.status != "completed" or not isinstance(
            artifact, ShotAssetArtifact
        ):
            raise RuntimeError(execution.result.error or "shot asset agent did not complete")
        next_state = state.model_copy(deep=True)
        next_state.context.state_version += 1
        next_state.evaluation.stages.asset = "passed"
        next_state.evaluation.stages.timeline = "ready"
        apply_shot_media(next_state, artifact)
        commit_shot_media(next_state, [asset.shot_id for asset in artifact.assets])
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
) -> ShotVideoAsset:
    root = Path(project_dir)
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
        labeled.append(ShotReferenceImage(path=path, description=description))
    return labeled


def shot_materials(
    state: ProjectState,
    project_dir: Path,
    shot_id: str,
) -> list[dict[str, Any]]:
    description = _shot_description(state, shot_id)
    if description is None:
        raise ValueError(f"production 里没有镜头 {shot_id}")
    materials: list[dict[str, Any]] = []
    if description.continuity.join == "continue_from_previous":
        previous_id = description.continuity.previous_shot_id
        if not previous_id:
            raise RuntimeError(f"{shot_id} 声称接上一镜，但没有 previous_shot_id")
        frame = load_shot_last_frame(project_dir, previous_id)
        materials.append(
            {
                "id": f"last_frame:{previous_id}",
                "kind": "previous_last_frame",
                "shot_id": previous_id,
                "path": (
                    frame.relative_to(project_dir).as_posix() if frame is not None else None
                ),
                "ready": frame is not None,
                "default_description": (
                    "上一镜的尾帧。本镜动作从这里连续，保持同一人物、服装、机位和场景。"
                ),
            }
        )
    for bind in description.spec.binds:
        item = identity_record(state, bind)
        name = str(getattr(item, "name", "") or bind) if item is not None else bind
        relative = getattr(item, "reference_image", None) if item is not None else None
        ready = readable_reference(project_dir, relative)
        kind = bind.partition(":")[0]
        label = {"cast": "人物", "prop": "道具", "place": "场景"}.get(kind, "身份")
        materials.append(
            {
                "id": bind,
                "kind": "identity",
                "name": name,
                "path": str(relative) if ready else None,
                "ready": ready,
                "default_description": (
                    f"{label}{name}的身份参考图。外貌、服装和气质必须与这张图一致。"
                ),
            }
        )
    return materials


def _default_shot_references(
    state: ProjectState,
    project_dir: Path,
    shot_id: str,
    materials: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    description = _shot_description(state, shot_id)
    if description is None:
        raise ValueError(f"production 里没有镜头 {shot_id}")
    if description.continuity.join == "continue_from_previous":
        previous_id = description.continuity.previous_shot_id or "?"
        previous = _shot_description(state, previous_id)
        if _shot_video_path(project_dir, previous) is None:
            raise RuntimeError(
                f"{shot_id} 需要上一镜 {previous_id} 的成片，请按 order 先生成"
            )
        last = next(
            (item for item in materials if item["kind"] == "previous_last_frame"),
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
    picked: list[tuple[dict[str, Any], str]] = []
    for item in materials:
        if item.get("ready"):
            picked.append((item, str(item["default_description"])))
    return picked[:MAX_REFERENCE_IMAGES]


def _material_not_ready(material: dict[str, Any]) -> str:
    ident = str(material.get("id") or "")
    if material.get("kind") == "previous_last_frame":
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


def _list_shots(
    state: ProjectState,
    project_dir: Path,
    scoped: list[str],
    session: dict[str, ShotVideoAsset],
) -> dict[str, Any]:
    shots: list[dict[str, Any]] = []
    pending: list[str] = []
    ordered = sorted(state.production.shots, key=lambda item: item.spec.order)
    for description in ordered:
        shot_id = description.spec.shot_id
        if shot_id not in scoped:
            continue
        has_media = shot_id in session or _shot_video_path(project_dir, description) is not None
        previous_id = None
        if description.continuity.join == "continue_from_previous":
            previous_id = description.continuity.previous_shot_id
        shots.append(
            {
                "shot_id": shot_id,
                "order": description.spec.order,
                "has_media": has_media,
                "previous_shot_id": previous_id,
                "binds": list(description.spec.binds),
                "materials": shot_materials(state, project_dir, shot_id),
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
