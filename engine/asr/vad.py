"""Group audio blocks into utterances with Silero VAD.

An utterance starts when a block's speech probability reaches `threshold` and ends after
`min_silence` seconds below `threshold - 0.15` (hysteresis, as in Silero's own helper).
`padding` seconds of audio are kept before and after the speech. Utterances longer than
`max_seconds` are force-cut at the quietest block in their second half; the rest carries
over into the next utterance.
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from .audio import BLOCK_SECONDS, BLOCK_SIZE, SAMPLE_RATE


@dataclass
class Utterance:
    audio: np.ndarray  # float32, 16 kHz, padding included
    start: float  # seconds from stream start, padding included
    end: float
    forced_cut: bool = False  # True if cut by the max_seconds cap rather than a pause


class SileroProb:
    """Callable block -> speech probability, using Silero VAD on CPU."""

    def __init__(self):
        import warnings

        import torch
        from silero_vad import load_silero_vad

        torch.set_num_threads(1)
        self._torch = torch
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # torch.jit.load deprecation
            self.model = load_silero_vad()

    def __call__(self, block):
        with self._torch.no_grad():
            return float(self.model(self._torch.from_numpy(block), SAMPLE_RATE).item())

    def reset(self):
        self.model.reset_states()


class Segmenter:
    def __init__(
        self,
        speech_prob=None,
        threshold=0.5,
        min_silence=0.6,
        padding=0.2,
        max_seconds=25.0,
        min_speech=0.2,
    ):
        self.speech_prob = speech_prob if speech_prob is not None else SileroProb()
        self.threshold = threshold
        self.neg_threshold = max(threshold - 0.15, 0.01)
        self.silence_blocks = max(1, round(min_silence / BLOCK_SECONDS))
        self.pad_blocks = round(padding / BLOCK_SECONDS)
        self.max_blocks = round(max_seconds / BLOCK_SECONDS)
        self.min_speech_blocks = max(1, round(min_speech / BLOCK_SECONDS))
        self.reset()

    def reset(self):
        if hasattr(self.speech_prob, "reset"):
            self.speech_prob.reset()
        self._index = 0  # blocks seen so far
        self._preroll = deque(maxlen=self.pad_blocks)  # (block, prob) before speech starts
        self._blocks = []  # current utterance: list of (block, prob)
        self._first = 0  # stream block index of self._blocks[0]
        self._last_voiced = -1  # index into self._blocks of the last voiced block
        self._voiced = 0
        self._silence = 0

    # --- public -----------------------------------------------------------

    def push(self, block):
        """Feed one block; returns a list of finished Utterances (usually empty)."""
        prob = self.speech_prob(block)
        index = self._index
        self._index += 1
        out = []

        if not self._blocks:
            if prob >= self.threshold:
                self._blocks = list(self._preroll)
                self._first = index - len(self._blocks)
                self._preroll.clear()
                self._append(block, prob, voiced=True)
            elif self.pad_blocks:
                self._preroll.append((block, prob))
            return out

        self._append(block, prob, voiced=prob >= self.neg_threshold)
        if self._silence >= self.silence_blocks:
            out.extend(self._finish())
        elif len(self._blocks) >= self.max_blocks:
            out.extend(self._force_cut())
        return out

    def flush(self):
        """End of stream: return the utterance in progress, if any."""
        out = self._finish() if self._blocks else []
        self.reset()
        return out

    def current(self):
        """(audio so far, start seconds) of the open utterance, or None (used by asr.live)."""
        if not self._blocks:
            return None
        return np.concatenate([b for b, _ in self._blocks]), self._first * BLOCK_SIZE / SAMPLE_RATE

    def feed(self, blocks):
        """Convenience: run a whole block iterable through push/flush."""
        for block in blocks:
            yield from self.push(block)
        yield from self.flush()

    # --- internals ----------------------------------------------------------

    def _append(self, block, prob, voiced):
        self._blocks.append((block, prob))
        if voiced:
            self._voiced += 1
            self._silence = 0
            self._last_voiced = len(self._blocks) - 1
        else:
            self._silence += 1

    def _make(self, blocks, first, forced):
        audio = np.concatenate([b for b, _ in blocks])
        start = first * BLOCK_SIZE / SAMPLE_RATE
        return Utterance(audio, start, start + len(audio) / SAMPLE_RATE, forced)

    def _finish(self):
        """Pause reached (or end of stream): emit speech + trailing padding."""
        end = min(len(self._blocks), self._last_voiced + 1 + self.pad_blocks)
        blocks, first, voiced = self._blocks[:end], self._first, self._voiced
        # Trailing silence beyond the padding becomes pre-roll for the next utterance.
        tail = self._blocks[end:]
        self._blocks, self._last_voiced, self._voiced, self._silence = [], -1, 0, 0
        self._preroll.clear()
        self._preroll.extend(tail[-self.pad_blocks :] if self.pad_blocks else [])
        if voiced < self.min_speech_blocks:
            return []
        return [self._make(blocks, first, forced=False)]

    def _force_cut(self):
        """Cap reached: cut after the quietest block in the second half."""
        half = len(self._blocks) // 2
        scores = [
            (prob, float(np.sqrt(np.mean(block**2))))
            for block, prob in self._blocks[half:]
        ]
        cut = half + min(range(len(scores)), key=lambda i: scores[i]) + 1
        head, rest = self._blocks[:cut], self._blocks[cut:]
        utt = self._make(head, self._first, forced=True)
        self._first += cut
        self._blocks = rest
        self._voiced = sum(1 for _, p in rest if p >= self.neg_threshold)
        self._last_voiced = max(
            (i for i, (_, p) in enumerate(rest) if p >= self.neg_threshold), default=-1
        )
        return [utt]
