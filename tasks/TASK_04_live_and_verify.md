# Task 04: Honest transcription + acoustic check + live feedback

## Why
Task 03 showed two blockers (see claude/PROJECT_SPEC.md, section 4):
1. The text prompt can HIDE real mistakes: in para3 the user said "six minutes a week", turbo+prompt wrote "ten minutes ... a day". In para2 the user said "steps" (target "step"); the prompt hid it in some runs.
2. Feedback only arrives after a 0.6 s pause. The user read 55 words in 2 stretches, so mistakes showed only at the end.

Approved approach (user + Brain, 2026-11-09):
- Transcribe WITHOUT the target text (honest: cannot hide mistakes). Compare with the matcher.
- Only where the transcript disagrees with the target, ask the model an "acoustic check" question: does this audio support the TARGET words? (teacher-forced scoring: log-probability of the target words vs the heard words on the same audio). Supported -> correct (it was just a hard word, e.g. a name). Not supported -> keep the mistake.
- Live feedback: show words while the user speaks, ~1-2 s after each word.

## Hard limits
- Personal clips `clips/para1-3` are the HELD-OUT final exam: run them ONCE at the very end with the chosen config. Never tune thresholds on them.
- Do not touch `app.py`. Keep all existing tests passing.
- Everything local. No secrets in files.
- Keep `core/session.py` UI-free (the GUI in Task 05 reuses it).

## What to do (in priority order)

### 1. Practice set with REAL spoken mistakes (`tools/make_tts_mistakes.py`)
- Use Windows TTS (System.Speech / SAPI, any installed English voices, vary voice and rate) to speak a MODIFIED text while the target stays the ORIGINAL text. This is the case the public set could not test.
- Texts: the 13 public clip texts + ~10 short everyday paragraphs you write (numbers, times, days, quantities).
- Mistake types, about 150 in total, with `.truth.json` per clip:
  - plausible swaps that sound natural in context: numbers (ten->six), time words (day->week, morning->evening), opposites (big->small, open->close), close sounds (this->these, step->steps, then->than, a->the);
  - deletions, insertions, a repeated phrase / self-correction ("when you close, when you open");
  - 30% of clips with NO mistake (to measure false alarms), including texts with names and rare words.
- Save in `clips/practice_tts/`. Say clearly in the report that TTS is clean speech: it is a practice set, not proof.

### 2. Acoustic check (`asr/verify.py`)
- After an utterance is transcribed without a prompt and aligned, collect each disputed span (target words marked wrong/misspelled/missed, with the heard words around them).
- Score on the same utterance audio, with no prompt: log P(text with the TARGET span) vs log P(text with the HEARD span). Use the same model (large-v3-turbo). Use whatever gives teacher-forced token log-probabilities: the CTranslate2 Whisper API if it exposes scoring, otherwise the Hugging Face `transformers` version of the same model in fp16 (check VRAM: both must fit in 6 GB together with the transcriber).
- Rule: if the target span is about as likely as the heard span (margin above a threshold tau), flip it to `correct` (Whisper just misspelled a hard word). Otherwise keep the mistake. Tune tau on the practice set + public clips ONLY.
- A missed word (heard nothing there) can also be checked: score "with the word" vs "without it".

### 3. Benchmark the options (practice TTS set + public short/long sets)
Configs, all with large-v3-turbo + hold-back:
- A: prompt `auto:around` (current)
- B: no prompt
- C: no prompt + acoustic check  (expected winner)
- D: prompt + double check (re-transcribe without prompt; disagree -> acoustic check)
Metrics, reported separately per set: **hidden mistakes** (spoken mistake marked correct, the most important), mistakes caught with the right label, false alarms on correct speech, ms per utterance (transcribe + check). Pick a default by: fewest hidden mistakes, then fewest false alarms, then latency.

### 4. Live feedback (`asr/live.py`, used by `core/session.py`)
- While an utterance is open, re-transcribe the growing buffer about every 1 s (no prompt; previously committed words of THIS utterance may be given as a prefix if it helps stability).
- Commit a word only when two consecutive passes agree on it (LocalAgreement-2). Committed words go to the matcher (`final=False`) and the acoustic check, and are drawn immediately. Uncommitted words are not shown.
- Trim the buffer after committed words so passes stay short. Keep the VAD cut (0.6 s) as the hard end of an utterance; at the end, run the normal final pass.
- Measure display latency on WAV files replayed in real time: time from the end of a spoken word (word timestamps) to when its colour is shown. Targets: median <= 1.5 s, 90th percentile <= 2.5 s. The GPU must keep up (no growing queue, report `MicSource.overflows`).
- Hold-back (Task 03) must not delay a word by more than one live pass.

### 5. Wire it in
- `reciteflow.py`: default = chosen config + live mode. Flags `--mode A|B|C|D`, `--no-live`.
- Update `matcher/README.md`/docstrings briefly.

### 6. Final exam (once, at the end)
- Run para1-3 with the chosen config (live, real-time replay). Known truth from the user: para3 said "six" for ten, "week" for day, "big" for small, "teaching" for learning, and self-corrections ("every book, every day", "your way, your life", "when you close, when you open"); para2 said "steps" for step. Report what was caught, hidden and falsely flagged, and the live latency numbers.

## Checks
- `python -m pytest -q` passes (old + new tests: verify rule on synthetic score inputs, LocalAgreement commit logic, buffer trimming).
- Benchmark table for A/B/C/D saved to `tasks/TASK_04_benchmark.csv`.
- Live latency numbers measured, not estimated.

## WIP protocol
- After each numbered step, update `tasks/TASK_04_progress.md` (done / in progress / next / anything to resume from).
- When done, write `tasks/TASK_04_report.md`: top line first, evidence (tables, numbers), rules changed or interpreted, problems, questions for the Brain, exact commands for the user. Then set `tasks/NEXT.md` Status to DONE.

## Git
- No git repo yet (user choice). Work in place.
