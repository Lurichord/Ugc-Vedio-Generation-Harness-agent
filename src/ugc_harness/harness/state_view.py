"""Scoped state views for agents: look, propose, never write.

The controller owns ProjectState. During one task an agent works against an
ExecutionBoard: a task-local working copy that registers every successful
tool result, tracks which required outputs are satisfied, and renders a fresh
StateView after each tool call. The agent plans its next step from that view
instead of rereading its own chat history; the real ledger stays frozen until
the controller commits the approved candidate.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import TaskEnvelope


class StateView(BaseModel):
    """Read-only snapshot an agent sees between tool calls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_version: int = Field(ge=0)
    format_id: str
    filled_layers: dict[str, str] = Field(default_factory=dict)
    completed_outputs: list[str] = Field(default_factory=list)
    pending_objectives: list[str] = Field(default_factory=list)
    steps_used: int = Field(ge=0)
    steps_remaining: int = Field(ge=0)


class _ToolEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    satisfies: tuple[str, ...] = ()
    layer: str
    count_fields: tuple[str, ...] = ()


# Which description layer each tool proposes to fill, and which
# TaskEnvelope required_output a success satisfies. Mirrors the assembly
# logic of the stage tool servers.
_TOOL_EFFECTS: dict[str, _ToolEffect] = {
    "voice.create_plan": _ToolEffect(
        layer="voice.plan",
        count_fields=("segments",),
    ),
    "audio.synthesize_narration": _ToolEffect(
        satisfies=("voice_artifact",),
        layer="voice.realization",
        count_fields=("realized_beats",),
    ),
    "editorial.create_plan": _ToolEffect(
        satisfies=("editorial_plan",),
        layer="editorial.plan",
        count_fields=("visual_requirements",),
    ),
    "asset.acquire_requirement": _ToolEffect(
        layer="assets.acquired",
        count_fields=("acquired", "pending"),
    ),
    "asset.prepare_image": _ToolEffect(
        layer="assets.prepared",
        count_fields=("prepared", "pending"),
    ),
    "timeline.compose": _ToolEffect(
        satisfies=("timeline_candidate",),
        layer="timeline.plan",
        count_fields=("captions",),
    ),
    "render.execute": _ToolEffect(
        satisfies=("render_candidate",),
        layer="deliverable.render",
        count_fields=("outputs",),
    ),
    "narrative.explainer.plan_sections": _ToolEffect(
        satisfies=("world_state",),
        layer="world + structure.sections",
        count_fields=("sections",),
    ),
    "narrative.explainer.expand_beats": _ToolEffect(
        satisfies=("planning",),
        layer="structure.beats",
        count_fields=("beats",),
    ),
    "narrative.explainer.write_script": _ToolEffect(
        satisfies=("script",),
        layer="voice.utterances",
        count_fields=("segments",),
    ),
    "narrative.explainer.compile_shots": _ToolEffect(
        satisfies=("shots",),
        layer="shots",
        count_fields=("shots",),
    ),
    "narrative.drama.design_world": _ToolEffect(
        satisfies=("world_state",),
        layer="world + world.cast",
        count_fields=("characters",),
    ),
    "narrative.drama.plan_story": _ToolEffect(
        layer="structure.scenes",
        count_fields=("scenes",),
    ),
    "narrative.drama.expand_scenes": _ToolEffect(
        satisfies=("planning",),
        layer="structure.actions",
        count_fields=("actions",),
    ),
    "narrative.drama.compile_shots": _ToolEffect(
        satisfies=("shots",),
        layer="shots",
        count_fields=("shots",),
    ),
    "narrative.tutorial.define_result": _ToolEffect(
        satisfies=("world_state",),
        layer="world + structure.result",
        count_fields=("materials",),
    ),
    "narrative.tutorial.plan_steps": _ToolEffect(
        satisfies=("planning",),
        layer="structure.steps + structure.actions",
        count_fields=("steps", "actions"),
    ),
    "narrative.tutorial.plan_explanations": _ToolEffect(
        satisfies=("script",),
        layer="voice.utterances",
        count_fields=("segments",),
    ),
    "narrative.tutorial.compile_shots": _ToolEffect(
        satisfies=("shots",),
        layer="shots",
        count_fields=("shots",),
    ),
}


class ExecutionBoard:
    """Task-local working state for one unified tool loop."""

    def __init__(self, task: TaskEnvelope) -> None:
        self.task = task
        self._satisfied: set[str] = set()
        self._layers: dict[str, str] = {}

    def apply_tool_result(self, tool_name: str, value: dict[str, Any]) -> None:
        effect = _TOOL_EFFECTS.get(tool_name)
        if effect is None:
            return
        counts = []
        for field in effect.count_fields:
            items = value.get(field)
            if isinstance(items, list):
                counts.append(f"{field}={len(items)}")
        self._layers[effect.layer] = "、".join(counts) if counts else "已生成"
        self._satisfied.update(effect.satisfies)

    def view(self, *, steps_used: int) -> StateView:
        required = list(self.task.required_outputs)
        completed = [name for name in required if name in self._satisfied]
        pending = [name for name in required if name not in self._satisfied]
        return StateView(
            state_version=self.task.based_on_state_version,
            format_id=self.task.format_id or "auto",
            filled_layers=dict(self._layers),
            completed_outputs=completed,
            pending_objectives=pending,
            steps_used=steps_used,
            steps_remaining=max(self.task.budget.max_steps - steps_used, 0),
        )

    def progress_message(self, tool_name: str, *, steps_used: int) -> str:
        view = self.view(steps_used=steps_used)
        if view.filled_layers:
            filled = "；".join(
                f"{layer}（{summary}）"
                for layer, summary in view.filled_layers.items()
            )
        else:
            filled = "尚无"
        pending = (
            "、".join(view.pending_objectives)
            if view.pending_objectives
            else "无"
        )
        lines = [
            f"工具 {tool_name} 已成功。当前状态视图：",
            f"- 视频文档已填实的层：{filled}",
            f"- 未完成的必需产出：{pending}",
            f"- 预算：已用 {view.steps_used}/{self.task.budget.max_steps} 步，"
            f"剩余 {view.steps_remaining} 步",
        ]
        if view.pending_objectives:
            lines.append(
                "请根据状态视图规划下一步，优先补齐未完成产出；"
                "没有明确问题时不要无理由重复生成同一草稿。"
            )
        else:
            submit = next(
                (
                    name
                    for name in self.task.allowed_tools
                    if name.endswith(".submit_candidate")
                ),
                "submit_candidate",
            )
            lines.append(f"所有必需产出已就绪；请调用 {submit} 提交候选。")
        return "\n".join(lines)


# Backwards-compatible alias from the narrative-only phase.
NarrativeExecutionBoard = ExecutionBoard
