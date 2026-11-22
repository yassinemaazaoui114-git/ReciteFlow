"""The window's UI-free helpers (no window is opened here)."""

from core import DisplayWord
from core.history import token_list
from recite import line_breaks, new_mistakes, tokens, visible


def _toks():
    words = [DisplayWord("The", "correct"), DisplayWord("cat", "wrong"),
             DisplayWord("sat", "pending", extras_before=["um"]), DisplayWord("down", "pending")]
    return token_list(words, [])


def test_hide_leaves_out_unsaid_words_only():
    assert [t.text for t in visible(_toks(), hide=True)] == ["The", "cat", "um"]
    assert [t.text for t in visible(_toks(), hide=False)] == ["The", "cat", "um", "sat", "down"]


def test_new_mistakes_beep_once_per_mistake():
    seen = set()
    assert len(new_mistakes(_toks(), seen)) == 2  # "cat" wrong + extra "um"
    assert new_mistakes(_toks(), seen) == set()  # same frame again: no new beep
    cleared = token_list([DisplayWord("The", "correct"), DisplayWord("cat", "correct")], [])
    assert new_mistakes(cleared, seen) == set()
    again = token_list([DisplayWord("The", "correct"), DisplayWord("cat", "wrong")], [])
    assert new_mistakes(again, seen) == set()  # a flicker of the same mistake does not beep twice


def test_tokens_notation_and_order():
    words = [DisplayWord("The", "correct"), DisplayWord("cat,", "missed"),
             DisplayWord("sat", "wrong", extras_before=["very"]), DisplayWord("down.", "pending")]
    assert tokens(words, ["ok"]) == [
        ("The", "correct", " "), ("[cat,]", "missed", " "), ("{very}", "extra", " "), ("sat", "wrong", " "),
        ("down.", "pending", " "), ("{ok}", "extra", " "),
    ]


def test_line_breaks_keep_paragraphs():
    text = "one two\n\nthree\nfour five"
    breaks = line_breaks(text, text.split())
    assert breaks == {1, 2, 4}
    words = [DisplayWord(w, "pending") for w in text.split()]
    assert [sep for _, _, sep in tokens(words, [], breaks)] == [" ", "\n", "\n", " ", "\n"]


def test_line_breaks_off_when_words_differ():
    assert line_breaks("one two\nthree", ["one", "two"]) == set()
