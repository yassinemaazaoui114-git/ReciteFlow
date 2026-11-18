"""Benchmark Whisper configs x prompt modes on recorded and public clips.

    python tools/benchmark.py                       # all configs and modes -> tasks/TASK_03_benchmark.csv
    python tools/benchmark.py --models small.en,large-v3-turbo --modes none,window
    python tools/benchmark.py --models large-v3-turbo --modes full,back,around   # Task 03 Part B

Clips (each NAME.wav with the exact spoken text in NAME.txt):
    clips/*.wav                  personal (record with tools/record_clip.py)
    clips/public/clean/*.wav     LibriSpeech test-clean (tools/fetch_samples.py)
    clips/public/other/*.wav     LibriSpeech test-other
    clips/public/long/*.wav      280-400 word LibriSpeech paragraphs (tools/fetch_samples.py --long)
When NAME.mistakes.txt + NAME.truth.json exist (tools/make_mistakes.py), the clip is also run
against the mistake text and scored with the expected labels. For a personal clip with only
NAME.truth.json, the truth applies to NAME.txt.

The audio is cut into utterances once with Silero VAD, then every config transcribes the same
utterances, which are fed one by one to matcher.Matcher with final=False (tail hold-back, as in
the app), then flush(); --no-holdback feeds with final=True as in Task 02b. Target words still
`pending` at the end are counted as missed.

Prompt modes (initial_prompt; for mistake runs the target is the MODIFIED text):
    none    no prompt
    full    the whole target text
    window  ~40 target words starting a few words before the matcher pointer, updated after each utterance
    back    last ~40 target words before the pointer (asr.prompt), updated after each utterance
    around  30 words before to ~100 after the pointer, capped under 200 tokens (asr.prompt)
The modes are applied as named, whatever the text length (the app's auto rule picks `full`
for texts under ~200 tokens, so on short clips auto == full).
"""

import argparse
import csv
import gc
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from asr.audio import SAMPLE_RATE, load_wav, to_blocks  # noqa: E402
from asr.vad import Segmenter  # noqa: E402
from asr.prompt import PromptBuilder  # noqa: E402
from matcher import Matcher  # noqa: E402

CONFIGS = [
    ("small.en", "float16"),
    ("medium.en", "int8_float16"),
    ("large-v3-turbo", "int8_float16"),
    ("distil-large-v3", "int8_float16"),
]
MODES = ["none", "full", "window", "back", "around"]
GROUPS = ["clean", "other", "long", "personal"]
STATES = ["correct", "misspelled", "wrong", "missed"]
EDIT_TYPES = ["swap", "delete", "insert", "misspell"]
VRAM_LIMIT_MB = 6 * 1024
WINDOW_WORDS = 40
WINDOW_BACK = 5
DELETE_SLACK = 2  # an extra counts for a deleted word if it lands within this many target positions

NO_CLIPS_HELP = """No clips found in {folder} or {folder}/public.

Get public clips (LibriSpeech test-clean/test-other) and their mistake versions:

    python tools/fetch_samples.py
    python tools/make_mistakes.py

and/or record your own (the .txt must hold the text you actually said):

    python tools/record_clip.py para1

Then run:

    python tools/benchmark.py
"""


def gpu_used_mb():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return int(out.split()[0])
    except Exception:
        return None


class VramPeak:
    """Polls total GPU memory in a thread; peak() is the max seen minus the baseline."""

    def __init__(self, interval=0.2):
        self.base = gpu_used_mb()
        self.max = self.base
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(interval,), daemon=True)
        self._thread.start()

    def _run(self, interval):
        while not self._stop.wait(interval):
            used = gpu_used_mb()
            if used is not None and (self.max is None or used > self.max):
                self.max = used

    def peak(self):
        return None if self.base is None else self.max - self.base

    def stop(self):
        self._stop.set()
        self._thread.join()
        return self.peak()


def read_text(path):
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    return text or None


def load_clips(folder):
    """Returns [(group, name, wav, [(variant, target text, truth or None), ...])]."""
    folder = Path(folder)
    sources = [("personal", folder), ("clean", folder / "public" / "clean"), ("other", folder / "public" / "other"),
               ("long", folder / "public" / "long")]
    clips = []
    for group, sub in sources:
        for wav in sorted(sub.glob("*.wav")):
            text = read_text(wav.with_suffix(".txt"))
            if not text:
                print(f"Skipping {wav.name}: no reference text in {wav.stem}.txt")
                continue
            truth_path = wav.with_name(wav.stem + ".truth.json")
            truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else None
            mistakes = read_text(wav.with_name(wav.stem + ".mistakes.txt"))
            if truth and mistakes:
                variants = [("original", text, None), ("mistakes", mistakes, truth)]
            elif truth:
                variants = [("mistakes", text, truth)]
            else:
                variants = [("original", text, None)]
            clips.append((group, wav.stem, wav, variants))
    return clips


def is_oom(err):
    msg = str(err).lower()
    return any(s in msg for s in ("out of memory", "cublas_status_alloc_failed", "memory budget"))


def prompt_for(mode, text, m, builder=None):
    if mode == "none":
        return None
    if mode == "full":
        return text
    if builder is not None:
        return builder.prompt(m.pointer)
    start = max(0, m.pointer - WINDOW_BACK)
    return " ".join(m.target[start:start + WINDOW_WORDS]) or None


def score_truth(states, extras, truth):
    """Compare matcher output with truth.json. Returns metric dict."""
    edits = truth["edits"]
    edit_idx = {e["index"] for e in edits if e["type"] != "delete"}
    used_extras, used_words = set(), set()
    r = {"edits": len(edits), "caught_right": 0, "caught_other": 0, "not_caught": 0}
    for t in EDIT_TYPES:
        r[f"right_{t}"] = 0
    for e in edits:
        if e["type"] == "delete":
            b, w = e["index"], e["original"]
            hit = next((k for k, (eb, ew) in enumerate(extras)
                        if k not in used_extras and ew == w and abs(eb - b) <= DELETE_SLACK), None)
            if hit is not None:
                used_extras.add(hit)
                outcome = "right"
            else:
                # Caught another way: some extra near the spot, or a neighbouring target word flagged.
                near_x = [k for k, (eb, _) in enumerate(extras) if k not in used_extras and abs(eb - b) <= 1]
                near_w = [k for k in (b - 1, b) if 0 <= k < len(states) and k not in edit_idx
                          and states[k] != "correct"]
                if near_x or near_w:
                    used_extras.update(near_x)
                    used_words.update(near_w)
                    outcome = "other"
                else:
                    outcome = "miss"
        else:
            s = states[e["index"]]
            outcome = "right" if s == e["expect"] else "other" if s != "correct" else "miss"
        if outcome == "right":
            r["caught_right"] += 1
            r[f"right_{e['type']}"] += 1
        elif outcome == "other":
            r["caught_other"] += 1
        else:
            r["not_caught"] += 1
    r["fa_words"] = sum(1 for k, s in enumerate(states)
                        if k not in edit_idx and k not in used_words and s != "correct")
    r["fa_extras"] = len(extras) - len(used_extras)
    return r


def flags(m, states, extras):
    out = [f"{s}:{m.target[k]}@{k}" for k, s in enumerate(states) if s != "correct"]
    out += [f"+{w}@{b}" for b, w in extras]
    return " ".join(out)


def run_variant(rec, utterances, text, truth, mode, cache, holdback=True):
    """Transcribe utterances (feeding the matcher one by one) and score the result.
    cache[(i, prompt)] = (words, seconds) so an identical call is not repeated."""
    m = Matcher(text)
    builder = PromptBuilder(text, mode, count_tokens=rec.count_tokens) if mode in ("back", "around") else None
    latencies, spoken = [], []
    for i, utt in enumerate(utterances):
        prompt = prompt_for(mode, text, m, builder)
        key = (i, prompt)
        if key not in cache:
            rec.initial_prompt = prompt
            t0 = time.perf_counter()
            words = rec.transcribe(utt.audio)
            cache[key] = (words, time.perf_counter() - t0)
        words, secs = cache[key]
        latencies.append(secs)
        spoken.extend(words)
        m.feed(words, final=not holdback)
    m.flush()
    states = ["missed" if s == "pending" else s for s in m.states()]
    extras = m.extras()
    row = {"target_words": len(states), **{s: states.count(s) for s in STATES}, "extra": len(extras)}
    if truth is None:
        row["accuracy_pct"] = round(100 * row["correct"] / max(1, len(states)), 1)
        row["fa_words"] = len(states) - row["correct"]
        row["fa_extras"] = len(extras)
    else:
        if len(truth["labels"]) != len(states):
            raise ValueError(f"truth.json has {len(truth['labels'])} labels, target has {len(states)} words")
        row.update(score_truth(states, extras, truth))
    row.update(
        prompt_tokens=rec.count_tokens(text),
        utterances=len(utterances),
        asr_s=sum(latencies),
        lat_max_ms=round(1000 * max(latencies, default=0)),
        transcript=" ".join(spoken),
        flags=flags(m, states, extras),
    )
    row["lat_mean_ms"] = round(1000 * row["asr_s"] / max(1, len(latencies)))
    return row


SUM_KEYS = ["target_words", *STATES, "extra", "fa_words", "fa_extras", "edits", "caught_right", "caught_other",
            "not_caught", *[f"right_{t}" for t in EDIT_TYPES], "utterances", "asr_s", "audio_s"]


def summary_row(rows, **labels):
    t = {"clip": "ALL", **labels}
    for k in SUM_KEYS:
        vals = [r[k] for r in rows if k in r]
        if vals:
            t[k] = round(sum(vals), 3)
    if rows and rows[0]["variant"] == "original":
        t["accuracy_pct"] = round(100 * t["correct"] / max(1, t["target_words"]), 1)
    t["lat_mean_ms"] = round(1000 * t["asr_s"] / max(1, t["utterances"]))
    t["lat_max_ms"] = max(r["lat_max_ms"] for r in rows)
    t["rtf"] = round(t["asr_s"] / max(t["audio_s"], 1e-9), 3)
    return t


def summarize(rows):
    out = []
    keyset = sorted({(r["model"], r["prompt"]) for r in rows if r.get("status") == "ok"},
                    key=lambda k: ([c[0] for c in CONFIGS].index(k[0]), MODES.index(k[1])))
    for model, mode in keyset:
        base = [r for r in rows if r["model"] == model and r["prompt"] == mode and r.get("status") == "ok"]
        for variant in ("original", "mistakes"):
            for group in GROUPS + ["ALL"]:
                sel = [r for r in base if r["variant"] == variant and (group == "ALL" or r["group"] == group)]
                if sel:
                    out.append(summary_row(sel, model=model, compute=sel[0]["compute"], prompt=mode,
                                           group=group, variant=variant, vram_mb=sel[0].get("vram_mb"),
                                           status="ok"))
    return out


def print_summary(summary, failed):
    cols = [("model", 15), ("prompt", 6), ("group", 8), ("acc%", 5), ("FA", 4),
            ("edits", 5), ("right", 5), ("other", 5), ("miss", 4), ("sw/de/in/ms", 11), ("FA_m", 4),
            ("ms/utt", 6), ("vram", 5)]
    print("  ".join(c.ljust(w) for c, w in cols))
    print("  ".join("-" * w for _, w in cols))
    index = {(r["model"], r["prompt"], r["group"], r["variant"]): r for r in summary}
    for (model, mode, group, variant), o in index.items():
        if variant != "original":
            continue
        x = index.get((model, mode, group, "mistakes"), {})
        vals = {
            "model": model, "prompt": mode, "group": group, "acc%": o.get("accuracy_pct"),
            "FA": o["fa_words"] + o["fa_extras"], "edits": x.get("edits", ""), "right": x.get("caught_right", ""),
            "other": x.get("caught_other", ""), "miss": x.get("not_caught", ""),
            "sw/de/in/ms": "/".join(str(x.get(f"right_{t}", "")) for t in EDIT_TYPES) if x else "",
            "FA_m": x["fa_words"] + x["fa_extras"] if x else "", "ms/utt": o["lat_mean_ms"], "vram": o.get("vram_mb"),
        }
        print("  ".join(str(vals[c]).ljust(w) for c, w in cols))
    for r in failed:
        print(f"{r['model']}: {r['status']}")
    print("\nacc% / FA: original texts (FA = target words not correct + extras). edits / right / other / miss:\n"
          "injected mistakes caught with the right label / another label / not at all; sw/de/in/ms = right\n"
          "per type (swap, delete, insert, misspell); FA_m = false alarms on unedited words of mistake texts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--clips", default=str(ROOT / "clips"))
    parser.add_argument("--out", default=str(ROOT / "tasks" / "TASK_03_benchmark.csv"))
    parser.add_argument("--no-holdback", action="store_true", help="feed the matcher with final=True (Task 02b)")
    parser.add_argument("--groups", help="comma-separated subset of: " + ", ".join(GROUPS))
    parser.add_argument("--models", help="comma-separated subset of: " + ", ".join(m for m, _ in CONFIGS))
    parser.add_argument("--modes", default=",".join(MODES), help="comma-separated subset of: " + ", ".join(MODES))
    args = parser.parse_args()

    clips = load_clips(args.clips)
    if args.groups:
        clips = [c for c in clips if c[0] in args.groups.split(",")]
    if not clips:
        print(NO_CLIPS_HELP.format(folder=args.clips))
        sys.exit(1)
    configs = CONFIGS
    if args.models:
        wanted = [m.strip() for m in args.models.split(",")]
        configs = [c for c in CONFIGS if c[0] in wanted]
    modes = [m.strip() for m in args.modes.split(",") if m.strip() in MODES]

    print(f"Segmenting {len(clips)} clip(s) with Silero VAD...")
    seg = Segmenter()
    prepared = []
    for group, name, wav, variants in clips:
        audio = load_wav(wav)
        seg.reset()
        utts = list(seg.feed(to_blocks(audio)))
        seconds = len(audio) / SAMPLE_RATE
        print(f"  [{group}] {name}: {seconds:.1f} s, {len(utts)} utterance(s), "
              f"variants: {', '.join(v for v, _, _ in variants)}")
        prepared.append((group, name, utts, seconds, variants))

    from asr.engine import Recognizer

    all_rows, failed = [], []
    for model, compute in configs:
        print(f"\n=== {model} ({compute}) ===", flush=True)
        vram = VramPeak()
        rec = None
        model_rows = []
        try:
            rec = Recognizer(model, compute)
            for group, name, utts, seconds, variants in prepared:
                cache = {}  # per clip: same utterance + same prompt -> same words
                for mode in modes:
                    for variant, text, truth in variants:
                        row = run_variant(rec, utts, text, truth, mode, cache, holdback=not args.no_holdback)
                        row.update(model=model, compute=compute, prompt=mode, group=group, clip=name,
                                   variant=variant, audio_s=round(seconds, 1), status="ok",
                                   rtf=round(row["asr_s"] / max(seconds, 1e-9), 3))
                        model_rows.append(row)
                if vram.peak() is not None and vram.peak() > VRAM_LIMIT_MB:
                    raise RuntimeError(f"over memory budget: {vram.peak()} MB > 6 GB")
                print(f"  {name} done", flush=True)
        except Exception as e:
            status = "skipped: does not fit in 6 GB" if is_oom(e) else f"error: {e}"
            print(f"  {status}")
            row = {"model": model, "compute": compute, "clip": "ALL", "status": status}
            model_rows = [row]
            failed.append(row)
        finally:
            del rec
            gc.collect()
            peak = vram.stop()
        print(f"  peak VRAM (whole GPU, above baseline): {peak} MB")
        for r in model_rows:
            r["vram_mb"] = peak
        all_rows.extend(model_rows)

    summary = summarize(all_rows)
    print()
    print_summary(summary, failed)
    fields = ["model", "compute", "prompt", "group", "variant", "clip", "target_words", *STATES, "extra",
              "accuracy_pct", "fa_words", "fa_extras", "edits", "caught_right", "caught_other", "not_caught",
              *[f"right_{t}" for t in EDIT_TYPES], "utterances", "lat_mean_ms", "lat_max_ms", "audio_s", "rtf",
              "vram_mb", "prompt_tokens", "status", "flags", "transcript"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows + summary + failed)
    print(f"\nSaved {args.out}")


if __name__ == "__main__":
    main()
