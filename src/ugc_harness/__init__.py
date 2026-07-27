"""UGC video generation harness."""

from .stage_one import CreativeBrief, StageOneArtifact, StageOnePipeline
from .stage_four import AssetAcquisitionPipeline, AssetStageArtifact
from .stage_five import TimelineCompositionPipeline, TimelineStageArtifact
from .stage_seven import (
    ImagePreparationPipeline,
    ImagePreparationStageArtifact,
)
from .stage_six import FinalRenderPipeline, RenderStageArtifact
from .stage_three import EditorialStageArtifact, EditorialStagePipeline
from .stage_two import VoiceStageArtifact, VoiceStagePipeline

__all__ = [
    "CreativeBrief",
    "AssetAcquisitionPipeline",
    "AssetStageArtifact",
    "TimelineCompositionPipeline",
    "TimelineStageArtifact",
    "ImagePreparationPipeline",
    "ImagePreparationStageArtifact",
    "FinalRenderPipeline",
    "RenderStageArtifact",
    "EditorialStageArtifact",
    "EditorialStagePipeline",
    "StageOneArtifact",
    "StageOnePipeline",
    "VoiceStageArtifact",
    "VoiceStagePipeline",
]
