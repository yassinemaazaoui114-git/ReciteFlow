"""Honest transcription + acoustic check (Task 04).

The utterance is transcribed WITHOUT the target text, so the prompt cannot hide a mistake. Where
the transcript disagrees with the target (as the matcher would align it now), we ask the same model
whether the audio supports the TARGET words: teacher-forced log P(text | audio) of the transcript
with the target span vs with the heard span (Recognizer.sequence_logprobs, no prompt).

    margin = log P(.. target span ..) - log P(.. heard span ..)
    margin >= -tau  -> the audio supports the target: the span is replaced by the target words
                       (Whisper just misspelled a hard word, e.g. a name)
    otherwise       -> keep what was heard (the matcher marks the mistake)

Spelling variants the audio cannot tell apart are accepted without scoring (margin = +inf):
the same letters with spaces removed ("court yard" / "courtyard") or with doubled letters
collapsed ("pencilled" / "penciled").

A missed target word is a span with no heard words ("with the word" vs "without it"); extra heard
words are a span with no target words.

Modes (Checker):
    A  prompt (PromptBuilder, `auto:around`), no check                  (Task 03 behaviour)
    B  no prompt, no check
    C  no prompt + acoustic check
    D  prompted pass + unprompted pass; the unprompted words are used. A disputed span is
       acoustic-checked only where the prompted pass agreed with the target (the passes disagree);
       where both passes disagree with the target, the mistake is kept without a check.
"""

import time
from dataclasses import dataclass, field

from matcher import Matcher, normalize
from matcher.align import _DIAG, _LEFT, _UP, COST_MATCH

MODES = ["A", "B", "C", "D"]
TAU = 1.0  # tuned in Task 04 on the TTS practice set + public clips (see tasks/TASK_04_report.md)


@dataclass
class Span:
    """A run of non-exact alignment ops. lo..hi index the checked words; target = target words."""

    lo: int
    hi: int
    target: list[str]
    t_lo: int  # window (target) index range, relative to the matcher pointer
    t_hi: int
    after: list[str] = field(default_factory=list)  # next target words (for a span at the end)


@dataclass
class Check:
    heard: list[str]
    target: list[str]
    margin: float
    accepted: bool  # True: replaced by the target words
    t_lo: int = 0  # target index range relative to the matcher pointer at check time
    t_hi: int = 0


@dataclass
class Result:
    words: list[str]  # normalized words to feed to the matcher
    raw: list[str] = field(default_factory=list)  # ASR words of the pass(es) used
    checks: list[Check] = field(default_factory=list)
    asr_s: float = 0.0
    check_s: float = 0.0


def disputes(matcher: Matcher, words: list[str]) -> list[Span]:
    """Spans of `words` (normalized, new) that the matcher would align as not exactly correct.
    Held-back words are prepended as the matcher does; spans touching them are not returned
    (they were checked on their own audio)."""
    held = matcher.held()
    spoken = held + words
    if not spoken:
        return []
    window = matcher._window(len(spoken))
    ops = matcher._align(spoken, window)
    spans, cur, i, j = [], None, 0, 0
    for op, _, cost in ops:
        if op == _DIAG and cost == COST_MATCH:
            if cur:
                spans.append(cur)
                cur = None
            i, j = i + 1, j + 1
            continue
        if cur is None:
            cur = [i, i, j, j]
        if op != _LEFT:
            i += 1
        if op != _UP:
            j += 1
        cur[1], cur[3] = i, j
    if cur:
        spans.append(cur)
    h = len(held)
    return [Span(a - h, b - h, window[c:d], c, d, window[d:d + 3]) for a, b, c, d in spans if a >= h]


def target_bad(matcher: Matcher, words: list[str]) -> set[int]:
    """Window indices the matcher would NOT mark exactly correct for `words` (an extra marks the
    target word it was said before)."""
    bad = set()
    for s in disputes(matcher, words):
        bad.update(range(s.t_lo, max(s.t_hi, s.t_lo + 1)))
    return bad


def _collapse(words):
    out = []
    for ch in "".join(words):
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def same_spelling(heard: list[str], target: list[str]) -> bool:
    """Orthographic variants only (no sound difference): compounds and doubled letters."""
    return bool(heard) and bool(target) and _collapse(heard) == _collapse(target)


class Verifier:
    def __init__(self, recognizer, tau: float = TAU):
        self.recognizer = recognizer
        self.tau = tau

    def check(self, matcher, audio, words, before=(), after=(), skip=None) -> tuple[list[str], list[Check]]:
        """Acoustic check of the disputed spans of `words` (normalized) heard in `audio`.
        `before`/`after`: other normalized words heard in the same audio (context, not checked).
        skip(span) -> True keeps that span as heard without a check. Returns (words, checks)."""
        spans = [s for s in disputes(matcher, words) if not (skip and skip(s))]
        # A compound cut by the end of a live chunk ("courtyard" vs "court | yard"): a span at the
        # end of the words may take the next target words when that makes the same spelling.
        for s in spans:
            if s.hi == len(words) and not same_spelling(words[s.lo: s.hi], s.target):
                for j in range(1, len(s.after) + 1):
                    if same_spelling(words[s.lo: s.hi], s.target + s.after[:j]):
                        s.target, s.t_hi = s.target + s.after[:j], s.t_hi + j
                        break
        out, checks = list(words), []
        for s in [s for s in spans if same_spelling(words[s.lo: s.hi], s.target)][::-1]:
            checks.append(Check(words[s.lo: s.hi], s.target, float("inf"), True, s.t_lo, s.t_hi))
            out[s.lo: s.hi] = s.target
        checks.reverse()
        spans = [s for s in spans if not same_spelling(words[s.lo: s.hi], s.target)]
        if not spans:
            return out, checks
        # Score on the words with the spelling variants already replaced (offsets shift): redo the
        # spans on `out` so every candidate differs from the heard text in one span only.
        if checks:
            words = out
            spans = [s for s in disputes(matcher, words) if not (skip and skip(s))
                     and not same_spelling(words[s.lo: s.hi], s.target)]
            if not spans:
                return out, checks
        before, after = list(before), list(after)
        texts = [" ".join(before + words + after)]
        for s in spans:
            texts.append(" ".join(before + words[: s.lo] + s.target + words[s.hi:] + after))
        scores = self.recognizer.sequence_logprobs(audio, texts)
        out, done = list(words), checks
        checks = []
        for s, score in sorted(zip(spans, scores[1:]), key=lambda x: -x[0].lo):  # right to left
            margin = score - scores[0]
            ok = margin >= -self.tau
            checks.append(Check(words[s.lo: s.hi], s.target, margin, ok, s.t_lo, s.t_hi))
            if ok:
                out[s.lo: s.hi] = s.target
        checks.reverse()
        return out, done + checks


class Checker:
    """One utterance of audio -> normalized words for the matcher, in mode A/B/C/D."""

    def __init__(self, recognizer, mode: str = "C", tau: float = TAU, prompts=None):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; use one of {MODES}")
        if mode in ("A", "D") and prompts is None:
            raise ValueError(f"mode {mode} needs a PromptBuilder")
        self.recognizer = recognizer
        self.mode = mode
        self.prompts = prompts
        self.verifier = Verifier(recognizer, tau)

    def _asr(self, audio, prompt):
        t0 = time.perf_counter()
        self.recognizer.initial_prompt = prompt
        if hasattr(self.recognizer, "transcribe_words"):
            raw = [w.word for w in self.recognizer.transcribe_words(audio)]
        else:  # test fakes
            raw = self.recognizer.transcribe(audio)
        return raw, time.perf_counter() - t0

    def words(self, matcher: Matcher, audio) -> Result:
        mode = self.mode
        if mode in ("A", "B"):
            prompt = self.prompts.prompt(matcher.pointer) if mode == "A" else None
            raw, secs = self._asr(audio, prompt)
            return Result(normalize(" ".join(raw)), raw, asr_s=secs)
        if mode == "C":
            raw, secs = self._asr(audio, None)
            t0 = time.perf_counter()
            words, checks = self.verifier.check(matcher, audio, normalize(" ".join(raw)))
            return Result(words, raw, checks, secs, time.perf_counter() - t0)
        # D: prompted pass first, unprompted pass last (its encoder output is reused by the check)
        p_raw, p_secs = self._asr(audio, self.prompts.prompt(matcher.pointer))
        raw, secs = self._asr(audio, None)
        t0 = time.perf_counter()
        p_bad = target_bad(matcher, normalize(" ".join(p_raw)))

        def both_disagree(s):
            return bool(p_bad & set(range(s.t_lo, max(s.t_hi, s.t_lo + 1))))

        words, checks = self.verifier.check(matcher, audio, normalize(" ".join(raw)), skip=both_disagree)
        return Result(words, p_raw + ["|"] + raw, checks, p_secs + secs, time.perf_counter() - t0)
