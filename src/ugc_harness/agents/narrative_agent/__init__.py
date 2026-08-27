"""Narrative domain contracts. Execution lives in the unified GenericAgent."""

# Content-plane models now live in ugc_harness.content; these leftover
# re-exports are unused after the VideoDescription / GenericAgent migration.
# from ...content import (
#     AudioRealizationSpec,
#     ProductionMode,
#     ProductionShot,
#     TimingSpec,
#     VisualRealizationSpec,
# )
from .brief import make_brief
from .models import (
    CreativeBrief,
    DramaPlanningArtifact,
    NarrativePlanningArtifact,
    NarrativeScriptArtifact,
    PlanningArtifact,
    ScriptArtifact,
    TutorialPlanningArtifact,
    TutorialScriptArtifact,
    NarrativeArtifact,
)

__all__ = [
    # "AudioRealizationSpec",
    "CreativeBrief",
    "DramaPlanningArtifact",
    "NarrativePlanningArtifact",
    "NarrativeScriptArtifact",
    "PlanningArtifact",
    # "ProductionMode",
    # "ProductionShot",
    "ScriptArtifact",
    "TutorialPlanningArtifact",
    "TutorialScriptArtifact",
    # "TimingSpec",
    # "VisualRealizationSpec",
    "NarrativeArtifact",
    "make_brief",
]
