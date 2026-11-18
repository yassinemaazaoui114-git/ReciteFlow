# Task 02b: Public test data, injected mistakes, run the benchmark (ReciteFlow)

## Why
Task 02 was built from an older version of the task file: `tools/fetch_samples.py` and `tools/make_mistakes.py` are missing, so the benchmark never ran on real speech. Do not change `matcher/` or `asr/` unless a bug forces it (report it if so).

## Build
1. `tools/fetch_samples.py`: downloads a small public English speech set (LibriSpeech test-clean AND test-other, e.g. Hugging Face `datasets` streaming, about 20 utterances each, no full download). Joins 4-6 consecutive utterances from the same speaker/chapter into paragraph-length clips (20-60 s). Saves WAV (16 kHz mono) + reference `.txt` in `clips/public/clean/` and `clips/public/other/`. If the download fails, report the exact error and use another public English source.
2. `tools/make_mistakes.py`: for each public clip, writes a second reference text with 4 known edits spread across the paragraph: one swapped word (replaced by a different real word), one deleted word, one inserted word, one misspelled word (near-miss, e.g. plural/tense change). Audio is unchanged. Save as `<clip>.mistakes.txt` + `<clip>.truth.json` (expected label per target word of the modified text + expected extras). Expected labels when the audio says the ORIGINAL text:
   - a word replaced in the target -> `wrong`
   - a word inserted into the target (not spoken) -> `missed`
   - a word deleted from the target (spoken, not in target) -> extra
   - a word misspelled in the target -> `misspelled`
3. `tools/benchmark.py`: extend it to run `clips/public/**` too, and to use `.truth.json` when present.
   - Prompt modes: `none`, `full` (whole reference text), `window` (about 40 target words starting a few words before the matcher pointer, updated after each utterance). For mistake clips the prompt is the MODIFIED text (that is what the user would have loaded).
   - Metrics per config x prompt mode, reported separately for clean / other / personal clips:
     - on original texts: % target words `correct` (word accuracy) and number of false alarms (anything not `correct`);
     - on mistake texts: injected mistakes caught with the right label, caught with another label, missed entirely; false alarms on unedited words;
     - ASR ms per utterance, peak VRAM.
   - Save `tasks/TASK_02b_benchmark.csv` (one row per config x mode x clip + summary rows).

## Run
- Run the full benchmark on the public clips (all 4 models x 3 prompt modes). Personal clips in `clips/` are included if any exist.

## Done when
- Benchmark ran on public clips. Write `tasks/TASK_02b_report.md`: what was added, rules changed/interpreted, the summary table, and a recommendation: which model + prompt mode to use as default, with the reason (accuracy first, then mistake detection, then latency). Flag clearly if the prompt hides real mistakes.
