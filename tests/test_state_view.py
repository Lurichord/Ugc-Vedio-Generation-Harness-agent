from ugc_harness.harness.models import TaskBudget, TaskEnvelope, TaskScope
from ugc_harness.harness.state_view import NarrativeExecutionBoard


def _drama_task() -> TaskEnvelope:
    return TaskEnvelope(
        task_id="task_narrative_p1_v0",
        agent="narrative_agent",
        goal="生成剧情候选",
        scope=TaskScope(project_id="p1"),
        based_on_state_version=0,
        format_id="drama",
        allowed_tools=[
            "narrative.drama.design_world",
            "narrative.drama.plan_story",
            "narrative.drama.expand_scenes",
            "narrative.drama.compile_shots",
            "narrative.submit_candidate",
        ],
        required_outputs=["world_state", "planning", "shots"],
        budget=TaskBudget(max_steps=10),
        input_hash="hash",
    )


def test_board_tracks_layers_and_pending_objectives() -> None:
    board = NarrativeExecutionBoard(_drama_task())

    view = board.view(steps_used=0)
    assert view.pending_objectives == ["world_state", "planning", "shots"]
    assert view.completed_outputs == []
    assert view.filled_layers == {}
    assert view.steps_remaining == 10

    board.apply_tool_result(
        "narrative.drama.design_world",
        {"characters": [{"character_id": "c1"}], "world_state": {}},
    )
    view = board.view(steps_used=1)
    assert view.completed_outputs == ["world_state"]
    assert view.pending_objectives == ["planning", "shots"]
    assert view.filled_layers["world + world.cast"] == "characters=1"

    board.apply_tool_result(
        "narrative.drama.plan_story",
        {"premise": "p", "scenes": [{}, {}]},
    )
    view = board.view(steps_used=2)
    # plan_story fills a layer but planning is only satisfied by expand_scenes.
    assert "structure.scenes" in view.filled_layers
    assert view.pending_objectives == ["planning", "shots"]

    board.apply_tool_result("narrative.drama.expand_scenes", {"actions": [{}] * 6})
    board.apply_tool_result("narrative.drama.compile_shots", {"shots": [{}] * 6})
    view = board.view(steps_used=4)
    assert view.pending_objectives == []
    assert view.completed_outputs == ["world_state", "planning", "shots"]
    assert view.steps_remaining == 6


def test_progress_message_guides_next_step() -> None:
    board = NarrativeExecutionBoard(_drama_task())
    board.apply_tool_result(
        "narrative.drama.design_world",
        {"characters": [{"character_id": "c1"}]},
    )

    message = board.progress_message("narrative.drama.design_world", steps_used=1)
    assert "world + world.cast" in message
    assert "planning、shots" in message
    assert "已用 1/10 步" in message
    assert "narrative.submit_candidate" not in message

    board.apply_tool_result("narrative.drama.plan_story", {"scenes": [{}]})
    board.apply_tool_result("narrative.drama.expand_scenes", {"actions": [{}]})
    board.apply_tool_result("narrative.drama.compile_shots", {"shots": [{}]})
    message = board.progress_message("narrative.drama.compile_shots", steps_used=4)
    assert "narrative.submit_candidate" in message
    assert "未完成的必需产出：无" in message


def test_unknown_tool_result_is_ignored() -> None:
    board = NarrativeExecutionBoard(_drama_task())
    board.apply_tool_result("narrative.configure_task", {"status": "configured"})
    view = board.view(steps_used=0)
    assert view.filled_layers == {}
    assert view.pending_objectives == ["world_state", "planning", "shots"]
