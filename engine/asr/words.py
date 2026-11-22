"""Word with timing, shared by the engine and the live pipeline (no GPU imports)."""

from typing import NamedTuple


class Word(NamedTuple):
    word: str
    start: float  # seconds from the start of the transcribed audio (asr.live: from stream start)
    end: float
    probability: float
