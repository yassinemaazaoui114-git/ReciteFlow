# Task 03: First end-to-end ReciteFlow (terminal) + matcher tail fix + long-text prompt

## Context
Read claude/PROJECT_SPEC.md and tasks/TASK_02b_report.md first. Follow the builder protocol in tasks/NEXT.md. Default ASR = `large-v3-turbo`, `int8_float16`, prompt = target text. Do not break existing tests.

## Part A: Matcher tail hold-back (fixes the known weakness)
- Add `Matcher.feed(words, final=True)`. With `final=False`, spoken words at the END of the utterance that do not align as correct/misspelled are held back (not committed) and prepended to the next `feed`. `Matcher.flush()` commits anything held (call at stop/end of text).
- Goal: a trailing hallucination ("... the quick thank you") or an utterance cut right after a deleted word must no longer mark the next target words `wrong`.
- Tests: the two cases above, plus all 17 existing matcher tests still pass with `final=False` used everywhere in streaming.

## Part B: Prompt for long texts
- Whisper keeps only the last ~223 prompt tokens, so `full` loses the start of long paragraphs (it keeps the END, the worst part).
- Implement prompt modes in `asr`: `full`; `back` (last ~40 target words BEFORE the matcher pointer, i.e. already recited); `around` (from 30 words before the pointer to ~100 words after, capped to stay under ~200 tokens). Rule: if the whole text fits under ~200 tokens use `full`, else use the configured long-text mode.
- Build 3 LONG public clips (200-400 words each, by joining more LibriSpeech utterances of one speaker; extend `fetch_samples.py` with an option) + mistake variants, and benchmark turbo with `full`, `back`, `around` on them, plus the existing short clips to confirm no regression. Pick the best long-text mode by: accuracy, then mistakes caught, then false alarms.

## Part C: `reciteflow.py` (terminal app)
- `python reciteflow.py --file text.txt` or `python reciteflow.py` then paste text and press Enter twice.
- Loads turbo once (with warm-up), starts mic, VAD, recognizer, matcher with `final=False`, prompt per Part B.
- After each utterance, redraws the whole target text (clear screen, colorama): `correct` green, `wrong` red, `missed` red in `[brackets]`, `misspelled` yellow, `pending` white/grey, extras shown in red `{braces}` at their position.
- Keeps going after mistakes (no stop). Ends when the text is finished (then `flush`) or on Ctrl+C.
- End summary: counts per state, extras, total time, mean latency.
- Options: `--model`, `--prompt-mode`, `--silence` (VAD pause), `--device` (mic).
- Keep the UI logic separate from the engine (a small `core/session.py` that the future GUI will reuse: takes text, emits state events).

## Done when
- All tests pass; benchmark for Part B ran; `reciteflow.py` works live (a quick smoke run is enough).
- Write `tasks/TASK_03_report.md`: what was added, rules changed/interpreted, test results, the Part B table + chosen long-text mode, known weaknesses, exact commands for the user.
