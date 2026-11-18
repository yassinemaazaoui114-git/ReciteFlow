"""Task 04 benchmark: modes A/B/C/D (asr.verify) on the TTS practice set and the public clips.

    python tools/benchmark_verify.py                          # A,B,C,D -> tasks/TASK_04_benchmark.csv
    python tools/benchmark_verify.py --modes C --tau 1,2,4    # several tau values for C
    python tools/benchmark_verify.py --sets personal --modes C --out tasks/TASK_04_final_utt.csv

Sets (personal clips are the held-out final exam; they are only run when asked by --sets):
    practice   clips/practice_tts   NAME.txt = target, NAME.truth.json = what the audio really says
    restart    clips/practice_restart  same format: a mistake, then the speaker goes back and re-recites
    short      clips/public/{clean,other}  original text (false alarms) + mistakes text (injected)
    long       clips/public/long           same, 300+ words
    personal   clips/para*.wav             no truth file: flags are printed for review
Utterances come from Silero VAD (as in the app) and are fed to the matcher with tail hold-back.
Metrics per set: hidden = a real mistake marked correct (most important), right = caught with the
expected label, other = flagged with another label, FA = flags on words that were said correctly
(target words not correct + unmatched extras), ms/utt = ASR + check per utterance.
Every acoustic check is logged with its margin and whether the span was a real mistake
(--checks-out), which is what tau is tuned on.
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from asr.audio import SAMPLE_RATE, load_wav, to_blocks  # noqa: E402
from asr.prompt import PromptBuilder  # noqa: E402
from asr.vad import Segmenter  # noqa: E402
from asr.verify import MODES, TAU, Checker  # noqa: E402
from matcher import Matcher, normalize  # noqa: E402

SETS = ["practice", "restart", "short", "long", "personal"]
SLACK = 2  # an output extra matches a truth extra with the same word within this many positions


def read(path):
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


def load(sets):
    """[(set, name, wav, [(variant, target, truth or None)])]"""
    clips = []
    for s, folder in (("practice", "practice_tts"), ("restart", "practice_restart")):
        if s not in sets:
            continue
        for wav in sorted((ROOT / "clips" / folder).glob("*.wav")):
            truth = json.loads(read(wav.with_name(wav.stem + ".truth.json")))
            clips.append((s, wav.stem, wav, [("spoken", read(wav.with_suffix(".txt")), truth)]))
    for s, subs in (("short", ["clean", "other"]), ("long", ["long"])):
        if s not in sets:
            continue
        for sub in subs:
            for wav in sorted((ROOT / "clips" / "public" / sub).glob("*.wav")):
                truth = json.loads(read(wav.with_name(wav.stem + ".truth.json")))
                clips.append((s, wav.stem, wav, [("original", read(wav.with_suffix(".txt")), None),
                                                 ("mistakes", read(wav.with_name(wav.stem + ".mistakes.txt")), truth)]))
    if "personal" in sets:
        for wav in sorted((ROOT / "clips").glob("para*.wav")):
            clips.append(("personal", wav.stem, wav, [("original", read(wav.with_suffix(".txt")), None)]))
    return clips


def mistake_items(truth):
    """(target index -> expected label, [group of (before, word) extras]). A practice-set
    self-correction ("when you close, when you open") is ONE mistake: a group of extras."""
    if truth is None:
        return {}, []
    labels = {k: s for k, s in enumerate(truth["labels"]) if s != "correct"}
    groups = [[(x["before"], w) for w in [x["word"]]] for x in truth["extras"]]
    if any("spoken" in e for e in truth.get("edits", [])):  # practice format
        groups = []
        for e in truth["edits"]:
            if e["type"] == "selfcorrect":
                groups.append([(e["index"], w) for w in e["extras"]])
            elif e["type"] == "insert":
                groups.append([(e["index"], w) for w in normalize(e["spoken"])])
    return labels, groups


def score(states, extras, truth):
    labels, groups = mistake_items(truth)
    r = {"mistakes": len(labels) + len(groups), "right": 0, "other": 0, "hidden": 0}
    used_x, used_w = set(), set()
    hidden_words, hidden_groups = [], []  # for tools/error_analysis.py
    for k, expect in labels.items():
        s = states[k]
        r["right" if s == expect else "other" if s != "correct" else "hidden"] += 1
        if s == "correct":
            hidden_words.append(k)
    for group in groups:
        hits = []
        for b, w in group:
            hit = next((i for i, (eb, ew) in enumerate(extras)
                        if i not in used_x and i not in hits and ew == w and abs(eb - b) <= SLACK + len(group)), None)
            if hit is not None:
                hits.append(hit)
        if hits:
            used_x.update(hits)
            r["right"] += 1
            continue
        b = group[0][0]
        near_x = [i for i, (eb, _) in enumerate(extras) if i not in used_x and b - 1 <= eb <= b + len(group)]
        near_w = [k for k in range(b - 1, b + len(group)) if 0 <= k < len(states) and k not in labels
                  and states[k] != "correct"]
        if near_x or near_w:
            used_x.update(near_x)
            used_w.update(near_w)
            r["other"] += 1
        else:
            r["hidden"] += 1
            hidden_groups.append(group)
    r["fa_words"] = sum(1 for k, s in enumerate(states) if k not in labels and k not in used_w and s != "correct")
    r["fa_extras"] = len(extras) - len(used_x)
    r["fa"] = r["fa_words"] + r["fa_extras"]
    r["items"] = {"hidden_words": hidden_words, "hidden_groups": hidden_groups,
                  "fa_words": [k for k, s in enumerate(states) if k not in labels and k not in used_w and s != "correct"],
                  "fa_extras": [x for i, x in enumerate(extras) if i not in used_x]}
    return r


def is_real(truth, pointer, check_span):
    """Was the disputed target span really different from the audio? (truth labels / extras)"""
    labels, groups = mistake_items(truth)
    lo, hi = pointer + check_span[0], pointer + check_span[1]
    if any(k in labels for k in range(lo, max(hi, lo + 1))):
        return True
    return any(lo <= b <= hi for g in groups for b, _ in g)


def run(checker, utts, text, truth, log):
    m = Matcher(text)
    lat, asr_words = [], []
    for i, utt in enumerate(utts):
        pointer = m.pointer
        res = checker.words(m, utt.audio)
        for c in res.checks:
            span = (c.t_lo, c.t_hi)
            log.append({"utt": i, "heard": " ".join(c.heard), "target": " ".join(c.target),
                        "margin": round(c.margin, 3), "accepted": c.accepted,
                        "real": is_real(truth, pointer, span) if truth is not None else ""})
        m.feed(res.words, final=False)
        lat.append(res.asr_s + res.check_s)
        asr_words += res.raw
    m.flush()
    states = ["missed" if s == "pending" else s for s in m.states()]
    extras = m.extras()
    row = {"target_words": len(states), "utterances": len(utts), "ms_utt": round(1000 * sum(lat) / max(1, len(lat))),
           "ms_max": round(1000 * max(lat, default=0)), "checks": len(log),
           "flags": " ".join([f"{s}:{m.target[k]}@{k}" for k, s in enumerate(states) if s != "correct"]
                             + [f"+{w}@{b}" for b, w in extras]),
           "transcript": " ".join(asr_words)}
    row.update(score(states, extras, truth))
    return row


TOTAL_KEYS = ["target_words", "utterances", "mistakes", "right", "other", "hidden", "fa_words", "fa_extras", "fa"]


def totals(rows, **labels):
    t = {"clip": "ALL", **labels}
    for k in TOTAL_KEYS:
        t[k] = sum(r.get(k, 0) for r in rows)
    t["ms_utt"] = round(sum(r["ms_utt"] * r["utterances"] for r in rows) / max(1, t["utterances"]))
    t["ms_max"] = max((r["ms_max"] for r in rows), default=0)
    return t


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--sets", default="practice,short,long")
    p.add_argument("--modes", default=",".join(MODES))
    p.add_argument("--tau", default=str(TAU), help="comma-separated tau values (modes C/D)")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--compute", default="int8_float16")
    p.add_argument("--out", default=str(ROOT / "tasks" / "TASK_04_benchmark.csv"))
    p.add_argument("--checks-out", default=str(ROOT / "tasks" / "TASK_04_checks.csv"))
    args = p.parse_args()
    sets = args.sets.split(",")
    clips = load(sets)
    seg = Segmenter()
    prepared = []
    for s, name, wav, variants in clips:
        audio = load_wav(wav)
        seg.reset()
        prepared.append((s, name, list(seg.feed(to_blocks(audio))), len(audio) / SAMPLE_RATE, variants))
    print(f"{len(prepared)} clips, {sum(len(u) for _, _, u, _, _ in prepared)} utterances", flush=True)

    from asr.engine import Recognizer

    rec = Recognizer(args.model, args.compute)
    rows, checks_log = [], []
    configs = [(m, float(t)) for m in args.modes.split(",") for t in (args.tau.split(",") if m in "CD" else ["0"])]
    for mode, tau in configs:
        t0 = time.perf_counter()
        for s, name, utts, secs, variants in prepared:
            for variant, text, truth in variants:
                checker = Checker(rec, mode, tau, PromptBuilder(text, "auto:around", count_tokens=rec.count_tokens))
                log = []
                row = run(checker, utts, text, truth, log)
                row.update(mode=mode, tau=tau if mode in "CD" else "", set=s, clip=name, variant=variant,
                           audio_s=round(secs, 1))
                rows.append(row)
                for c in log:
                    c.update(mode=mode, tau=tau, set=s, clip=name, variant=variant)
                checks_log += log
                if s == "personal":
                    print(f"  [{mode}] {name}: {row['flags']}\n        heard: {row['transcript']}")
        print(f"mode {mode} tau {tau}: {time.perf_counter() - t0:.0f} s", flush=True)

    summary = []
    for mode, tau in configs:
        for s in sets:
            for variant in ("spoken", "original", "mistakes"):
                sel = [r for r in rows if r["mode"] == mode and (r["tau"] == tau or mode not in "CD")
                       and r["set"] == s and r["variant"] == variant]
                if sel:
                    summary.append(totals(sel, mode=mode, tau=tau if mode in "CD" else "", set=s, variant=variant))
    print(f"\n{'mode':4} {'tau':>4} {'set':9} {'variant':9} {'mist':>4} {'right':>5} {'other':>5} {'HIDDEN':>6} "
          f"{'FA':>4} {'ms/utt':>6}")
    for t in summary:
        print(f"{t['mode']:4} {str(t['tau']):>4} {t['set']:9} {t['variant']:9} {t['mistakes']:>4} {t['right']:>5} "
              f"{t['other']:>5} {t['hidden']:>6} {t['fa']:>4} {t['ms_utt']:>6}")
    fields = ["mode", "tau", "set", "variant", "clip", "target_words", "utterances", "mistakes", "right", "other",
              "hidden", "fa", "fa_words", "fa_extras", "checks", "ms_utt", "ms_max", "audio_s", "flags", "transcript"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows + summary)
    with open(args.checks_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["mode", "tau", "set", "clip", "variant", "utt", "heard", "target", "margin",
                                          "accepted", "real"])
        w.writeheader()
        w.writerows(checks_log)
    print(f"Saved {args.out} and {args.checks_out}")


if __name__ == "__main__":
    main()
