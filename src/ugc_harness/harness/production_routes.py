from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..agents.narrative_agent.models import NarrativeArtifact


ProductionFormat = Literal["explainer", "drama", "tutorial"]
AssetRoute = Literal["editorial_assets", "shot_ai_video"]


class ProductionRoute(BaseModel):
    """Harness-owned routing decision for downstream production stages."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_id: ProductionFormat
    asset_route: AssetRoute
    stages: tuple[str, ...]


def resolve_production_route(narrative: NarrativeArtifact) -> ProductionRoute:
    """Resolve a route from committed Shot contracts, not an Agent's plan."""

    shots = narrative.shots.shots if narrative.shots is not None else []
    if not shots:
        if narrative.brief.production_mode not in {"auto", "explainer"}:
            raise ValueError("non-explainer Narrative requires ProductionShots")
        return ProductionRoute(
            format_id="explainer",
            asset_route="editorial_assets",
            stages=("voice", "editorial", "asset", "timeline", "render"),
        )

    formats = {shot.shot_kind for shot in shots}
    if len(formats) != 1:
        raise ValueError("one Narrative artifact cannot mix production formats")
    format_id = formats.pop()
    if format_id == "explainer":
        return ProductionRoute(
            format_id="explainer",
            asset_route="editorial_assets",
            stages=("voice", "editorial", "asset", "timeline", "render"),
        )

    expected_visual = {
        "drama": "generated_scene",
        "tutorial": "procedure_demo",
    }[format_id]
    invalid = [
        shot.shot_id
        for shot in shots
        if shot.visual.realization_type != expected_visual
    ]
    if invalid:
        raise ValueError(
            f"{format_id} shots must use {expected_visual}: {', '.join(invalid)}"
        )
    return ProductionRoute(
        format_id=format_id,
        asset_route="shot_ai_video",
        stages=("asset", "timeline", "render"),
    )
