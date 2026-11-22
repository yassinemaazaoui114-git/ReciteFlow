"""One recitation of one target text: audio in, state updates out. No UI code here; the terminal
app (reciteflow.py) and the future GUI both drive a Session.

    session = Session(text, recognizer, mode=DEFAULT_MODE, live=True)
    for update in session.run(mic.blocks(), Segmenter()):
        redraw(session.display())                     # after every update
    session.summary()

Modes (asr.verify): A prompt, B no prompt, C no prompt + acoustic check, D prompt + double check.
live=True (modes B/C): words are committed while the user speaks (asr.live, ~0.6 s passes) and
fed to the matcher at once; otherwise one update per VAD utterance (Task 03 behaviour).
realign=True (live only): at each pause the whole utterance is transcribed and checked again, as in
per-pause mode, and that result overrides the live colours of the utterance (the matcher goes back
to its state before the utterance). Live colours are fast; the pause result is more precise
(live passes lose or double words at buffer cuts).
The Session default stays mode A / not live (the Task 03 API); apps pass DEFAULT_MODE.

`run` stops by itself when the text is finished; on Ctrl+C / stop call `session.finish()`.
"""

import time
from dataclasses import dataclass, field

from asr.live import LiveTranscriber, RealClock
from asr.prompt import PromptBuilder
from asr.verify import TAU, Checker
from matcher import CORRECT, EXTRA, MISSED, MISSPELLED, PENDING, WRONG, Event, Matcher, normalize

DEFAULT_MODE = "C"  # Task 04 benchmark: no prompt + acoustic check
LIVE_MODES = ("B", "C")
LIVE_INTERVAL = 0.6  # s between live passes (Task 04: 1.0 gave median 1.9 s display latency)
HOLD_TIMEOUT = None  # live: s of silence after a pause before held words are committed; None = wait for
# the next utterance (Task 03 rule). 1.0 was tried: committing a held self-correction at a pause shifted
# the alignment for the rest of the text (tasks/TASK_04_report.md).

# When a displayed word covers several normalized words ("It's" -> it is), it shows the worst state.
_WORST_FIRST = [WRONG, MISSED, MISSPELLED, PENDING, CORRECT]
STATES = [CORRECT, MISSPELLED, WRONG, MISSED, PENDING]


@dataclass
class Update:
    events: list[Event]  # matcher state changes caused by this update
    words: list[str] = field(default_factory=list)  # raw ASR words (for logs; the UI does not show them)
    latency: float = 0.0  # ASR (+ check) seconds for this update
    finished: bool = False
    checks: list = field(default_factory=list)  # asr.verify.Check of this update


@dataclass
class DisplayWord:
    text: str  # the target word as written (case and punctuation kept)
    state: str
    extras_before: list[str] = field(default_factory=list)  # spoken extra words said before it
    recovered: bool = False  # said wrong first, then re-recited correctly (R14: stays red, underlined)


class Session:
    def __init__(self, text: str, recognizer, prompt_mode: str = "auto:around", mode: str = "A",
                 live: bool = False, tau: float = TAU, hold_timeout: float | None = HOLD_TIMEOUT, clock=None,
                 live_interval: float = LIVE_INTERVAL, realign: bool = True):
        self.text = text
        self.recognizer = recognizer
        self.matcher = Matcher(text)
        count = getattr(recognizer, "count_tokens", None)
        self.prompts = PromptBuilder(text, prompt_mode, count_tokens=count)
        self.mode = mode
        self.live = live and mode in LIVE_MODES
        self.checker = Checker(recognizer, mode, tau, self.prompts)
        self.hold_timeout = hold_timeout
        self.live_interval = live_interval
        self.realign = self.live and realign
        self._snap = None  # matcher state before the open utterance's first live commit
        self.realign_s: list[float] = []  # ASR + check seconds per re-align
        self.realign_lags: list[float] = []  # s from the end of the utterance audio to the override
        self.realign_changed = 0  # target words whose colour the re-align changed
        self.clock = clock or RealClock()
        self.raw_words = self.prompts.words
        self._spans, n = [], 0  # normalized index range of each raw word
        for w in self.raw_words:
            k = len(normalize(w))
            self._spans.append((n, n + k))
            n += k
        self.latencies: list[float] = []
        self.display_lags: list[float] = []  # live: seconds from the end of a spoken word to its colour
        self.checks: list = []
        self.heard: list[list[str]] = []  # ASR words of each final pass (per pause / re-align), for history
        self.started = time.perf_counter()
        self.ended: float | None = None
        self._closed = False
        self._pending_ends: list[float] = []  # end times of fed words not yet shown (held back)
        self._hold_deadline = None
        self.live_stats: dict = {}

    @property
    def finished(self) -> bool:
        return self.matcher.finished()

    # --- feeding --------------------------------------------------------------

    def _feed(self, words, ends, work_s=0.0, finish=True):
        """Feed normalized words (with the end time of each) and record display lags."""
        t0 = time.perf_counter()
        events = self.matcher.feed(words, final=False)
        if finish and self.matcher.finished():
            events += self.finish()
        self.clock.work(work_s + time.perf_counter() - t0)
        self._shown(ends)
        return events

    def _shown(self, ends=()):
        """Record the display lag of the fed words that are not held back."""
        now = self.clock.now()
        pending = self._pending_ends + list(ends)
        held = len(self.matcher.held())
        shown, self._pending_ends = (pending[:-held], pending[-held:]) if held else (pending, [])
        self.display_lags += [now - e for e in shown]

    def _realign(self, audio, end=None) -> list[Event]:
        """Re-align the open utterance: back to the state before it, then feed the whole-utterance
        transcript (+ acoustic check in mode C). Returns events for the words that changed."""
        old_states, old_extras = self.matcher.states(), self.matcher.extras()
        t0 = time.perf_counter()
        self.matcher.restore(self._snap)
        res = self.checker.words(self.matcher, audio)
        self.heard.append(list(res.raw))
        self.matcher.feed(res.words, final=False)
        self.clock.work(time.perf_counter() - t0)
        self.realign_s.append(res.asr_s + res.check_s)
        if end is not None:
            self.realign_lags.append(self.clock.now() - end)
        states = self.matcher.states()
        events = [Event(k, s) for k, (o, s) in enumerate(zip(old_states, states)) if o != s]
        self.realign_changed += len(events)
        events += [Event(k, EXTRA, w) for k, w in self.matcher.extras() if (k, w) not in old_extras]
        self._shown()
        return events

    def _release_held(self):
        """Commit held-back words (end of text, stop, or the hold timeout)."""
        events = self.matcher.flush()
        now = self.clock.now()
        self.display_lags += [now - e for e in self._pending_ends]
        self._pending_ends = []
        return events

    def process(self, audio, start: float = 0.0) -> Update:
        """Transcribe one whole utterance (mode A/B/C/D) and feed it to the matcher."""
        res = self.checker.words(self.matcher, audio)
        self.heard.append(list(res.raw))
        latency = res.asr_s + res.check_s
        self.latencies.append(latency)
        self.checks += res.checks
        end = start + len(audio) / 16000
        events = self._feed(res.words, [end] * len(res.words), work_s=0.0)
        return Update(events, res.raw, latency, self.matcher.finished(), res.checks)

    def _process_commit(self, commit) -> Update:
        if self.realign and self._snap is None:
            self._snap = self.matcher.snapshot()
        heard = [k for w in commit.words for k in normalize(w.word)]
        ends = [w.end for w in commit.words for _ in normalize(w.word)]
        t0 = time.perf_counter()
        checks = []
        if self.mode == "C" and heard:
            words, checks = self.checker.verifier.check(self.matcher, commit.audio, heard, commit.before, commit.after)
        else:
            words = heard
        check_s = time.perf_counter() - t0
        if len(words) != len(ends):  # a check changed the word count: reuse the last end time
            ends = (ends + [ends[-1] if ends else self.clock.now()] * len(words))[: len(words)]
        self.checks += checks
        events = self._feed(words, ends, work_s=check_s, finish=not self.realign)
        if self.realign and commit.final and commit.utterance is not None:
            events += self._realign(commit.utterance.audio, commit.utterance.end)
            self._snap = None
        elif self.realign and self.matcher.finished():  # text done mid-utterance: re-align what was said
            cur = self._live.seg.current()
            if cur is not None:
                events += self._realign(cur[0])
        if self.realign and self.matcher.finished():
            events += self.finish()
        if commit.final and self.matcher.held():
            self._hold_deadline = self.clock.now() + self.hold_timeout if self.hold_timeout is not None else None
        elif commit.words:
            self._hold_deadline = None
        return Update(events, [w.word for w in commit.words], check_s, self.matcher.finished(), checks)

    def finish(self) -> list[Event]:
        """End of the recitation (text finished, Ctrl+C or Stop): commit held words."""
        if self._closed:
            return []
        self._closed = True
        self.ended = time.perf_counter()
        return self._release_held()

    # --- running ----------------------------------------------------------------

    def run(self, blocks, segmenter):
        """Yield an Update per commit (live) or per utterance until the text is finished or the
        blocks run out."""
        if self.live:
            yield from self._run_live(blocks, segmenter)
            return
        for block in blocks:
            for utt in segmenter.push(block):
                update = self.process(utt.audio, utt.start)
                yield update
                if update.finished:
                    return
        for utt in segmenter.flush():
            yield self.process(utt.audio, utt.start)
            if self.finished:
                return

    def _run_live(self, blocks, segmenter):
        live = LiveTranscriber(self.recognizer, segmenter, interval=self.live_interval, clock=self.clock)
        self._live = live
        try:
            for block in blocks:
                for commit in live.push(block):
                    update = self._process_commit(commit)
                    yield update
                    if update.finished:
                        return
                if self._hold_deadline is not None and self.clock.now() >= self._hold_deadline:
                    self._hold_deadline = None
                    yield Update(self._release_held(), finished=self.matcher.finished())
            for commit in live.flush():
                update = self._process_commit(commit)
                yield update
                if update.finished:
                    return
        finally:
            self.live_stats = live.stats()

    # --- output -----------------------------------------------------------------

    def display(self) -> tuple[list[DisplayWord], list[str]]:
        """Per raw target word: text, state, extras said before it. Second value: extras said
        after the last word."""
        states = self.matcher.states()
        recovered = self.matcher.recovered()
        out = []
        for w, (a, b) in zip(self.raw_words, self._spans):
            if a == b:  # no normalized word (e.g. "-" or a lone letter): follow the pointer / next word
                said = self.matcher.pointer > a or (a < len(states) and states[a] != PENDING)
                state = CORRECT if said else PENDING
            else:
                state = min(states[a:b], key=_WORST_FIRST.index)
            out.append(DisplayWord(w, state, recovered=any(k in recovered for k in range(a, b))))
        tail = []
        for index, word in self.matcher.extras():
            k = next((k for k, (a, b) in enumerate(self._spans) if b > index), None)
            (out[k].extras_before if k is not None else tail).append(word)
        return out, tail

    def summary(self) -> dict:
        states = self.matcher.states()
        end = self.ended or time.perf_counter()
        lags = sorted(self.display_lags)
        pct = (lambda q: lags[min(len(lags) - 1, int(q * len(lags)))]) if lags else (lambda q: 0.0)
        return {
            "words": len(states),
            **{s: states.count(s) for s in STATES},
            "extra": len(self.matcher.extras()),
            "recovered": len(self.matcher.recovered()),
            "extras": [w for _, w in self.matcher.extras()],
            "utterances": len(self.latencies),
            "total_s": end - self.started,
            "latency_mean_ms": 1000 * sum(self.latencies) / len(self.latencies) if self.latencies else 0.0,
            "display_median_s": pct(0.5),
            "display_p90_s": pct(0.9),
            "checks": len(self.checks),
            "flipped": sum(1 for c in self.checks if c.accepted),
            "realigns": len(self.realign_s),
            "realign_ms_mean": 1000 * sum(self.realign_s) / len(self.realign_s) if self.realign_s else 0.0,
            "realign_changed": self.realign_changed,
            **self.live_stats,
        }
