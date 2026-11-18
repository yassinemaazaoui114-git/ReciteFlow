# Task 02: Audio + VAD + Whisper, and a benchmark (ReciteFlow)

## Goal
Turn microphone audio (and WAV files) into clean finished utterances (lists of words), fast, on an RTX 4050 6 GB. Then measure which Whisper model and settings are best. No UI, no matcher changes.

## Setup
- New package `asr/` next to `matcher/`. Do not touch `matcher/` or `app.py`.
- Allowed new deps: `faster-whisper`, `silero-vad` (or `torch` hub load), `sounddevice`, `numpy`, `soundfile`. Add to requirements.txt.
- Windows: faster-whisper on CUDA needs cuBLAS and cuDNN 12 DLLs (e.g. pip `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). If CUDA fails, report the exact error and the fix you used.

## Build
1. `asr/audio.py`: non-blocking `sounddevice` input at 16 kHz, mono, thread-safe queue (like V8, keep that part). Also a `from_wav(path)` source that yields the same audio blocks, so everything is testable offline.
2. `asr/vad.py`: Silero VAD that groups audio into utterances. An utterance ends after a pause (default 0.6 s silence, configurable). Add 0.2 s padding before/after speech. Cap an utterance at 25 s (force a cut at the quietest point).
3. `asr/engine.py`: `class Recognizer(model_name, compute_type, initial_prompt=None)` using faster-whisper: `language="en"`, `beam_size=5`, `temperature=0`, `condition_on_previous_text=False`, `vad_filter=False` (we do VAD ourselves), `word_timestamps=True`. Method `transcribe(audio) -> list[str]` returning words (raw; normalization is the matcher's job). Drop words where Whisper's `no_speech_prob` > 0.6 or avg word probability is very low. NO word blacklist.
4. `asr/stream.py`: glue: mic or WAV -> VAD -> Recognizer -> yields `(words, start_time, end_time, latency_seconds)`.
5. `tools/record_clip.py`: records a WAV clip from the mic (press Enter to start/stop) and saves it with a `.txt` file beside it containing the exact reference text the user types or pastes. Saved in `clips/`.
6. `tools/benchmark.py`: for every clip in `clips/`, run each config, then compute with the existing `matcher.Matcher` how many target words are `correct`, `misspelled`, `wrong`, `missed`, plus extras and the latency per utterance and VRAM used. Print one comparison table and save `tasks/TASK_02_benchmark.csv`.

## Configs to benchmark
- `small.en` fp16, `medium.en` int8_float16, `large-v3-turbo` int8_float16, `distil-large-v3` int8_float16 (skip any that do not fit in 6 GB and say so).
- Each with and without `initial_prompt` = the clip's reference text.

## Tests
- Unit tests (no GPU) for VAD grouping using synthetic audio (silence + tones): correct number of utterances, pause threshold respected, 25 s cap works.
- Test that `from_wav` and the mic path produce the same block format.
- Benchmark can only run once the user has recorded clips. If `clips/` is empty, print clear instructions and exit; do not invent clips.

## Done when
- Unit tests pass; one live smoke run prints words from the mic with latency.
- Write `tasks/TASK_02_report.md`: what was added, rules changed/interpreted, test results, CUDA setup notes, known weaknesses. If the benchmark could not run (no clips), say so and list the exact commands for the user to run.
