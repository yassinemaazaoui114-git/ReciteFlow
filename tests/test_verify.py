"""Acoustic check rule (asr.verify) with synthetic scores: no GPU."""

from asr.verify import Checker, Verifier, disputes, same_spelling
from matcher import Matcher


class FakeScorer:
    """sequence_logprobs: score per text from a dict (default -10); records the texts asked."""

    def __init__(self, scores=None, words=None):
        self.scores = scores or {}
        self.words = words or []
        self.asked = []
        self.initial_prompt = None

    def sequence_logprobs(self, audio, texts):
        self.asked.append(list(texts))
        return [self.scores.get(t, -10.0) for t in texts]

    def transcribe(self, audio):
        return list(self.words)


TEXT = "when you open a book for just ten minutes a day"


def test_disputes_spans():
    m = Matcher(TEXT)
    spans = disputes(m, "when you open a book for just six minutes a week".split())
    assert [(s.lo, s.hi, s.target) for s in spans] == [(7, 8, ["ten"]), (10, 11, ["day"])]


def test_supported_target_flips_to_correct():
    heard = "when you open a book for just tin minutes a day".split()
    rec = FakeScorer({" ".join(heard): -5.0, TEXT: -6.0})  # target 1.0 less likely: within tau
    words, checks = Verifier(rec, tau=2.0).check(Matcher(TEXT), None, heard)
    assert words == TEXT.split()
    assert checks[0].accepted and checks[0].heard == ["tin"] and abs(checks[0].margin + 1.0) < 1e-9


def test_unsupported_target_keeps_mistake():
    heard = "when you open a book for just six minutes a day".split()
    rec = FakeScorer({" ".join(heard): -5.0, TEXT: -14.0})
    words, checks = Verifier(rec, tau=2.0).check(Matcher(TEXT), None, heard)
    assert words == heard and not checks[0].accepted and checks[0].margin == -9.0


def test_each_span_scored_alone_in_one_batch():
    heard = "when you open a book for just six minutes a week".split()
    alt1 = "when you open a book for just ten minutes a week"
    alt2 = "when you open a book for just six minutes a day"
    rec = FakeScorer({" ".join(heard): -5.0, alt1: -20.0, alt2: -5.5})
    words, checks = Verifier(rec, tau=1.0).check(Matcher(TEXT), None, heard)
    assert rec.asked == [[" ".join(heard), alt1, alt2]]
    assert words == "when you open a book for just six minutes a day".split()
    assert [c.accepted for c in checks] == [False, True]


def test_missed_word_checked_with_and_without():
    heard = "when you open book for just ten minutes a day".split()
    rec = FakeScorer({" ".join(heard): -5.0, TEXT: -30.0})  # audio clearly has no "a"
    words, checks = Verifier(rec).check(Matcher(TEXT), None, heard)
    assert words == heard and checks[0].heard == [] and checks[0].target == ["a"]


def test_extra_words_can_be_removed():
    heard = "when you open a book uh for just ten minutes a day".split()
    rec = FakeScorer({" ".join(heard): -5.0, TEXT: -5.2})
    words, checks = Verifier(rec).check(Matcher(TEXT), None, heard)
    assert words == TEXT.split() and checks[0].heard == ["uh"] and checks[0].target == []


def test_context_words_are_part_of_the_scored_text():
    heard = "minutes a dey".split()
    rec = FakeScorer()
    m = Matcher(TEXT)
    m.feed("when you open a book for just ten".split())
    Verifier(rec).check(m, None, heard, before=["ten"], after=["you", "learn"])
    assert rec.asked[0] == ["ten minutes a dey you learn", "ten minutes a day you learn"]


def test_spelling_variants_accepted_without_scoring():
    assert same_spelling(["courtyard"], ["court", "yard"]) and same_spelling(["penciled"], ["pencilled"])
    assert not same_spelling(["step"], ["steps"]) and not same_spelling([], ["a"])
    m = Matcher("in the court yard he penciled a note")
    rec = FakeScorer()
    words, checks = Verifier(rec).check(m, None, "in the courtyard he pencilled a note".split())
    assert words == "in the court yard he penciled a note".split()
    assert rec.asked == [] and all(c.accepted and c.margin == float("inf") for c in checks)


def test_spans_on_held_words_are_not_checked():
    m = Matcher(TEXT)
    m.feed("when you open a book for".split(), final=False)
    m.feed("just tin".split(), final=False)
    assert m.held() == ["tin"]
    assert disputes(m, "minutes a day".split()) == []


def test_mode_c_uses_unprompted_words_and_checks():
    heard = "when you open a book for just tin minutes a day"
    rec = FakeScorer({heard: -5.0, TEXT: -5.5}, words=heard.split())
    res = Checker(rec, "C", tau=2.0).words(Matcher(TEXT), None)
    assert rec.initial_prompt is None and res.words == TEXT.split() and res.checks[0].accepted


def test_mode_d_keeps_mistake_both_passes_heard():
    class TwoPass(FakeScorer):
        def transcribe(self, audio):
            # prompted pass hears "six" too: both passes disagree with the target -> no check
            return "when you open a book for just six minutes a day".split()

    from asr.prompt import PromptBuilder

    rec = TwoPass({"when you open a book for just six minutes a day": -5.0, TEXT: -5.1})
    res = Checker(rec, "D", tau=2.0, prompts=PromptBuilder(TEXT, "full")).words(Matcher(TEXT), None)
    assert "six" in res.words and res.checks == [] and rec.asked == []


def test_compound_cut_at_chunk_end():
    m = Matcher("they crossed the court yard slowly")
    rec = FakeScorer()
    words, checks = Verifier(rec).check(m, None, "they crossed the courtyard".split())
    assert words == "they crossed the court yard".split() and rec.asked == []
