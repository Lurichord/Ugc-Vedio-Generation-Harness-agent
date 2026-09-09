from __future__ import annotations

from fastapi.testclient import TestClient

from app.backend.beat_projector import planning_units, shot_beat_id, shot_list
from app.backend.intake_service import _wants_continue
from app.backend.main import app
from app.backend.production_view import STAGE_LABELS, gate_question, next_stage_after


def test_health_and_frontend_are_available() -> None:
    client = TestClient(app)
    assert client.get("/api/health").json() == {"status": "ok"}
    response = client.get("/")
    assert response.status_code == 200
    assert "UGC Beat Studio" in response.text
    assert "chatThread" in response.text
    assert "已完成内容" in response.text
    assert "composerInput" in response.text
    assert "chat-dock" in response.text
    assert "依赖图" in response.text
    assert 'data-drawer="graph"' in response.text


def test_intake_session_api_shape_and_continue_without_project() -> None:
    client = TestClient(app)
    created = client.post("/api/intake/sessions")
    assert created.status_code == 201
    payload = created.json()
    assert payload["session_id"]
    assert payload["messages"]
    assert payload["production"]["stages"] == []
    assert payload["gate"] is None
    session_id = payload["session_id"]
    fetched = client.get(f"/api/intake/sessions/{session_id}")
    assert fetched.status_code == 200
    assert fetched.json()["session_id"] == session_id
    continued = client.post(f"/api/intake/sessions/{session_id}/continue")
    assert continued.status_code == 409


def test_continue_phrases_and_gate_copy() -> None:
    assert _wants_continue("可以")
    assert _wants_continue("一样")
    assert _wants_continue("继续")
    assert not _wants_continue("第三段太长，改短")
    assert not _wants_continue("先改配音")
    question = gate_question("narrative", "voice")
    assert STAGE_LABELS["narrative"] in question
    assert STAGE_LABELS["voice"] in question
    assert next_stage_after("narrative", {
        "narrative": "passed",
        "voice": "pending",
        "editorial": "not_required",
        "references": "not_required",
        "asset": "pending",
        "timeline": "pending",
        "render": "pending",
    }) == "voice"
    assert next_stage_after("narrative", {
        "narrative": "passed",
        "voice": "not_required",
        "editorial": "not_required",
        "references": "ready",
        "asset": "pending",
        "timeline": "pending",
        "render": "pending",
    }) == "references"


def test_old_drama_status_still_starts_at_references() -> None:
    from pathlib import Path

    from ugc_harness.harness.models import (
        EvaluationState,
        ProjectContext,
        ProjectState,
        StageStatusBoard,
    )

    from app.backend.project_service import ProjectService, SHOT_PIPELINE
    from app.backend.repository import AppDataRepository

    root = Path(".tmp") / "pipeline_drama"
    root.mkdir(parents=True, exist_ok=True)
    state = ProjectState(
        context=ProjectContext(
            constraints={"narrative_format": "drama", "production_mode": "drama"}
        ),
        evaluation=EvaluationState(
            stages=StageStatusBoard(
                narrative="passed",
                voice="not_required",
                editorial="not_required",
                references="not_required",
                asset="ready",
                timeline="pending",
                render="pending",
            )
        ),
    )
    projects = ProjectService(root, AppDataRepository(root / "appdata"))
    assert projects.current_stage(state, "missing") == "narrative"
    assert next_stage_after("narrative", pipeline=SHOT_PIPELINE) == "references"


def test_production_pipeline_follows_format() -> None:
    from ugc_harness.harness.models import ProjectContext, ProjectState

    from app.backend.project_service import (
        EXPLAINER_PIPELINE,
        SHOT_PIPELINE,
        production_pipeline,
    )

    drama = ProjectState(
        context=ProjectContext(
            constraints={"narrative_format": "drama", "production_mode": "drama"}
        )
    )
    explainer = ProjectState(
        context=ProjectContext(constraints={"narrative_format": "explainer"})
    )
    assert production_pipeline(drama) == SHOT_PIPELINE
    assert production_pipeline(explainer) == EXPLAINER_PIPELINE
    assert next_stage_after("narrative", pipeline=SHOT_PIPELINE) == "references"
    assert next_stage_after("references", pipeline=SHOT_PIPELINE) == "asset"
    assert next_stage_after("narrative", pipeline=EXPLAINER_PIPELINE) == "voice"
    assert "voice" not in SHOT_PIPELINE
    assert "references" not in EXPLAINER_PIPELINE


def test_existing_projects_have_beat_task_and_timeline_views() -> None:
    client = TestClient(app)
    projects = client.get("/api/projects").json()
    if not projects:
        return
    project_key = projects[0]["path_key"]

    stage = client.get(f"/api/projects/{project_key}/stages/narrative")
    task_history = client.get(f"/api/projects/{project_key}/tasks")
    timeline = client.get(f"/api/projects/{project_key}/timeline")
    graph = client.get(f"/api/projects/{project_key}/graph")

    assert stage.status_code == 200
    assert stage.json()["beats"] or stage.json()["core_status"]
    assert task_history.status_code == 200
    assert "chronological" in task_history.json()
    assert timeline.status_code == 200
    assert "tracks" in timeline.json()
    assert graph.status_code == 200
    assert "nodes" in graph.json()
    assert "graph_version" in graph.json()
    assert "history" in graph.json()
    for node in graph.json()["nodes"]:
        assert "content_hash" not in node
        assert "dependency_hashes" not in node
    for project in projects:
        editorial = client.get(
            f"/api/projects/{project['path_key']}/stages/editorial"
        ).json()
        editorial_artifacts = [
            item for beat in editorial["beats"] for item in beat["artifacts"]
        ]
        if editorial_artifacts:
            assert all(item["summary"] for item in editorial_artifacts)
            return


def test_project_path_cannot_escape_outputs() -> None:
    client = TestClient(app)
    assert client.get("/api/projects/not-a-project/tasks").status_code == 404
    assert client.get("/api/projects/not-a-project/graph").status_code == 404


def test_asset_media_is_projected_before_timeline_stage_runs() -> None:
    client = TestClient(app)
    checked = False
    for project in client.get("/api/projects").json():
        asset_stage = client.get(
            f"/api/projects/{project['path_key']}/stages/asset"
        ).json()
        assets = [
            item
            for beat in asset_stage["beats"]
            for item in beat["artifacts"]
            if item["kind"] in {"asset", "shot"} and item.get("media_url")
        ]
        if not assets:
            continue
        timeline = client.get(
            f"/api/projects/{project['path_key']}/timeline"
        ).json()
        visual_beats = {item["beat_id"] for item in timeline["tracks"]["visuals"]}
        assert all(item["media_url"] for item in assets)
        assert {item["beat_id"] for item in assets} <= visual_beats
        checked = True
    if not client.get("/api/projects").json():
        return
    assert checked


def test_shot_list_reads_nested_or_flat_payloads() -> None:
    nested = {"shots": {"shots": [{"shot_id": "s1"}, {"shot_id": "s2"}, {"shot_id": "s3"}]}}
    flat = {"shots": [{"shot_id": "s1"}, {"shot_id": "s2"}]}
    assert [item["shot_id"] for item in shot_list(nested)] == ["s1", "s2", "s3"]
    assert [item["shot_id"] for item in shot_list(flat)] == ["s1", "s2"]


def test_planning_units_cover_drama_scenes_and_all_shots() -> None:
    narrative = {
        "planning": {
            "scenes": [
                {"scene_id": "sc1", "order": 1, "purpose": "开场"},
                {"scene_id": "sc2", "order": 2, "purpose": "冲突"},
            ]
        },
        "shots": {
            "shots": [
                {
                    "shot_id": "shot_a1",
                    "order": 1,
                    "purpose": "进门",
                    "source_refs": ["scene:sc1", "action:a1"],
                },
                {
                    "shot_id": "shot_a2",
                    "order": 2,
                    "purpose": "对峙",
                    "source_refs": ["scene:sc2", "action:a2"],
                },
                {
                    "shot_id": "shot_a3",
                    "order": 3,
                    "purpose": "收束",
                    "source_refs": ["scene:sc2", "action:a3"],
                },
            ]
        },
    }
    units = planning_units(narrative)
    assert [item["planned_beat_id"] for item in units] == ["sc1", "sc2"]
    shots = shot_list(narrative)
    assert len(shots) == 3
    assert shot_beat_id(shots[0]) == "sc1"
    assert shot_beat_id(shots[2]) == "sc2"


def test_graph_projector_lists_nodes_without_hashes() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
    from ugc_harness.harness.description import Utterance, VideoWorld, VoiceDesign, WorldProp
    from ugc_harness.harness.models import DependencyEdge, DependencyGraphState, ProductionState

    from app.backend.graph_projector import GraphProjector

    graph_state = DependencyGraph(DependencyGraphState())
    graph_state.commit_batch(
        task_id="t_narrative",
        produced_by="narrative_agent",
        commits=[
            NodeCommit("prop:e1", "prop", "flour"),
            NodeCommit("beat:b3", "beat", "beat three"),
            NodeCommit(
                "utterance:ss3",
                "utterance",
                "line three",
                edges=(DependencyEdge(to="beat:b3", family="plot"),),
            ),
        ],
    )
    production = ProductionState(
        world=VideoWorld(
            props=[
                WorldProp(prop_id="e1", name="面粉", description="中筋面粉 200g"),
            ]
        ),
        voice=VoiceDesign(
            utterances=[
                Utterance(
                    utterance_id="ss3",
                    anchor_ref="beat:b3",
                    text="先把面粉过筛。",
                )
            ]
        ),
    )
    projects = SimpleNamespace(
        read_state=lambda _path: SimpleNamespace(
            dependency_graph=graph_state.state,
            production=production,
        )
    )
    view = GraphProjector(projects).view(Path("unused"))
    refs = {node["ref"] for node in view["nodes"]}
    assert view["node_count"] == 3
    assert view["kinds"]["beat"] == 1
    assert view["history"] == []
    assert view["history_version"] is None
    assert refs == {"prop:e1", "beat:b3", "utterance:ss3"}
    prop = next(node for node in view["nodes"] if node["ref"] == "prop:e1")
    assert prop["title"] == "面粉"
    assert "中筋面粉" in prop["summary"]
    utterance = next(node for node in view["nodes"] if node["ref"] == "utterance:ss3")
    assert utterance["depends_on"] == ["beat:b3"]
    assert utterance["title"] == "先把面粉过筛。"
    assert any(item["text"] == "先把面粉过筛。" for item in utterance["details"])
    for node in view["nodes"]:
        assert "content_hash" not in node
        assert "dependency_hashes" not in node


def test_media_unit_id_unifies_shot_id_and_beat_id() -> None:
    from app.backend.beat_projector import media_unit_id

    assert media_unit_id({"beat_id": "b01"}) == "b01"
    assert media_unit_id({"shot_id": "shot_a2"}) == "shot_a2"
    narrative = {
        "shots": {
            "shots": [
                {
                    "shot_id": "shot_a2",
                    "source_refs": ["action:a2", "utterance:u2"],
                    "purpose": "接船",
                }
            ]
        }
    }
    assert media_unit_id({"shot_id": "shot_a2"}, narrative) == "a2"
    assert media_unit_id({"shot_id": "shot_a2", "beat_id": "shot_a2"}, narrative) == "a2"
