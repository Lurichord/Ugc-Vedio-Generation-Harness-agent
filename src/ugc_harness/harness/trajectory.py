from __future__ import annotations

from .models import (
    AgentResult,
    EvaluationResult,
    GraphUpdateRecord,
    PhaseTrajectory,
    TaskEnvelope,
    TaskKind,
    TaskTrajectoryRecord,
    TrajectoryState,
    TransitionRecord,
)


def task_kind_for(trajectory: TrajectoryState, phase: str) -> TaskKind:
    phase_state = trajectory.phases.get(phase)
    return "revision" if phase_state and phase_state.tasks else "generation"


def record_task(
    trajectory: TrajectoryState,
    *,
    phase: str,
    task_kind: TaskKind,
    task: TaskEnvelope,
    agent_result: AgentResult,
    evaluation: EvaluationResult,
    transition: TransitionRecord,
    graph_update: GraphUpdateRecord,
) -> None:
    phase_state = trajectory.phases.setdefault(
        phase,
        PhaseTrajectory(phase=phase),
    )
    phase_state.tasks.append(
        TaskTrajectoryRecord(
            task_kind=task_kind,
            task=task,
            agent_result=agent_result,
            evaluation=evaluation,
            transition=transition,
            graph_update=graph_update,
        )
    )
