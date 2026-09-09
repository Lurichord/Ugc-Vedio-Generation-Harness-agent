from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from tests.test_production_routes import (
    FakeIdentityImageProvider,
    FakeShotVideoProvider,
)
from tests.test_shot_repair import (
    _SubmitPatchesModel,
    _shot_task,
    _write_tutorial_project,
)
from ugc_harness.harness.description import ShotMedia
from ugc_harness.harness.repair import load_project_state, write_project_state
from ugc_harness.harness.shot_repair import resolve_shot_refs, run_shot_repair
from ugc_harness.harness.world_identities import (
    bound_identity_refs,
    identity_image_path,
    set_reference_image,
)
from ugc_harness.intake.actions import repair_description
from ugc_harness.intake.host import new_session
from ugc_harness.intake.identity import update_identity


def _plant_identity_images(state, project_dir: Path) -> None:
    for ref in bound_identity_refs(state):
        path = identity_image_path(project_dir, ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"seed:{ref}".encode())
        set_reference_image(state, ref, path.relative_to(project_dir).as_posix())


def _plant_shot_videos(state, project_dir: Path) -> dict[str, str]:
    folder = project_dir / "assets" / "generated_shots"
    folder.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for shot in state.production.shots:
        content = f"seed-video:{shot.spec.shot_id}".encode()
        path = folder / f"asset_{shot.spec.shot_id}.mp4"
        path.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        hashes[shot.spec.shot_id] = digest
        frame = folder.parent / "shot_frames" / f"{shot.spec.shot_id}_last.png"
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b"PNGFRAME")
        shot.media = ShotMedia(
            modality="ai_video",
            local_path=path.relative_to(project_dir).as_posix(),
            sha256=digest,
            duration_ms=4000,
            generator_model="seed",
            generation_prompt="seed",
        )
    return hashes


def test_repair_one_prop_regenerates_only_its_image_and_shots() -> None:
    project_dir = Path(".tmp") / "shot_graph_repair" / uuid4().hex
    state = _write_tutorial_project(project_dir)
    _plant_identity_images(state, project_dir)
    before_hashes = _plant_shot_videos(state, project_dir)
    state.evaluation.stages.references = "passed"
    state.evaluation.stages.asset = "passed"
    write_project_state(project_dir, state)

    shot = next(item for item in state.production.shots if item.spec.binds)
    prop_ref = next(bind for bind in shot.spec.binds if bind.startswith("prop:"))
    dependent = [
        ref.partition(":")[2] for ref in resolve_shot_refs(state, [prop_ref])
    ]
    untouched = [
        item.spec.shot_id
        for item in state.production.shots
        if item.spec.shot_id not in set(dependent)
    ]
    assert dependent

    session = new_session()
    session.project_dir = str(project_dir)
    _, updated = update_identity(
        session,
        patches=[{"ref": prop_ref, "name": "蜂蜜", "description": "液态蜂蜜"}],
    )
    assert updated["ok"] is True
    images = FakeIdentityImageProvider()
    videos = FakeShotVideoProvider()

    def run_task(task, instruction: str) -> None:
        current = load_project_state(project_dir)
        patches = []
        for ref in task.scope.target_refs:
            if not ref.startswith("shot:"):
                continue
            shot_id = ref.partition(":")[2]
            spec = next(
                item.spec
                for item in current.production.shots
                if item.spec.shot_id == shot_id
            )
            patches.append({"ref": ref, "value": spec.model_dump(mode="json")})
        run_shot_repair(
            project_dir,
            task,
            instruction,
            tool_model=_SubmitPatchesModel(patches),
            image_provider=images,
            video_provider=videos,
        )

    _, result = repair_description(
        session,
        updated["changed_refs"],
        "画面按蜂蜜来",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert result["identity_refs"] == [prop_ref]
    assert set(result["shot_refs"]) == {f"shot:{shot_id}" for shot_id in dependent}
    assert images.refs == [prop_ref]
    assert videos.shot_ids == dependent

    final = load_project_state(project_dir)
    rewritten = next(
        item
        for item in final.production.world.props
        if f"prop:{item.prop_id}" == prop_ref
    )
    assert rewritten.name == "蜂蜜"
    assert rewritten.reference_image
    assert (project_dir / rewritten.reference_image).read_bytes() == f"img:{prop_ref}".encode()
    for shot_id in dependent:
        media = next(
            item.media
            for item in final.production.shots
            if item.spec.shot_id == shot_id
        )
        assert media is not None
        assert media.sha256 != before_hashes[shot_id]
        assert final.dependency_graph.nodes[f"shot:{shot_id}"].status == "current"
    for shot_id in untouched:
        media = next(
            item.media
            for item in final.production.shots
            if item.spec.shot_id == shot_id
        )
        assert media is not None
        assert media.sha256 == before_hashes[shot_id]


def test_identity_repair_before_asset_only_rewrites_image() -> None:
    project_dir = Path(".tmp") / "shot_graph_refs_only" / uuid4().hex
    state = _write_tutorial_project(project_dir)
    _plant_identity_images(state, project_dir)
    state.evaluation.stages.references = "passed"
    state.evaluation.stages.asset = "ready"
    write_project_state(project_dir, state)

    shot = next(item for item in state.production.shots if item.spec.binds)
    prop_ref = next(bind for bind in shot.spec.binds if bind.startswith("prop:"))
    session = new_session()
    session.project_dir = str(project_dir)
    _, updated = update_identity(
        session,
        patches=[{"ref": prop_ref, "name": "蜂蜜", "description": "液态蜂蜜"}],
    )
    assert updated["ok"] is True
    images = FakeIdentityImageProvider()
    videos = FakeShotVideoProvider()

    def run_task(task, instruction: str) -> None:
        current = load_project_state(project_dir)
        patches = []
        for ref in task.scope.target_refs:
            if not ref.startswith("shot:"):
                continue
            shot_id = ref.partition(":")[2]
            spec = next(
                item.spec
                for item in current.production.shots
                if item.spec.shot_id == shot_id
            )
            patches.append({"ref": ref, "value": spec.model_dump(mode="json")})
        run_shot_repair(
            project_dir,
            task,
            instruction,
            tool_model=_SubmitPatchesModel(patches),
            image_provider=images,
            video_provider=videos,
        )

    _, result = repair_description(
        session,
        updated["changed_refs"],
        "画面按蜂蜜来",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert images.refs == [prop_ref]
    assert videos.shot_ids == []
    final = load_project_state(project_dir)
    rewritten = next(
        item
        for item in final.production.world.props
        if f"prop:{item.prop_id}" == prop_ref
    )
    assert (project_dir / rewritten.reference_image).read_bytes() == f"img:{prop_ref}".encode()
    assert all(item.media is None for item in final.production.shots)
    assert final.evaluation.stages.asset == "ready"


def test_run_shot_repair_without_providers_still_patches_spec() -> None:
    project_dir = Path(".tmp") / "shot_graph_spec_only" / uuid4().hex
    state = _write_tutorial_project(project_dir)
    shot = state.production.shots[0]
    other = state.production.shots[1]
    ref = f"shot:{shot.spec.shot_id}"
    value = shot.spec.model_dump(mode="json")
    value["purpose"] = "只改规格"
    before_other = other.spec.purpose
    run_shot_repair(
        project_dir,
        _shot_task(state, [ref]),
        "只改这一镜的目的",
        tool_model=_SubmitPatchesModel([{"ref": ref, "value": value}]),
    )
    updated = load_project_state(project_dir)
    assert updated.production.shots[0].spec.purpose == "只改规格"
    assert updated.production.shots[1].spec.purpose == before_other
    assert updated.evaluation.stages.references == "not_required"
    assert updated.evaluation.stages.asset == "pending"
