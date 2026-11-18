# Task 02b report: public clips + injected mistakes + benchmark (DONE)

Result: the benchmark ran on 10 public LibriSpeech paragraph clips (5 test-clean, 5 test-other; 334 s of audio, 50 VAD utterances), all 4 models x 3 prompt modes, on the original texts and on the texts with injected mistakes. No model ran out of memory. `pytest`: 28 passed. `matcher/` and `asr/` were not changed. There are no personal clips yet, so the `personal` group is empty.

**Recommendation: `large-v3-turbo` (int8_float16) + prompt `full`.** It has the best accuracy (99.6 %, 3 false alarms in 830 target words), catches 40/40 injected mistakes with the right label, takes 339 ms per utterance and uses about 1.2 GB VRAM. **On turbo, the prompt did not hide any real mistake.** It **did** hide one on small.en (details below). The `window` mode, as specified, is risky on small.en and medium.en.

## Added
- `tools/fetch_samples.py`: streams `openslr/librispeech_asr` (Hugging Face) parquet with HTTP range requests through `huggingface_hub.HfFileSystem` + `pyarrow`. It reads one 100-row group (~13 MB) at a time; the 350 MB file is never fully downloaded. No new dependency (`datasets` is not installed and was not needed). The clips come from row groups spread across the file, so each one has a different speaker. Each clip joins 4-6 consecutive utterances of the same speaker and chapter (20-60 s) with 0.7 s of silence between them. Output: `clips/public/{clean,other}/NAME.wav` (16 kHz mono PCM16) + `NAME.txt`. 5 clips per set (`--clips-per-set`), 21 + 22 = 43 source utterances.
- `tools/make_mistakes.py`: writes `NAME.mistakes.txt` + `NAME.truth.json` with 4 edits, one per quarter of the paragraph, in a random order (seeded per clip, reproducible): swap, delete, insert, misspell. `truth.json` = `labels` (one per normalized target word), `extras`, `edits`.
- `tools/benchmark.py` (extended): personal + `public/clean` + `public/other` clips, original and mistake variants, prompt modes `none|full|window`, truth scoring, per-group summaries. CLI: `--models`, `--modes`, `--clips`, `--out` (default `tasks/TASK_02b_benchmark.csv`).
- `tasks/TASK_02b_benchmark.csv`: 240 clip rows (4 models x 3 modes x 10 clips x 2 variants) + summary rows (`clip=ALL`, per group clean/other/ALL). Columns `flags` (every non-correct word and every extra) and `transcript` are included for debugging.

## Rules changed / interpreted
1. **Edits are made on normalized words** (`matcher.normalize`), so the indices in `truth.json` are the same as in `Matcher.states()`. `.mistakes.txt` is therefore lowercase without punctuation. That is harmless here, because LibriSpeech text already has neither.
2. **Edit choice.** Never in the first or last 2 words. Only alphabetic words of at least 4 letters, and not the same as a neighbour (that would make the alignment ambiguous). The swap word is a common real word that is not a near-miss of the original. The misspelling is a plural/tense change (`+s`, `-s`, `ed->ing`) that the matcher's own near-miss rule accepts, and function words are excluded (no "that->thats"). Some misspellings are still not real words ("fifteens", "trulys", "greats"), so they read as typos, not plural/tense changes.
3. **Truth is self-checked.** Feeding the original text to a Matcher built on the mistake text reproduces every label and extra in all 10 truth files. While checking, I found this: when an utterance boundary falls right after a deleted word ("will **wait** | until"), the matcher can mark the next word `wrong` plus an extra. This is the known trailing-word weakness (Task 03 `final=False`), not a truth error.
4. **Scoring.** Swap/insert/misspell: right = the expected label at that index, other = any other non-correct label, missed = `correct`. Delete: right = an extra with that word within ±2 target positions. Other = any extra within ±1, or a flagged neighbour (those neighbours are then not also counted as false alarms). False alarms = unedited target words that are not `correct`, plus extras that are not explained by an edit. `pending` at the end counts as `missed` (same as Task 02).
5. **Window mode** = 40 normalized target words starting 5 words before `Matcher.pointer`, recomputed before each utterance. This follows the task exactly. See the warning below.
6. **Caching.** Within a clip, an identical (utterance, prompt) pair is transcribed once and reused. In practice this means `none` mode is shared between the original and mistake variants. The reused latency is the measured one.
7. **0.7 s gaps between joined utterances** are just over the VAD's 0.6 s end-of-utterance pause, like a natural pause between sentences. Silero still split some clips more finely (50 VAD utterances from 43 source utterances).

## Summary (all 10 public clips; 830 target words, 40 injected mistakes)
acc% / FA = original texts (FA = non-correct target words + extras). Caught = injected mistakes with the right label / another label / not caught; per type = right swap/delete/insert/misspell. FA_m = false alarms on unedited words of the mistake texts.

| model | prompt | acc% clean | acc% other | acc% all | FA | caught right/other/miss | per type | FA_m | ms/utt | VRAM MB |
|---|---|---|---|---|---|---|---|---|---|---|
| small.en fp16 | none | 96.6 | 93.3 | 95.0 | 46 | 40/0/0 | 10/10/10/10 | 45 | 212 | 731 |
| small.en fp16 | full | 99.5 | 98.3 | 98.9 | 12 | 39/0/**1** | 10/10/10/9 | 28 | 223 | 731 |
| small.en fp16 | window | 81.2 | 94.8 | 87.8 | 127 | 35/4/1 | 9/9/9/8 | 191 | 239 | 731 |
| medium.en int8_f16 | none | 97.2 | 93.1 | 95.2 | 41 | 38/2/0 | 10/9/10/9 | 38 | 435 | 1194 |
| medium.en int8_f16 | full | 92.0 | 90.1 | 91.1 | 133 | 38/2/0 | 10/10/10/8 | 124 | 629 | 1194 |
| medium.en int8_f16 | window | 58.7 | 84.7 | 71.2 | 338 | 35/5/0 | 9/8/9/9 | 260 | 564 | 1194 |
| **large-v3-turbo int8_f16** | none | 97.2 | 95.8 | 96.6 | 31 | 39/1/0 | 10/9/10/10 | 28 | 337 | 1224 |
| **large-v3-turbo int8_f16** | **full** | **99.8** | **99.5** | **99.6** | **3** | **40/0/0** | 10/10/10/10 | **3** | 339 | 1224 |
| large-v3-turbo int8_f16 | window | 99.8 | 99.5 | 99.6 | 3 | 40/0/0 | 10/10/10/10 | 4 | 334 | 1224 |
| distil-large-v3 int8_f16 | none | 97.2 | 96.5 | 96.9 | 28 | 40/0/0 | 10/10/10/10 | 27 | 319 | 1160 |
| distil-large-v3 int8_f16 | full | 97.2 | 96.8 | 97.0 | 27 | 40/0/0 | 10/10/10/10 | 26 | 318 | 1160 |
| distil-large-v3 int8_f16 | window | 97.2 | 96.3 | 96.8 | 29 | 40/0/0 | 10/10/10/10 | 28 | 317 | 1160 |

Per-group mistake numbers are in the CSV (`group=clean|other`, `variant=mistakes`). Personal: no clips.

## Does the prompt hide real mistakes? (FLAG)
A mistake test shows this directly. The audio says the original word and the prompt holds the edited word, which is the same situation as a user saying a wrong word while the correct target is in the prompt.
- **Yes, once, on small.en.** Audio "i **swear**", target/prompt "swears". With prompt `full` and `window`, small.en wrote "i swears", so the misspelling was marked `correct` (hidden). Without a prompt it wrote "i swear" and the mistake was caught. medium.en, turbo and distil were not fooled.
- **large-v3-turbo: 0 of 40 hidden** in both prompt modes, including all 10 subtle plural/tense changes. distil: 0 of 40.
- Caveat: 40 edits is a small sample, and the swaps are obvious ("were" -> "garden"). A wrong word that sounds close to the target is the hardest case for a prompt, and it is not covered here. Test it with personal clips.

## Other findings
- **The prompt mainly fixes names and rare words** that would otherwise be false alarms: Muhammad/mohammed, neo platonists, paschal, jehovah, rosamun, risdon graeme. turbo goes from 31 to 3 false alarms. The 3 that remain: "however" missed once, "what" missed and "on" -> wrong once (a real ASR miss plus "bolts in/on the door"; LibriSpeech says "on").
- **Window mode (as specified) has a failure mode on small.en/medium.en.** The window includes the words *about to be spoken*. Whisper treats the prompt as text already transcribed and outputs only what comes after it. Example (small.en, clean_6930, a 23 s utterance): the prompt ended at "...object of worship the queen mother", and the output started at "gave the French..." (the first ~20 words of the utterance were dropped and became `missed`/`wrong`). When the window runs past the end of the utterance, small.en added "Thank you very much." x4 (other_2609). Turbo and distil did not show this.
- **"Thank you" hallucinations come with prompts.** 16 runs contain "thank you", all with small.en or medium.en and a `full` or `window` prompt. There are none with no prompt, and none with turbo or distil.
- **medium.en gets worse with a prompt** (95.2 -> 91.1 % full, 71.2 % window): it repeats or drops runs of prompt text ("paul sticks to his..." output twice). It is also the slowest (435-629 ms). Drop it.
- **distil-large-v3 mostly ignores the prompt for word choice** (only style changes: lowercase, no punctuation). It is the best model without a prompt (96.9 %) but does not benefit from one.
- **Latency** (ASR only, utterances of ~2-23 s): small 212 ms, distil 319, turbo 337, medium 435-629. Lower than the Task 02 TTS dry run (~0.4 s). The user-perceived delay is about this plus the 0.6 s VAD pause.
- **VRAM** (whole-GPU peak above baseline, ±100 MB): small 731, medium 1194, turbo 1224, distil 1160 MB. All far below 6 GB.

## Recommendation
Default: **large-v3-turbo, int8_float16, prompt `full`.**
1. Accuracy: best (99.6 % clean+other, 99.5 % even on test-other).
2. Mistake detection: 40/40 with the right label; the prompt hid nothing; 3 false alarms on the mistake texts.
3. Latency: 339 ms per utterance, about 120 ms slower than small.en, which is acceptable. VRAM about 1.2 GB.

Fallback if the prompt causes trouble with personal clips: `distil-large-v3` with no prompt (96.9 %, 40/40, 319 ms). Do not use small.en or medium.en with a prompt.

`full` vs `window` on turbo: same score here, but all paragraphs here are at most 126 words, which is under Whisper's ~223-token prompt limit, so the truncation problem `window` was meant to fix was **not tested**. For longer texts, my suggestion for Task 03 (not tested) is a window that **ends** at the matcher pointer, i.e. only the last ~40 words already recited, which is how Whisper uses prompts in training. The current forward-looking window is what triggers the skip and hallucination failures on the smaller models.

## Known weaknesses
- Small sample: 10 clips, 830 words, 40 edits (10 per type). One error is about 0.1 % accuracy. The differences between turbo, distil and small+prompt are real, but turbo full vs turbo window (3 vs 4 FA) is noise.
- The mistakes are in the text, not in the audio (as specified). Real recitation mistakes (hesitations, self-corrections, partial words, near-sounding wrong words) are not covered. Personal clips are still needed (the 3 pending from the user).
- LibriSpeech is read audiobook speech recorded on good microphones, with joined sentences and clean 0.7 s gaps. A laptop mic in a room will be worse, especially for small.en.
- The edits sometimes land on an utterance boundary, where the trailing-word matcher weakness can turn a deletion into "wrong + extra" (scored as "other"). This happened 1-2 times per model.
- LibriSpeech references contain a few ground-truth quirks ("bolts on the door" when the speaker may say "in"), which count as false alarms for every model.

## Commands
```
python tools/fetch_samples.py      # -> clips/public/{clean,other}/  (~130 MB of range reads)
python tools/make_mistakes.py      # -> NAME.mistakes.txt + NAME.truth.json
python tools/benchmark.py          # all models x modes -> tasks/TASK_02b_benchmark.csv (~15 min)
python tools/benchmark.py --models large-v3-turbo,distil-large-v3 --modes none,full
```
