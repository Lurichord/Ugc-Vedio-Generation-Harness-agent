from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ..agents.editorial_agent.models import EditorialArtifact
from ..agents.narrative_agent.models import NarrativeArtifact
from ..agents.voice_agent.models import VoiceArtifact
from ..agents.asset_agent.models import AssetArtifact
from ..agents.timeline_agent.models import TimelineArtifact
from ..agents.render_agent.models import RenderArtifact
from .dependencies import NodeCommit
from .description import (
    DramaStructure,
    ExplainerStructure,
    TutorialStructure,
    VideoDescription,
    VoiceDesign,
)
from .description_builder import build_video_description
from .models import DependencyEdge

if TYPE_CHECKING:
    from .models import ProductionState

CONTENT_PREFIXES = (
    "cast:",
    "prop:",
    "place:",
    "beat:",
    "scene:",
    "step:",
    "utterance:",
    "audio:",
    "shot:",
)
CONTENT_ROOT_PREFIXES = (
    "cast:",
    "prop:",
    "place:",
    "beat:",
    "scene:",
    "step:",
)


def refs_with_prefix(
    nodes: Mapping[str, object] | Iterable[str],
    *prefixes: str,
) -> list[str]:
    keys = nodes.keys() if isinstance(nodes, Mapping) else nodes
    return sorted(ref for ref in keys if ref.startswith(prefixes))


def has_content_graph(nodes: Mapping[str, object] | Iterable[str]) -> bool:
    keys = nodes.keys() if isinstance(nodes, Mapping) else nodes
    return any(ref.startswith(CONTENT_PREFIXES) for ref in keys)


_PLOT_RELATIONS = frozenset(
    {"callback", "evidence_for", "example_of", "resolution"}
)
_PLOT_SOURCE_PREFIXES = (
    "beat:",
    "scene:",
    "step:",
    "utterance:",
)


def _element_edges_for_audio(
    utterance_id: str,
    speaker_ref: str | None,
) -> tuple[DependencyEdge, ...]:
    edges = [
        DependencyEdge(to=f"utterance:{utterance_id}", family="element"),
    ]
    if speaker_ref:
        edges.append(DependencyEdge(to=speaker_ref, family="element"))
    return tuple(edges)


def content_commits(document: VideoDescription) -> list[NodeCommit]:
    """Copy addressable production refs into the graph, with typed edges."""

    commits: list[NodeCommit] = []
    world = document.world
    for member in world.cast:
        commits.append(
            NodeCommit(f"cast:{member.character_id}", "cast", member)
        )
    for prop in world.props:
        commits.append(NodeCommit(f"prop:{prop.prop_id}", "prop", prop))
    for place in world.places:
        commits.append(NodeCommit(f"place:{place.place_id}", "place", place))

    structure = document.structure
    if isinstance(structure, ExplainerStructure):
        previous_ref: str | None = None
        for beat in structure.beats:
            ref = f"beat:{beat.planned_beat_id}"
            edges = []
            if (
                previous_ref is not None
                and beat.relation_to_previous in _PLOT_RELATIONS
            ):
                edges.append(DependencyEdge(to=previous_ref, family="plot"))
            commits.append(NodeCommit(ref, "beat", beat, edges=tuple(edges)))
            previous_ref = ref
    elif isinstance(structure, DramaStructure):
        for scene in structure.scenes:
            commits.append(NodeCommit(f"scene:{scene.scene_id}", "scene", scene))
    else:
        assert isinstance(structure, TutorialStructure)
        for step in structure.steps:
            commits.append(NodeCommit(f"step:{step.step_id}", "step", step))

    if document.voice is not None:
        for utterance in document.voice.utterances:
            edges = []
            if utterance.anchor_ref and utterance.anchor_ref.startswith(
                _PLOT_SOURCE_PREFIXES
            ):
                edges.append(DependencyEdge(to=utterance.anchor_ref, family="plot"))
            commits.append(
                NodeCommit(
                    f"utterance:{utterance.utterance_id}",
                    "utterance",
                    utterance,
                    edges=tuple(edges),
                )
            )
            if utterance.audio_segment is not None:
                commits.append(
                    NodeCommit(
                        f"audio:{utterance.utterance_id}",
                        "audio",
                        utterance.audio_segment,
                        edges=_element_edges_for_audio(
                            utterance.utterance_id,
                            utterance.speaker_ref,
                        ),
                    )
                )

    for shot in document.shots:
        edges = [
            DependencyEdge(to=ref, family="plot")
            for ref in shot.spec.source_refs
            if ref.startswith(_PLOT_SOURCE_PREFIXES)
        ]
        edges.extend(
            DependencyEdge(to=ref, family="identity") for ref in shot.spec.binds
        )
        previous_id = shot.continuity.previous_shot_id
        if shot.continuity.uses_previous_tail() and previous_id:
            edges.append(
                DependencyEdge(to=f"shot:{previous_id}", family="element")
            )
        commits.append(
            NodeCommit(
                f"shot:{shot.spec.shot_id}",
                "shot",
                shot,
                edges=tuple(edges),
            )
        )
    return commits


def narrative_commits(artifact: NarrativeArtifact) -> list[NodeCommit]:
    return content_commits(build_video_description(artifact))


def voice_commits(
    artifact: VoiceArtifact,
    voice: VoiceDesign | None = None,
) -> list[NodeCommit]:
    speakers = {
        item.utterance_id: item.speaker_ref
        for item in (voice.utterances if voice is not None else [])
        if item.speaker_ref
    }
    return [
        NodeCommit(
            f"audio:{audio.script_segment_id}",
            "audio",
            audio,
            edges=_element_edges_for_audio(
                audio.script_segment_id,
                speakers.get(audio.script_segment_id),
            ),
        )
        for audio in artifact.timed_audio.segments
    ]


def editorial_commits(artifact: EditorialArtifact) -> list[NodeCommit]:
    del artifact
    return []


def asset_commits(artifact: AssetArtifact) -> list[NodeCommit]:
    del artifact
    return []


def timeline_commits(
    artifact: TimelineArtifact,
    production: "ProductionState | None" = None,
) -> list[NodeCommit]:
    del artifact, production
    return []


def render_commits(artifact: RenderArtifact) -> list[NodeCommit]:
    del artifact
    return []
