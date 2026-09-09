import json
from pathlib import Path

from tests.test_shot_repair import (
    _SequentialSubmitModel,
    _SubmitPatchesModel,
    _project_dir,
    _write_tutorial_project,
)
from ugc_harness.harness.graph_edit import plan_shot_create
from ugc_harness.harness.repair import load_project_state
from ugc_harness.harness.shot_repair import apply_shot_patches, run_shot_repair
from ugc_harness.harness.shot_repair import RepairCandidate
from ugc_harness.intake.graph_ops import bind_shot, create_shot, remove_node
from ugc_harness.intake.host import new_session
from ugc_harness.intake.identity import create_identity
import pytest


def _session(project_dir: Path):
    session = new_session()
    session.project_dir = str(project_dir)
    return session


def _created_shot_value(state, spec):
    neighbor = state.production.shots[0].spec.model_dump(mode="json")
    neighbor["shot_id"] = spec.shot_id
    neighbor["order"] = spec.order
    neighbor["source_refs"] = spec.source_refs
    neighbor["binds"] = spec.binds
    neighbor["visual"]["action_id"] = spec.action_id
    neighbor["payload"]["action_ids"] = [spec.action_id]
    return neighbor


def _step_and_prop(state):
    shot = state.production.shots[0]
    step = next(ref for ref in shot.spec.source_refs if ref.startswith("step:"))
    prop = next(ref for ref in shot.spec.binds if ref.startswith("prop:"))
    return f"shot:{shot.spec.shot_id}", step, prop


def test_identity_create_adds_root_without_edges() -> None:
    project_dir = _project_dir()
    _write_tutorial_project(project_dir)
    _, result = create_identity(
        _session(project_dir),
        kind="prop",
        name="小船",
        description="江上的木船",
    )
    assert result["ok"] is True
    ref = result["ref"]
    state = load_project_state(project_dir)
    node = state.dependency_graph.nodes[ref]
    assert node.kind == "prop"
    assert node.dependents == []
    assert node.depends_on == []
    added = next(item for item in state.production.world.props if item.name == "小船")
    assert f"prop:{added.prop_id}" == ref
    assert any(item.material_id == added.prop_id for item in state.production.structure.materials)


def test_shot_bind_appends_identity_and_keeps_old_binds() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot_ref, _step, old_prop = _step_and_prop(state)
    _, created = create_identity(
        _session(project_dir),
        kind="prop",
        name="小船",
        description="江上的木船",
    )
    _, bound = bind_shot(
        _session(project_dir),
        shot_refs=[shot_ref],
        identity_ref=created["ref"],
    )
    assert bound["ok"] is True
    assert bound["shot_refs"] == [shot_ref]
    updated = load_project_state(project_dir)
    binds = next(
        item.spec.binds
        for item in updated.production.shots
        if f"shot:{item.spec.shot_id}" == shot_ref
    )
    assert old_prop in binds
    assert created["ref"] in binds
    assert created["ref"] in updated.dependency_graph.nodes[shot_ref].depends_on
    assert shot_ref in updated.dependency_graph.nodes[created["ref"]].dependents


def test_apply_shot_patches_still_rejects_dropping_or_replacing_binds() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot = state.production.shots[0]
    ref = f"shot:{shot.spec.shot_id}"
    value = shot.spec.model_dump(mode="json")
    value["binds"] = [item for item in value["binds"] if not item.startswith("prop:")]
    if value["binds"] == shot.spec.binds:
        value["binds"] = ["prop:other"]
    with pytest.raises(ValueError, match="binds"):
        apply_shot_patches(
            state,
            RepairCandidate.model_validate({"patches": [{"ref": ref, "value": value}]}),
            [ref],
        )


def test_remove_identity_fails_while_shot_binds_then_succeeds_after_shot_removed() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot_ref, _step, _prop = _step_and_prop(state)
    _, created = create_identity(
        _session(project_dir),
        kind="prop",
        name="小船",
        description="江上的木船",
    )
    bind_shot(_session(project_dir), shot_refs=[shot_ref], identity_ref=created["ref"])
    _, blocked = remove_node(_session(project_dir), created["ref"])
    assert blocked["ok"] is False
    assert "仍被" in blocked["error"]
    _, dropped = remove_node(_session(project_dir), shot_ref)
    assert dropped["ok"] is True
    _, gone = remove_node(_session(project_dir), created["ref"])
    assert gone["ok"] is True
    final = load_project_state(project_dir)
    assert created["ref"] not in final.dependency_graph.nodes
    assert shot_ref not in final.dependency_graph.nodes
    assert all(item.spec.shot_id != shot_ref.partition(":")[2] for item in final.production.shots)


def test_remove_shot_compacts_order() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    ordered = sorted(state.production.shots, key=lambda item: item.spec.order)
    assert len(ordered) >= 2
    first = f"shot:{ordered[0].spec.shot_id}"
    second = ordered[1]
    _, result = remove_node(_session(project_dir), first)
    assert result["ok"] is True
    updated = load_project_state(project_dir)
    leftover = sorted(updated.production.shots, key=lambda item: item.spec.order)
    assert leftover[0].spec.shot_id == second.spec.shot_id
    assert [item.spec.order for item in leftover] == list(range(1, len(leftover) + 1))


def test_shot_create_inserts_after_and_grows_edges() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    after_ref, step_ref, prop_ref = _step_and_prop(state)
    spec = plan_shot_create(
        state,
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
    )
    neighbor = _created_shot_value(state, spec)
    before_count = len(state.production.shots)

    def run_task(task, instruction):
        run_shot_repair(
            project_dir,
            task,
            instruction,
            tool_model=_SubmitPatchesModel(
                [{"ref": f"shot:{spec.shot_id}", "value": neighbor}]
            ),
        )

    _, result = create_shot(
        _session(project_dir),
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
        instruction="加一镜把材料放进锅里",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert result["shot_ref"] == f"shot:{spec.shot_id}"
    updated = load_project_state(project_dir)
    assert len(updated.production.shots) == before_count + 1
    created = next(
        item for item in updated.production.shots if item.spec.shot_id == spec.shot_id
    )
    assert created.spec.order == spec.order
    assert created.spec.binds == [prop_ref]
    node = updated.dependency_graph.nodes[f"shot:{spec.shot_id}"]
    assert step_ref in node.depends_on
    assert prop_ref in node.depends_on
    assert f"shot:{spec.shot_id}" in updated.dependency_graph.nodes[prop_ref].dependents
    following = [
        item
        for item in updated.production.shots
        if item.spec.shot_id != spec.shot_id and item.spec.order >= spec.order
    ]
    assert all(item.spec.order > spec.order for item in following)


def test_shot_create_retries_when_model_also_patches_neighbor() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    after_ref, step_ref, prop_ref = _step_and_prop(state)
    spec = plan_shot_create(
        state,
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
    )
    created = _created_shot_value(state, spec)
    following = next(
        item
        for item in state.production.shots
        if item.spec.order >= spec.order and item.spec.shot_id != spec.shot_id
    )
    original_purpose = following.spec.purpose
    hijacked = following.spec.model_dump(mode="json")
    hijacked["order"] = spec.order + 1
    hijacked["purpose"] = "被模型误改的邻镜"
    model = _SequentialSubmitModel(
        [
            [
                {"ref": f"shot:{spec.shot_id}", "value": created},
                {"ref": f"shot:{following.spec.shot_id}", "value": hijacked},
            ],
            [{"ref": f"shot:{spec.shot_id}", "value": created}],
        ]
    )

    def run_task(task, instruction):
        run_shot_repair(project_dir, task, instruction, tool_model=model)

    _, result = create_shot(
        _session(project_dir),
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
        instruction="加一镜把材料放进锅里",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert model.calls == 2
    fed_back = json.dumps(model.messages[1], ensure_ascii=False)
    assert f"shot create 必须只交 shot:{spec.shot_id}" in fed_back
    updated = load_project_state(project_dir)
    neighbor = next(
        item
        for item in updated.production.shots
        if item.spec.shot_id == following.spec.shot_id
    )
    assert neighbor.spec.purpose == original_purpose
    assert neighbor.spec.order == following.spec.order + 1


def test_shot_create_retries_invalid_schema() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    after_ref, step_ref, prop_ref = _step_and_prop(state)
    spec = plan_shot_create(
        state,
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
    )
    valid = _created_shot_value(state, spec)
    invalid = dict(valid)
    invalid["purpose"] = 1
    model = _SequentialSubmitModel(
        [
            [{"ref": f"shot:{spec.shot_id}", "value": invalid}],
            [{"ref": f"shot:{spec.shot_id}", "value": valid}],
        ]
    )

    def run_task(task, instruction):
        run_shot_repair(project_dir, task, instruction, tool_model=model)

    _, result = create_shot(
        _session(project_dir),
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
        instruction="加一镜把材料放进锅里",
        run_task=run_task,
    )
    assert result["ok"] is True
    assert model.calls == 2
    fed_back = json.dumps(model.messages[1], ensure_ascii=False)
    assert "purpose" in fed_back
    updated = load_project_state(project_dir)
    assert any(item.spec.shot_id == spec.shot_id for item in updated.production.shots)


def test_shot_create_failure_leaves_no_node() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    after_ref, step_ref, prop_ref = _step_and_prop(state)
    before_shots = {item.spec.shot_id for item in state.production.shots}
    before_nodes = set(state.dependency_graph.nodes)

    def run_task(task, instruction):
        raise RuntimeError("generic refused")

    _, result = create_shot(
        _session(project_dir),
        anchor_ref=step_ref,
        binds=[prop_ref],
        after_ref=after_ref,
        instruction="加一镜",
        run_task=run_task,
    )
    assert result["ok"] is False
    after = load_project_state(project_dir)
    assert {item.spec.shot_id for item in after.production.shots} == before_shots
    assert set(after.dependency_graph.nodes) == before_nodes
