"""recite.py: ready-to-draw tokens, the done line numbers and corrections, on a temporary history (no GPU)."""

from core import DisplayWord
from core.history import History, token_list
from types import SimpleNamespace

from asr.words import Word
from core import Session
from recite import HISTORY_DB, Engine, draw_tokens, heard_words, result


def _words():
    return [DisplayWord("The", "correct"), DisplayWord("cat", "wrong", extras_before=["um"]),
            DisplayWord("sat", "pending")]


SUMMARY = {"words": 3, "correct": 1, "misspelled": 0, "wrong": 1, "missed": 0, "extra": 1, "pending": 1,
           "total_s": 2.0}


def test_history_path():
    assert HISTORY_DB.parts[-3:] == ("ReciteFlow", "data", "history.db")


def test_draw_tokens_and_result():
    toks = token_list(_words(), [])
    drawn = draw_tokens(toks, {2})
    assert [(t["text"], t["state"], t["sep"]) for t in drawn] == [
        ("The", "correct", " "), ("{um}", "extra", " "), ("cat", "wrong", " "), ("sat", "pending", "\n")]
    assert drawn[1]["labels"] == ["extra", "not_said"]
    assert result(SUMMARY, toks) == {"correct": 1, "total": 3, "said": 2, "extras": ["um"], "fixed": 0,
                                     "corrections": 0}
    assert not any(t["heard"] for t in drawn)
    assert [t["heard"] for t in draw_tokens(toks, {2}, heard={2})] == [False, False, False, True]


def test_heard_words_are_the_next_ones_after_the_coloured_words():
    sess = Session("The cat - sat on the mat", recognizer=None)
    sess.matcher.feed(["the", "cat"], final=False)
    assert heard_words(sess) == set()  # no live pass yet
    sess._live = SimpleNamespace(_prev=[Word("sat", 1.0, 1.2, 0.9), Word("on", 1.2, 1.3, 0.9)])
    assert heard_words(sess) == {2, 3, 4}  # "-" has no spoken word: shown with its neighbours
    sess._live._prev = [Word("sat", 1.0, 1.2, 0.9)] * 9  # never past the end of the text
    assert heard_words(sess) == {2, 3, 4, 5, 6}


def test_correct_saves_and_updates_the_counts(tmp_path):
    history = History(tmp_path / "h.db")
    rec_id = history.save(text="The cat sat", words=_words(), tail=[], summary=SUMMARY)
    engine = Engine(lambda *a: None, history=history)
    engine.toks, engine.summary, engine.rec_id, engine.breaks = token_list(_words(), []), SUMMARY, rec_id, set()
    out = engine.correct(2, "correct")  # "cat" was said right
    assert out["counts"]["correct"] == 2 and out["counts"]["wrong"] == 0
    assert out["tokens"][2]["corrected"] and out["result"]["corrections"] == 1
    out = engine.correct(1, "not_said")  # the app invented "um"
    assert out["result"]["extras"] == [] and out["counts"]["extra"] == 0
    assert [t.user_state for t in history.tokens(rec_id)] == [None, "not_said", "correct", None]
    out = engine.correct(2, None)  # undo
    assert out["counts"]["wrong"] == 1 and not out["tokens"][2]["corrected"]


def test_stalled_when_the_audio_stops_coming():
    import threading
    import time

    gate = threading.Event()

    def blocks():
        for _ in range(3):
            time.sleep(0.02)
            yield b""
        gate.wait(5)  # the audio stops: no more blocks
        yield b""

    engine = Engine(lambda *a: None)
    assert not engine.stalled(0)  # nothing running
    engine._running = True
    engine.progress_at = time.perf_counter()
    t = threading.Thread(target=lambda: list(engine._until_stop(blocks())), daemon=True)
    t.start()
    time.sleep(0.04)
    assert not engine.stalled(0.3)  # blocks still arriving
    time.sleep(0.5)
    assert engine.stalled(0.3)
    gate.set()
    t.join(2)
    assert not engine.stalled(0.3)  # moved again
