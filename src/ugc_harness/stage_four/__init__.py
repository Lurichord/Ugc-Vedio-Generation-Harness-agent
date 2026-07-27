"""Stage four: first-success asset acquisition."""

from .models import AssetStageArtifact
from .pipeline import AssetAcquisitionPipeline

__all__ = ["AssetAcquisitionPipeline", "AssetStageArtifact"]
