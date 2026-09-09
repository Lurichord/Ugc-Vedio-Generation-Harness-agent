from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path

from pydantic import Field

from .dependencies import DependencyGraph, NodeCommit, semantic_hash
from .narrative_formats import default_narrative_format_registry
from .models import (
    DependencySnapshot,
    HarnessModel,
    ProjectState,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
)


class RepairBlocker(HarnessModel):
    ref: str
    reason: str


class RepairPlan(HarnessModel):
    desired_refs: list[str]
    tasks: list[TaskEnvelope] = Field(default_factory=list)
    blockers: list[RepairBlocker] = Field(default_factory=list)
    complete: bool = False


class RepairRunResult(HarnessModel):
    ok: bool
    complete: bool = False
    dry_run: bool = False
    target_refs: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)
    planned_tasks: int = 0
    rounds: int = 0
    tasks_run: list[str] = Field(default_factory=list)
    blockers: list[RepairBlocker] = Field(default_factory=list)
    error: str | None = None


AGENT_STAGE: dict[str, str] = {
    "narrative_agent": "narrative",
    "voice_agent": "voice",
    "editorial_agent": "editorial",
    "asset_agent": "asset",
    "timeline_agent": "timeline",
    "render_agent": "render",
}


_NARRATIVE_FORMATS = default_narrative_format_registry()


_TOOLS: dict[str, list[str]] = {
    "narrative_agent": [
        *_NARRATIVE_FORMATS.capability_tools,
    ],
    "voice_agent": [
        "voice.create_plan",
        "audio.synthesize_narration",
        "voice.submit_candidate",
    ],
    "editorial_agent": ["editorial.create_plan", "editorial.submit_candidate"],
    "asset_agent": [
        "asset.acquire_requirement",
        "asset.prepare_image",
        "asset.submit_candidate",
    ],
    "timeline_agent": ["timeline.compose", "timeline.submit_candidate"],
    "render_agent": ["render.execute", "render.submit_candidate"],
}


class RepairScheduler:
    """Plan the next runnable frontier of a stale dependency subgraph."""

    def plan(
        self,
        state: ProjectState,
        desired_refs: list[str],
    ) -> RepairPlan:
        graph = DependencyGraph(state.dependency_graph)
        blockers: list[RepairBlocker] = []
        for ref in desired_refs:
            if ref not in state.dependency_graph.nodes:
                blockers.append(RepairBlocker(ref=ref, reason="missing target node"))
        if blockers:
            return RepairPlan(desired_refs=desired_refs, blockers=blockers)

        relevant = self._stale_ancestors(state, desired_refs)
        if not relevant:
            return RepairPlan(desired_refs=desired_refs, complete=True)

        locked = sorted(
            ref for ref in relevant if state.dependency_graph.nodes[ref].locked
        )
        if locked:
            return RepairPlan(
                desired_refs=desired_refs,
                blockers=[
                    RepairBlocker(ref=ref, reason="stale node is locked")
                    for ref in locked
                ],
            )

        frontier = {
            ref
            for ref in relevant
            if all(
                dependency not in relevant
                for dependency in state.dependency_graph.nodes[ref].depends_on
            )
        }
        by_agent: dict[str, set[str]] = {}
        for ref in frontier:
            agent = state.dependency_graph.nodes[ref].produced_by
            if not agent or agent not in _TOOLS:
                blockers.append(
                    RepairBlocker(
                        ref=ref,
                        reason=f"no repair executor registered for {agent!r}",
                    )
                )
                continue
            by_agent.setdefault(agent, set()).add(ref)

        tasks: list[TaskEnvelope] = []
        for agent, seeds in sorted(by_agent.items()):
            targets = self._expand_same_agent_branch(
                state,
                relevant,
                agent,
                seeds,
            )
            required = sorted(
                {
                    dependency
                    for ref in targets
                    for dependency in state.dependency_graph.nodes[ref].depends_on
                    if dependency not in targets
                }
            )
            if not graph.dependencies_satisfied(required):
                blockers.append(
                    RepairBlocker(
                        ref=",".join(sorted(targets)),
                        reason="repair inputs are not current",
                    )
                )
                continue
            snapshots = graph.snapshot(required)
            scope = self._scope(state, targets)
            task_id = self._task_id(
                agent,
                targets,
                state.context.state_version,
                state.dependency_graph.graph_version,
            )
            if agent == "narrative_agent":
                mode = state.context.constraints.get(
                    "narrative_format",
                    state.context.constraints.get(
                        "production_mode",
                        "auto",
                    ),
                )
                if not isinstance(mode, str):
                    mode = "auto"
                pack = _NARRATIVE_FORMATS.resolve(mode)
                tasks.append(
                    pack.create_repair_task(
                        task_id=task_id,
                        scope=scope,
                        state_version=state.context.state_version,
                        input_hash=repair_input_hash(
                            snapshots,
                            sorted(targets),
                        ),
                        dependency_snapshot=snapshots,
                    )
                )
                continue
            tasks.append(
                TaskEnvelope(
                    task_id=task_id,
                    agent=agent,
                    goal=(
                        "局部修复失效依赖节点，保持 scope 外的已批准内容不变"
                    ),
                    scope=scope,
                    based_on_state_version=state.context.state_version,
                    allowed_tools=_TOOLS[agent],
                    forbidden_actions=[
                        "modify_outside_repair_scope",
                        "consume_stale_dependency",
                        "overwrite_locked_node",
                    ],
                    acceptance_criteria=[
                        "所有 target_refs 重新变为 current",
                        "scope 外节点的语义 hash 不变",
                        "输出通过所属领域的独立 Critic",
                    ],
                    budget=TaskBudget(
                        max_steps=8 if agent == "narrative_agent" else 4,
                        max_retries=1,
                        fallback_policy="use_best_available",
                    ),
                    input_hash=repair_input_hash(snapshots, sorted(targets)),
                    dependency_snapshot=snapshots,
                )
            )
        return RepairPlan(
            desired_refs=desired_refs,
            tasks=tasks,
            blockers=blockers,
        )

    @staticmethod
    def _stale_ancestors(
        state: ProjectState,
        desired_refs: list[str],
    ) -> set[str]:
        relevant: set[str] = set()
        queue = list(desired_refs)
        visited: set[str] = set()
        while queue:
            ref = queue.pop()
            if ref in visited:
                continue
            visited.add(ref)
            node = state.dependency_graph.nodes[ref]
            if node.status == "stale":
                relevant.add(ref)
            queue.extend(node.depends_on)
        return relevant

    @staticmethod
    def _expand_same_agent_branch(
        state: ProjectState,
        relevant: set[str],
        agent: str,
        seeds: set[str],
    ) -> set[str]:
        selected = set(seeds)
        changed = True
        while changed:
            changed = False
            for ref in relevant - selected:
                node = state.dependency_graph.nodes[ref]
                stale_dependencies = {
                    dep for dep in node.depends_on if dep in relevant
                }
                if (
                    node.produced_by == agent
                    and stale_dependencies
                    and stale_dependencies <= selected
                ):
                    selected.add(ref)
                    changed = True
        return selected

    @staticmethod
    def _scope(state: ProjectState, refs: set[str]) -> TaskScope:
        beat_ids: set[str] = set()
        script_ids: set[str] = set()
        shot_ids: set[str] = set()
        queue = list(refs)
        visited: set[str] = set()
        while queue:
            ref = queue.pop()
            if ref in visited:
                continue
            visited.add(ref)
            prefix, _, identifier = ref.partition(":")
            if prefix in {"beat", "scene", "step"}:
                beat_ids.add(identifier)
            elif prefix in {"utterance", "audio"}:
                script_ids.add(identifier)
            elif prefix == "shot":
                shot_ids.add(identifier)
            node = state.dependency_graph.nodes.get(ref)
            if node:
                queue.extend(node.depends_on)
        return TaskScope(
            project_id=state.context.project_id,
            shot_ids=sorted(shot_ids),
            beat_ids=sorted(beat_ids),
            script_segment_ids=sorted(script_ids),
            target_refs=sorted(refs),
        )

    @staticmethod
    def _task_id(
        agent: str,
        refs: set[str],
        state_version: int,
        graph_version: int,
    ) -> str:
        digest = hashlib.sha256("\n".join(sorted(refs)).encode()).hexdigest()[:10]
        return f"task_repair_{agent}_{digest}_s{state_version}_g{graph_version}"


def repair_input_hash(
    snapshots: list[DependencySnapshot],
    target_refs: list[str],
) -> str:
    payload = {
        "dependencies": [
            {"ref": item.ref, "version": item.version}
            for item in snapshots
        ],
        "target_refs": sorted(target_refs),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def select_repair_commits(
    graph: DependencyGraph,
    commits: list[NodeCommit],
    task: TaskEnvelope,
) -> list[NodeCommit]:
    targets = set(task.scope.target_refs)
    if not targets:
        return commits
    candidates = {item.ref: item for item in commits}
    missing = targets - set(candidates)
    if missing:
        raise ValueError(f"repair output is missing target refs: {sorted(missing)}")
    unauthorized_changes: list[str] = []
    for ref, candidate in candidates.items():
        if ref in targets:
            continue
        previous = graph.state.nodes.get(ref)
        if previous is None or previous.content_hash != semantic_hash(candidate.value):
            unauthorized_changes.append(ref)
    if unauthorized_changes:
        raise ValueError(
            "REPAIR_SCOPE_VIOLATION: outputs changed outside target_refs: "
            f"{sorted(unauthorized_changes)}"
        )
    return [candidates[ref] for ref in sorted(targets)]


def invalidate_subgraph(state: ProjectState, seeds: Iterable[str]) -> list[str]:
    """Mark seeds and all dependents stale. Locked nodes abort the whole mark."""

    queue = list(seeds)
    visited: set[str] = set()
    while queue:
        ref = queue.pop(0)
        if ref in visited:
            continue
        node = state.dependency_graph.nodes.get(ref)
        if node is None:
            raise ValueError(f"unknown repair target: {ref}")
        if node.locked:
            raise ValueError(f"dependency node is locked: {ref}")
        visited.add(ref)
        node.status = "stale"
        queue.extend(node.dependents)
    return sorted(visited)


on_project_state_written = None


def write_project_state(project_dir: str | Path, state: ProjectState) -> None:
    path = Path(project_dir) / "harness" / "project_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    if on_project_state_written is not None:
        on_project_state_written(Path(project_dir), state)


def load_project_state(project_dir: str | Path) -> ProjectState:
    path = Path(project_dir) / "harness" / "project_state.json"
    return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))


def drive_repair(
    *,
    desired_refs: list[str],
    instruction: str,
    load_state: Callable[[], ProjectState],
    run_task: Callable[[TaskEnvelope, str], None],
    max_rounds: int = 12,
) -> RepairRunResult:
    """Run the next stale frontier until desired_refs are current."""

    tasks_run: list[str] = []
    scheduler = RepairScheduler()
    for round_index in range(1, max_rounds + 1):
        plan = scheduler.plan(load_state(), desired_refs)
        if plan.blockers:
            return RepairRunResult(
                ok=False,
                target_refs=desired_refs,
                rounds=round_index - 1,
                tasks_run=tasks_run,
                planned_tasks=len(plan.tasks),
                blockers=plan.blockers,
                error="；".join(item.reason for item in plan.blockers),
            )
        if plan.complete:
            return RepairRunResult(
                ok=True,
                complete=True,
                target_refs=desired_refs,
                rounds=round_index - 1,
                tasks_run=tasks_run,
            )
        if not plan.tasks:
            return RepairRunResult(
                ok=False,
                target_refs=desired_refs,
                rounds=round_index - 1,
                tasks_run=tasks_run,
                error="repair scheduler produced no runnable task",
            )
        for task in plan.tasks:
            run_task(task, instruction)
            tasks_run.append(task.task_id)
    return RepairRunResult(
        ok=False,
        target_refs=desired_refs,
        rounds=max_rounds,
        tasks_run=tasks_run,
        error=f"repair did not converge within {max_rounds} scheduler rounds",
    )
