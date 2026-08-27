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
        "asset": "pending",
        "timeline": "pending",
        "render": "pending",
    }) == "voice"
    assert next_stage_after("narrative", {
        "narrative": "passed",
        "voice": "not_required",
        "editorial": "not_required",
        "asset": "pending",
        "timeline": "pending",
        "render": "pending",
    }) == "asset"


def test_existing_projects_have_beat_task_and_timeline_views() -> None:
    client = TestClient(app)
    projects = client.get("/api/projects").json()
    if not projects:
        return
    project_key = projects[0]["path_key"]

    stage = client.get(f"/api/projects/{project_key}/stages/narrative")
    task_history = client.get(f"/api/projects/{project_key}/tasks")
    timeline = client.get(f"/api/projects/{project_key}/timeline")

    assert stage.status_code == 200
    assert stage.json()["beats"] or stage.json()["core_status"]
    assert task_history.status_code == 200
    assert "chronological" in task_history.json()
    assert timeline.status_code == 200
    assert "tracks" in timeline.json()
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
            if item["kind"] == "asset"
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
