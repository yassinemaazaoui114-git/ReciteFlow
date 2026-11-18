# ReciteFlow — project context (read this first)
Updated: 2026-11-12

## In one line
Local, real-time recitation checker: Yassine recites a paragraph from memory into the mic; the app shows the text and
colours each word as he says it (green correct, red wrong, red [missed], yellow misspelled, red {extra}, grey not yet
said). Runs locally (cloud services and new libraries allowed since 2026-11-12), English only.

## Status
- Engine done and benchmarked (Tasks 01–04 + live re-align + re-recite, 99 tests). Terminal app works: `reciteflow.py`.
- GUI first version works: `gui.py` (CustomTkinter, dark): text box, Start/Stop, live coloured words, counters,
  mic picker. Same engine and defaults as the terminal app (engine in a worker thread, window polls a queue).
  Checked by replaying `clips/this_step` (55/55) and by Yassine with the mic (2026-11-12, good).
- History (`core/history.py`): every mic recitation saved in `data/history.db` (SQLite) + its WAV in
  `data/recordings/` while "Keep my recordings for training" is ticked. After a recitation, click a word in
  the GUI to correct the app; corrections are the labels for future fine-tuning.
- Reciting from memory (GUI, both on by default): "Hide the text while I recite" (words appear only once
  said, all shown after Stop) and "Beep on a mistake" (live, ~1.3 s after the word; a live false alarm can beep).
- Re-recite: after a mistake you can go back (sentence start, up to 25 words) and say it again; the mistake
  stays red and is underlined (recovered). Restart practice set: false alarms 337 -> 53, hidden 2 -> 2 per pause
  (live idle-GPU run 2026-11-12: 1 -> 2, one clip, open item in SPEC section 7).
- Default pipeline ("mode C + live"): mic → Silero VAD → faster-whisper `large-v3-turbo` (int8_float16) **without**
  the target text → matcher (edit-distance alignment) → acoustic check on disagreements (teacher-forced score of
  target words vs heard words, tau 1.0) → screen redraw every 0.6 s (LocalAgreement-2) → at each pause the whole
  utterance is re-transcribed + checked and overrides the live colours.
- Why no text prompt: giving Whisper the text made it "hear" the text and hide real mistakes (said "six", wrote "ten").
- Final exam on Yassine's own clips (para1-3, run once): every known mistake caught, none hidden.
- Live latency: median ~1.3 s, p90 ~1.6 s after each word; pause override ~1.4 s (p90 2.1 s) after the utterance. GPU ~75% busy while speaking, ~1.5 GB VRAM.

## Next — resume point (stopped 2026-11-12)
Where we stopped: the app is usable. GUI (`python gui.py`) tested by Yassine with the mic on 2026-11-12:
typed a sentence, recited from memory (text hidden, beep on), all words correct; further mic tests reported "done"
(no problems reported). Nothing is half-built; 99 tests pass at the last run.

To-do, in recommended order (ask Yassine which one when he comes back):
1. Open matcher item `daily_bakery_r2` (SPEC section 7): a re-recited mistake shown as correct (hidden 1 -> 2 on
   the restart set). Fix in the matcher, benchmark on practice sets only.
2. GUI polish (whatever annoyed Yassine while using it).
3. Benchmark streaming (live-first) ASR models vs Whisper live: latency, hidden mistakes, false alarms.
4. Installer: packaged v1 (setup.exe) installed apart from this folder.
Other open items: see `SPEC.md` sections 7–8.

## Key numbers (details in SPEC.md)
| Setup | Hidden mistakes (152 spoken, TTS practice) | False alarms (practice) |
|---|---|---|
| A: text as prompt (old) | 15 | 15 |
| C: no prompt + acoustic check, per pause only (`--no-live`) | 4 | 27 |
| C live, no re-align (old) | 4 | 70 |
| C live + re-align at pauses (default) | 4 | 28 |
Public LibriSpeech: C catches 39-40/40 short and 23-24/24 long injected mistakes.

## Stack
Python 3.12, Windows, RTX 4050 6 GB. faster-whisper 1.2.1 (ctranslate2), silero-vad, sounddevice, numpy, soundfile,
rapidfuzz, num2words, colorama, customtkinter, pytest. CUDA via pip `nvidia-cublas-cu12` / `nvidia-cudnn-cu12` (fixed in
`asr/_cuda.py`; no system CUDA needed). Models cached in `~/.cache/huggingface/hub`.

## Commands
```
python -m pytest -q                                          # tests
python gui.py                                                # window app (paste text, Start/Stop)
python gui.py --file t.txt --wav rec.wav                     # window app replaying a recording (testing)
python reciteflow.py --file clips/para1.txt                  # live app, mode C (default)
python reciteflow.py --file clips/para1.txt --no-live        # update only after each pause (no live colours)
python reciteflow.py                                         # paste a text, Enter on an empty line
python reciteflow.py --list-devices                          # then --device N
python reciteflow.py --file t.txt --wav rec.wav --realtime   # replay a recording
python tools/record_clip.py NAME                             # record a clip + reference text into clips/
python tools/benchmark_verify.py                             # modes A/B/C/D table (~16 min)
python tools/live_latency.py                                 # live latency + accuracy (~6 min)
python tools/live_latency.py --sets practice,restart        # incl. the re-recite set (recovered x/33)
python tools/make_tts_mistakes.py --restarts                 # rebuild clips/practice_restart
python tools/error_analysis.py                               # cause of each hidden mistake / false alarm (~6 min)
python reciteflow.py --file t.txt --no-audio                 # save the result but not the recording (--no-save: nothing)
```

## History (one line each; full reports in tasks/)
- 01 matcher + normalization · 02 mic, VAD, faster-whisper, CUDA fix · 02b public benchmark → turbo wins ·
  03 terminal app, tail hold-back, long-text prompt · 04 no-prompt + acoustic check, live mode, TTS practice set,
  final exam passed.
- Old V8 (`app.py`, Gemini era): fixed 1.5 s chunks, word blacklist, greedy pointer — replaced, kept as reference.
