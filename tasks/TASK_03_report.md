# Task 03 report: terminal app + matcher tail fix + long-text prompt

**Top line:** all three parts are built and 54/54 tests pass. The tail hold-back fixes the deletion-at-a-pause weakness (long clips: 22 → 24 of 24 mistakes caught with the right label). The chosen long-text prompt mode is **`around`** (default `auto:around`). `reciteflow.py` works end to end from a WAV and opens the mic live. **One new risk from your personal clips:** on para3, a forward-looking prompt (`full` and `around`) hid a real mistake (said "six", heard "ten").

## What was added
| File | What |
|---|---|
| `matcher/align.py` | `feed(words, final=False)`, `flush()`, `held()`. A bad tail (words not correct/misspelled) is held and prepended to the next feed. |
| `asr/prompt.py` | `PromptBuilder` with modes `none`, `full`, `back`, `around` and `auto:<mode>` (`full` if the text fits under 200 tokens). |
| `asr/engine.py` | `Recognizer.count_tokens(text)` (real Whisper tokenizer). |
| `core/session.py` | `Session(text, recognizer, prompt_mode)`: `process(audio)` → `Update`, `run(blocks, segmenter)`, `finish()`, `display()`, `summary()`. No UI code; the GUI can reuse it. |
| `reciteflow.py` | Terminal app (colorama, clears and redraws the screen after each utterance). Also `--wav/--realtime` for test runs and `--list-devices`. |
| `tools/fetch_samples.py --long` | 3 long clips in `clips/public/long/`: 2 clean + 1 other, 300 / 320 / 335 words (327-380 tokens). |
| `tools/make_mistakes.py` | Texts of 200+ words get 8 edits (2 of each type). Short clips' files are byte-identical to before. |
| `tools/benchmark.py` | Group `long`, modes `back`/`around`, hold-back on by default (`--no-holdback` for Task 02b behaviour), `--groups`, `prompt_tokens` column. |
| tests | `test_matcher.py`: every test runs with `final=True` and `final=False`, plus 5 hold-back tests. New `test_prompt.py` (5) and `test_session.py` (2). |

## Rules changed / interpreted
1. **Hold-back rule.** Words after the last correct/misspelled alignment are held, with two exceptions (my additions, needed for the old tests and for the end of the text):
   - **Restart:** if the next utterance's first word would align as an extra, the held words are committed as first aligned. Without this, test 10 (restart after "wolf") would turn "fox" green.
   - **End of text:** if the alignment already reaches the last target word, nothing is held. Otherwise a wrong last word would never commit and the text would never "finish".
2. **Held words show nothing until the next utterance or the stop.** A wrong final word in an utterance appears about one utterance later.
3. **`back` gives no prompt at pointer 0** (nothing recited yet), as specified. This is why it is weak on short texts.
4. **`around` trimming:** cut 5 words at a time from the end until it fits under 200 tokens.
5. **Long clips were sized at 300-335 words**, inside the 200-400 range but well past the 223-token limit. The first fetch only gave 205-212 words, barely over the limit.
6. **Display:** a raw word covering several normalized words ("It's" = it is) shows the worst state. Extras are shown before the word they were said before.
7. **Paste input ends at the first empty line** (Enter twice after the text).

## Test results
`python -m pytest -q` → **54 passed, 0 failed**: matcher 31 (3 normalize + 14 streaming tests × final/streaming), hold-back 5, ASR 11, prompt 5, session 2. Previously 28.

## Part B benchmark (large-v3-turbo int8_float16, hold-back on) — `tasks/TASK_03_benchmark.csv`
acc / FA on the original texts. Caught / FA_m on the mistake texts (FA_m = false alarms on unedited words).

| Prompt | Group | Word acc | FA | Caught (right label) | FA_m | ms/utt |
|---|---|---|---|---|---|---|
| full | clean (5 short) | 99.8% | 1 | 20/20 | 1 | 330 |
| full | other (5 short) | 99.5% | 2 | 20/20 | 2 | 419 |
| full | **long (3)** | 99.1% | 10 | 24/24 | 11 | 435 |
| back | clean | 97.9% | 9 | 20/20 | 8 | 330 |
| back | other | 96.5% | 16 | 20/20 | 15 | 460 |
| back | **long** | 99.0% | 10 | 24/24 | 11 | 420 |
| around | clean | 99.8% | 1 | 20/20 | 1 | 330 |
| around | other | 99.5% | 2 | 20/20 | 2 | 441 |
| around | **long** | **99.2%** | **8** | 24/24 | **8** | 389 |

**Chosen long-text mode: `around`.** On the long clips it has the best accuracy, ties on mistakes caught, and has the fewest false alarms. Caveat: the margin over `full`/`back` on long clips is 1-3 words out of 955. That is real but small: 3 clips, about 0.1% per word.

On the short clips, `around` equals `full`, since the window covers the whole text. In the app the auto rule uses `full` there anyway. `back` is clearly worse on short texts (−2 to −3%): with no prompt for the first utterance, names and rare words come back as false alarms.

**No regression on short clips:** turbo + full is 99.6% across clean+other combined (same as Task 02b), 40/40 caught, 3 FA_m (same).

### Hold-back effect (same runs with `--no-holdback`, `tasks/TASK_03_benchmark_noholdback.csv`)
| | Long, caught right | Long, deletes right | Personal, acc (full) | All, FA (full) |
|---|---|---|---|---|
| Without hold-back | 22/24 | 4/6 | 93.9% | 38 |
| With hold-back | **24/24** | **6/6** | **96.4%** | **34** |

### Personal clips (the benchmark also found `clips/para1-3`; one run, not tuned on)
They are short (55 words, 61 tokens), so in the app they always use `full`.
| Prompt | para1 | para2 | para3 (has real mistakes) |
|---|---|---|---|
| full | 100% | 98.2% (+ "we'll be right back" as 5 extras) | see below |
| back | 94.5% | 98.2% | see below |
| around | 100% | 98.2% (same 5 extras) | see below |

- **para2:** turbo hallucinated "We'll be right back" during a pause, with `full`/`around` prompts and not with `back` or no prompt. With hold-back these show as `{extras}` instead of marking the next words wrong (that is the 93.9 → 96.4% above).
- **para3** (your notes: close→open, six→ten, big→small, teaching→learning):
  - **big→small and teaching→learning:** caught as `wrong` in every mode.
  - **six→ten:** **hidden by `full` and `around`.** The transcript was "just ten minutes a day six minutes a week", so the prompt added the target words in front of what you said. With `back` (and with no prompt) the transcript was "justice. six minutes a week", so ten was marked wrong.
  - **close→open:** with `full` the transcript was "when you close, when you open", so close was caught as extras. With `around`/`back`/no prompt it was heard as "open", so it was not caught. Did you self-correct? This needs listening.
  - Other flags on para3 ("every book", "your way, your life", "day" wrong): the ASR heard the "every book… every day" and "your way, your life" sequences in all modes, so I believe you said them. This needs listening.

## Known weaknesses
1. **A forward-looking prompt can hide a real mistake when the wrong word sounds plausible** (para3, six→ten). This is the "near-sounding wrong word" case Task 02b could not test. The public mistake injection does not catch it, because there the audio is correct and the text is changed. It is the main open risk for the "nearly perfect" goal. Options are for the Brain (see questions).
2. Held words appear about one utterance late. A wrong word right before you stop shows only at Ctrl+C.
3. Long-text sample is small (3 clips, 24 edits). The mode choice margin is small.
4. The live smoke run only covered: model load + warm-up, mic open, listening screen, about 31 blocks/s with no overflows. I could not speak into the mic, so a live recitation and the Ctrl+C summary path were not exercised live. The finish/summary path is covered by the WAV run, which printed the full redraw and summary (335 words, 17 utterances, 530 ms mean ASR without real-time pacing).
5. The ASR latency on long clips (about 390-530 ms) is higher than on short clips (330 ms). The prompt is longer and the GPU was also busy.

## Questions for the Brain
1. Six→ten hidden by the prompt: should Task 04 test prompt-free or `back`-style prompting for the *verification* of a word? For example, re-transcribe an utterance without a prompt when the prompted and target words agree but the probabilities are low. Or should we use `back` everywhere and accept −2-3% on names? This needs the personal clips as a held-out test, not as a tuning set.
2. Is it fine that para1-3 now exist in `clips/` and the default benchmark includes them? Per the workflow they are held-out. I ran them once here and changed nothing based on them.
3. Confirm the colors (R15). They are implemented as specified.

## Commands for you
```
python -m pytest -q                                   # 54 tests
python reciteflow.py --file clips/para1.txt           # live: recite, watch colors, Ctrl+C or finish
python reciteflow.py                                  # paste a text, Enter on an empty line
python reciteflow.py --list-devices                   # then --device N if the wrong mic is used
python reciteflow.py --file t.txt --wav rec.wav --realtime   # replay a recording
python tools/fetch_samples.py --long ; python tools/make_mistakes.py   # rebuild long clips
python tools/benchmark.py --models large-v3-turbo --modes full,back,around   # ~10 min
```
Please listen to `clips/para3.wav` around "when you open… ten minutes" and tell the Brain what you actually said.
