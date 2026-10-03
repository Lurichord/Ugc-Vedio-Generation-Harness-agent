from __future__ import annotations

from pathlib import Path

import pytest

from app.backend.agent_trace import parse_intake_events, parse_tool_log_lines
from app.backend.repository import AppDataRepository
from app.backend.run_log_projector import merge_run_log
from app.backend.schemas import RunLogRecord, RunStageRequest
from app.backend.stage_runner import StageRunner
from app.backend.state_projector import project_state_view
from ugc_harness.harness.description import CastMember, Deliverable
from ugc_harness.harness.models import (
    AgentResult,
    DependencyGraphState,
    DependencyNode,
    EvaluationResult,
    EvaluationState,
    GraphUpdateRecord,
    PhaseTrajectory,
    ProjectContext,
    ProjectState,
    StageStatusBoard,
    TaskEnvelope,
    TaskScope,
    TaskTrajectoryRecord,
    TrajectoryState,
    TransitionRecord,
)


def _task(task_id: str = "task_1", goal: str = "写脚本") -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        agent="narrative_agent",
        goal=goal,
        scope=TaskScope(project_id="p1", shot_ids=["shot_a1"], target_refs=["shot:shot_a1"]),
        based_on_state_version=1,
        allowed_tools=["narrative.submit_candidate"],
        input_hash="hash",
    )


def _trajectory_record(task: TaskEnvelope) -> TaskTrajectoryRecord:
    return TaskTrajectoryRecord(
        task_kind="generation",
        task=task,
        agent_result=AgentResult(
            task_id=task.task_id,
            status="completed",
            state_version_used=1,
            input_hash=task.input_hash,
        ),
        evaluation=EvaluationResult(
            critic_id="narrative_critic",
            target_ref="narrative",
            passed=True,
        ),
        transition=TransitionRecord(
            transition_id="tr_1",
            from_agent="narrative_agent",
            to_agent="references",
            outcome="advance",
            trigger_ref="narrative",
            reason="ok",
            committed_state_version=1,
        ),
        graph_update=GraphUpdateRecord(
            update_id="gu_1",
            task_id=task.task_id,
            graph_version_before=0,
            graph_version_after=1,
            committed=True,
            reason="ok",
        ),
    )


def test_parse_intake_events_keeps_skill_and_terminal_lines() -> None:
    tools, lines = parse_intake_events([
        {"event": "call", "tool": "skill.activate", "arguments": {"name": "revise-segment"}},
        {"event": "done", "tool": "skill.activate", "arguments": {"name": "revise-segment"}, "result": {"ok": True}},
        {"event": "call", "tool": "harness.repair", "arguments": {"target_refs": ["shot:shot_a1"]}},
        {"event": "done", "tool": "harness.repair", "result": {"ok": False, "error": "blocked"}},
    ])
    assert [item.tool for item in tools] == ["skill.activate", "harness.repair"]
    assert tools[0].skill == "revise-segment"
    assert tools[1].result_ok is False
    assert any("[intake] call skill.activate" in line for line in lines)
    assert any("ok=false error=blocked" in line for line in lines)


def test_parse_tool_log_lines_extracts_call_arguments() -> None:
    calls = parse_tool_log_lines([
        '[narrative_agent] call skill.activate {"name":"generate-shot"}',
        '[narrative_agent] done skill.activate {"name":"generate-shot"} 12ms keys=ok',
        '[narrative_agent] call repair.submit_candidate {"patches":[{"ref":"shot:a"}]}',
        "[narrative_agent] done repair.submit_candidate 0ms keys=patches",
    ])
    assert [item["tool"] for item in calls] == [
        "skill.activate",
        "repair.submit_candidate",
    ]
    assert calls[0]["arguments"] == {"name": "generate-shot"}
    assert calls[0]["skill"] == "generate-shot"
    assert calls[0]["duration_ms"] == 12
    assert calls[1]["arguments"]["patches"][0]["ref"] == "shot:a"


def test_project_state_view_shows_drama_pipeline() -> None:
    state = ProjectState(
        context=ProjectContext(
            project_id="p1",
            state_version=4,
            constraints={"narrative_format": "drama", "production_mode": "drama"},
        ),
        evaluation=EvaluationState(
            stages=StageStatusBoard(
                narrative="passed",
                voice="not_required",
                editorial="not_required",
                references="passed",
                asset="ready",
                timeline="pending",
                render="pending",
            )
        ),
    )
    view = project_state_view(state, project_key="demo", current_stage="asset")
    assert view.format_id == "drama"
    assert view.pipeline == ["narrative", "references", "asset", "timeline", "render"]
    required = [item.stage for item in view.stages if item.required]
    assert required == ["narrative", "references", "asset", "timeline", "render"]
    assert view.stages[0].status == "passed"
    voice = next(item for item in view.stages if item.stage == "voice")
    assert voice.required is False
    assert voice.status == "not_required"
    current = next(item for item in view.stages if item.current)
    assert current.stage == "asset"


def test_project_state_view_shows_tutorial_voice_stage() -> None:
    state = ProjectState(
        context=ProjectContext(
            project_id="p1",
            state_version=4,
            constraints={"narrative_format": "tutorial", "production_mode": "tutorial"},
        ),
        evaluation=EvaluationState(
            stages=StageStatusBoard(
                narrative="passed",
                script="passed",
                voice="pending",
                editorial="not_required",
                references="passed",
                asset="passed",
                timeline="pending",
                render="pending",
            )
        ),
    )
    view = project_state_view(state, project_key="demo", current_stage="voice")
    assert view.format_id == "tutorial"
    assert view.pipeline == [
        "narrative",
        "references",
        "asset",
        "voice",
        "timeline",
        "render",
    ]
    required = [item.stage for item in view.stages if item.required]
    assert required == [
        "narrative",
        "voice",
        "references",
        "asset",
        "timeline",
        "render",
    ]
    voice = next(item for item in view.stages if item.stage == "voice")
    assert voice.required is True
    assert voice.status == "pending"
    editorial = next(item for item in view.stages if item.stage == "editorial")
    assert editorial.required is False


def test_project_state_view_lists_plane_fields_without_media_or_hashes() -> None:
    task = _task()
    state = ProjectState(
        context=ProjectContext(
            project_id="p1",
            state_version=4,
            constraints={"narrative_format": "drama", "production_mode": "drama"},
        ),
        evaluation=EvaluationState(
            stages=StageStatusBoard(
                narrative="passed",
                voice="not_required",
                editorial="not_required",
                references="passed",
                asset="ready",
                timeline="pending",
                render="pending",
            )
        ),
        dependency_graph=DependencyGraphState(
            graph_version=2,
            nodes={
                "cast:c1": DependencyNode(
                    ref="cast:c1",
                    kind="cast",
                    version=1,
                    content_hash="hash-should-hide",
                    dependency_hashes={"shot:a": "dep-hash"},
                )
            },
        ),
        trajectory=TrajectoryState(
            phases={
                "narrative": PhaseTrajectory(
                    phase="narrative",
                    tasks=[_trajectory_record(task)],
                )
            }
        ),
    )
    state.production.world.cast.append(
        CastMember(
            character_id="c1",
            name="屈原",
            role="performer",
            description="楚国诗人",
            reference_image="references/cast_c1.png",
        )
    )
    state.production.deliverable = Deliverable(
        video_file="render/final_cut.mp4",
        sha256="abc123",
    )
    view = project_state_view(state, project_key="demo", current_stage="asset")
    planes = view.planes
    assert set(planes) == {
        "schema_version",
        "context",
        "creative",
        "production",
        "evaluation",
        "dependency_graph",
        "trajectory",
    }
    cast = planes["production"]["world"]["cast"][0]
    assert cast["name"] == "屈原"
    assert cast["description"] == "楚国诗人"
    assert cast["reference_image"] == "cast_c1"
    assert planes["production"]["deliverable"]["video_file"] == "final_cut"
    assert "sha256" not in planes["production"]["deliverable"]
    node = planes["dependency_graph"]["nodes"]["cast:c1"]
    assert node["ref"] == "cast:c1"
    assert "content_hash" not in node
    assert "dependency_hashes" not in node
    recorded = planes["trajectory"]["phases"]["narrative"]["tasks"][0]
    assert recorded["task"]["goal"] == "写脚本"
    assert "input_hash" not in recorded["task"]
    assert "input_hash" not in recorded["agent_result"]
    dumped = str(planes)
    assert "hash-should-hide" not in dumped
    assert "dep-hash" not in dumped
    assert "references/cast_c1.png" not in dumped
    assert "render/final_cut.mp4" not in dumped


def test_run_log_merges_dispatch_and_trajectory() -> None:
    task = _task()
    state = ProjectState(
        context=ProjectContext(project_id="p1", state_version=2),
        trajectory=TrajectoryState(
            phases={
                "narrative": PhaseTrajectory(
                    phase="narrative",
                    tasks=[_trajectory_record(task)],
                )
            }
        ),
    )
    recorded = [
        RunLogRecord(
            run_id="run_1",
            project_id="p1",
            source="intake",
            stage="narrative",
            task_id=task.task_id,
            goal="按指令改第一镜",
            instruction="加一镜月光",
            status="completed",
            lines=[
                '[narrative_agent] call skill.activate {"name":"generate-shot"}',
                '[narrative_agent] done skill.activate {"name":"generate-shot"} 18ms keys=ok',
            ],
        )
    ]
    view = merge_run_log(state, recorded, project_key="demo")
    assert len(view.items) == 1
    item = view.items[0]
    assert item.source == "intake"
    assert item.instruction == "加一镜月光"
    assert item.critic_passed is True
    assert item.context["task"]["goal"] == "写脚本"
    assert "input_hash" not in item.context["task"]
    assert item.context["instruction"] == "加一镜月光"
    assert item.actions[0].arguments == {"name": "generate-shot"}
    assert item.actions[0].skill == "generate-shot"
    extra = _task("task_old", "历史脚本")
    state.trajectory.phases["narrative"].tasks.append(_trajectory_record(extra))
    view = merge_run_log(state, recorded, project_key="demo")
    assert [item.task_id for item in view.items] == ["task_1", "task_old"]
    assert view.items[1].source == "trajectory"
    assert view.items[1].context["task"]["goal"] == "历史脚本"
    assert "input_hash" not in view.items[1].context["task"]


def test_stage_runner_records_intake_task(tmp_path: Path) -> None:
    repository = AppDataRepository(tmp_path / "data")
    project_dir = tmp_path / "out" / "demo"
    project_dir.mkdir(parents=True)
    runner = StageRunner(tmp_path / "out", repository)
    runner._asset = lambda *args, **kwargs: None
    runner.run(
        "asset",
        project_dir,
        RunStageRequest(),
        task=_task("task_repair_1", "重出第一镜"),
        feedback="人物再清楚一点",
    )
    items = repository.run_log("demo")
    assert len(items) == 1
    assert items[0].source == "intake"
    assert items[0].status == "completed"
    assert items[0].instruction == "人物再清楚一点"
    assert items[0].task_id == "task_repair_1"
    assert "重出第一镜" in (items[0].received or "")
    assert "人物再清楚一点" in (items[0].received or "")


def test_stage_runner_records_failed_studio_run(tmp_path: Path) -> None:
    repository = AppDataRepository(tmp_path / "data")
    project_dir = tmp_path / "out" / "demo"
    project_dir.mkdir(parents=True)
    runner = StageRunner(tmp_path / "out", repository)

    def boom(*args, **kwargs):
        raise RuntimeError("seedance down")

    runner._references = boom
    with pytest.raises(RuntimeError, match="seedance down"):
        runner.run("references", project_dir, RunStageRequest())
    items = repository.run_log("demo")
    assert items[0].source == "studio"
    assert items[0].status == "failed"
    assert "seedance down" in (items[0].error or "")


def test_stage_runner_captures_terminal_lines(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "agent-tools.log"
    monkeypatch.setenv("AGENT_TOOL_LOG", str(log_path))
    repository = AppDataRepository(tmp_path / "data")
    project_dir = tmp_path / "out" / "demo"
    project_dir.mkdir(parents=True)
    runner = StageRunner(tmp_path / "out", repository)

    def write_log(*args, **kwargs):
        log_path.write_text("[narrative_agent] call skill.activate {\"name\":\"generate-shot\"}\n", encoding="utf-8")

    runner._asset = write_log
    runner.run(
        "asset",
        project_dir,
        RunStageRequest(),
        task=_task("task_1", "出第一镜"),
        feedback="按参考图出片",
    )
    item = repository.run_log("demo")[0]
    assert any("skill.activate" in line for line in item.lines)
    assert "generate-shot" in "\n".join(item.lines)
    assert item.actions[0].tool == "skill.activate"
    assert item.actions[0].arguments == {"name": "generate-shot"}
    assert item.actions[0].skill == "generate-shot"
    assert item.context["task"]["goal"] == "出第一镜"
    assert item.context["instruction"] == "按参考图出片"
    assert "input_hash" not in item.context["task"]
    assert item.context["asset_context"]["scoped_shot_ids"] == ["shot_a1"]
    assert item.context["asset_context"]["turn"]["instruction"] == "按参考图出片"
    assert item.context["asset_context"]["turn"]["regenerate_shot_ids"] == ["shot_a1"]
    assert "memory" in item.context["asset_context"]
    assert item.messages[0]["role"] == "system"
    assert "Skill 目录" in item.messages[0]["content"]
    assert "当前 memory" in item.messages[0]["content"]
    assert "system 里的 memory" in item.messages[0]["content"]
    assert item.messages[1]["role"] == "user"
    assert "按参考图出片" in item.messages[1]["content"]


def test_asset_run_context_includes_last_assembly(tmp_path: Path) -> None:
    from app.backend.agent_trace import injected_context
    from tests.test_description import _drama_artifact
    from ugc_harness.harness.description import ShotGenerationReference, ShotMedia
    from ugc_harness.harness.description_builder import apply_narrative_document

    project_dir = tmp_path / "demo"
    (project_dir / "harness").mkdir(parents=True)
    artifact = _drama_artifact()
    state = ProjectState(
        context=ProjectContext(
            project_id=artifact.brief.project_id,
            state_version=1,
            constraints={"narrative_format": "drama"},
        )
    )
    apply_narrative_document(state, artifact)
    first = state.production.shots[0]
    cast = state.production.world.cast[0]
    cast.generation_prompt = "9:16 竖屏身份静帧。阿周。"
    first.media = ShotMedia(
        modality="ai_video",
        local_path="assets/generated_shots/asset_shot.mp4",
        generation_prompt="9:16 竖屏。阿周举起月饼。",
        generation_references=[
            ShotGenerationReference(
                id=f"cast:{cast.character_id}",
                description="人物阿周",
            )
        ],
    )
    task = TaskEnvelope(
        task_id="task_asset",
        agent="asset_stage",
        goal="重出第一镜",
        scope=TaskScope(
            project_id=state.context.project_id,
            shot_ids=[first.spec.shot_id],
        ),
        based_on_state_version=1,
        allowed_tools=["asset.generate_shot"],
        input_hash="repair-asset",
    )
    context = injected_context(
        task,
        instruction="机位再近一点",
        project_dir=project_dir,
        stage="asset",
        state=state,
    )
    memory = context["asset_context"]["memory"]
    assert any(
        item["generation_prompt"] == "9:16 竖屏身份静帧。阿周。"
        for item in memory["identities"]
    )
    shot_memory = next(
        item for item in memory["shots"] if item["shot_id"] == first.spec.shot_id
    )
    assert shot_memory["generation_prompt"] == "9:16 竖屏。阿周举起月饼。"
    assert shot_memory["generation_references"][0]["id"] == f"cast:{cast.character_id}"
    assert context["asset_context"]["turn"]["instruction"] == "机位再近一点"
    from app.backend.agent_trace import run_opening_messages

    messages = run_opening_messages(
        task,
        instruction="机位再近一点",
        project_dir=project_dir,
        stage="asset",
        state=state,
        existing=context,
    )
    assert messages[0]["role"] == "system"
    assert "9:16 竖屏。阿周举起月饼。" in messages[0]["content"]
    assert "generate-shot" in messages[0]["content"]
    assert "机位再近一点" in messages[1]["content"]


def test_old_asset_run_log_rebuilds_opening_messages(tmp_path: Path) -> None:
    from tests.test_description import _drama_artifact
    from ugc_harness.harness.description import ShotGenerationReference, ShotMedia
    from ugc_harness.harness.description_builder import apply_narrative_document

    project_dir = tmp_path / "demo"
    (project_dir / "harness").mkdir(parents=True)
    artifact = _drama_artifact()
    state = ProjectState(
        context=ProjectContext(
            project_id=artifact.brief.project_id,
            state_version=1,
            constraints={"narrative_format": "drama"},
        )
    )
    apply_narrative_document(state, artifact)
    first = state.production.shots[0]
    first.media = ShotMedia(
        modality="ai_video",
        local_path="assets/generated_shots/asset_shot.mp4",
        generation_prompt="9:16 竖屏。阿周举起月饼。",
        generation_references=[
            ShotGenerationReference(
                id=f"cast:{state.production.world.cast[0].character_id}",
                description="人物阿周",
            )
        ],
    )
    (project_dir / "harness" / "project_state.json").write_text(
        state.model_dump_json(),
        encoding="utf-8",
    )
    recorded = [
        RunLogRecord(
            run_id="run_old_asset",
            source="studio",
            stage="asset",
            status="completed",
            goal="出第一镜",
            instruction="机位再近一点",
            context={
                "asset_context": {
                    "project_id": state.context.project_id,
                    "shot_count": 1,
                    "scoped_shot_ids": [first.spec.shot_id],
                }
            },
        )
    ]
    view = merge_run_log(
        state,
        recorded,
        project_key="demo",
        project_dir=project_dir,
    )
    item = view.items[0]
    assert item.messages[0]["role"] == "system"
    assert "Skill 目录" in item.messages[0]["content"]
    assert "9:16 竖屏。阿周举起月饼。" in item.messages[0]["content"]
    assert "机位再近一点" in item.messages[1]["content"]
