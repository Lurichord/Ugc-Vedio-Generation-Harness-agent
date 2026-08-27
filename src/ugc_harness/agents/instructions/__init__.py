"""Stage instruction files (the per-task "agent.md") for the unified agent."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parent


def load_instructions(name: str) -> str:
    """Load one stage's instruction markdown, injected as agent_instructions."""

    return (_ROOT / f"{name}.md").read_text(encoding="utf-8")
