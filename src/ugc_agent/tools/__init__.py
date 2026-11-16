from . import extract_video_frame as extract_video_frame_tool
from . import image_gen as image_gen_tool
from . import merge_video as merge_video_tool
from . import split_audio as split_audio_tool
from . import text_to_speech as text_to_speech_tool
from . import video_gen as video_gen_tool
from . import video_gen_minimax as video_gen_minimax_tool
from . import vision_understanding as vision_understanding_tool
from .state import PROJECT_TOOLS

AGENT_TOOLS = [
    *PROJECT_TOOLS,
    vision_understanding_tool.vision_understanding,
    image_gen_tool.image_gen,
    video_gen_tool.video_gen,
    video_gen_minimax_tool.video_gen_minimax,
    text_to_speech_tool.text_to_speech,
    extract_video_frame_tool.extract_video_frame,
    split_audio_tool.split_audio,
    merge_video_tool.merge_video,
]

__all__ = ["AGENT_TOOLS", "PROJECT_TOOLS"]
