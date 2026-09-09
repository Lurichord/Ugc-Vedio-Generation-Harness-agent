from pathlib import Path
from uuid import uuid4
import json

import pytest

from tests.test_description import _tutorial_artifact
from ugc_harness.harness.dependencies import DependencyGraph, NodeCommit
from ugc_harness.harness.dependency_builders import narrative_commits
from ugc_harness.harness.description import WorldProp
from ugc_harness.harness.description_builder import apply_narrative_document
from ugc_harness.intake.graph_view import list_graph
from ugc_harness.harness.models import (
    ProjectContext,
    ProjectState,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
)
from ugc_harness.harness.repair import load_project_state, write_project_state
from ugc_harness.harness.shot_repair import (
    REPAIR_SUBMIT_TOOL,
    RepairCandidate,
    apply_shot_patches,
    build_repair_pack,
    resolve_shot_refs,
    run_shot_repair,
)
from ugc_harness.intake.host import new_session
from ugc_harness.intake.identity import update_identity
from ugc_harness.agents.generic import StepDecision


class _SubmitPatchesModel:
    def __init__(self, patches: list[dict]) -> None:
        self.patches = patches

    def decide(self, messages, tools):
        del messages
        name = next(
            str(item["name"])
            for item in tools
            if str(item["name"]).endswith("submit_candidate")
        )
        return StepDecision(
            tool=name,
            arguments={"patches": self.patches},
        )


class _SequentialSubmitModel:
    def __init__(self, rounds: list[list[dict]]) -> None:
        self.rounds = rounds
        self.calls = 0
        self.messages: list[list[dict]] = []

    def decide(self, messages, tools):
        self.messages.append(list(messages))
        patches = self.rounds[min(self.calls, len(self.rounds) - 1)]
        self.calls += 1
        return StepDecision(
            tool=next(
                str(item["name"])
                for item in tools
                if str(item["name"]).endswith("submit_candidate")
            ),
            arguments={"patches": patches},
        )


def _project_dir() -> Path:
    path = Path(".tmp") / "shot_repair" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_tutorial_project(project_dir: Path) -> ProjectState:
    artifact = _tutorial_artifact()
    state = ProjectState(
        context=ProjectContext(
            project_id=artifact.brief.project_id,
            state_version=1,
            constraints={"narrative_format": "tutorial"},
        )
    )
    apply_narrative_document(state, artifact)
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="seed",
        produced_by="narrative_agent",
        commits=narrative_commits(artifact),
    )
    project_dir.mkdir(parents=True, exist_ok=True)
    write_project_state(project_dir, state)
    (project_dir / "narrative_artifact.json").write_text(
        artifact.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return load_project_state(project_dir)


def _shot_task(state: ProjectState, refs: list[str]) -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_shot_repair",
        agent="narrative_agent",
        goal="微调圈中的 shot",
        scope=TaskScope(
            project_id=state.context.project_id,
            target_refs=refs,
        ),
        based_on_state_version=state.context.state_version,
        allowed_tools=[REPAIR_SUBMIT_TOOL],
        required_outputs=["patches"],
        budget=TaskBudget(max_steps=3, max_retries=0),
        input_hash="hash",
    )


def test_resolve_and_pack_circled_shots() -> None:
    state = _write_tutorial_project(_project_dir())
    first = state.production.shots[0]
    second = state.production.shots[1]
    first_ref = f"shot:{first.spec.shot_id}"
    pack = build_repair_pack(state, [first_ref], "画面按橄榄油来")
    kinds = {node["ref"]: node for node in pack["nodes"]}
    assert kinds[first_ref]["editable"] is True
    neighbor = f"shot:{second.spec.shot_id}"
    assert kinds[neighbor]["editable"] is False
    identity = [node for node in pack["nodes"] if node["kind"] in {"prop", "cast", "place"}]
    assert identity
    assert all(node["editable"] is False for node in identity)


def test_apply_shot_patches_rejects_locked_fields() -> None:
    state = _write_tutorial_project(_project_dir())
    shot = state.production.shots[0]
    ref = f"shot:{shot.spec.shot_id}"
    value = shot.spec.model_dump(mode="json")
    value["purpose"] = "改过的目的"
    apply_shot_patches(
        state,
        RepairCandidate.model_validate(
            {"patches": [{"ref": ref, "value": value}]}
        ),
        [ref],
    )
    assert state.production.shots[0].spec.purpose == "改过的目的"

    value["shot_id"] = "other"
    with pytest.raises(ValueError, match="shot_id"):
        apply_shot_patches(
            state,
            RepairCandidate.model_validate(
                {"patches": [{"ref": ref, "value": value}]}
            ),
            [ref],
        )

    value = shot.spec.model_dump(mode="json")
    value["binds"] = [item for item in value["binds"] if not item.startswith("prop:")]
    if value["binds"] == shot.spec.binds:
        value["binds"] = [*value["binds"], "prop:other_oil"]
    with pytest.raises(ValueError, match="binds"):
        apply_shot_patches(
            state,
            RepairCandidate.model_validate(
                {"patches": [{"ref": ref, "value": value}]}
            ),
            [ref],
        )


def test_run_shot_repair_writes_only_circled_shot() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot = state.production.shots[0]
    other = state.production.shots[1]
    ref = f"shot:{shot.spec.shot_id}"
    value = shot.spec.model_dump(mode="json")
    value["purpose"] = "只改第一镜"
    before_other = other.spec.purpose
    run_shot_repair(
        project_dir,
        _shot_task(state, [ref]),
        "只改这一镜的目的",
        tool_model=_SubmitPatchesModel([{"ref": ref, "value": value}]),
    )
    updated = load_project_state(project_dir)
    assert updated.production.shots[0].spec.purpose == "只改第一镜"
    assert updated.production.shots[1].spec.purpose == before_other
    assert updated.dependency_graph.nodes[ref].status == "current"


def test_run_shot_repair_retries_invalid_schema() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot = state.production.shots[0]
    ref = f"shot:{shot.spec.shot_id}"
    valid = shot.spec.model_dump(mode="json")
    valid["purpose"] = "只改第一镜"
    invalid = dict(valid)
    invalid["purpose"] = 1
    model = _SequentialSubmitModel(
        [
            [{"ref": ref, "value": invalid}],
            [{"ref": ref, "value": valid}],
        ]
    )
    run_shot_repair(
        project_dir,
        _shot_task(state, [ref]),
        "只改这一镜的目的",
        tool_model=model,
    )
    assert model.calls == 2
    fed_back = json.dumps(model.messages[1], ensure_ascii=False)
    assert "purpose" in fed_back
    updated = load_project_state(project_dir)
    assert updated.production.shots[0].spec.purpose == "只改第一镜"


def test_identity_update_rewrites_prop_then_shots_still_bind() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    shot = next(item for item in state.production.shots if item.spec.binds)
    prop_ref = next(
        bind for bind in shot.spec.binds if bind.startswith("prop:")
    )
    before_prop_ids = [item.prop_id for item in state.production.world.props]
    before_binds = [list(item.spec.binds) for item in state.production.shots]
    session = new_session()
    session.project_dir = str(project_dir)
    _, result = update_identity(
        session,
        patches=[
            {
                "ref": prop_ref,
                "name": "橄榄油",
                "description": "冷榨橄榄油",
            }
        ],
    )
    assert result["ok"] is True
    updated = load_project_state(project_dir)
    prop_id = prop_ref.partition(":")[2]
    rewritten = next(
        item for item in updated.production.world.props if item.prop_id == prop_id
    )
    assert rewritten.name == "橄榄油"
    assert [item.prop_id for item in updated.production.world.props] == before_prop_ids
    assert [list(item.spec.binds) for item in updated.production.shots] == before_binds
    assert resolve_shot_refs(updated, [prop_ref])


def _duplicate_named_prop(state: ProjectState, project_dir: Path) -> tuple[str, str, str]:
    source = state.production.world.props[0]
    duplicate = WorldProp(
        prop_id="e_dup",
        name=source.name,
        description="同一件东西的第二张卡",
    )
    state.production.world.props.append(duplicate)
    DependencyGraph(state.dependency_graph).commit_batch(
        task_id="dup",
        produced_by="narrative_agent",
        commits=[NodeCommit(f"prop:{duplicate.prop_id}", "prop", duplicate)],
    )
    write_project_state(project_dir, state)
    return source.name, source.prop_id, duplicate.prop_id


def test_identity_update_by_name_rewrites_all_same_named_props() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    name, first_id, second_id = _duplicate_named_prop(state, project_dir)
    around = list_graph(load_project_state(project_dir), around_ref=name)
    assert around["ok"] is True
    assert set(around["resolved_refs"]) == {f"prop:{first_id}", f"prop:{second_id}"}
    before_binds = [list(item.spec.binds) for item in state.production.shots]
    session = new_session()
    session.project_dir = str(project_dir)
    _, result = update_identity(
        session,
        patches=[{"ref": name, "name": "蜂蜜", "description": "液态蜂蜜"}],
    )
    assert result["ok"] is True
    assert set(result["changed_refs"]) == {f"prop:{first_id}", f"prop:{second_id}"}
    updated = load_project_state(project_dir)
    renamed = [
        item
        for item in updated.production.world.props
        if item.prop_id in {first_id, second_id}
    ]
    assert {item.prop_id for item in renamed} == {first_id, second_id}
    assert all(item.name == "蜂蜜" for item in renamed)
    assert [list(item.spec.binds) for item in updated.production.shots] == before_binds


def test_identity_update_by_id_rewrites_same_named_siblings() -> None:
    project_dir = _project_dir()
    state = _write_tutorial_project(project_dir)
    _name, first_id, second_id = _duplicate_named_prop(state, project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    _, result = update_identity(
        session,
        patches=[{"ref": f"prop:{first_id}", "name": "蜂蜜"}],
    )
    assert result["ok"] is True
    assert set(result["changed_refs"]) == {f"prop:{first_id}", f"prop:{second_id}"}
    updated = load_project_state(project_dir)
    names = {
        item.prop_id: item.name
        for item in updated.production.world.props
        if item.prop_id in {first_id, second_id}
    }
    assert names == {first_id: "蜂蜜", second_id: "蜂蜜"}


def test_list_graph_identity_roots_include_name_and_shot_dependents() -> None:
    state = _write_tutorial_project(_project_dir())
    overview = list_graph(state)
    assert overview["ok"] is True
    props = [node for node in overview["nodes"] if node["kind"] == "prop"]
    assert props
    assert all(node.get("name") for node in props)
    assert any(
        any(item.startswith("shot:") for item in node["dependents"])
        for node in props
    )
    assert all(not node["ref"].startswith("shot:") for node in overview["nodes"])
    named = list_graph(state, around_ref=props[0]["name"])
    assert named["ok"] is True
    named_refs = {node["ref"] for node in named["nodes"]}
    assert props[0]["ref"] in named_refs
    assert any(ref.startswith("shot:") for ref in named_refs)


def test_identity_update_requires_patches() -> None:
    project_dir = _project_dir()
    _write_tutorial_project(project_dir)
    session = new_session()
    session.project_dir = str(project_dir)
    _, result = update_identity(session, patches=None)
    assert result["ok"] is False
    assert "patches" in result["error"]
