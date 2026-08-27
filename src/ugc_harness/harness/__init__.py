"""Contracts shared by harness controllers, agents, tools, and critics."""

from typing import Any

from ..content import CausalLink, VideoWorldState, WorldClaim, WorldEntity
from .models import (
    ActionRecord,
    AgentResult,
    ArtifactRef,
    CriticIssue,
    DependencyGraphState,
    DependencyNode,
    DependencySnapshot,
    ElementStatus,
    EvaluationResult,
    ExecutionState,
    ProjectState,
    RuntimeContext,
    StatePatch,
    TaskBudget,
    TaskEnvelope,
    TaskScope,
    TrajectoryState,
    TransitionRecord,
    VideoState,
)
from .description import VideoDescription, element_refs
from .description_builder import build_video_description, initial_execution_state
from .state_view import NarrativeExecutionBoard, StateView
from .dependencies import DependencyGraph
from .repair import RepairPlan, RepairScheduler
from .production_routes import ProductionRoute, resolve_production_route

# Controllers are exported lazily (PEP 562): they import the unified agent,
# which imports harness.models; importing them eagerly here would re-enter
# a partially initialized ugc_harness.agents.generic module.
_LAZY_EXPORTS = {
    "AssetHarnessController": ".asset_controller",
    "AssetHarnessRun": ".asset_controller",
    "ShotAssetArtifact": ".shot_asset_controller",
    "ShotAssetHarnessController": ".shot_asset_controller",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "ActionRecord",
    "AssetHarnessController",
    "AssetHarnessRun",
    "ProductionRoute",
    "resolve_production_route",
    "ShotAssetArtifact",
    "ShotAssetHarnessController",
    "AgentResult",
    "ArtifactRef",
    "CriticIssue",
    "DependencyGraph",
    "DependencyGraphState",
    "DependencyNode",
    "DependencySnapshot",
    "ElementStatus",
    "EvaluationResult",
    "ExecutionState",
    "NarrativeExecutionBoard",
    "StateView",
    "VideoDescription",
    "build_video_description",
    "element_refs",
    "initial_execution_state",
    "CausalLink",
    "ProjectState",
    "RuntimeContext",
    "RepairPlan",
    "RepairScheduler",
    "StatePatch",
    "TaskBudget",
    "TaskEnvelope",
    "TaskScope",
    "TrajectoryState",
    "TransitionRecord",
    "VideoState",
    "VideoWorldState",
    "WorldClaim",
    "WorldEntity",
]
