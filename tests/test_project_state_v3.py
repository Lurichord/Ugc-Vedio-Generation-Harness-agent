from ugc_harness.harness.dependencies import DependencyGraph
from ugc_harness.harness.dependency_builders import content_commits, narrative_commits
from ugc_harness.harness.description import AudioSegmentBinding
from ugc_harness.harness.description_builder import build_video_description
from ugc_harness.harness.models import (
    DependencyEdge,
    ProjectState,
    RuntimeContext,
    VideoState,
)

from tests.test_description import _drama_artifact, _tutorial_artifact


def test_v2_constructor_migrates_and_writes_through() -> None:
    state = ProjectState(
        runtime_context=RuntimeContext(available_tools=["narrative.submit_candidate"]),
        video=VideoState(
            project_id="p_v3",
            state_version=2,
            narrative_status="passed",
            voice_status="ready",
        ),
    )
    assert state.schema_version == "project-state.v3"
    assert state.context.project_id == "p_v3"
    assert state.context.state_version == 2
    assert state.evaluation.stages.narrative == "passed"
    assert state.evaluation.stages.voice == "ready"

    state.context.state_version += 1
    state.evaluation.stages.asset = "ready"
    state.context.available_tools.append("asset.submit_candidate")
    assert state.context.state_version == 3
    assert state.evaluation.stages.asset == "ready"
    assert "asset.submit_candidate" in state.context.available_tools

    dumped = state.model_dump()
    assert "video" not in dumped
    assert "description" not in dumped
    assert "runtime_context" not in dumped
    assert dumped["context"]["project_id"] == "p_v3"
    loaded = ProjectState.model_validate(dumped)
    assert loaded.context.state_version == 3
    assert loaded.evaluation.stages.asset == "ready"


def test_description_assignment_fills_creative_and_production() -> None:
    artifact = _drama_artifact()
    document = build_video_description(artifact)
    state = ProjectState(
        runtime_context=RuntimeContext(),
        video=VideoState(project_id=artifact.brief.project_id, state_version=1),
        description=document,
    )
    assert state.creative.intent is not None
    assert state.creative.intent.format_id == "drama"
    assert state.production.world.cast
    assert state.production.shots
    state.production.shots[0].spec.purpose = "改过的镜头目的"
    assert state.production.shots[0].spec.purpose == "改过的镜头目的"


def test_content_graph_uses_identity_and_typed_edges() -> None:
    artifact = _drama_artifact()
    document = build_video_description(artifact)
    commits = content_commits(document)
    refs = {item.ref for item in commits}
    assert "cast:character_azhou" in refs
    assert "place:location_kitchen" in refs
    assert any(ref.startswith("scene:") for ref in refs)
    assert not any(ref.startswith("action:") for ref in refs)
    assert any(ref.startswith("shot:") for ref in refs)
    shot = next(item for item in commits if item.ref.startswith("shot:"))
    families = {edge.family for edge in shot.edges}
    assert families == {"plot", "identity"}
    assert any(edge.to == "cast:character_azhou" for edge in shot.edges)
    assert any(edge.family == "plot" and edge.to.startswith("scene:") for edge in shot.edges)

    graph = DependencyGraph(ProjectState().dependency_graph)
    graph.commit_batch(
        task_id="content_graph",
        produced_by="narrative_agent",
        commits=narrative_commits(artifact),
    )
    node = graph.state.nodes[shot.ref]
    assert any(edge.family == "identity" for edge in node.edges)


def test_tutorial_props_become_bind_targets() -> None:
    artifact = _tutorial_artifact()
    document = build_video_description(artifact)
    commits = content_commits(document)
    refs = {item.ref for item in commits}
    assert {"prop:mat_leaf", "prop:mat_rice", "prop:mat_string"} <= refs
    assert any(ref.startswith("step:") for ref in refs)
    assert not any(ref.startswith("action:") for ref in refs)
    assert any(ref.startswith("utterance:") for ref in refs)
    shot = next(item for item in commits if item.ref.startswith("shot:"))
    assert any(edge.family == "identity" and edge.to.startswith("prop:") for edge in shot.edges)
    utterance = next(item for item in commits if item.ref.startswith("utterance:"))
    assert all(edge.family == "plot" for edge in utterance.edges)
    assert not any(edge.to.startswith("cast:") for edge in utterance.edges)


def test_legacy_voice_family_loads_as_element() -> None:
    edge = DependencyEdge.model_validate(
        {"to": "utterance:ss01", "family": "voice"}
    )
    assert edge.family == "element"


def test_audio_element_edges_point_to_utterance_and_speaker() -> None:
    artifact = _tutorial_artifact()
    document = build_video_description(artifact)
    utterance = document.voice.utterances[0]
    utterance.speaker_ref = "cast:host"
    utterance.audio_segment = AudioSegmentBinding(
        file="audio/ss.wav",
        start_ms=0,
        end_ms=1000,
        duration_ms=1000,
    )
    commits = content_commits(document)
    audio = next(item for item in commits if item.ref.startswith("audio:"))
    assert {edge.family for edge in audio.edges} == {"element"}
    assert any(
        edge.to == f"utterance:{utterance.utterance_id}" for edge in audio.edges
    )
    assert any(edge.to == "cast:host" for edge in audio.edges)
    spoken = next(
        item
        for item in commits
        if item.ref == f"utterance:{utterance.utterance_id}"
    )
    assert not any(edge.to.startswith("cast:") for edge in spoken.edges)
