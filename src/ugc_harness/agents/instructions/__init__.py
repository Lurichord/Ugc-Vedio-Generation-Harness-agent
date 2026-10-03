"""Stage instruction files (the per-task agent.md) for the unified agent.

GenericAgent picks the file from the current TaskEnvelope: ``agent`` is the
step, and narrative additionally uses ``format_id``. Controllers do not inject
the markdown text into the envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...harness.models import TaskEnvelope

_ROOT = Path(__file__).parent

_AGENT_FILES = {
    "references_agent": "references",
    "asset_stage": "shot_asset",
    "asset_agent": "asset",
    "voice_agent": "voice",
    "editorial_agent": "editorial",
    "timeline_agent": "timeline",
    "render_agent": "render",
}


def load_instructions(name: str) -> str:
    """Load one stage's instruction markdown."""

    path = _ROOT / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"missing instruction file: {path.name}")
    return path.read_text(encoding="utf-8")


def instruction_name_for_task(task: TaskEnvelope) -> str:
    """Which instruction markdown the current task should run under."""

    if (
        "repair.submit_candidate" in task.allowed_tools
        or task.create_shot is not None
        or "patches" in task.required_outputs
    ):
        return "shot_repair"
    if task.agent == "narrative_agent":
        format_id = task.format_id
        if format_id in {"explainer", "drama", "tutorial"}:
            return f"narrative_{format_id}"
        raise ValueError(
            f"narrative task needs format_id explainer/drama/tutorial, "
            f"got {format_id!r}"
        )
    name = _AGENT_FILES.get(task.agent)
    if name is None:
        raise ValueError(f"no instruction file for agent {task.agent!r}")
    return name


def system_prompt_for_task(task: TaskEnvelope) -> str:
    """System prompt for one GenericAgent run: the md of the current step."""

    return load_instructions(instruction_name_for_task(task))


def compose_opening_messages(
    *,
    system: str,
    agent: str,
    memory: Any | None,
    user_payload: Any,
) -> list[dict[str, Any]]:
    """Opening messages: system prompt, then optional context / artifacts."""

    from ..skills import list_agent_skills

    catalog = list_agent_skills(agent)
    catalog_block = (
        f"\n\n## Skill 目录\n{json.dumps(catalog, ensure_ascii=False, indent=2)}"
    )
    if (
        isinstance(memory, dict)
        and "context" in memory
        and "artifacts" in memory
    ):
        return [
            {"role": "system", "content": f"{system}{catalog_block}"},
            {
                "role": "user",
                "content": json.dumps(
                    {"上下文": memory["context"]},
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"叙事产物": memory["artifacts"]},
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ]
    text = system
    if memory is not None:
        text = (
            f"{system}{catalog_block}\n\n"
            f"## 当前 memory\n{json.dumps(memory, ensure_ascii=False, indent=2)}"
        )
    elif catalog_block:
        text = f"{system}{catalog_block}"
    return [
        {"role": "system", "content": text},
        {
            "role": "user",
            "content": json.dumps(
                user_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
