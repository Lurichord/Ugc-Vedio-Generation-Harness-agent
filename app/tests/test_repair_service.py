from __future__ import annotations

from pathlib import Path

from app.backend.project_service import ProjectService
from app.backend.repair_service import RepairService
from app.backend.repository import AppDataRepository
from app.backend.schemas import FeedbackRecord
from tests.test_intake import _write_demo_project
from tests.test_repair import commit_repair_targets
from ugc_harness.harness.models import TaskEnvelope
from ugc_harness.harness.repair import load_project_state


class _FakeStageRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, list[str]]] = []

    def run(
        self,
        stage: str,
        project_dir: Path,
        options,
        *,
        task: TaskEnvelope | None = None,
        feedback: str | None = None,
    ) -> None:
        assert task is not None
        self.calls.append((stage, feedback, list(task.scope.target_refs)))
        commit_repair_targets(project_dir, task)


def test_repair_service_drives_the_same_loop(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_demo_project(project_dir)
    repository = AppDataRepository(tmp_path / "appdata")
    projects = ProjectService(tmp_path, repository)
    repository.save_feedback(
        project_dir.name,
        FeedbackRecord(
            feedback_id="fb1",
            stage="narrative",
            beat_id="b3",
            target_ref="planned_beat:b3",
            node_version=1,
            instruction="收成两句",
        ),
    )
    runner = _FakeStageRunner()
    RepairService(projects, repository, runner).run(project_dir, "fb1")

    stored = repository.feedback(project_dir.name)[0]
    assert stored.status == "resolved"
    assert stored.resolved_at
    assert [(stage, refs) for stage, _feedback, refs in runner.calls] == [
        ("narrative", ["shot:shot_b3"]),
    ]
    state = load_project_state(project_dir)
    assert state.dependency_graph.nodes["beat:b3"].status == "current"
    assert state.dependency_graph.nodes["shot:shot_b3"].status == "current"
    assert state.dependency_graph.nodes["audio:ss3"].status == "current"


def test_repair_service_reopens_feedback_when_runner_fails(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    _write_demo_project(project_dir)
    repository = AppDataRepository(tmp_path / "appdata")
    projects = ProjectService(tmp_path, repository)
    repository.save_feedback(
        project_dir.name,
        FeedbackRecord(
            feedback_id="fb1",
            stage="narrative",
            beat_id="b3",
            target_ref="beat:b3",
            node_version=1,
            instruction="收成两句",
        ),
    )

    class _Boom:
        def run(self, *args, **kwargs) -> None:
            raise RuntimeError("stage failed")

    try:
        RepairService(projects, repository, _Boom()).run(project_dir, "fb1")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected runner failure")
    assert repository.feedback(project_dir.name)[0].status == "open"
