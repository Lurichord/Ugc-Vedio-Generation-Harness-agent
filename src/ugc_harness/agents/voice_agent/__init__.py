"""Voice domain contracts and deterministic audio capabilities.

Execution moved to the unified GenericAgent; this package keeps the voice
artifact models, planning, and TTS capabilities used as its tools.
"""

from .models import VoiceArtifact, VoicePlan, VoiceQuality

__all__ = [
    "VoiceArtifact",
    "VoicePlan",
    "VoiceQuality",
]
