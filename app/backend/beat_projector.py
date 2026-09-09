from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ugc_harness.harness.models import ProjectState

from .project_service import ProjectService, production_pipeline
from .repository import AppDataRepository
from .review_service import ReviewService
from .schemas import ArtifactView, BeatView, StageName, StageView


STAGE_PREFIXES: dict[StageName, tuple[str, ...]] = {
    "narrative": ("beat:", "utterance:", "shot:", "scene:", "step:"),
    "voice": ("audio:",),
    "editorial": ("beat:", "shot:"),
    "references": ("cast:", "prop:", "place:"),
    "asset": ("shot:",),
    "timeline": ("shot:", "audio:"),
    "render": ("shot:",),
}


class BeatProjector:
    def __init__(self, projects: ProjectService, reviews: ReviewService) -> None:
        self.projects = projects
        self.reviews = reviews

    def stage_view(self, project_dir: Path, stage: StageName) -> StageView:
        state = self.projects.read_state(project_dir)
        storage_key = project_dir.name
        artifacts = self._all_artifacts(project_dir, state)
        narrative = self.projects.stage_artifact(project_dir, "narrative") or {}
        beats_payload = planning_units(narrative)
        by_beat: dict[str, list[ArtifactView]] = defaultdict(list)
        for item in artifacts:
            if item.stage == stage:
                by_beat[item.beat_id].append(item)
        realized = {
            item["planned_beat_id"]: item
            for item in (self.projects.stage_artifact(project_dir, "voice") or {}).get(
                "realized_beats", []
            )
        }
        beats = []
        for index, payload in enumerate(beats_payload, 1):
            beat_id = payload["planned_beat_id"]
            clock = realized.get(beat_id, {})
            beats.append(
                BeatView(
                    beat_id=beat_id,
                    order=int(payload.get("order", index)),
                    section_id=payload.get("section_id"),
                    proposition=payload.get("semantic_goal"),
                    start_ms=clock.get("start_ms"),
                    end_ms=clock.get("end_ms"),
                    artifacts=by_beat.get(beat_id, []),
                )
            )
        evaluation = self.projects.evaluation(project_dir, stage)
        core_status = self.projects.core_status(state, stage)
        critic_passed = bool(evaluation.get("passed")) if evaluation else core_status == "passed"
        approval = self.reviews.valid_approval(storage_key, stage, state)
        open_feedback = self.reviews.open_feedback(storage_key, stage)
        pipeline = list(production_pipeline(state, project_dir))
        previous_ok = True
        if stage in pipeline:
            stage_index = pipeline.index(stage)
            if stage_index:
                previous = pipeline[stage_index - 1]
                previous_ok = (
                    self.reviews.valid_approval(storage_key, previous, state) is not None
                )
        can_run = previous_ok and core_status in {
            "pending", "ready", "failed", "needs_revision", "stale", "blocked"
        }
        can_approve = critic_passed and not open_feedback and bool(beats)
        return StageView(
            stage=stage,
            core_status=core_status,
            critic_passed=critic_passed,
            user_approved=approval is not None,
            can_run=can_run,
            can_approve=can_approve and previous_ok,
            can_advance=critic_passed and approval is not None and not open_feedback,
            state_version=state.context.state_version,
            graph_version=state.dependency_graph.graph_version,
            issues=list((evaluation or {}).get("issues", [])),
            beats=beats,
        )

    def _all_artifacts(self, project_dir: Path, state: ProjectState) -> list[ArtifactView]:
        result: list[ArtifactView] = []
        feedback = {
            item.target_ref: item
            for item in self.reviews.open_feedback(project_dir.name)
        }
        approvals = {
            stage: self.reviews.valid_approval(project_dir.name, stage, state)
            for stage in STAGE_PREFIXES
        }

        def add(
            *, stage: StageName, ref: str, kind: str, beat_id: str,
            title: str, summary: str | None, payload: dict[str, Any],
            media: str | None = None, start: int | None = None, end: int | None = None,
        ) -> None:
            node = state.dependency_graph.nodes.get(ref)
            approved = approvals[stage]
            review_status = "rejected" if ref in feedback else (
                "approved" if approved and ref in approved.approved_refs else "pending"
            )
            result.append(ArtifactView(
                ref=ref, kind=kind, stage=stage, beat_id=beat_id,
                title=title, summary=summary, payload=payload, media_url=media,
                start_ms=start, end_ms=end,
                version=node.version if node else None,
                node_status=node.status if node else None,
                review_status=review_status,
            ))

        narrative = self.projects.stage_artifact(project_dir, "narrative") or {}
        for beat in planning_units(narrative):
            bid = beat["planned_beat_id"]
            add(
                stage="narrative",
                ref=f"beat:{bid}",
                kind="beat",
                beat_id=bid,
                title=str(beat.get("title") or "规划"),
                summary=beat.get("semantic_goal"),
                payload=beat,
            )
        for segment in (narrative.get("script") or {}).get("segments", []):
            bid = (
                segment.get("planned_beat_id")
                or segment.get("step_id")
                or "script"
            )
            sid = (
                segment.get("script_segment_id")
                or segment.get("explanation_segment_id")
                or bid
            )
            add(
                stage="narrative",
                ref=f"utterance:{sid}",
                kind="utterance",
                beat_id=str(bid),
                title="口播文本",
                summary=segment.get("text"),
                payload=segment,
            )
        for shot in shot_list(narrative):
            shot_id = str(shot.get("shot_id") or "")
            if not shot_id:
                continue
            add(
                stage="narrative",
                ref=f"shot:{shot_id}",
                kind="shot",
                beat_id=shot_beat_id(shot),
                title=_shot_title(shot),
                summary=shot_summary(shot),
                payload=shot,
            )

        voice = self.projects.stage_artifact(project_dir, "voice") or {}
        realized_to_planned = {
            item["beat_id"]: item["planned_beat_id"]
            for item in voice.get("realized_beats", [])
        }

        def planned_beat_id(beat_id: str) -> str:
            return realized_to_planned.get(beat_id, beat_id)

        for segment in voice.get("timed_audio", {}).get("segments", []):
            bid = segment["planned_beat_id"]
            sid = segment["script_segment_id"]
            add(stage="voice", ref=f"audio:{sid}", kind="audio", beat_id=bid,
                title="配音片段", summary=f"{segment.get('duration_ms', 0) / 1000:.2f} 秒", payload=segment,
                media=f"/api/projects/{project_dir.name}/media/{segment.get('file', '')}",
                start=segment.get("start_ms"), end=segment.get("end_ms"))

        editorial = self.projects.stage_artifact(project_dir, "editorial") or {}
        plan = editorial.get("editorial_plan", {})
        for claim in plan.get("claims", []):
            bid = planned_beat_id(claim["beat_id"])
            add(stage="editorial", ref=f"beat:{bid}", kind="beat", beat_id=bid,
                title="内容主张", summary=claim.get("statement"), payload=claim)
        for visual in plan.get("visual_requirements", []):
            bid = planned_beat_id(visual["beat_id"])
            add(stage="editorial", ref=f"shot:shot_{bid}", kind="shot", beat_id=bid,
                title="画面需求", summary=visual.get("purpose"), payload=visual)

        assets = self.projects.stage_artifact(project_dir, "asset") or {}
        for asset in assets.get("assets", []):
            bid = planned_beat_id(media_unit_id(asset, narrative))
            if not bid:
                continue
            shot_id = str(asset.get("shot_id") or "")
            ref = f"shot:{shot_id}" if shot_id else f"shot:shot_{bid}"
            add(stage="asset", ref=ref, kind="shot", beat_id=bid,
                title="画面素材", summary=asset.get("modality"), payload=asset,
                media=f"/api/projects/{project_dir.name}/media/{asset.get('local_path', '')}")
        for resolution in assets.get("resolutions", []):
            bid = planned_beat_id(media_unit_id(resolution, narrative))
            if not bid:
                continue
            add(stage="asset", ref=f"shot:shot_{bid}", kind="shot", beat_id=bid,
                title="素材决策", summary=resolution.get("status"), payload=resolution)

        timeline = self.projects.stage_artifact(project_dir, "timeline") or {}
        clips = timeline.get("timeline", {}).get("clips") or timeline.get("clips") or []
        clip_by_id = {item["clip_id"]: item for item in clips if item.get("clip_id")}
        for clip in clips:
            bid = planned_beat_id(media_unit_id(clip, narrative))
            if not bid:
                continue
            add(stage="timeline", ref=f"clip:{clip.get('clip_id')}", kind="clip", beat_id=bid,
                title="画面剪辑", summary=clip.get("playback_policy") or clip.get("modality"), payload=clip,
                media=f"/api/projects/{project_dir.name}/media/{clip.get('playback_path', '')}",
                start=clip.get("timeline_start_ms", clip.get("start_ms")),
                end=clip.get("timeline_end_ms", clip.get("end_ms")))
        for cue in timeline.get("captions", []):
            bid = planned_beat_id(media_unit_id(cue, narrative))
            if not bid:
                continue
            cid = cue["cue_id"]
            add(stage="timeline", ref=f"caption:{cid}", kind="caption", beat_id=bid,
                title="字幕", summary=cue.get("text"), payload=cue,
                start=cue.get("start_ms"), end=cue.get("end_ms"))
        for transform in timeline.get("visual_transforms", []):
            clip = clip_by_id.get(transform["clip_id"], {})
            bid = planned_beat_id(media_unit_id(clip, narrative) or "unknown")
            add(stage="timeline", ref=f"timeline_transform:{bid}", kind="timeline_transform", beat_id=bid,
                title="展示方式", summary=transform.get("motion_preset"), payload=transform,
                start=clip.get("timeline_start_ms", clip.get("start_ms")),
                end=clip.get("timeline_end_ms", clip.get("end_ms")))
        for overlay in timeline.get("overlays", []):
            bid = planned_beat_id(media_unit_id(overlay, narrative))
            if not bid:
                continue
            oid = overlay["overlay_id"]
            add(stage="timeline", ref=f"timeline_overlay:{oid}", kind="timeline_overlay", beat_id=bid,
                title="画面标注", summary=overlay.get("text"), payload=overlay,
                start=overlay.get("start_ms"), end=overlay.get("end_ms"))

        render = self.projects.stage_artifact(project_dir, "render") or {}
        render_units = list(voice.get("realized_beats") or [])
        if not render_units:
            render_units = [
                {"planned_beat_id": item["planned_beat_id"]}
                for item in planning_units(narrative)[:1]
            ]
        for output in render.get("outputs", []):
            kind = str(output.get("kind") or "final")
            for beat in render_units:
                bid = beat.get("planned_beat_id")
                if not bid:
                    continue
                add(
                    stage="render",
                    ref=f"rendered_media:{kind}",
                    kind="rendered_media",
                    beat_id=str(bid),
                    title="成片" if kind == "final" else "预览",
                    summary=output.get("local_path"),
                    payload=output,
                    media=f"/api/projects/{project_dir.name}/media/{output.get('local_path', '')}",
                    start=beat.get("start_ms"),
                    end=beat.get("end_ms"),
                )
        return result


def stage_refs(view: StageView) -> list[str]:
    return sorted({item.ref for beat in view.beats for item in beat.artifacts})


def media_unit_id(item: dict[str, Any], narrative: dict[str, Any] | None = None) -> str:
    """Grouping id for explainer (beat_id) and drama/tutorial (shot_id) media."""

    shot_id = str(item.get("shot_id") or "").strip()
    if shot_id and narrative:
        for shot in shot_list(narrative):
            if str(shot.get("shot_id") or "") == shot_id:
                return shot_beat_id(shot)
    return str(item.get("beat_id") or shot_id or "").strip()


def shot_list(narrative: dict[str, Any]) -> list[dict[str, Any]]:
    raw = narrative.get("shots")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        inner = raw.get("shots")
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
    return []


def planning_units(narrative: dict[str, Any]) -> list[dict[str, Any]]:
    planning = narrative.get("planning") or {}
    beats = planning.get("beats") or []
    if beats:
        units: list[dict[str, Any]] = []
        for index, beat in enumerate(beats, start=1):
            beat_id = str(beat.get("planned_beat_id") or "")
            if not beat_id:
                continue
            units.append(
                {
                    **beat,
                    "planned_beat_id": beat_id,
                    "order": int(beat.get("order") or index),
                    "title": "Beat 规划",
                    "semantic_goal": beat.get("semantic_goal"),
                }
            )
        return units
    scenes = planning.get("scenes") or []
    if scenes:
        return [
            {
                "planned_beat_id": str(scene.get("scene_id") or f"scene_{index}"),
                "order": int(scene.get("order") or index),
                "section_id": scene.get("location_id"),
                "title": "场景",
                "semantic_goal": scene.get("purpose") or scene.get("emotional_turn"),
            }
            for index, scene in enumerate(scenes, start=1)
        ]
    steps = planning.get("steps") or []
    if steps:
        return [
            {
                "planned_beat_id": str(step.get("step_id") or f"step_{index}"),
                "order": int(step.get("order") or index),
                "title": "步骤",
                "semantic_goal": step.get("instruction") or step.get("expected_result"),
            }
            for index, step in enumerate(steps, start=1)
        ]
    return [
        {
            "planned_beat_id": shot_beat_id(shot),
            "order": int(shot.get("order") or index),
            "title": "镜头",
            "semantic_goal": shot.get("purpose"),
        }
        for index, shot in enumerate(shot_list(narrative), start=1)
        if shot.get("shot_id")
    ]


def shot_beat_id(shot: dict[str, Any]) -> str:
    for ref in shot.get("source_refs") or []:
        kind, _, ident = str(ref).partition(":")
        if ident and kind in {"beat", "planned_beat", "scene", "step", "action"}:
            return ident
    visual = shot.get("visual") or {}
    for key in ("scene_id", "step_id"):
        if visual.get(key):
            return str(visual[key])
    return str(shot.get("shot_id") or "shot")


def shot_summary(shot: dict[str, Any]) -> str:
    visual = shot.get("visual") or {}
    payload = shot.get("payload") or {}
    parts = [
        shot.get("purpose"),
        visual.get("action_description"),
        visual.get("generation_prompt"),
        visual.get("critical_detail"),
        visual.get("visual_source"),
        payload.get("narration_text"),
        "；".join(payload.get("dialogue_lines") or []) or None,
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return "\n".join(ordered)


def _shot_title(shot: dict[str, Any]) -> str:
    order = shot.get("order")
    prefix = f"镜头 {order}" if order else "镜头"
    purpose = str(shot.get("purpose") or shot.get("shot_id") or "").strip()
    return f"{prefix} · {purpose}" if purpose else prefix
