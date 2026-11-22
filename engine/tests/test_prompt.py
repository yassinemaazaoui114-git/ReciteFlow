from asr.prompt import PromptBuilder, estimate_tokens

SHORT = "It's 5 o'clock, and the quick brown fox jumps."


def w(i):
    """Distinct letters-only word for index i (normalizes to exactly one word)."""
    return "w" + "".join("abcdefghij"[int(d)] for d in str(i))


LONG = " ".join(w(i) for i in range(300))


def test_auto_uses_full_for_short_text():
    b = PromptBuilder(SHORT, "auto:back")
    assert b.mode == "full"
    assert b.prompt(0) == SHORT and b.prompt(5) == SHORT


def test_auto_uses_long_mode_for_long_text():
    assert estimate_tokens(LONG) > 200
    assert PromptBuilder(LONG, "auto:back").mode == "back"
    assert PromptBuilder(LONG, "auto:around").mode == "around"


def test_back_is_last_40_recited_words():
    b = PromptBuilder(LONG, "back")
    assert b.prompt(0) is None
    assert b.prompt(10).split() == [w(i) for i in range(10)]
    assert b.prompt(100).split() == [w(i) for i in range(60, 100)]


def test_around_is_capped_by_tokens():
    b = PromptBuilder(LONG, "around", count_tokens=lambda t: len(t.split()))
    words = b.prompt(100).split()
    assert words[0] == w(70)
    assert len(words) <= 200 and words[-1] == w(199)  # 30 before + 100 after fits
    tight = PromptBuilder(LONG, "around", max_tokens=50, count_tokens=lambda t: len(t.split()))
    words = tight.prompt(100).split()
    assert words[0] == w(70) and len(words) <= 50


def test_pointer_maps_through_normalization():
    # "It's" -> 2 normalized words, "5" -> 1: pointer 3 = after "It's 5".
    b = PromptBuilder(SHORT, "back")
    assert b.prompt(3) == "It's 5"
    assert b.prompt(2) == "It's"
