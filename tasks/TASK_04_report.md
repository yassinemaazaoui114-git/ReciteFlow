# Task 04 report: honest transcription + acoustic check + live feedback

**Top line:** Default is now **mode C** (no prompt + acoustic check), **live**.
- **Final exam (para1-3, run once): every known mistake was caught, including six->ten.** The prompt hid six->ten in Task 03.
- Live latency in real time: **median 1.20 s, p90 1.95 s**, no backlog. Practice set (simulated real time, matches real time within 0.06 s): median 1.18 s, p90 1.48 s.
- On the TTS practice set the old prompt mode A hides **15 of 152** real spoken mistakes; C hides **4**.
- 77/77 tests pass.

Main open weakness: **live mode has more false alarms than utterance mode** (67 vs 27 on the practice set).

## Evidence

### Step 3: A/B/C/D benchmark (large-v3-turbo int8_float16, hold-back on, tau 1.0), `tasks/TASK_04_benchmark.csv`
Utterance mode (one update per VAD pause), same utterances for every mode.

| Mode | Set | Mistakes | Right label | Other label | **Hidden** | False alarms | ms/utt |
|---|---|---|---|---|---|---|---|
| A prompt | practice TTS (52 clips) | 152 | 133 | 4 | **15** | 15 | 608 |
| B none | practice TTS | 152 | 141 | 7 | **4** | 50 | 548 |
| **C none + check** | practice TTS | 152 | 141 | 7 | **4** | 27 | 550 |
| D double check | practice TTS | 152 | 141 | 7 | **4** | 33 | 1059 |
| A | public short (10), original / mistakes | 40 | 40 | 0 | 0 | 3 / 3 | 363 |
| B | public short | 40 | 39 | 1 | 0 | 31 / 28 | 430 |
| **C** | public short | 40 | 39 | 1 | 0 | 15 / 17 | 463 |
| D | public short | 40 | 39 | 1 | 0 | 15 / 17 | 892 |
| A | public long (3), original / mistakes | 24 | 24 | 0 | 0 | 8 / 8 | 456 |
| B | public long | 24 | 24 | 0 | 0 | 10 / 10 | 411 |
| **C** | public long | 24 | 23 | 0 | **1** | 2 / 2 | 481 |
| D | public long | 24 | 24 | 0 | 0 | 7 / 7 | 907 |

How to read the sets:
- **Public "mistakes":** the audio is correct and the text was edited (as in Task 02b).
- **Practice:** the audio really contains the mistakes, and the target text is the original.

Why C:
- **Fewest hidden mistakes where it matters.** Practice set: A 15, and B/C/D 4 each.
- **Fewest false alarms among B/C/D.** Practice 27 (B 50, D 33). Short 15/17, the same as D. Long 2, the best of all modes.
- **Half of D's latency.**
- **C's one hidden public mistake is not a speech mistake.** The old injected edit "darkness" -> "darknesss" is accepted by the doubled-letter spelling rule. No audio can tell those two apart, so I did not count it against C.
- **A's low false-alarm count is the prompt hiding things.** It writes the target text into the transcript, which is exactly how it hides mistakes.

The 4 that B/C/D all hide are ASR-level, the same with or without the check:
- the -> a (TTS / Whisper)
- dollars -> dollar
- one TTS self-correction
- one short morph

The check accepted 47 of 292 disputed spans: 21 by the spelling rule and 26 by score. Only 1 accepted span was a real mistake (the "darknesss" artifact), per `tasks/TASK_04_checks.csv`.

### Step 2: tau tuning (practice + public only), `tasks/TASK_04_tau_sweep.csv`
This ran before the matcher fixes below. False alarms fall as tau grows, and hidden mistakes stay at 4 up to tau 1.0:

| tau | 0 | 0.5 | **1.0** | 1.5 | 2.0 | 3.0 |
|---|---|---|---|---|---|---|
| practice hidden | 4 | 4 | **4** | 6 | 6 | 14 |
| practice FA | 42 | 42 | **37** | 32 | 30 | 26 |

Rule: the largest tau that adds no hidden mistake (B had 4). Measured margins: real mistakes sit mostly at -43..-2. Misheard names and rare words sit mostly at -5..+8.

### Step 4: live feedback
Practice set, `tasks/TASK_04_live_sim.csv`, 3741 words: median **1.18 s**, p90 **1.48 s**, max 3.46 s, max backlog 0.86 s.
- 15 passes were skipped to catch up, and the queue never grew.
- Accuracy: 4 hidden (the same as utterance mode), 67 false alarms.

Real-time replay check (6 clips, `tasks/TASK_04_live_realtime.csv`): median 1.23 s, p90 1.90 s. Per clip, it matches the simulated clock within 0.01-0.06 s, with identical flags.

Pass interval: 1.0 s gave a median of 1.87 s, which misses the target. **0.6 s** gives about 1.2 s (`TASK_04_live_sim_i1.0.csv` / `_i0.6.csv`, both from before the matcher fixes). A pass is about 450 ms, most of it the 30 s Whisper encoder window, so the GPU is about 75% busy while you speak.

`MicSource.overflows` was not measured: there was no live mic run. Backlog was measured as processing time vs audio time instead, max 0.86 s.

### Step 6: final exam, para1-3, run ONCE (C, live, real-time replay), `tasks/TASK_04_final_exam.csv`
| Clip | Flags | Live latency |
|---|---|---|
| para1 | wrong:this, misspelled:step | median 1.24 s, p90 2.49 s |
| para2 | misspelled:step | median 1.15 s, p90 1.46 s |
| para3 | wrong:day@2, wrong:open, wrong:just, wrong:ten, wrong:day@27, wrong:small, misspelled:step, missed:makes, wrong:learning, +way +your | median 1.21 s, p90 1.84 s |

Against your known truth:
- **Caught:** ten (six), day (week), small (big), learning (teaching), "every book, every day" (day@2), "your way, your life" (+way +your), "when you close, when you open" (open), para2 steps.
- **Hidden: none.**

Unknown truth. To check, I made one read-only no-prompt transcription; nothing was changed based on it:
- **para1 "this" + "step":** heard "these small daily steps". Probably real, and it answers the para1 step/steps question. Please listen.
- **para3 "just":** heard "justice six" (= "just six"). This is a false alarm from the ASR.
- **para3 "makes":** the ASR hears it at the end of an utterance. Live mode lost it at a buffer cut, so this is a false alarm from the live pipeline.
- **para3 "step":** it is flagged in all 3 clips, so you probably say "steps" every time.

## What was added / changed
| File | What |
|---|---|
| `tools/make_tts_mistakes.py` | Practice set: 52 clips in `clips/practice_tts/`. 17 have no mistake (33%). 152 mistakes: 60 swaps, 22 morphs, 28 deletions, 11 insertions, 31 self-corrections. Texts: 11 everyday paragraphs + 13 public texts. |
| `asr/verify.py` | `disputes`, `Verifier` (the acoustic check plus a spelling-variant rule), `Checker` for modes A/B/C/D. |
| `asr/engine.py` | `transcribe_words` (word timestamps). `sequence_logprobs`: teacher-forced log P(text \| audio) via ctranslate2 `Whisper.align`. It reuses the encoder output of the transcription, so a check costs about 15-25 ms. Same model, no `transformers`, no extra VRAM. |
| `asr/live.py` | `LiveTranscriber`: LocalAgreement-2, buffer trim (5 s), own-words prompt after a trim, catch-up skipping, `RealClock`/`SimClock`. |
| `asr/words.py`, `asr/vad.py` | `Word` type (no GPU imports); `Segmenter.current()`. |
| `core/session.py` | `mode`, `live`, `tau`, `live_interval`, display-lag tracking, live summary. `DEFAULT_MODE = "C"`, `LIVE_INTERVAL = 0.6`. |
| `reciteflow.py` | Default: C + live. `--mode A\|B\|C\|D`, `--no-live`, `--tau`. A/D fall back to non-live. |
| `matcher/normalize.py` | `$15 ,000` -> fifteen thousand dollars (also £, €); `20 %`; `per cent`. |
| `matcher/align.py` | Restart rule (see below). |
| `tools/benchmark_verify.py`, `tools/live_latency.py` | Step 3 and step 4/6 benchmarks. |
| tests | `test_verify.py` (12), `test_live.py` (6), +3 matcher tests. 77 total; all old tests unchanged. |

## Rules changed / interpreted (please review)
1. **Mode D** is defined this way: use the unprompted words. Acoustic-check a disputed span only where the prompted pass agreed with the target. Where both passes disagree with the target, keep the mistake with no check.
2. **The check runs on normalized lowercase text with no punctuation.** Each disputed span is scored alone against the heard text, all in one batch. A missed word is scored "with the word" vs "without it". Extra words are scored "without them" vs "with them".
3. **Spelling-variant rule (my addition).** Spans that are the same letters with spaces removed ("court yard"/"courtyard") or with doubled letters collapsed ("pencilled"/"penciled") are accepted without scoring. At the end of a live chunk, a span may take up to 3 following target words to complete a compound.
4. **Matcher restart rule (my addition, needed for live).** If a feed starts with exactly the last k target words already passed, and dropping that repeat makes the alignment cheaper, the repeat is dropped. Limits: k ≤ 8; k = 1 only if that word was not correct. The re-said words change no state, so a wrong word stays wrong, as in R14 (no underline yet). They are also not counted as extras. Without this rule, small live chunks turned every self-correction into a run of wrong words.
   - Side effect: a self-correction now shows as the slipped word marked wrong, instead of as {extras}. This is why "other label" went up.
5. **Hold-back in live mode.** Inside an utterance, a held word waits at most one live pass, as required. **After a pause, held words wait for the next utterance** (the Task 03 rule). I tried committing them after 1 s of silence. It committed a half-said self-correction ("at last…") as wrong words, and the rest of the text shifted. So a wrong word said right before a pause shows when you continue, or at stop. Constant: `HOLD_TIMEOUT` in `core/session.py`.
6. **The live pass interval is 0.6 s, not about 1 s.** At 1 s the latency target was missed.
7. **Selfcorrect scoring:** one self-correction counts as one mistake. It is caught if any of its words is flagged.

## Problems / known weaknesses
1. **Live false alarms are 2.5x those of utterance mode** (67 vs 27 on practice). Causes still left:
   - words lost or doubled at buffer cuts (para3 "makes");
   - self-corrections that don't repeat the target exactly;
   - small chunks giving the alignment less context.
   
   The utterance mode (`--no-live`) is the more precise option.
2. **TTS is clean speech from one voice.** Only Microsoft Zira is installed; I varied rate and pitch. The practice set is for tuning, not proof. The real proof is the personal clips: 3 clips and 55 words each.
3. **The 4 mistakes hidden at the ASR level** (a/the, dollar(s)…) are not fixed by the check. The check only runs where the transcript already disagrees with the target.
4. **Forced cuts (25 s cap) in live mode** can drop a word near the cut (seen in a unit test). Utterances that long are rare.
5. **No live mic test.** I cannot speak, so all latency numbers come from WAV replay. The mic path code is the same as in Task 03.
6. **GPU load:** about 75% busy while speaking (0.6 s passes, ~450 ms each). It kept up on this laptop (RTX 4050), with max backlog 0.86 s.

## Questions for the Brain
1. Accept live as the default despite the higher false-alarm rate? Or make `--no-live` the default until the live false alarms are fixed? A middle option: live colours while speaking, then re-align each utterance as a whole at the pause, and let the utterance result override.
2. Are rules 4 (restart) and 5 (no hold timeout) OK as the base for R12/R14 re-recite handling in a later task?
3. para1 "these small daily steps": please ask the user to listen. Is it a real slip?
4. Should the practice set get more voices? This needs installing Windows voices, or using a local neural TTS.

## Commands for you
```
python -m pytest -q                                         # 77 tests
python reciteflow.py --file clips/para1.txt                 # live, mode C (default)
python reciteflow.py --file clips/para1.txt --no-live       # update after each pause (fewer false alarms)
python reciteflow.py --file t.txt --mode A --no-live        # Task 03 behaviour
python reciteflow.py --file clips/para3.txt --wav clips/para3.wav --realtime   # replay
python tools/make_tts_mistakes.py                           # rebuild the practice set (~2 min)
python tools/benchmark_verify.py                            # A/B/C/D table (~16 min)
python tools/live_latency.py                                # live latency + accuracy, simulated real time (~6 min)
```
