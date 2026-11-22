"""Session tests with a fake recognizer (no GPU): each utterance's audio is its words."""

from core import Session

TEXT = "It's 5 o'clock, and the quick brown fox jumps."


class FakeRecognizer:
    def __init__(self):
        self.initial_prompt = None
        self.prompts = []

    def transcribe(self, audio):
        self.prompts.append(self.initial_prompt)
        return audio.split()


def test_session_follows_and_finishes():
    rec = FakeRecognizer()
    s = Session(TEXT, rec)
    u = s.process("it is five")
    assert not u.finished and u.latency >= 0
    u = s.process("o'clock and the quick thank you")  # trailing hallucination is held back
    words, tail = s.display()
    assert [w.state for w in words] == ["correct"] * 6 + ["pending"] * 3
    u = s.process("brown fox jumps")
    assert u.finished
    words, tail = s.display()
    assert all(w.state == "correct" for w in words)
    assert words[6].text == "brown" and words[6].extras_before == ["thank", "you"]
    assert rec.prompts == [TEXT] * 3  # short text -> auto uses the full prompt
    summary = s.summary()
    assert summary["correct"] == 10 and summary["extra"] == 2 and summary["utterances"] == 3


def test_display_worst_state_and_finish_flushes():
    s = Session(TEXT, FakeRecognizer())
    s.process("it was five o'clock and the quick brown wolf")
    words, _ = s.display()
    assert words[0].text == "It's" and words[0].state == "wrong"  # "it was" vs "it is"
    assert words[7].state == "pending"  # "wolf" held back
    s.finish()
    words, _ = s.display()
    assert words[7].state == "wrong"
    assert s.finish() == []


class FakeWordRecognizer(FakeRecognizer):
    """transcribe_words like asr.engine.Recognizer: the audio string is the words."""

    def transcribe_words(self, audio, initial_prompt=False):
        from asr.words import Word

        return [Word(w, k, k + 0.5, 0.9) for k, w in enumerate(audio.split())]


def live_commit(text, final=False, utterance=None):
    from asr.live import Commit
    from asr.vad import Utterance
    from asr.words import Word

    words = [Word(w, k, k + 0.5, 0.9) for k, w in enumerate(text.split())]
    utt = Utterance(utterance, 0.0, 10.0) if utterance is not None else None
    return Commit(words, text, final=final, utterance=utt)


def test_live_realign_at_pause_overrides_live_words():
    s = Session(TEXT, FakeWordRecognizer(), mode="B", live=True)
    s._process_commit(live_commit("it is five o'clock and the"))
    s._process_commit(live_commit("the quick brown"))  # "the" doubled at a live buffer cut
    assert [w for _, w in s.matcher.extras()] == ["the"]
    u = s._process_commit(live_commit("fox", final=True,
                                      utterance="it is five o'clock and the quick brown box"))
    words, _ = s.display()
    assert s.matcher.extras() == []  # the whole-utterance pass overrides the live extra
    assert [w.state for w in words[:7]] == ["correct"] * 7
    assert words[7].text == "fox" and words[7].state == "pending"  # "box" held back, like per-pause mode
    assert not u.finished and s.summary()["realigns"] == 1
    # next utterance: re-aligned from the state after the last re-align
    u = s._process_commit(live_commit("fox jumps", final=True, utterance="fox jumps"))
    assert u.finished
    words, _ = s.display()
    assert all(w.state == "correct" for w in words) and s.matcher.extras() == [(8, "box")]


def test_live_without_realign_keeps_live_words():
    s = Session(TEXT, FakeWordRecognizer(), mode="B", live=True, realign=False)
    s._process_commit(live_commit("it is five o'clock and the"))
    s._process_commit(live_commit("the quick brown"))
    s._process_commit(live_commit("fox", final=True, utterance="it is five o'clock and the quick brown fox"))
    assert [w for _, w in s.matcher.extras()] == ["the"] and s.summary()["realigns"] == 0


def test_display_marks_recovered_words():
    s = Session("the quick brown fox jumps over the lazy dog", FakeRecognizer())
    s.process("the quick brown wolf")
    s.process("the quick brown fox jumps over the lazy dog")
    words, _ = s.display()
    assert words[3].state == "wrong" and words[3].recovered
    assert not any(w.recovered for i, w in enumerate(words) if i != 3)
    assert s.summary()["recovered"] == 1
