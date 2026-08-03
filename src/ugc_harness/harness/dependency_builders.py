from __future__ import annotations

from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..agents.asset_agent.models import AssetArtifact
from ..agents.timeline_agent.models import TimelineArtifact
from ..agents.render_agent.models import RenderArtifact
from .dependencies import NodeCommit


def narrative_commits(artifact: NarrativeArtifact) -> list[NodeCommit]:
    brief_ref = "input:brief"
    world_ref = "world:video"
    profile_ref = "profile:video"
    commits = [
        NodeCommit(brief_ref, "brief", artifact.brief),
        NodeCommit(
            world_ref,
            "world_state",
            artifact.planning.world_state,
            (brief_ref,),
        ),
        NodeCommit(
            profile_ref,
            "video_profile",
            artifact.planning.video_profile,
            (brief_ref, world_ref),
        ),
    ]
    for section in artifact.planning.sections:
        commits.append(
            NodeCommit(
                f"section:{section.section_id}",
                "section",
                section,
                (brief_ref, world_ref),
            )
        )
    for beat in artifact.planning.beats:
        commits.append(
            NodeCommit(
                f"planned_beat:{beat.planned_beat_id}",
                "planned_beat",
                beat,
                (
                    f"section:{beat.section_id}",
                    world_ref,
                    profile_ref,
                ),
            )
        )
    for segment in artifact.script.segments:
        commits.append(
            NodeCommit(
                f"script_segment:{segment.script_segment_id}",
                "script_segment",
                segment,
                (
                    f"planned_beat:{segment.planned_beat_id}",
                    world_ref,
                ),
            )
        )
    aggregate_dependencies = tuple(
        [world_ref, profile_ref]
        + [
            f"planned_beat:{beat.planned_beat_id}"
            for beat in artifact.planning.beats
        ]
        + [
            f"script_segment:{segment.script_segment_id}"
            for segment in artifact.script.segments
        ]
    )
    commits.append(
        NodeCommit(
            "artifact:narrative",
            "narrative_artifact",
            {
                "planning": artifact.planning.model_dump(mode="json"),
                "script": artifact.script.model_dump(mode="json"),
            },
            aggregate_dependencies,
        )
    )
    return commits


def voice_commits(artifact: VoiceArtifact) -> list[NodeCommit]:
    commits = [
        NodeCommit(
            "voice_profile:main",
            "voice_profile",
            {
                "speaker": artifact.voice_plan.speaker.model_dump(mode="json"),
                "global_settings": artifact.voice_plan.global_settings.model_dump(
                    mode="json"
                ),
            },
            ("artifact:narrative",),
        )
    ]
    voice_segments = {
        item.voice_segment_id: item for item in artifact.voice_plan.segments
    }
    audio_segments = {
        item.audio_segment_id: item for item in artifact.timed_audio.segments
    }
    for segment in artifact.voice_plan.segments:
        commits.append(
            NodeCommit(
                f"voice_segment:{segment.voice_segment_id}",
                "voice_segment",
                segment,
                (
                    f"script_segment:{segment.script_segment_id}",
                    "voice_profile:main",
                ),
            )
        )
    for audio in artifact.timed_audio.segments:
        commits.append(
            NodeCommit(
                f"audio_segment:{audio.audio_segment_id}",
                "audio_segment",
                audio,
                (
                    f"voice_segment:{audio.voice_segment_id}",
                    f"script_segment:{audio.script_segment_id}",
                ),
            )
        )
    for script_id in sorted(
        {word.script_segment_id for word in artifact.word_alignment.words}
    ):
        words = [
            word.model_dump(mode="json")
            for word in artifact.word_alignment.words
            if word.script_segment_id == script_id
        ]
        related_audio = [
            audio.audio_segment_id
            for audio in artifact.timed_audio.segments
            if audio.script_segment_id == script_id
        ]
        commits.append(
            NodeCommit(
                f"alignment_segment:{script_id}",
                "word_alignment_segment",
                words,
                tuple(f"audio_segment:{ref}" for ref in related_audio),
            )
        )
    for beat in artifact.realized_beats:
        dependencies = [f"planned_beat:{beat.planned_beat_id}"]
        dependencies.extend(
            f"script_segment:{ref}" for ref in beat.script_segment_ids
        )
        dependencies.extend(
            f"audio_segment:{ref}" for ref in beat.audio_segment_ids
        )
        dependencies.extend(
            f"alignment_segment:{ref}" for ref in beat.script_segment_ids
        )
        commits.append(
            NodeCommit(
                f"realized_beat:{beat.beat_id}",
                "realized_beat",
                beat,
                tuple(dependencies),
            )
        )
    narration_dependencies = tuple(
        f"audio_segment:{item.audio_segment_id}"
        for item in artifact.timed_audio.segments
    )
    commits.append(
        NodeCommit(
            "audio:narration",
            "narration_audio",
            {
                "audio_file": artifact.timed_audio.audio_file,
                "duration_ms": artifact.timed_audio.duration_ms,
                "encoding": artifact.timed_audio.encoding,
            },
            narration_dependencies,
        )
    )
    commits.append(
        NodeCommit(
            "artifact:voice",
            "voice_artifact",
            {
                "voice_plan": artifact.voice_plan.model_dump(mode="json"),
                "timed_audio": artifact.timed_audio.model_dump(mode="json"),
                "word_alignment": artifact.word_alignment.model_dump(mode="json"),
                "realized_beats": [
                    beat.model_dump(mode="json")
                    for beat in artifact.realized_beats
                ],
            },
            tuple(
                ["audio:narration"]
                + [
                    f"realized_beat:{beat.beat_id}"
                    for beat in artifact.realized_beats
                ]
            ),
        )
    )
    # Assert mappings while building so malformed cross-references fail early.
    if any(
        audio.voice_segment_id not in voice_segments
        for audio in audio_segments.values()
    ):
        raise ValueError("audio segment references an unknown voice segment")
    return commits


def editorial_commits(artifact: EditorialArtifact) -> list[NodeCommit]:
    commits: list[NodeCommit] = []
    for claim in artifact.editorial_plan.claims:
        dependencies = [f"realized_beat:{claim.beat_id}"]
        dependencies.extend(
            f"script_segment:{ref}" for ref in claim.script_segment_ids
        )
        commits.append(
            NodeCommit(
                f"claim:{claim.claim_id}",
                "claim",
                claim,
                tuple(dependencies),
            )
        )
    for visual in artifact.editorial_plan.visual_requirements:
        covered_claims = sorted(
            {
                claim_id
                for direction in visual.directions
                for claim_id in direction.covers_claim_ids
            }
        )
        dependencies = [
            f"realized_beat:{visual.beat_id}",
            "profile:video",
        ]
        dependencies.extend(f"claim:{ref}" for ref in covered_claims)
        commits.append(
            NodeCommit(
                f"visual_requirement:{visual.visual_request_id}",
                "visual_requirement",
                visual,
                tuple(dependencies),
            )
        )
    commits.append(
        NodeCommit(
            "artifact:editorial",
            "editorial_artifact",
            artifact.editorial_plan,
            tuple(
                [
                    f"claim:{claim.claim_id}"
                    for claim in artifact.editorial_plan.claims
                ]
                + [
                    f"visual_requirement:{visual.visual_request_id}"
                    for visual in artifact.editorial_plan.visual_requirements
                ]
            ),
        )
    )
    return commits


def asset_commits(artifact: AssetArtifact) -> list[NodeCommit]:
    commits: list[NodeCommit] = []
    assets = {item.asset_id: item for item in artifact.assets}
    inspections = {item.asset_id: item for item in artifact.inspections}
    prepared_images = {item.asset_id: item for item in artifact.prepared_images}
    character_references: dict[str, str] = {}
    for asset in artifact.assets:
        if asset.character_id and asset.identity_reference_path:
            existing = character_references.setdefault(
                asset.character_id, asset.identity_reference_path
            )
            if existing != asset.identity_reference_path:
                raise ValueError(
                    f"character {asset.character_id} uses multiple identity references"
                )
    for character_id, reference_path in character_references.items():
        commits.append(
            NodeCommit(
                f"character_reference:{character_id}",
                "character_reference",
                {"character_id": character_id, "local_path": reference_path},
                ("world:video",),
            )
        )
    for asset in artifact.assets:
        dependencies = [
            f"visual_requirement:{asset.visual_request_id}",
            f"realized_beat:{asset.beat_id}",
        ]
        if asset.character_id and asset.identity_reference_path:
            dependencies.append(f"character_reference:{asset.character_id}")
        if asset.previous_asset_id:
            if asset.previous_asset_id not in assets:
                raise ValueError(
                    f"asset references unknown previous asset: {asset.previous_asset_id}"
                )
            dependencies.append(f"asset:{asset.previous_asset_id}")
        commits.append(
            NodeCommit(
                f"asset:{asset.asset_id}",
                "asset",
                asset,
                tuple(dependencies),
            )
        )
    for inspection in artifact.inspections:
        if inspection.asset_id not in assets:
            raise ValueError(
                f"inspection references unknown asset: {inspection.asset_id}"
            )
        commits.append(
            NodeCommit(
                f"asset_inspection:{inspection.asset_id}",
                "asset_inspection",
                inspection,
                (f"asset:{inspection.asset_id}",),
            )
        )
    for prepared in artifact.prepared_images:
        if prepared.asset_id not in assets or prepared.asset_id not in inspections:
            raise ValueError(
                f"prepared image is missing its asset or inspection: {prepared.asset_id}"
            )
        commits.append(
            NodeCommit(
                f"prepared_image:{prepared.asset_id}",
                "prepared_image",
                prepared,
                (
                    f"asset:{prepared.asset_id}",
                    f"asset_inspection:{prepared.asset_id}",
                ),
            )
        )
    for resolution in artifact.resolutions:
        dependencies = [
            f"visual_requirement:{resolution.visual_request_id}",
            f"realized_beat:{resolution.beat_id}",
        ]
        if resolution.asset_id:
            if resolution.asset_id not in assets:
                raise ValueError(
                    f"resolution references unknown asset: {resolution.asset_id}"
                )
            dependencies.append(
                f"prepared_image:{resolution.asset_id}"
                if resolution.asset_id in prepared_images
                else f"asset:{resolution.asset_id}"
            )
        commits.append(
            NodeCommit(
                f"visual_resolution:{resolution.visual_request_id}",
                "visual_resolution",
                resolution,
                tuple(dependencies),
            )
        )
    commits.append(
        NodeCommit(
            "artifact:assets",
            "asset_artifact",
            {
                "assets": [item.model_dump(mode="json") for item in artifact.assets],
                "resolutions": [
                    item.model_dump(mode="json") for item in artifact.resolutions
                ],
                "inspections": [
                    item.model_dump(mode="json") for item in artifact.inspections
                ],
                "prepared_images": [
                    item.model_dump(mode="json") for item in artifact.prepared_images
                ],
            },
            tuple(
                [f"asset:{item.asset_id}" for item in artifact.assets]
                + [
                    f"asset_inspection:{item.asset_id}"
                    for item in artifact.inspections
                ]
                + [
                    f"prepared_image:{item.asset_id}"
                    for item in artifact.prepared_images
                ]
                + [
                    f"visual_resolution:{item.visual_request_id}"
                    for item in artifact.resolutions
                ]
            ),
        )
    )
    return commits


def timeline_commits(artifact: TimelineArtifact) -> list[NodeCommit]:
    commits: list[NodeCommit] = []
    derivatives = {item.derivative_id: item for item in artifact.derivatives}
    for derivative in artifact.derivatives:
        commits.append(
            NodeCommit(
                f"timeline_derivative:{derivative.derivative_id}",
                "timeline_derivative",
                derivative,
                (
                    f"asset:{derivative.source_asset_id}",
                    f"realized_beat:{derivative.beat_id}",
                ),
            )
        )
    for clip in artifact.timeline.clips:
        dependencies = [
            f"realized_beat:{clip.beat_id}",
            f"visual_resolution:{clip.visual_request_id}",
            "audio:narration",
        ]
        if clip.derivative_id:
            if clip.derivative_id not in derivatives:
                raise ValueError(
                    f"timeline clip references unknown derivative: {clip.derivative_id}"
                )
            dependencies.append(f"timeline_derivative:{clip.derivative_id}")
        commits.append(
            NodeCommit(
                f"timeline_clip:{clip.beat_id}",
                "timeline_clip",
                clip,
                tuple(dependencies),
            )
        )
    for caption in artifact.captions:
        commits.append(
            NodeCommit(
                f"caption:{caption.cue_id}",
                "caption_cue",
                caption,
                (f"realized_beat:{caption.beat_id}",),
            )
        )
    clip_by_id = {item.clip_id: item for item in artifact.timeline.clips}
    for transform in artifact.visual_transforms:
        clip = clip_by_id.get(transform.clip_id)
        if clip is None:
            raise ValueError(
                f"visual transform references unknown clip: {transform.clip_id}"
            )
        commits.append(
            NodeCommit(
                f"timeline_transform:{clip.beat_id}",
                "timeline_transform",
                transform,
                (f"timeline_clip:{clip.beat_id}",),
            )
        )
    for overlay in artifact.overlays:
        commits.append(
            NodeCommit(
                f"timeline_overlay:{overlay.overlay_id}",
                "timeline_overlay",
                overlay,
                (f"timeline_clip:{overlay.beat_id}",),
            )
        )
    dependencies = tuple(
        [f"timeline_clip:{item.beat_id}" for item in artifact.timeline.clips]
        + [f"caption:{item.cue_id}" for item in artifact.captions]
        + [
            f"timeline_transform:{clip_by_id[item.clip_id].beat_id}"
            for item in artifact.visual_transforms
        ]
        + [f"timeline_overlay:{item.overlay_id}" for item in artifact.overlays]
    )
    commits.append(
        NodeCommit(
            "artifact:timeline",
            "timeline_artifact",
            artifact,
            dependencies,
        )
    )
    return commits


def render_commits(artifact: RenderArtifact) -> list[NodeCommit]:
    commits = [
        NodeCommit(
            "render:composition",
            "render_composition",
            artifact.composition,
            ("artifact:timeline", "audio:narration"),
        )
    ]
    for output in artifact.outputs:
        commits.append(
            NodeCommit(
                f"rendered_media:{output.kind}",
                "rendered_media",
                output,
                ("render:composition",),
            )
        )
    commits.append(
        NodeCommit(
            "artifact:render",
            "render_artifact",
            artifact,
            tuple(f"rendered_media:{item.kind}" for item in artifact.outputs),
        )
    )
    return commits
