from __future__ import annotations

from pathlib import Path
from typing import Any

from .beat_projector import media_unit_id
from .project_service import ProjectService
from .schemas import TimelineItemView, UnifiedTimelineView


class TimelineProjector:
    def __init__(self, projects: ProjectService) -> None:
        self.projects = projects

    def view(self, project_dir: Path) -> UnifiedTimelineView:
        state = self.projects.read_state(project_dir)
        voice = self.projects.stage_artifact(project_dir, "voice") or {}
        narrative = self.projects.stage_artifact(project_dir, "narrative") or {}
        assets = self.projects.stage_artifact(project_dir, "asset") or {}
        timeline = self.projects.stage_artifact(project_dir, "timeline") or {}
        project_key = project_dir.name
        tracks: dict[str, list[TimelineItemView]] = {
            key: [] for key in ("beats", "audio", "text", "visuals", "presentation", "captions", "overlays")
        }
        realized = voice.get("realized_beats", [])
        realized_to_planned = {
            item["beat_id"]: item["planned_beat_id"] for item in realized
        }
        realized_by_id = {item["beat_id"]: item for item in realized}
        realized_by_planned = {item["planned_beat_id"]: item for item in realized}

        def planned_beat_id(beat_id: str) -> str:
            return realized_to_planned.get(beat_id, beat_id)
        scripts: dict[str, dict[str, Any]] = {}
        for item in (narrative.get("script") or {}).get("segments", []):
            key = item.get("planned_beat_id") or item.get("step_id")
            if key:
                scripts[key] = item
        for beat in realized:
            bid = beat["planned_beat_id"]
            start, end = beat["start_ms"], beat["end_ms"]
            tracks["beats"].append(self._item(
                f"beat-{bid}", f"beat:{bid}", "beats", bid,
                start, end, beat.get("proposition") or bid, beat,
            ))
            script = scripts.get(bid, {})
            script_id = script.get("script_segment_id") or script.get(
                "explanation_segment_id"
            )
            if script and script_id:
                tracks["text"].append(self._item(
                    f"text-{bid}", f"utterance:{script_id}", "text", bid,
                    start, end, script.get("text", "口播"), script,
                ))
        for audio in voice.get("timed_audio", {}).get("segments", []):
            bid = audio["planned_beat_id"]
            tracks["audio"].append(self._item(
                audio["audio_segment_id"], f"audio:{audio['script_segment_id']}", "audio", bid,
                audio["start_ms"], audio["end_ms"], "配音", audio,
                f"/api/projects/{project_key}/media/{audio['file']}",
            ))
        clip_by_id: dict[str, dict[str, Any]] = {}
        clip_beat_ids: set[str] = set()
        nested_clips = timeline.get("timeline", {}).get("clips") or timeline.get("clips") or []
        for clip in nested_clips:
            clip_by_id[clip["clip_id"]] = clip
            raw_bid = media_unit_id(clip, narrative)
            if not raw_bid:
                continue
            bid = planned_beat_id(raw_bid)
            clip_beat_ids.add(bid)
            tracks["visuals"].append(self._item(
                clip["clip_id"], f"clip:{clip['clip_id']}", "visuals", bid,
                int(clip.get("timeline_start_ms", clip.get("start_ms") or 0)),
                int(clip.get("timeline_end_ms", clip.get("end_ms") or 0)),
                clip.get("playback_modality") or clip.get("modality") or "画面", clip,
                f"/api/projects/{project_key}/media/{clip.get('playback_path', '')}",
            ))
        # Asset-stage fallback: place completed media on the realized beat clock
        # until the timeline stage creates an authoritative clip for that beat.
        # Drama/tutorial shots have no voice clock, so they stack by duration_ms.
        cursor = 0
        for asset in assets.get("assets", []):
            raw_bid = media_unit_id(asset, narrative)
            if not raw_bid:
                continue
            bid = planned_beat_id(raw_bid)
            if bid in clip_beat_ids:
                continue
            beat = realized_by_id.get(raw_bid) or realized_by_planned.get(bid)
            if beat:
                start, end = int(beat["start_ms"]), int(beat["end_ms"])
            else:
                start = cursor
                end = start + int(asset.get("duration_ms") or 4000)
                cursor = end
            tracks["visuals"].append(self._item(
                asset["asset_id"], f"asset:{asset['asset_id']}", "visuals", bid,
                start, end,
                asset.get("modality", "画面素材"), asset,
                f"/api/projects/{project_key}/media/{asset.get('local_path', '')}",
            ))
        for transform in timeline.get("visual_transforms", []):
            clip = clip_by_id.get(transform["clip_id"])
            if not clip:
                continue
            raw_bid = media_unit_id(clip, narrative)
            if not raw_bid:
                continue
            bid = planned_beat_id(raw_bid)
            tracks["presentation"].append(self._item(
                f"transform-{bid}", f"timeline_transform:{raw_bid}", "presentation", bid,
                int(clip.get("timeline_start_ms", clip.get("start_ms") or 0)),
                int(clip.get("timeline_end_ms", clip.get("end_ms") or 0)),
                transform.get("motion_preset", "展示方式"), transform,
            ))
        for cue in timeline.get("captions", []):
            bid = planned_beat_id(media_unit_id(cue, narrative))
            if not bid:
                continue
            tracks["captions"].append(self._item(
                cue["cue_id"], f"caption:{cue['cue_id']}", "captions", bid,
                cue["start_ms"], cue["end_ms"], cue.get("text", "字幕"), cue,
            ))
        for overlay in timeline.get("overlays", []):
            bid = planned_beat_id(media_unit_id(overlay, narrative))
            if not bid:
                continue
            tracks["overlays"].append(self._item(
                overlay["overlay_id"], f"timeline_overlay:{overlay['overlay_id']}", "overlays", bid,
                overlay["start_ms"], overlay["end_ms"], overlay.get("text", "标注"), overlay,
            ))
        duration = int(
            timeline.get("timeline", {}).get("duration_ms")
            or timeline.get("duration_ms")
            or voice.get("timed_audio", {}).get("duration_ms")
            or max((item.get("end_ms", 0) for item in realized), default=0)
            or max((item.end_ms for items in tracks.values() for item in items), default=0)
        )
        for items in tracks.values():
            items.sort(key=lambda item: (item.start_ms, item.end_ms))
        return UnifiedTimelineView(duration_ms=duration, tracks=tracks)

    @staticmethod
    def _item(
        item_id: str, ref: str, track: str, beat_id: str, start: int, end: int,
        label: str, payload: dict[str, Any], media_url: str | None = None,
    ) -> TimelineItemView:
        return TimelineItemView(
            id=item_id, ref=ref, track=track, beat_id=beat_id,
            start_ms=start, end_ms=end, label=label,
            payload=payload, media_url=media_url,
        )
