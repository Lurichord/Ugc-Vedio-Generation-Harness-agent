from dataclasses import dataclass

from .store import ProjectStore


@dataclass(frozen=True)
class AgentContext:
    project_id: str
    turn_id: str
    project_store: ProjectStore
