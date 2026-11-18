# Task 01: Matcher engine (no audio, no GPU)

## Goal
A pure-Python module that compares a stream of recognized words against a target paragraph and labels every target word. Must be fully testable without a microphone, torch or Whisper.

## Setup
- Folder: `memorization_app/`. Keep old `app.py` untouched (reference only).
- New package: `matcher/` with `normalize.py`, `align.py`, `__init__.py`; tests in `tests/`.
- Dependencies allowed: `rapidfuzz`, `num2words`, `pytest`. Nothing else.

## Normalization (`normalize.py`)
`normalize(text) -> list[str]`: lowercase; strip punctuation; split hyphens/underscores; numbers to words ("5" -> "five", "1984" -> "nineteen eighty-four" style handled by num2words, then tokenized); expand common contractions consistently on both sides ("it's" and "it is" must compare equal); remove stray single letters except "a" and "i". Same function is used for target and spoken text.

## API (`align.py`)
```python
class Matcher:
    def __init__(self, target_text: str, lookahead: int = 15): ...
    def feed(self, spoken_words: list[str]) -> list[Event]: ...   # one finished utterance
    def states(self) -> list[str]   # per target word: pending|correct|misspelled|wrong|missed
    def extras(self) -> list[tuple[int, str]]  # (position in target before which it was said, word)
    def finished(self) -> bool
```
Events describe what changed (target index, new state) so a UI can redraw.

## Algorithm
- Semi-global alignment (edit distance DP) of the new spoken words against target words starting at the current pointer, window = `lookahead` words.
- Costs: exact match 0; near-miss (rapidfuzz similarity >= 0.75, also phonetic-ish like foxes/fox) 0.3 -> `misspelled`; substitution 1 -> `wrong`; skipped target word 1 -> `missed`; extra spoken word 1 -> extra.
- Trailing target words after the last spoken word are FREE (not yet spoken, stay `pending`, never marked missed).
- Target words are marked `missed` only if a later target word was matched in the same alignment.
- Pointer advances past the last aligned target word. Repeated words ("that that") are handled by alignment, no cooldown hacks. No hallucination blacklist.
- Never mutate states already `correct` from earlier feeds.

## Tests (pytest, must pass)
1. Perfect recitation of a 3-sentence paragraph with punctuation, numbers and contractions -> all `correct`.
2. Same, fed in random-sized chunks (1 to 8 words) -> identical result.
3. One word wrong -> that word `wrong`, rest `correct`.
4. One word skipped -> `missed`, rest `correct`. Two consecutive skipped -> both `missed`.
5. Extra word inserted -> in `extras()`, all target words `correct`.
6. Near-miss ("foxes" for "fox") -> `misspelled`.
7. Repeated legit words in target ("that that is") handled correctly.
8. Words "for", "you", "thank" in the target are matched normally.
9. Stopping mid-paragraph -> remaining words `pending`, none `missed`.
10. Speaker restarts a sentence after a mistake -> app keeps going, no crash, states sensible (exact rewind behavior is NOT required yet).
11. 500-word paragraph feed runs in well under 1 second total.

## Done when
- `pytest` passes, no audio/torch imports anywhere in `matcher/`.
- Short `matcher/README.md` with the API and the cost table.
- Report any rule above you had to change and why.
