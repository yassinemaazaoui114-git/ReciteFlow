# matcher

Pure-Python engine that labels each target word as the user recites. No audio, torch or Whisper.
Dependencies: `rapidfuzz`, `num2words` (tests: `pytest`).

## API

```python
from matcher import Matcher, normalize

m = Matcher(target_text, lookahead=15)
events = m.feed(["the", "quick", "brown"])  # one finished utterance -> list[Event]
events = m.feed(words, final=False)         # streaming: hold back a bad tail (see below)
events = m.flush()                          # commit held words (stop / end of text)
m.held()      # spoken words currently held back
m.states()    # per target word: pending | correct | misspelled | wrong | missed
m.extras()    # [(target position the word was said before, spoken word), ...]
m.finished()  # True once the pointer has passed the last target word
```

`Event(index, state, word=None)`: a target word at `index` changed to `state`.
For extra words, `state == "extra"`, `index` is the position in the target and `word` is what was said.

`normalize(text) -> list[str]` is used for both the target and the spoken words: it lowercases the text,
turns numbers into words (`5` -> five, `1984` -> nineteen eighty four, `2nd` -> second, `3.5`, `50%`),
currency into words (`$15,000` -> fifteen thousand dollars; also `£`, `€`), rejoins numbers that
Whisper's word list splits (`$15 ,000`, `20 %`), maps `per cent` to `percent`,
expands contractions (`it's` = `it is`, `don't` = `do not`, `can't`/`cannot` = `can not`),
drops possessive apostrophes (`john's` -> `johns`), splits on hyphens, underscores and punctuation,
and removes single letters other than `a` and `i`.

## Alignment

Each `feed` runs a semi-global edit-distance DP of the spoken words against
`target[pointer : pointer + len(spoken) + lookahead]`.

| Operation | Cost | Result |
|-----------|------|--------|
| Exact match | 0 | `correct` |
| Near-miss (rapidfuzz ratio >= 0.75, or same stem after stripping s/es/ed/ing/ly) | 0.3 | `misspelled` |
| Substitution | 1 | `wrong` |
| Target word skipped before a later aligned word | 1 | `missed` |
| Extra spoken word | 1 | added to `extras()` |
| Target words after the last spoken word | free | stay `pending` |

Ties on cost go first to the alignment with more exact matches, then to the one that skips fewer
target words, then to the one that advances further. The pointer moves past the last aligned
target word. Words already `correct` are never changed.

## Tail hold-back (streaming)

With `feed(words, final=False)`, spoken words at the end of the utterance that do not align as
`correct` or `misspelled` are not committed: they are held and prepended to the next `feed`. This
stops a trailing hallucination ("... the quick thank you") or an utterance cut right after an
extra/deleted word from marking the next target words `wrong`. Two exceptions:
- If the alignment already reaches the end of the target, nothing is held (no more words will come).
- If the next utterance starts with a restart (its first word would align as an extra), the held
  words are committed as they were first aligned, then the new utterance is aligned on its own.

`flush()` commits whatever is held. With `final=True` (default) behaviour is unchanged.

In live mode (`asr/live.py`) a "feed" is one live commit (~1 s of speech), so a held word waits
at most one live pass. After a pause (VAD end), `core.session` commits held words after
`HOLD_TIMEOUT` (1 s) of silence, so a wrong last word still shows ~1.6 s after the user stops.

## Who calls it (Task 04)

`asr/verify.py` runs before `feed`: the words come from Whisper WITHOUT the target text, and every
span the matcher would mark not-correct (`verify.disputes`, a preview alignment with the same
window and held words) gets an acoustic check: if the audio supports the target words about as
well as the heard words, the span is replaced by the target words before `feed`. The matcher
itself is unchanged.
