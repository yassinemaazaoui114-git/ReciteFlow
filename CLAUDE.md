@PROJECT_CONTEXT.md

# ReciteFlow — rules for Claude Code

## How we work
Yassine directs Claude Code directly, in plain requests ("change X", "add Y", "why does Z happen?"). There is no
separate planner chat, no task files, no approval queue and no written reports. Do what is asked, test it, tell
Yassine what changed. Each request usually starts in a fresh conversation: read `PROJECT_CONTEXT.md` (loaded above)
first, and open `SPEC.md` only when the request touches a decision, a number or an open question.
- Do what was asked, no more: no extra features. New libraries and cloud services are allowed (Yassine, 2026-11-12);
  say which one and why when you add it.
- If a request is unclear, contradicts `SPEC.md`, or needs a design decision, ask one short question before building.
  Give options with exactly one recommended.
- Explain results in simple words. When a change teaches a speech/ML idea, add one short sentence about it.
  Skip it when there is nothing to learn.
- **Keep the final message short.** Only what Yassine needs to know or do: what changed (one or two lines), whether
  tests passed, measured numbers if accuracy/latency changed, anything that failed, and any decision needed from him.
  No file-by-file tour, no repeating what he asked. Details only if he asks.
- When a decision, number or status changes, update `SPEC.md` (the item + a change-log row, flag Confirmed or
  Assumption). Keep `PROJECT_CONTEXT.md` in sync when status, stack, commands or rules change.
- `tasks/` and `claude/` are old history from the previous workflow (Tasks 01–04: task files, reports, benchmark
  CSVs). Read them for background; do not add to them.

## What this project is
ReciteFlow: a real-time memorization app. Yassine recites a whole paragraph in one go; the app follows
along word by word and marks every mistake (wrong, missed, extra, misspelled). English only. Python 3.12 on Windows,
RTX 4050 (6 GB VRAM). Goal: "nearly perfect", proven by benchmarks, not by feel.

## Sources of truth
- `SPEC.md` = all decisions (requirements R1–R16, architecture, benchmark results, decision log, open questions,
  change log). It wins over everything else.
- `PROJECT_CONTEXT.md` = the short overview: status, stack, layout, commands, current numbers.
- Code tells the rest: `matcher/README.md`, docstrings in `asr/`, `core/session.py`.

## Hard rules
1. **Accuracy first, measured.** Any change to ASR, VAD, matcher, check or live logic must be benchmarked
   (`tools/benchmark_verify.py`, `tools/live_latency.py`) and the before/after numbers reported. Never claim an
   improvement without numbers. Most important metric: **hidden mistakes** (a spoken mistake shown as correct), then
   false alarms, then latency.
2. **Held-out data is sacred.** `clips/para1-3` were used once as the final exam (Task 04). Do not tune on them.
   Practice = `clips/practice_tts/` + `clips/public/`. New final checks need new recordings from Yassine.
3. **Fix in code, not with one-case hacks.** No word blacklists, no special cases for one clip.
4. **Never touch `app.py`** (old V8, reference only).
5. **`core/session.py` stays UI-free** (terminal and future GUI both reuse it).
6. **Encoding:** write files as UTF-8 without BOM. Never edit source files through PowerShell text commands.
7. **No secrets or private data in files** (API keys go in environment variables, never in the code).
8. Do not run two GPU-heavy jobs at once (benchmarks, live app); the 4050 has 6 GB.

## Testing
After finishing a change:
1. `python -m pytest -q` passes (99 tests at the last count; never delete a test to make it pass).
2. If engine behaviour changed: run the relevant benchmark and compare with the numbers in `SPEC.md`.
3. If the terminal app changed: replay a clip, e.g.
   `python reciteflow.py --file clips/para1.txt --wav clips/para1.wav --realtime`, and check the redraw and summary.
4. If anything fails and you cannot fix it, stop and tell Yassine what failed.

## Git
No git repo yet (Yassine's choice). Work in place. Ask before adding git. If git is added later: small clear
commits, never rewrite history, never commit clips, models or secrets.

## Folder layout
```
CLAUDE.md  PROJECT_CONTEXT.md  SPEC.md  requirements.txt  reciteflow.py  gui.py  app.py (old, do not touch)
matcher/   normalize + alignment (states: pending/correct/misspelled/wrong/missed, extras, hold-back)
asr/       audio.py (mic/WAV), vad.py (Silero), engine.py (faster-whisper + scoring), verify.py (acoustic check),
           live.py (LocalAgreement-2 live passes), prompt.py, _cuda.py (Windows DLL fix)
core/      session.py (UI-free: text in, state events out), history.py (SQLite history + corrections)
tools/     record_clip, fetch_samples, make_mistakes, make_tts_mistakes, benchmark, benchmark_verify, live_latency,
           error_analysis
tests/     pytest suite
data/      history.db + recordings/ (Yassine's private recitations: never share, never tune held-out checks on them)
benchmarks/ error_analysis.csv
clips/     public/ (LibriSpeech short+long), practice_tts/ (TTS with spoken mistakes), practice_restart/ (TTS re-recites), para1-3 (held-out, used)
tasks/ claude/   old history (Tasks 01-04), read-only
```
