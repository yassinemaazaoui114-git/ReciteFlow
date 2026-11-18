"""History (core.history): SQLite store of recitations, per-word results and user corrections."""

import json

import numpy as np
import pytest
import soundfile as sf

from core import AudioTap, DisplayWord, History, Session
from core.history import corrected_counts, token_list

from .test_session import TEXT, FakeRecognizer


def recitation(tmp_path, keep_audio=True, audio=None):
    h = History(tmp_path / "h.db")
    s = Session(TEXT, FakeRecognizer())
    s.process("it was five o'clock and the quick brown fox jumps")
    s.finish()
    words, tail = s.display()
    rec_id = h.save(text=TEXT, words=words, tail=tail, summary=s.summary(), heard=s.heard, audio=audio,
                    keep_audio=keep_audio, mode="A", model="fake", finished=s.finished)
    return h, rec_id


def test_token_order_matches_view():
    words = [DisplayWord("The", "correct"), DisplayWord("sat", "wrong", extras_before=["very"])]
    toks = token_list(words, ["ok"])
    assert [(t.pos, t.kind, t.word_index, t.text, t.app_state) for t in toks] == [
        (0, "word", 0, "The", "correct"), (1, "extra", 1, "very", "extra"), (2, "word", 1, "sat", "wrong"),
        (3, "extra", -1, "ok", "extra")]


def test_save_and_read_back(tmp_path):
    h, rec_id = recitation(tmp_path, keep_audio=False)
    row = h.recitation(rec_id)
    assert row["text"] == TEXT and row["finished"] == 1 and row["audio_path"] is None
    assert row["keep_for_training"] == 0 and row["wrong"] >= 1
    assert json.loads(row["heard"])[0][:3] == ["it", "was", "five"]  # the ASR words are kept
    toks = h.tokens(rec_id)
    assert toks[0].text == "It's" and toks[0].app_state == "wrong" and toks[0].user_state is None
    assert h.recent()[0]["id"] == rec_id and h.recent()[0]["corrections"] == 0


def test_corrections_are_labels(tmp_path):
    h, rec_id = recitation(tmp_path, keep_audio=False)
    tok = h.correct(rec_id, 0, "correct")  # the user: "It's" was actually said right
    assert tok.user_state == "correct" and tok.state == "correct"
    assert h.recent()[0]["corrections"] == 1
    assert h.correct(rec_id, 0, None).user_state is None  # undo
    assert h.correct(rec_id, 0, "wrong").user_state is None  # same as the app = no correction
    with pytest.raises(ValueError):
        h.correct(rec_id, 0, "not_said")  # an extra-word label on a target word
    with pytest.raises(KeyError):
        h.correct(rec_id, 999, "correct")


def test_corrected_counts():
    summary = {"words": 3, "correct": 1, "wrong": 1, "missed": 1, "misspelled": 0, "pending": 0, "extra": 1}
    toks = token_list([DisplayWord("a", "correct"), DisplayWord("b", "wrong", ["x"]), DisplayWord("c", "missed")], [])
    toks[2].user_state = "correct"  # b was right
    toks[1].user_state = "not_said"  # x was never said
    c = corrected_counts(summary, toks)
    assert (c["correct"], c["wrong"], c["missed"], c["extra"]) == (2, 0, 1, 0)


def test_audio_kept_only_with_consent(tmp_path):
    tap = AudioTap(iter([np.full(512, 0.1, np.float32), np.zeros(512, np.float32)]))
    assert len(list(tap)) == 2 and tap.audio().shape == (1024,)
    h, rec_id = recitation(tmp_path, keep_audio=True, audio=tap.audio())
    path = tmp_path / h.recitation(rec_id)["audio_path"]
    audio, rate = sf.read(str(path))
    assert rate == 16000 and len(audio) == 1024
    h2, rec2 = recitation(tmp_path, keep_audio=False, audio=tap.audio())
    assert h2.recitation(rec2)["audio_path"] is None


def test_keep_audio_setting(tmp_path):
    h = History(tmp_path / "h.db")
    assert h.keep_audio is True  # default: keep (local only)
    h.keep_audio = False
    assert History(tmp_path / "h.db").keep_audio is False


def test_recovered_is_stored(tmp_path):
    h = History(tmp_path / "h.db")
    words = [DisplayWord("six", "wrong", recovered=True), DisplayWord("in", "correct")]
    rec_id = h.save(text="six in", words=words, tail=[], summary={"words": 2})
    toks = h.tokens(rec_id)
    assert toks[0].recovered and not toks[1].recovered
    assert h.correct(rec_id, 0, "correct").recovered


def test_old_database_gets_the_new_column(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE tokens (recitation_id INTEGER NOT NULL, pos INTEGER NOT NULL, kind TEXT NOT NULL, "
               "word_index INTEGER, text TEXT NOT NULL, app_state TEXT NOT NULL, user_state TEXT, corrected_at TEXT, "
               "PRIMARY KEY (recitation_id, pos))")
    db.commit()
    db.close()
    h = History(tmp_path / "old.db")
    rec_id = h.save(text="a", words=[DisplayWord("a", "correct")], tail=[], summary={"words": 1})
    assert h.tokens(rec_id)[0].recovered is False
