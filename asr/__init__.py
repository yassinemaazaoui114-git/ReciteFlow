"""ReciteFlow audio -> words pipeline. `engine` (faster-whisper) and `stream` are not imported
here, so the VAD and audio parts stay usable and testable without CUDA:
    from asr.engine import Recognizer
    from asr.stream import stream
"""

from .audio import BLOCK_SIZE, SAMPLE_RATE, MicSource, from_wav, load_wav, to_blocks
from .vad import Segmenter, SileroProb, Utterance

__all__ = [
    "SAMPLE_RATE",
    "BLOCK_SIZE",
    "MicSource",
    "from_wav",
    "load_wav",
    "to_blocks",
    "Segmenter",
    "SileroProb",
    "Utterance",
]
