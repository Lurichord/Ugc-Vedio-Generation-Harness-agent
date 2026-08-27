"""Editorial domain contracts for claims and A/B-roll visual requirements.

Execution moved to the unified GenericAgent; this package keeps the
editorial plan models and prompts used as its tools.
"""

from .models import (
    EditorialArtifact,
    EditorialPlan,
    EditorialQuality,
    ExplorationDirection,
    VisualRequirement,
)

__all__ = [
    "EditorialArtifact",
    "EditorialPlan",
    "EditorialQuality",
    "ExplorationDirection",
    "VisualRequirement",
]
