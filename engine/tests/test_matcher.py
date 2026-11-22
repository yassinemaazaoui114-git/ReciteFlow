import random
import time

import pytest

from matcher import Matcher, normalize

PARAGRAPH = (
    "It's 5 o'clock, and the quick brown fox jumps over the lazy dog. "
    "In 1984, they didn't know what we'd become; that's the truth. "
    "Thank you for listening, and don't forget the well-known 2nd rule!"
)

# How a speaker / Whisper might render the same paragraph.
SPOKEN = (
    "it is five o'clock and the quick brown fox jumps over the lazy dog "
    "in nineteen eighty four they did not know what we would become that is the truth "
    "thank you for listening and do not forget the well known second rule"
)


@pytest.fixture(params=[True, False], ids=["final", "streaming"])
def final(request):
    """Every streaming test runs both with final=True and with final=False (tail hold-back)."""
    return request.param


def all_correct(m):
    return all(s == "correct" for s in m.states())


# --- normalization -------------------------------------------------------

def test_normalize_contractions_equal_on_both_sides():
    assert normalize("It's") == normalize("it is")
    assert normalize("don't") == normalize("do not")
    assert normalize("can't") == normalize("cannot") == normalize("can not")


def test_normalize_numbers_hyphens_punctuation_single_letters():
    assert normalize("5") == ["five"]
    assert normalize("1984") == ["nineteen", "eighty", "four"]
    assert normalize("well-known snake_case!") == ["well", "known", "snake", "case"]
    assert normalize("I saw a b c") == ["i", "saw", "a"]


def test_target_and_spoken_normalize_identically():
    assert normalize(PARAGRAPH) == normalize(SPOKEN)


# --- required tests ------------------------------------------------------

def test_1_perfect_recitation(final):
    m = Matcher(PARAGRAPH)
    m.feed(SPOKEN.split(), final=final)
    assert all_correct(m)
    assert m.extras() == []
    assert m.finished()


def test_2_random_chunks_identical_result(final):
    reference = Matcher(PARAGRAPH)
    reference.feed(SPOKEN.split(), final=final)
    words = SPOKEN.split()
    for seed in range(20):
        rng = random.Random(seed)
        m = Matcher(PARAGRAPH)
        i = 0
        while i < len(words):
            size = rng.randint(1, 8)
            m.feed(words[i : i + size], final=final)
            i += size
        assert m.states() == reference.states()
        assert m.extras() == reference.extras()
        assert m.finished()


def test_3_one_word_wrong(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown cat jumps over the lazy dog".split(), final=final)
    states = m.states()
    assert states[3] == "wrong"
    assert all(s == "correct" for i, s in enumerate(states) if i != 3)


def test_4_one_word_skipped(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick fox jumps over the lazy dog".split(), final=final)
    states = m.states()
    assert states[2] == "missed"
    assert all(s == "correct" for i, s in enumerate(states) if i != 2)


def test_4_two_consecutive_skipped(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick jumps over the lazy dog".split(), final=final)
    states = m.states()
    assert states[2] == states[3] == "missed"
    assert all(s == "correct" for i, s in enumerate(states) if i not in (2, 3))


def test_5_extra_word(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown fox really jumps over the lazy dog".split(), final=final)
    assert all_correct(m)
    assert m.extras() == [(4, "really")]


def test_6_near_miss(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown foxes jumps over the lazy dog".split(), final=final)
    states = m.states()
    assert states[3] == "misspelled"
    assert all(s == "correct" for i, s in enumerate(states) if i != 3)


def test_7_repeated_words_in_target(final):
    target = "that that is is that that is not is not"
    m = Matcher(target)
    m.feed(target.split(), final=final)
    assert all_correct(m)
    assert m.extras() == []

    # Same, word by word.
    m = Matcher(target)
    for w in target.split():
        m.feed([w], final=final)
    assert all_correct(m)

    # One repeated word dropped: exactly one missed, no extras.
    m = Matcher("I know that that is true")
    m.feed("I know that is true".split(), final=final)
    assert m.states().count("missed") == 1
    assert m.states().count("correct") == 5
    assert m.extras() == []


def test_8_blacklist_words_matched_normally(final):
    target = "thank you for the food, and thank you for coming"
    m = Matcher(target)
    m.feed(target.split(), final=final)
    assert all_correct(m)


def test_9_stop_mid_paragraph(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown fox".split(), final=final)
    assert m.states() == ["correct"] * 4 + ["pending"] * 5
    assert "missed" not in m.states()
    assert not m.finished()


def test_10_restart_after_mistake(final):
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown wolf".split(), final=final)
    m.feed("the quick brown fox jumps over the lazy dog".split(), final=final)
    states = m.states()
    assert states[:3] == ["correct"] * 3  # never un-corrected
    assert states[3] in ("wrong", "missed")
    assert states[4:] == ["correct"] * 5
    assert m.finished()


def test_10b_correct_states_never_mutated(final):
    m = Matcher("one two three four five")
    m.feed(["one", "two"], final=final)
    before = m.states()[:2]
    m.feed(["two", "one", "three"], final=final)
    assert m.states()[:2] == before


def test_11_500_words_fast(final):
    rng = random.Random(0)
    vocab = normalize(PARAGRAPH)
    words = [rng.choice(vocab) for _ in range(500)]
    target = " ".join(words)

    t0 = time.perf_counter()
    m = Matcher(target)
    i = 0
    while i < len(words):
        size = rng.randint(1, 8)
        m.feed(words[i : i + size], final=final)
        i += size
    chunked = time.perf_counter() - t0
    assert all_correct(m)

    t0 = time.perf_counter()
    m = Matcher(target)
    m.feed(words, final=final)
    single = time.perf_counter() - t0
    assert all_correct(m)

    assert chunked < 1.0
    assert single < 1.0


def test_events_report_changes(final):
    m = Matcher("hello big world")
    events = m.feed(["hello", "small", "world", "again"], final=final)
    kinds = [(e.index, e.state) for e in events]
    assert (0, "correct") in kinds
    assert (1, "wrong") in kinds
    assert (2, "correct") in kinds
    assert (3, "extra") in kinds
    assert m.feed([]) == []


# --- tail hold-back (feed final=False + flush) ----------------------------

FOX = "the quick brown fox jumps over the lazy dog"


def test_trailing_hallucination_does_not_mark_next_words_wrong():
    # Old behaviour (final=True): the hallucinated tail is aligned onto "brown fox".
    m = Matcher(FOX)
    m.feed("the quick thank you".split())
    assert m.states()[2:4] == ["wrong", "wrong"]

    m = Matcher(FOX)
    m.feed("the quick thank you".split(), final=False)
    assert m.states() == ["correct"] * 2 + ["pending"] * 7
    assert m.held() == ["thank", "you"]
    m.feed("brown fox jumps over the lazy dog".split(), final=False)
    assert all_correct(m)
    assert m.extras() == [(2, "thank"), (2, "you")]
    assert m.held() == []
    assert m.finished()


def test_cut_right_after_deleted_word():
    # The target lacks "brown" (deleted from the text), the speaker says it as the last word
    # of an utterance. Old behaviour: "brown" is aligned onto "fox" -> fox wrong + extra fox.
    target = "the quick fox jumps over the lazy dog"
    m = Matcher(target)
    m.feed("the quick brown".split())
    m.feed("fox jumps over the lazy dog".split())
    assert m.states()[2] == "wrong"

    m = Matcher(target)
    m.feed("the quick brown".split(), final=False)
    assert m.states()[2] == "pending"
    m.feed("fox jumps over the lazy dog".split(), final=False)
    assert all_correct(m)
    assert m.extras() == [(2, "brown")]


def test_cut_right_after_skipped_word():
    # Speaker skips "fox", the utterance ends on a wrong word, the next starts after it.
    m = Matcher(FOX)
    m.feed("the quick brown cat".split(), final=False)
    assert m.states()[3] == "pending"
    m.feed("jumps over the lazy dog".split(), final=False)
    assert m.states()[3] == "wrong"
    assert all(s == "correct" for i, s in enumerate(m.states()) if i != 3)
    assert m.extras() == []


def test_flush_commits_held_words():
    m = Matcher(FOX)
    m.feed("the quick brown wolf".split(), final=False)
    assert m.states()[3] == "pending"
    events = m.flush()
    assert m.states()[3] == "wrong"
    assert (3, "wrong") in [(e.index, e.state) for e in events]
    assert m.held() == [] and m.flush() == []


def test_wrong_last_word_of_text_is_not_held():
    m = Matcher(FOX)
    m.feed("the quick brown fox jumps over the lazy cat".split(), final=False)
    assert m.states()[8] == "wrong"
    assert m.finished() and m.held() == []


def test_restart_to_fix_a_slip_in_a_later_feed(final):
    # Live chunks: "at last" is committed, then the speaker goes back: "at first she sold ...".
    m = Matcher("twelve years ago at first she sold only bread but now she makes cakes")
    m.feed("twelve years ago at last".split(), final=final)
    m.feed("ago at first she sold only bread".split(), final=final)
    m.feed("but now she makes cakes".split(), final=final)
    states = m.states()
    assert states[4] == "wrong"  # the slip stays marked (R14: re-recited words stay red)
    assert [s for i, s in enumerate(states) if i != 4] == ["correct"] * 13
    assert m.extras() == []


def test_one_word_repeat_of_a_correct_word_is_not_a_restart(final):
    m = Matcher("one two three four")
    m.feed(["one", "two"], final=final)
    m.feed(["two", "three", "four"], final=final)
    assert m.states() == ["correct"] * 4
    assert m.extras() == [(2, "two")]


def test_restart_after_held_wrong_word():
    m = Matcher("a quiet village in the mountains the road was narrow")
    m.feed("a quiet village in a".split(), final=False)
    assert m.held() == ["a"]
    m.feed("village in the mountains the road was narrow".split(), final=False)
    states = m.states()
    assert states[4] == "wrong" and [s for i, s in enumerate(states) if i != 4] == ["correct"] * 9
    assert m.extras() == []


def test_snapshot_restore_forgets_later_feeds():
    m = Matcher("the quick brown fox jumps")
    m.feed(["the", "quick"], final=False)
    snap = m.snapshot()
    m.feed(["brown", "lazy", "fox", "box"], final=False)
    assert m.extras() == [(3, "lazy")] and m.pointer == 5
    m.restore(snap)
    assert m.pointer == 2 and m.extras() == [] and m.held() == []
    assert m.states() == ["correct", "correct", "pending", "pending", "pending"]
    m.feed(["brown", "fox", "jumps"])
    assert m.finished() and m.states() == ["correct"] * 5


def test_jump_back_to_sentence_start_after_a_mistake():
    # Yassine's take: "... at nine ... my teeth", then the whole sentence again (13 words back).
    text = "I wake up at six in the morning. First, I brush my teeth and wash my face. Then I make tea."
    m = Matcher(text)
    m.feed("i wake up at nine in the morning first i brush my teeth".split(), final=False)
    m.feed("i wake up at six in the morning first i brush my teeth".split(), final=False)
    assert m.pointer == 13 and m.extras() == []
    m.feed("and wash my face then i make tea".split(), final=False)
    m.flush()
    states = m.states()
    assert states[4] == "wrong" and m.recovered() == {4}  # the mistake stays, re-recited right
    assert [s for i, s in enumerate(states) if i != 4] == ["correct"] * (len(states) - 1)
    assert m.finished()


def test_jump_back_in_live_chunks():
    # Live passes cut the re-recited sentence into short chunks.
    text = "I wake up at six in the morning. First, I brush my teeth and wash my face."
    m = Matcher(text)
    m.feed("i wake up at nine in the morning first i brush my teeth".split(), final=False)
    for chunk in ("i wake up", "at six in the", "morning first i brush my teeth", "and wash my face"):
        m.feed(chunk.split(), final=False)
    m.flush()
    assert m.recovered() == {4} and m.extras() == []
    assert m.states().count("correct") == len(m.states()) - 1 and m.finished()


def test_no_jump_back_without_an_earlier_mistake():
    # Saying words that also appear earlier is not a restart when nothing was wrong before:
    # the wrong phrase must stay visible (no hidden mistake).
    m = Matcher("in the morning I read and in the evening I sleep well")
    m.feed("in the morning i read and".split(), final=False)
    m.feed("in the morning i sleep well".split(), final=False)
    m.flush()
    assert m.states()[8] in ("wrong", "misspelled")  # "evening" said as "morning"
    assert m.pointer == len(m.target) and m.recovered() == set()


def test_repeat_right_word_again_is_not_recovered():
    m = Matcher("the quick brown fox jumps")
    m.feed("the quick brown fox".split(), final=False)
    m.feed("brown fox jumps".split(), final=False)
    m.flush()
    assert m.states() == ["correct"] * 5 and m.recovered() == set()


def test_snapshot_keeps_recovered():
    m = Matcher("the quick brown fox jumps over the lazy dog")
    m.feed("the quick brown wolf".split(), final=True)
    snap = m.snapshot()
    m.feed("the quick brown fox".split(), final=True)
    assert m.recovered() == {3}
    m.restore(snap)
    assert m.recovered() == set() and m.pointer == 4
