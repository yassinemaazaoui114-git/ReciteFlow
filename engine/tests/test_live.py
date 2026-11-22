"""Live mode (asr.live): LocalAgreement-2 commits and buffer trimming, no GPU.

The fake audio's sample values ARE the stream time in seconds, so the fake recognizer knows which
part of the stream a pass got and returns the scripted words inside it (times relative to the
buffer, like Whisper). The word ending last in a buffer is unstable (returned wrong) until the
buffer is 0.5 s past it, so LocalAgreement must never commit it early.
"""

import numpy as np

from asr import BLOCK_SIZE, SAMPLE_RATE, Segmenter, to_blocks
from asr.words import Word
from asr.live import LiveTranscriber, SimClock, agreed

SPEECH = (0.5, 8.8)  # seconds of "speech" in the stream
SCRIPT = [(f"w{i}", 0.6 + 0.5 * i, 0.6 + 0.5 * i + 0.4) for i in range(15)]  # 15 words, 0.6 .. 7.9 s


def stream(seconds=10.0):
    return (np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE).astype(np.float32)


def speech_prob(block):
    t = float(block[0])
    return 1.0 if SPEECH[0] <= t <= SPEECH[1] and block[-1] > 0 else 0.0


class ScriptRecognizer:
    def __init__(self):
        self.calls = []  # (buffer start, buffer end, prompt)

    def transcribe_words(self, audio, initial_prompt=None):
        a, b = float(audio[0]), float(audio[-1])
        self.calls.append((a, b, initial_prompt))
        out = []
        for w, s, e in SCRIPT:
            if s >= a - 0.05 and e <= b:
                stable = b - e >= 0.5
                out.append(Word(w if stable else "zz", s - a, e - a, 0.9))
        return out


def run(**kw):
    rec = ScriptRecognizer()
    live = LiveTranscriber(rec, Segmenter(speech_prob=speech_prob, padding=0.0), clock=SimClock(), **kw)
    commits = []
    for block in to_blocks(stream()):
        for c in live.push(block):
            c.passes = live.passes  # passes run when this commit was made
            commits.append(c)
    commits += live.flush()
    return rec, live, commits


def test_agreed_prefix():
    w = lambda *xs: [Word(x, 0, 0, 1) for x in xs]  # noqa: E731
    assert agreed(w("one", "red", "cat"), w("one", "red", "dog")) == 2
    assert agreed(w("Ten,"), w("10")) == 1  # compared after normalization
    assert agreed(w("cat"), []) == 0


def test_every_word_committed_once_in_order_never_unstable():
    rec, live, commits = run(trim_seconds=100)
    words = [w.word for c in commits for w in c.words]
    assert words == [w for w, _, _ in SCRIPT]
    assert "zz" not in words
    assert sum(c.final for c in commits) == 1 and commits[-1].final
    assert len(commits) > 3  # committed while speaking, not only at the end
    # times are absolute (stream seconds)
    first = commits[0].words[0]
    assert abs(first.start - SCRIPT[0][1]) < 1e-3


def test_commit_needs_two_agreeing_passes():
    rec, live, commits = run(trim_seconds=100)
    assert commits[0].passes >= 2 and not commits[0].final


def test_buffer_trimmed_and_prompted_with_own_words():
    rec, live, commits = run(trim_seconds=1.5)
    words = [w.word for c in commits for w in c.words]
    assert words == [w for w, _, _ in SCRIPT]  # no word lost or doubled at the cuts
    starts = [a for a, _, _ in rec.calls]
    assert max(starts) > 2.0  # later passes start after the trim point
    assert max(b - a for a, b, _ in rec.calls) < 4.0  # passes stay short
    prompts = [p for _, _, p in rec.calls if p]
    assert prompts and prompts[-1].split()[0].startswith("w") and "zz" not in prompts[-1]


def test_passes_skipped_when_behind():
    rec = ScriptRecognizer()
    clock = SimClock()
    live = LiveTranscriber(rec, Segmenter(speech_prob=speech_prob, padding=0.0), clock=clock, max_lag=0.5)
    for i, block in enumerate(to_blocks(stream())):
        if i == 100:
            clock.work(3.0)  # one very slow step: 3 s behind real time
        live.push(block)
    live.flush()
    assert live.skipped > 0 and live.stats()["lag_max_s"] >= 2.5


def test_forced_cut_does_not_repeat_committed_words():
    rec = ScriptRecognizer()
    live = LiveTranscriber(rec, Segmenter(speech_prob=speech_prob, padding=0.0, max_seconds=3.0),
                           clock=SimClock(), trim_seconds=100)
    commits = []
    for block in to_blocks(stream()):
        commits += live.push(block)
    commits += live.flush()
    # (a forced cut is final for its piece, so the fake's "unstable last word" zz can be committed there)
    words = [w.word for c in commits for w in c.words if w.word != "zz"]
    assert sum(c.final for c in commits) >= 3  # several forced cuts happened
    assert len(words) == len(set(words)) and len(words) >= 10  # no word committed twice
