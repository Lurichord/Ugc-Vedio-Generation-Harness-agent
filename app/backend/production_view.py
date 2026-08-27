"""Slim completed-content snapshot for the studio rail. App-only."""

from __future__ import annotations

from pathlib import Path

from .beat_projector import BeatProjector, shot_list, shot_beat_id, shot_summary
from .project_service import STAGES, ProjectService
from .schemas import ProductionItem, ProductionSnapshot, StageName, StageSnapshot

STAGE_LABELS: dict[StageName, str] = {
    "narrative": "脚本",
    "voice": "声音",
    "editorial": "画面规划",
    "asset": "镜头 / 素材",
    "timeline": "时间线",
    "render": "成片",
}

_PREFERRED_KINDS: dict[StageName, tuple[str, ...]] = {
    "narrative": ("script_segment", "planned_beat", "shot"),
    "voice": ("audio_segment", "realized_beat", "voice_segment"),
    "editorial": ("visual_requirement", "claim"),
    "asset": ("asset", "shot_video", "visual_resolution"),
    "timeline": ("timeline_clip", "caption"),
    "render": ("rendered_media",),
}


class ProductionView:
    def __init__(self, projects: ProjectService, beats: BeatProjector) -> None:
        self.projects = projects
        self.beats = beats

    def snapshot(self, project_key: str | None) -> ProductionSnapshot:
        if not project_key:
            return ProductionSnapshot()
        try:
            directory = self.projects.project_dir(project_key)
        except FileNotFoundError:
            return ProductionSnapshot(project_key=project_key)
        state = self.projects.read_state(directory)
        stages: list[StageSnapshot] = []
        for stage in STAGES:
            status = self.projects.core_status(state, stage)
            required = status != "not_required"
            view = self.beats.stage_view(directory, stage)
            items = _pick_items(view.beats, stage)
            items.extend(_shot_items(directory, project_key, stage))
            items = _dedupe(items)
            stages.append(
                StageSnapshot(
                    stage=stage,
                    label=STAGE_LABELS[stage],
                    status=status,
                    required=required,
                    user_approved=view.user_approved,
                    item_count=len(items),
                    items=items,
                )
            )
        return ProductionSnapshot(
            project_key=project_key,
            project_id=state.video.project_id,
            current_stage=self.projects.current_stage(state, project_key),
            stages=stages,
        )


def required_stages(status_by_stage: dict[StageName, str]) -> list[StageName]:
    return [
        stage
        for stage in STAGES
        if status_by_stage.get(stage) != "not_required"
    ]


def next_stage_after(
    stage: StageName,
    status_by_stage: dict[StageName, str],
) -> StageName | None:
    pipeline = required_stages(status_by_stage)
    try:
        index = pipeline.index(stage)
    except ValueError:
        return None
    if index + 1 >= len(pipeline):
        return None
    return pipeline[index + 1]


def gate_question(stage: StageName, nxt: StageName | None) -> str:
    current = STAGE_LABELS[stage]
    if nxt is None:
        return f"{current}已经完成，成片在右侧。可以就这样，还是要改？"
    return (
        f"{current}已经完成。可以继续做{STAGE_LABELS[nxt]}吗，还是一样先看右侧内容、有问题再改？"
    )


def _pick_items(beats, stage: StageName) -> list[ProductionItem]:
    preferred = _PREFERRED_KINDS[stage]
    collected: list[ProductionItem] = []
    for beat in beats:
        ranked = sorted(
            beat.artifacts,
            key=lambda item: (
                preferred.index(item.kind) if item.kind in preferred else len(preferred),
                0 if item.media_url else 1,
            ),
        )
        for item in ranked:
            collected.append(
                ProductionItem(
                    ref=item.ref,
                    kind=item.kind,
                    beat_id=item.beat_id,
                    title=item.title,
                    summary=item.summary,
                    media_url=item.media_url,
                )
            )
    return collected


def _shot_items(directory: Path, project_key: str, stage: StageName) -> list[ProductionItem]:
    items: list[ProductionItem] = []
    if stage == "narrative":
        narrative = ProjectService.stage_artifact(directory, "narrative") or {}
        for shot in shot_list(narrative):
            shot_id = str(shot.get("shot_id") or "")
            if not shot_id:
                continue
            order = shot.get("order")
            title = f"镜头 {order}" if order else f"镜头 {shot_id}"
            purpose = str(shot.get("purpose") or "").strip()
            items.append(
                ProductionItem(
                    ref=f"shot:{shot_id}",
                    kind="shot",
                    beat_id=shot_beat_id(shot),
                    title=f"{title} · {purpose}" if purpose else title,
                    summary=shot_summary(shot) or str(shot.get("shot_kind") or ""),
                )
            )
    if stage == "asset":
        payload = ProjectService.stage_artifact(directory, "asset") or {}
        for asset in payload.get("assets") or []:
            if asset.get("modality") != "ai_video" and not asset.get("shot_id"):
                continue
            shot_id = str(asset.get("shot_id") or asset.get("asset_id") or "")
            path = asset.get("local_path")
            items.append(
                ProductionItem(
                    ref=f"shot_video:{shot_id}",
                    kind="shot_video",
                    beat_id=shot_id,
                    title=f"镜头 {shot_id}",
                    summary=str(asset.get("generation_prompt") or asset.get("modality") or ""),
                    media_url=(
                        f"/api/projects/{project_key}/media/{path}" if path else None
                    ),
                )
            )
    return items


def _dedupe(items: list[ProductionItem]) -> list[ProductionItem]:
    seen: set[str] = set()
    unique: list[ProductionItem] = []
    for item in items:
        if item.ref in seen:
            continue
        seen.add(item.ref)
        unique.append(item)
    return unique
