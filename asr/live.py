"""Live words while the user speaks (Task 04): LocalAgreement-2 over repeated passes.

While a VAD utterance is open, the growing buffer is re-transcribed (no target prompt) about every
`interval` seconds. A word is committed when two consecutive passes agree on it (same normalized
words, in order, after the already committed ones). Committed words are final: they go to the
matcher and are drawn at once; uncommitted words are never shown. When the buffer is longer than
`trim_seconds`, it is cut after the last committed word, and the committed words of this
utterance become the pass prompt (the user's own words, not the target text). The VAD end
(0.6 s pause) is the hard end: a final pass commits everything that is left.

    live = LiveTranscriber(recognizer, Segmenter())
    for block in blocks:
        for commit in live.push(block):
            ...                     # commit.words, commit.audio, commit.before/after (check context)
    for commit in live.flush(): ...

Timing: `clock` measures display latency. RealClock is the wall clock (mic, or a WAV replayed in
real time). SimClock replays a WAV as fast as possible but keeps the time a live run would have:
audio arrives in real time and every pass/check adds its measured duration, so the latency and
the backlog are those of a real-time run without waiting for it.
"""

import time
from dataclasses import dataclass, field

import numpy as np

from matcher import normalize

from .audio import BLOCK_SECONDS, SAMPLE_RATE


class RealClock:
    def __init__(self):
        self.t0 = None

    def block(self, audio_t):
        if self.t0 is None:
            self.t0 = time.perf_counter() - audio_t

    def work(self, seconds):
        pass

    def now(self):
        return 0.0 if self.t0 is None else time.perf_counter() - self.t0


class SimClock:
    """Virtual time of a real-time run: max(audio time, busy until) + work."""

    def __init__(self):
        self.t = 0.0

    def block(self, audio_t):
        self.t = max(self.t, audio_t)

    def work(self, seconds):
        self.t += seconds

    def now(self):
        return self.t


@dataclass
class Commit:
    words: list  # asr.engine.Word, times in seconds from stream start
    audio: np.ndarray  # the pass audio these words were heard in (encoder output is cached for it)
    before: list[str] = field(default_factory=list)  # normalized words in `audio` before `words`
    after: list[str] = field(default_factory=list)  # uncommitted normalized words after them
    final: bool = False  # last commit of an utterance (VAD end)
    utterance: object = None  # final commits: the whole asr.vad.Utterance (for the re-align at the pause)


def key(word) -> str:
    return " ".join(normalize(word.word))


def agreed(a, b) -> int:
    """Length of the common prefix of two hypotheses (by normalized words)."""
    n = 0
    for x, y in zip(a, b):
        if key(x) != key(y):
            break
        n += 1
    return n


class LiveTranscriber:
    def __init__(self, recognizer, segmenter, interval=1.0, min_audio=0.5, trim_seconds=5.0, max_lag=0.5,
                 prompt_words=30, clock=None):
        self.rec = recognizer
        self.seg = segmenter
        self.interval = interval
        self.min_audio = min_audio
        self.trim_seconds = trim_seconds
        self.max_lag = max_lag
        self.prompt_words = prompt_words
        self.clock = clock or RealClock()
        self.audio_t = 0.0
        self.passes, self.skipped, self.pass_s, self.lags = 0, 0, [], []
        self._reset_utterance()

    def _reset_utterance(self):
        self._offset = 0  # samples trimmed from the start of the open utterance
        self._committed = []  # committed Words of this utterance (absolute times)
        self._in_buf = 0  # how many of them are still inside the buffer (after the trim point)
        self._prev = None  # uncommitted part of the last pass
        self._last_pass_at = 0.0  # audio length of the open utterance at the last pass

    # --- passes ---------------------------------------------------------------

    def _pass(self, audio, start):
        """Transcribe audio[offset:]; return (pass audio, new words after the committed ones)."""
        buf = audio[self._offset:]
        prompt = None
        trimmed = self._committed[: len(self._committed) - self._in_buf]
        if self._offset and trimmed:  # buffer was cut: this utterance's earlier words as context
            prompt = " ".join(w.word for w in trimmed[-self.prompt_words:])
        t0 = time.perf_counter()
        words = self.rec.transcribe_words(buf, initial_prompt=prompt)
        secs = time.perf_counter() - t0
        self.clock.work(secs)
        self.passes += 1
        self.pass_s.append(secs)
        shift = start + self._offset / SAMPLE_RATE
        words = [w._replace(start=w.start + shift, end=w.end + shift) for w in words if key(w)]
        # Drop the committed words that are still in the buffer: by count when the pass agrees
        # with them, else by time.
        inbuf = self._committed[len(self._committed) - self._in_buf:]
        if inbuf:
            n = agreed(inbuf, words)
            if n == len(inbuf):
                words = words[n:]
            else:
                end = inbuf[-1].end
                words = [w for w in words if (w.start + w.end) / 2 > end]
        return buf, words

    def _commit(self, words, buf, rest, final):
        inbuf = self._committed[len(self._committed) - self._in_buf:]
        c = Commit(list(words), buf, [k for w in inbuf for k in normalize(w.word)],
                   [k for w in rest for k in normalize(w.word)], final)
        self._committed += words
        self._in_buf += len(words)
        return c

    def _trim(self, audio, start):
        """Cut the buffer after the last committed word when it got long."""
        if not self._in_buf or (len(audio) - self._offset) / SAMPLE_RATE < self.trim_seconds:
            return
        last = self._committed[-1]
        nxt = self._prev[0].start if self._prev else last.end
        cut_t = (last.end + max(last.end, min(nxt, last.end + 0.2))) / 2
        cut = int(round((cut_t - start) * SAMPLE_RATE))
        if self._offset < cut < len(audio):
            self._offset = cut
            self._in_buf = 0

    def _live_pass(self):
        cur = self.seg.current()
        if cur is None:
            return []
        audio, start = cur
        length = len(audio) / SAMPLE_RATE
        if length - self._offset / SAMPLE_RATE < self.min_audio or length - self._last_pass_at < self.interval:
            return []
        lag = self.clock.now() - self.audio_t
        self.lags.append(lag)
        if lag > self.max_lag:  # behind real time: let the queue drain first
            self.skipped += 1
            return []
        self._last_pass_at = length
        buf, words = self._pass(audio, start)
        out = []
        if self._prev is not None:
            n = agreed(self._prev, words)
            if n:
                out.append(self._commit(words[:n], buf, words[n:], final=False))
                words = words[n:]
        self._prev = words
        self._trim(audio, start)
        return out

    def _final(self, utt):
        """VAD end: transcribe what is left and commit all of it."""
        out = []
        if self._offset < len(utt.audio):
            buf, words = self._pass(utt.audio, utt.start)
            if words:
                out.append(self._commit(words, buf, [], final=True))
        if out:
            out[-1].final = True
        elif self._committed:
            out.append(Commit([], utt.audio[self._offset:], final=True))
        if out:
            out[-1].utterance = utt
        carry = self._offset - len(utt.audio)  # forced cut inside the trimmed part
        # Forced cut (25 s cap): words already committed after the cut are in the next utterance's
        # audio too; keep them as "committed, in buffer" so the next passes drop them.
        keep = [w for w in self._committed if w.end > utt.end] if utt.forced_cut else []
        self._reset_utterance()
        if carry > 0:
            self._offset = carry
        self._committed, self._in_buf = keep, len(keep)
        return out

    # --- public -----------------------------------------------------------------

    def push(self, block):
        """Feed one audio block; returns the Commits it caused (usually none)."""
        self.audio_t += BLOCK_SECONDS
        self.clock.block(self.audio_t)
        out = []
        for utt in self.seg.push(block):
            out += self._final(utt)
        return out + self._live_pass()

    def flush(self):
        out = []
        for utt in self.seg.flush():
            out += self._final(utt)
        return out

    def stats(self):
        lags = self.lags or [0.0]
        return {"passes": self.passes, "skipped_passes": self.skipped,
                "pass_ms_mean": 1000 * float(np.mean(self.pass_s)) if self.pass_s else 0.0,
                "lag_max_s": float(max(lags)), "lag_end_s": float(self.clock.now() - self.audio_t)}
