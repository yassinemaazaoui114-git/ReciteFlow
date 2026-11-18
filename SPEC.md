# ReciteFlow: SPEC (single source of truth for decisions)
Last updated: 2026-11-11 (rev 16)
Status line: Task 04 DONE + live re-align at pauses. Default = mode C (no prompt + acoustic check) + live (0.6 s passes) + re-align each utterance at the pause. Practice set live: 4 hidden, 28 false alarms (was 70), latency median 1.3 s, p90 1.6 s. GUI first version built (`gui.py`). History (SQLite) + correction button + error analysis + re-recite (go back up to 25 words, recovered words underlined) added 2026-11-11 (97 tests). Next: Yassine's mic test in the GUI, then polish.

## 1. Goal
Local, real-time memorization app. User recites a whole paragraph in one go; the app follows along and marks every mistake. Target: "nearly perfect", measured by benchmarks, not by feel.

## 2. Requirements
| # | Item | Status |
|---|------|--------|
| R1 | Local by default; cloud models/APIs and new libraries are allowed (was: fully local, no cloud; changed 2026-11-12) | Confirmed (user) |
| R2 | Hardware: RTX 4050 6 GB VRAM, Windows, Python 3.12 | Confirmed |
| R3 | English only | Confirmed |
| R4 | Whole paragraph recited at once (not sentence by sentence) | Confirmed |
| R5 | Four mistake types: wrong, missed, extra, misspelled (near-miss) | Confirmed |
| R6 | UI shows target text with highlighting only (no live transcript) | Confirmed |
| R16 | Feedback must be live: a mistake appears ~1-2 s after the word, not after a pause/end | Confirmed (user, 2026-11-09) |
| R7 | Priority: accuracy of model + matcher; UI minimal first, polished later | Confirmed |
| R8 | UI order: terminal (done) -> live + hidden-mistake fix (done) -> live false-alarm fix (next) -> CustomTkinter GUI -> polish | Assumption |
| R9 | Custom text via paste or file | Confirmed |
| R10 | Rebuilt from scratch; old V8 app.py kept as reference only | Confirmed |
| R11 | On a mistake: keep going and mark it; user stops and re-recites by choice | Confirmed |
| R12 | Retry / re-recite handling in app: go back (e.g. to the sentence start) and say it again | Built 2026-11-11 (jump back <= 25 words) |
| R13 | Product name: ReciteFlow | Confirmed |
| R14 | Re-recited correctly: word stays red + gets underline (recovered) | Built 2026-11-11 (GUI, terminal, history `tokens.recovered`) |
| R15 | Colors: correct green, wrong red, missed red [brackets], misspelled yellow, extra red {braces}, pending grey | Confirmed |
| R17 | Save every recitation locally (history) + let the user correct the app's marks; corrections + kept recordings are the training data for later fine-tuning | Confirmed (user, 2026-11-11) |

## 3. Architecture
| Part | Choice | Status |
|------|--------|--------|
| Layout | Packages `matcher/`, `asr/`, `tools/`, `tests/`; Task 03 adds `core/session.py` + `reciteflow.py` | Confirmed (built) |
| Audio | sounddevice 16 kHz mono, 32 ms blocks, queue, no busy loop; `from_wav` for offline tests | Confirmed (Task 02) |
| Segmentation | Silero VAD, end after 0.6 s silence, 0.2 s padding, 25 s cap at quietest point, 0.2 s min speech | Confirmed (Task 02) |
| ASR | faster-whisper `large-v3-turbo` int8_float16, beam 5, temp 0, word timestamps; segment dropped if no_speech_prob > 0.6 or mean word prob < 0.3; no word blacklist | Confirmed on public data; Assumption until personal clips |
| ASR fallback | `distil-large-v3`, no prompt (96.9%, 40/40) | Assumption |
| Prompt | Mode A (`auto:around` prompt) kept as option only (`--mode A`); hides real mistakes (15/152 practice) | Replaced by mode C (Task 04) |
| Matcher | Normalize (punctuation, numbers, contractions) + edit-distance alignment, window = len(spoken)+15; costs exact 0, near-miss 0.3, wrong/missed/extra 1; tie-break: more exact, fewer missed, further ahead | Confirmed (Task 01) |
| Near-miss | rapidfuzz >= 0.75 or shared stem (s/es/ed/ing/ly) | Confirmed (Task 01) |
| Mode C (default) | Transcribe WITHOUT the target text; disputed spans get an acoustic check: teacher-forced log P via ctranslate2 `Whisper.align`, reusing the encoder output (~15-25 ms, no extra VRAM); accept target if margin >= tau=1.0; spelling-variant rule (compounds, doubled letters) | Confirmed (Task 04) |
| Live feedback (default) | `asr/live.py`: re-transcribe open utterance every 0.6 s, LocalAgreement-2, 5 s buffer trim, catch-up skipping; GPU ~75% busy while speaking. Median 1.3 s, p90 1.6 s. `--no-live` = update per pause only | Confirmed (Task 04) |
| Live re-align (default) | At each VAD pause (or when live words finish the text) the matcher goes back to its state before the utterance and the whole utterance is transcribed + checked as in per-pause mode; that result overrides the live colours (`Session(realign=True)`, `Matcher.snapshot/restore`). Override median 1.4 s, p90 2.1 s after the utterance audio ends | Confirmed (benchmark 2026-11-10) |
| Matcher restart rule (re-recite) | A feed may start before the pointer: (a) repeat = exact last k <= 8 target words (penalty 0.5 word); (b) jump = 2 exact target words up to 25 words back AND a mistake (wrong/missed/misspelled/extra) in that stretch (penalty 1.5 words, so a wrong phrase that equals earlier words does not hide a mistake). Chosen only when cheaper than aligning forward. The pointer moves back; re-said words keep their first state; a mistaken word said right again = recovered (`Matcher.recovered()`) | Built 2026-11-11 (jump rule); repeat rule Task 04 |
| Live hold-back | Inside an utterance a held word waits <= 1 live pass; after a pause it waits for the next utterance or stop (1 s timeout tried, broke self-corrections) | Built (Task 04) |
| Tail hold-back | `feed(words, final=False)` + `flush()`; long clips 22->24/24 caught | Confirmed (Task 03) |
| App | `core/session.py` (UI-free) + `reciteflow.py` terminal | Confirmed (Task 03) |
| History (R17) | `core/history.py`: SQLite (stdlib `sqlite3`, no server) at `data/history.db`: tables `recitations` (text, heard ASR words, counts, mode/model, audio path), `tokens` (per view token: app_state + user_state = the user's correction), `settings`. Audio as 16 kHz PCM16 WAV in `data/recordings/` only while "Keep my recordings for training" is on (default on, remembered). Mic recitations saved by GUI + terminal; replays only with `--save` | Built 2026-11-11 |
| Corrections (R17) | GUI, after a recitation: click a word -> menu (correct / misspelled / wrong / missed; extras: really said / not said); stored as `tokens.user_state`, corrected words get a tinted background, counters use the corrections | Built 2026-11-11 |
| CUDA on Windows | `asr/_cuda.py` adds pip nvidia DLL folders; no system CUDA needed | Confirmed (Task 02) |

## 4. Benchmark results (Task 02b, public LibriSpeech, 10 clips, 830 words, 40 injected mistakes)
| Model + prompt | Word acc | Caught | False alarms | ms/utt | VRAM |
|---|---|---|---|---|---|
| large-v3-turbo + full | 99.6% | 40/40 | 3 | 339 | 1.2 GB |
| distil-large-v3 none | 96.9% | 40/40 | 28 | 319 | 1.2 GB |
| small.en + full | 98.9% | 39/40 (1 hidden) | 12 | 223 | 0.7 GB |
| medium.en + full | 91.1% | 38/40 | 133 | 629 | 1.2 GB |
Task 03 (hold-back on): long clips (300-335 words) turbo+around 99.2%, 24/24 caught. Short clips unchanged (99.6%, 40/40).
Personal clips (held-out, run once): para1 100% with prompt; para2 98.2% + hallucinated "we'll be right back" (shown as extras); para3: big->small, teaching->learning caught; **six->ten HIDDEN by the prompt** (prompted transcript invented "ten minutes"). Also without a prompt every model hears "steps" (target "step") in para1/2, but turbo+full shows para1 as 100%: likely a second hidden mistake.
Lesson: public mistake injection (correct audio, edited text) cannot test near-sounding spoken mistakes. Need audio that contains the mistakes.

### Task 04 (practice set = 52 Windows-TTS clips, 152 spoken mistakes, 17 clean clips; one voice, Zira)
| Mode | Practice hidden | Practice false alarms | Public short FA | Public long FA | ms/utt |
|---|---|---|---|---|---|
| A prompt | 15 | 15 | 3 | 8 | 608 |
| B no prompt | 4 | 50 | 31 | 10 | 548 |
| **C no prompt + check (default)** | **4** | **27** | 15 | **2** | 550 |
| D double check | 4 | 33 | 15 | 7 | 1059 |
Live mode (C): same 4 hidden, but 67 false alarms (vs 27 per-pause). **Live + re-align (2026-11-10, `tools/live_latency.py`, simulated real time):** before 4 hidden / 125 right / 23 other / 70 FA, latency median 1.29 s p90 1.60 s, 84 skipped passes; after 4 hidden / 141 right / 7 other / **28 FA**, latency median 1.30 s p90 1.62 s, 491 skipped passes (re-align GPU time; latency unchanged), override median 1.39 s p90 2.10 s max 10.6 s (long utterances). Remaining 4 hidden are ASR-level (the->a, dollars->dollar...): the check never sees them.
**Re-recite (2026-11-11):** new set `clips/practice_restart/` (22 TTS clips, 33 restarts: a mistake, 0-3 more words, pause, back to the sentence start or 3-17 words back, all again; `tools/make_tts_mistakes.py --restarts`). Per pause (C): restart set hidden 2 -> 2, FA **337 -> 53**; practice 4/27 -> 4/27 (identical); public short 0 hidden / FA 15, long 1 hidden / FA 2 (unchanged range). Live (C + re-align): restart set hidden 2 -> 2, FA **296 -> 53**, recovered shown 0 -> **29/33**; practice hidden 4 -> 4, FA 27 -> 28. **Idle-GPU re-check (2026-11-12, `tools/live_latency.py --sets practice,restart`, before = scratch copy with the old repeat-only rule, run back to back):** display latency median 1.27 -> **1.26 s**, p90 1.78 -> **1.59 s**, max 7.05 -> 3.88 s; pause override median 1.30 -> 1.34 s, p90 1.66 -> 1.71 s. No latency cost. Same run: practice 4 hidden / 27 FA both; restart set FA 292 -> 53, recovered 0 -> 29/33, hidden 1 -> 2: `daily_bakery_r2` (said "closed on mondays", went back 5 words) now shows {and it is closed on mondays} as 6 red extras and "Sundays" green; the old rule happened to mark it wrong in this run (live timing; earlier busy runs had 2 -> 2). See to-do. Restart search costs < 5 ms per 100-word feed. Yassine's own take #3 (mistake "nine", back 13 words): before 8 wrong + 4 extras, after only "nine" wrong + recovered.
**Error analysis (2026-11-11, `tools/error_analysis.py`, practice set, default C + live + re-align; output `benchmarks/error_analysis.csv`):** same totals (4 hidden, 28 FA). **All 32 start in the ASR model**: in every case Whisper's own words (no check) already give the wrong mark; the check never flips a right answer to wrong and the matcher adds none. Hidden: dollars->dollar, the, second, a dropped self-correction "in an new" (Whisper "cleans up" what was said). FA: mostly names / rare spellings (Okafor, squire, Bannister, Rosamund, Risdon Graeme, wretch, idle) and words next to a spoken mistake; 4 checks were just below tau (margins -1.04 to -1.25, not tuned). Meaning: further gains need a better / adapted ASR model (fine-tuning on verbatim speech), not matcher work.
**Final exam (held-out, run once, C + live):** all known mistakes caught (six, week, big, teaching, para2 steps, self-corrections), none hidden. False alarms: para3 "just" (heard "justice"), para3 "makes" (lost at a live buffer cut). para1 flags "this"+"step" (heard "these small daily steps"): probably real, user to listen. Personal clips are now USED; future final checks need new recordings.

## 5. Decision log
| Date | Decision | Why |
|---|---|---|
| 2026-11-09 | Rebuild from scratch | V8 flaws structural (fixed chunks, blacklist, greedy pointer) |
| 2026-11-09 | Whisper via faster-whisper, Silero VAD | Speed + natural utterance cuts |
| 2026-11-09 | Engine before UI; terminal first | Accuracy is priority |
| 2026-11-09 | Use public data + synthetic mistake injection, plus 3 personal clips | Ground truth without hand recording |
| 2026-11-09 | Default = large-v3-turbo + full prompt; drop medium.en | Task 02b benchmark |
| 2026-11-09 | No git repo for now | User choice |
| 2026-11-09 | Brain/Builder workflow (planner chat + task files) | User setup (replaced 2026-11-10) |
| 2026-11-10 | Local-only workflow: Yassine directs Claude Code directly; rules in CLAUDE.md, overview in PROJECT_CONTEXT.md, decisions here. `tasks/` and `claude/` are old history | User choice (save tokens, fresh chat per request) |
| 2026-11-09 | No prompt + acoustic check (user idea: compare after transcription, don't show the model the text) instead of prompt-only; embeddings/hashing rejected (embeddings measure meaning, e.g. six~ten, big~small) | Prompt hid real mistakes in para2/para3 |
| 2026-11-09 | Practice set = Windows TTS speaking real mistakes; personal clips = held-out final exam | Public injection cannot test spoken mistakes |
| 2026-11-09 | Default = mode C + live (0.6 s passes), tau 1.0 | Task 04: fewest hidden mistakes, final exam clean |
| 2026-11-11 | History in SQLite (one local file), not Postgres; audio as WAV files next to it | Local app, one user, no server needed; stdlib only |
| 2026-11-11 | Recording kept for training by default, with a visible opt-out checkbox; text + results always kept | User wants usage data for fine-tuning; everything stays on the PC |
| 2026-11-11 | Before any fine-tuning, measure where errors come from (`tools/error_analysis.py`) | Fine-tuning only helps errors that start in the ASR model |
| 2026-11-10 | Live re-align at each pause on by default | Practice FA 70 -> 28, hidden 4 -> 4, live latency unchanged |

## 6. Lessons from old V0-V8
- Wav2Vec2 (letter CTC) gave phonetic garbage -> Whisper.
- Fixed 1.5 s chunks with overlap -> duplicate words; cooldown hack blocks real repeats.
- Word blacklist ("for", "you", "thank") deletes real words.
- No normalization; greedy pointer gets stuck; busy loop 100% CPU.

## 7. To-do
| Item | Status |
|---|---|
| Live mic test of `reciteflow.py` (live vs `--no-live`), user's impression | First run 2026-11-10 on para1 text: 0 skipped passes, 1.2 s median, re-align changed 0 colours; flagged [this] missed + 1 misspelled. Impression open (Yassine) |
| Re-record `clips/this_step` (para1 text, "this small daily step") | Done 2026-11-10: clean take (Yassine unsure of any mistake); app 55/55 correct, 1 live flash on "builds" fixed at pause; turbo and large-v3 without prompt both hear the exact text. Practice data |
| Listen to para1.wav: "this small daily step" or "these small daily steps"? | Open (Yassine) |
| Fix live false alarms (live colours while speaking, re-align each utterance at the pause and let it override) | Done 2026-11-10 (FA 70 -> 28) |
| More TTS voices for the practice set | Proposed |
| New held-out recordings (para1-3 are used) | Later |
| Re-check live latency of the re-recite change on an idle GPU (`tools/live_latency.py --sets practice,restart`) | Done 2026-11-12: median 1.27 -> 1.26 s, p90 1.78 -> 1.59 s (no cost) |
| Re-recite after a short go-back: `daily_bakery_r2` first attempt ("closed on mondays") becomes 6 extras, the corrected word is shown green (counted hidden; the mistake is visible only as red extras). Investigate the matcher/re-align path | Open |
| CustomTkinter GUI (reuse `core/session.py`) | First version done 2026-11-10 (`gui.py`: text box, Start/Stop, live colours, counters, mic picker); replay of this_step 55/55. Waiting for Yassine's mic test, then polish |
| Re-recite handling R12/R14 (base: matcher restart rule) | Later |
| GUI: hide the text while reciting (words appear as said) + beep on each new live mistake | Built 2026-11-12; first mic test by Yassine 2026-11-12: one typed sentence recited from memory, all words correct |
| Benchmark streaming (live-first) ASR models vs Whisper live: latency + hidden mistakes + FA, GPU cost no concern | After the first usable version (user, 2026-11-12) |
| Installer: build a packaged v1 (setup.exe) installed apart from this folder, so the installed app keeps working while this folder changes | After the first usable version (user, 2026-11-12) |

## 8. Open questions
- Mic model/quality (affects VAD thresholds)?
- para1: "these small daily steps"? (model hears it; user unsure, to listen)
- More TTS voices for the practice set (install Windows voices or local neural TTS)? Recommended: yes, cheap.
- New held-out recordings needed (para1-3 now used once).
## 9. Change log
| Date | Change | Why |
|------|--------|-----|
| 2026-11-09 | Spec created; rebuild from scratch | Gemini handoff vs app.py review |
| 2026-11-09 | English only, highlight-only UI, full paragraph, engine first | User answers |
| 2026-11-09 | All 4 mistake types, keep-going, retry later, 6 GB VRAM; Task 01 written | User answers |
| 2026-11-09 | Name ReciteFlow; red + underline rule; report workflow; Task 01 done; Task 02 written | User + report |
| 2026-11-09 | No git; public data + mistake injection added to Task 02 | User question |
| 2026-11-09 | Task 02 done; Task 02b for missing parts | Task 02 report |
| 2026-11-09 | User smoke test: mic path works, small.en weak on user's voice | Screenshot |
| 2026-11-09 | Task 02b done: turbo + full prompt default; Task 03 written | Task 02b report |
| 2026-11-09 | Moved to Brain/Builder workflow: SPEC.md -> claude/PROJECT_SPEC.md (project + PC), handoff.md, tasks/NEXT.md | User set up Claude Project |
| 2026-11-09 | Colors (R15) confirmed; 3 personal clips recorded; folder renamed to ReciteFlow | User |
| 2026-11-09 | para3 known mistakes recorded (4 swaps, list may be incomplete) | User |
| 2026-11-09 | Task 03 done; personal benchmark run; found prompt-hidden mistakes and late feedback; R16 live feedback added; GUI pushed to Task 05 | Task 03 report + user test |
| 2026-11-09 | Task 04 approved and written (honest transcription + acoustic check + live feedback). para2 "steps" confirmed as a real user mistake | User |
| 2026-11-09 | Task 04 done: mode C + live default, final exam passed; live false alarms open; GUI moved to Task 06, Task 05 proposed = live false-alarm fix | Task 04 report |
| 2026-11-10 | Switched to local-only workflow; spec moved from claude/PROJECT_SPEC.md to SPEC.md; CLAUDE.md + PROJECT_CONTEXT.md added | User choice |
| 2026-11-10 | Live re-align at pauses built and made default; benchmark before/after in section 4; 80 tests | User request |
| 2026-11-10 | this_step re-recorded and checked: 55/55 correct, 0 final false alarms | New practice clip |
| 2026-11-10 | GUI first version (`gui.py`, CustomTkinter added to requirements); engine unchanged; 83 tests (Confirmed) | User request |
| 2026-11-11 | Re-recite built (R12/R14): jump back <= 25 words after a mistake, recovered words underlined; restart practice set; 97 tests (Confirmed, latency re-check open) | User test: going back to the sentence start broke the rest |
| 2026-11-11 | R17 history (SQLite) + correction button + consent checkbox; `tools/error_analysis.py`; engine unchanged; 89 tests (Confirmed) | User request |
| 2026-11-12 | R1 relaxed: cloud services and new libraries allowed; CLAUDE.md rule removed (Confirmed) | User choice |
| 2026-11-12 | GUI: "Hide the text while I recite" + "Beep on a mistake" (both on by default); engine unchanged; to-do: streaming-model benchmark, installer (Confirmed) | User: cannot recite from memory with the text on screen |
| 2026-11-12 | Idle-GPU latency re-check of re-recite: no latency cost (p90 1.78 -> 1.59 s); restart hidden 1 -> 2 in this run (daily_bakery_r2, open to-do) (Confirmed) | Paused item finished |
| 2026-11-12 | First mic test of the GUI from memory (one sentence): all correct, no false alarm (Confirmed, single try) | Yassine's test |
