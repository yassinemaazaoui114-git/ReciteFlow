# Task 02 report: Audio + VAD + Whisper + benchmark (code DONE, real benchmark NOT RUN: no clips)

Result: pytest 28 passed in ~5.6 s (17 matcher + 11 new ASR). CUDA works on the RTX 4050. All 4 configs fit in 6 GB.
Live mic smoke run works end to end (words + latency printed). `clips/` is empty, so the real benchmark has not run; commands are below.
`matcher/` and `app.py` were not changed.

## Added
- `asr/audio.py`: `MicSource` (sounddevice callback -> thread-safe `queue.Queue`, blocking `get(timeout)`, no busy loop) and `from_wav(path, realtime=False)`. Both yield the same block: 1-D float32, 512 samples (32 ms), 16 kHz mono. `load_wav` downmixes and resamples.
- `asr/vad.py`: `Segmenter` + `SileroProb` (Silero on CPU) -> `Utterance(audio, start, end, forced_cut)`. Defaults: threshold 0.5, end after 0.6 s silence, 0.2 s padding, 25 s cap, 0.2 s minimum speech. All configurable.
- `asr/engine.py`: `Recognizer(model_name, compute_type, initial_prompt=None)` with the exact settings from the task. `transcribe(audio) -> list[str]` returns raw words. `initial_prompt` can be changed between calls.
- `asr/stream.py`: `stream(blocks, recognizer, segmenter)` yields `(words, start, end, latency_s)`. CLI: `python -m asr.stream [--model --compute --wav --realtime --prompt --silence --device --seconds]`.
- `asr/_cuda.py`: Windows DLL path fix (see CUDA notes).
- `tools/record_clip.py`, `tools/benchmark.py`, `tests/test_asr.py`, an empty `clips/` folder, and the new deps in `requirements.txt`.

## Rules changed / interpreted
1. **Speech detector is pluggable.** `Segmenter(speech_prob=...)` defaults to Silero. The tests use an energy detector on synthetic tones because Silero (correctly) does not treat tones as speech. One test checks that the real Silero returns nothing for silence + tone.
2. **Hysteresis.** Speech starts at prob >= 0.5. Silence is counted below 0.35 (Silero's own rule).
3. **25 s cap.** The cut goes after the block with the lowest (speech prob, RMS) in the second half of the utterance, so each piece is at least about 12.5 s. The rest carries into the next utterance: no audio lost or duplicated (tested).
4. **Minimum speech 0.2 s** (not in the task). Clicks shorter than this are dropped. A very short isolated word could be dropped too; lower `min_speech` if that happens.
5. **Drop rule is per Whisper segment, not per word.** A segment is dropped when `no_speech_prob > 0.6` or its mean word probability is < 0.3. faster-whisper's built-in combined rule is turned off (`no_speech_threshold=None`) so only our rule applies. Single low-probability words are kept on purpose, because a misspoken word must still reach the matcher as `wrong`. Dropped segments are logged in `Recognizer.dropped`.
6. **Latency** = ASR time after VAD closes the utterance. The user-perceived delay is about latency + 0.6 s pause.
7. **Warm-up.** `Recognizer.__init__` transcribes 1 s of silence once. Without it the first utterance took about 1.6 s instead of about 0.4 s.
8. **Benchmark method.** Each clip is segmented once with Silero, and every config transcribes the same utterances. Utterances are fed to `Matcher` one by one, like live use. Target words still `pending` at the end count as `missed`. With prompt = the full `.txt` text. VRAM = peak whole-GPU `nvidia-smi` memory.used above the baseline taken before each model loads (per-process VRAM is not available under Windows WDDM). A config is marked skipped if it OOMs or goes over 6 GB. The CSV has one row per config x clip, plus `ALL` rows and the transcript.

## Test results
- `pytest`: 28 passed. The VAD tests cover the number of utterances, the 0.6 s threshold (a 0.4 s gap is one utterance, 0.7 s is two, and `min_silence=0.3` splits), padding, flush at end of stream, short-click drop, the 25 s cap at the quietest point, the cap on 70 s of uniform speech, silence only, and Silero on silence + tone. Block-format tests check that `from_wav` blocks and `MicSource` callback blocks have the same dtype/shape/contiguity and that the callback copies its buffer. Resampling and downmixing (44.1 kHz stereo) are also tested.
- `python tools/benchmark.py` with empty `clips/` prints instructions and exits with code 1. No clips were invented.
- **Dry run of the benchmark code (not a result).** I used one synthetic Windows-TTS clip (12.4 s, 3 utterances, 34 normalized target words), kept in a temp folder, not in `clips/`. All configs: 100 % correct, with and without prompt. This only shows that the code and models work; clean TTS says nothing about real accuracy.

  | config | mean ASR ms / utterance (~4 s audio) | peak VRAM |
  |---|---|---|
  | small.en fp16 | 415-420 | 731 MB |
  | medium.en int8_float16 | 867-1072 | 1224 MB |
  | large-v3-turbo int8_float16 | 444-457 | ~1224 MB* |
  | distil-large-v3 int8_float16 | 399-403 | 1160 MB |

  *Same figure as medium. Sampling every 0.2 s may miss short peaks. Treat VRAM as approximate (±100 MB).
- **Live mic smoke run** (`python -m asr.stream --seconds 30`, small.en, default mic "Microphone Array (Intel Smart Sound)"). It printed 6 utterances with ASR latency 118-487 ms, for example `[ 13.95- 16.51s  asr   340 ms] So they wanted to move it because we don't want it.` Caveat: I can't speak, so I played TTS through the laptop speakers. The mic did not pick up the TTS (probably the mic array's echo cancellation). What it transcribed was other speech in the room. So the mic -> VAD -> Whisper path is shown working, but not its accuracy. One utterance came back empty (segment dropped by the filter). The last one, "Thank you.", may be real speech or a hallucination; I can't tell. **Please run one smoke test yourself** (command below).

## CUDA setup notes (Windows)
- Installed: `faster-whisper 1.2.1` (ctranslate2 4.8.5), `silero-vad 6.2.3`, `soundfile 0.14.0`, `nvidia-cublas-cu12 12.9.2.10`, `nvidia-cudnn-cu12 9.28.1.4`. torch 2.14.1+cu126 and sounddevice 0.5.6 were already installed.
- Exact error on first try: `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`. The DLLs were installed but sit in `site-packages/nvidia/{cublas,cudnn,cuda_nvrtc}/bin`, which Windows does not search.
- Fix: `asr/_cuda.py` adds each `site-packages/nvidia/*/bin` to `os.add_dll_directory` and to `PATH` before `faster_whisper` is imported (done automatically by `asr.engine`). No system CUDA install needed. Loading torch (Silero) in the same process caused no DLL conflict.
- Hugging Face prints a symlink warning on Windows. It is harmless; the tools set `HF_HUB_DISABLE_SYMLINKS_WARNING=1`. Models are cached in `~/.cache/huggingface/hub`, about 5 GB for the 4 models (all downloaded already).

## Commands for you
```
python -m asr.stream                          # live smoke test: speak, pause, see words + latency (Ctrl+C)
python tools/record_clip.py para1             # Enter start / Enter stop, then paste the exact text
python tools/record_clip.py para2 --text my_paragraph.txt
python tools/benchmark.py                     # -> table + tasks/TASK_02_benchmark.csv
python tools/benchmark.py --per-clip --models small.en,large-v3-turbo
```
Suggested clips: 3-5 paragraphs of 20-60 s, recited naturally in one go. Make at least one with deliberate mistakes (a wrong word, a skipped word, an added word); for that clip the `.txt` must contain the original target text. Include one paragraph longer than 25 s without pausing, to exercise the cap.

## Known weaknesses
- Real accuracy is unknown until clips exist. The TTS dry run is too clean to choose a model. On TTS, turbo and distil were as fast as small.en, and medium.en was about 2x slower.
- The prompt is the full paragraph. Whisper keeps only the last ~223 prompt tokens, so for long texts the beginning of the paragraph is dropped from the bias. A sliding window around the matcher pointer would be better (Task 03+).
- The prompt can also bias Whisper toward the target text, which risks hiding real mistakes (a wrong word "corrected" into the right one). The benchmark only shows this if the clips contain deliberate mistakes.
- Whisper outputs like `well -known` or `1984` are raw by design; the matcher normalizes them (works in the dry run).
- VAD runs on the main loop. A long transcription delays VAD, but no audio is lost (the queue buffers it). Watch `MicSource.overflows`.
- The trailing-hallucination issue from Task 01 is still open (planned `feed(..., final=False)`, Task 03).
- VRAM numbers are whole-GPU and approximate (see above).
