"""Where do the remaining errors come from? Runs the default app pipeline (mode C + live + re-align)
on the practice set and gives every hidden mistake and every false alarm a cause:

    python tools/error_analysis.py                       # practice set, ~6 min on the GPU
    python tools/error_analysis.py --limit 5             # first 5 clips (quick look)

Causes (from the final pass of each utterance = the re-align at the pause, which sets the colours):
    asr        Whisper wrote something else than what was said, and the acoustic check did not fix it
               (hidden: Whisper wrote the target word; false alarm: Whisper misheard a correct word)
    check      the acoustic check made the wrong call (accepted the target over a real mistake, or
               rejected a target word that was said)
    matcher    Whisper's words were right but the alignment / live logic marked it wrong
"asr only" = what the matcher shows when fed Whisper's words with no acoustic check. If the ASR-only
view is already wrong, the model is the cause; a better model (or fine-tuning) is what would help.
Practice set only (never personal clips: they are the held-out exam).
"""

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from asr.audio import from_wav  # noqa: E402
from asr.live import SimClock  # noqa: E402
from asr.vad import Segmenter  # noqa: E402
from core.session import DEFAULT_MODE, Session  # noqa: E402
from matcher import Matcher, normalize  # noqa: E402
from tools.benchmark_verify import SLACK, load, mistake_items, score  # noqa: E402


def covering(log, k):
    """The acoustic check of the final passes whose target span covers target index k."""
    for p, res in log:
        for c in res.checks:
            lo, hi = p + c.t_lo, p + max(c.t_hi, c.t_lo + 1)
            if lo <= k < hi:
                return c
    return None


def heard_near(log, k):
    """Whisper's words of the final pass that starts at or before target index k."""
    best = None
    for p, res in log:
        if p <= k:
            best = res
    return " ".join(best.raw) if best else ""


def analyse(name, text, truth, log, states, extras, items):
    asr = Matcher(text)  # what Whisper's words alone give, no acoustic check
    for _, res in log:
        asr.feed(normalize(" ".join(res.raw)), final=False)
    asr.flush()
    asr_states = ["missed" if s == "pending" else s for s in asr.states()]
    asr_extras = asr.extras()
    target = asr.target
    labels, _ = mistake_items(truth)
    rows = []

    def row(kind, k, word, expect, got, cause, c):
        rows.append({"clip": name, "kind": kind, "index": k, "word": word, "truth": expect, "app": got,
                     "asr_only": asr_states[k] if 0 <= k < len(asr_states) else "",
                     "cause": cause, "check_margin": "" if c is None else round(c.margin, 2),
                     "check": "" if c is None else f"{' '.join(c.heard)} -> {' '.join(c.target)}",
                     "target_near": " ".join(target[max(0, k - 3): k + 4]), "heard": heard_near(log, k)})

    for k in items["hidden_words"]:
        c = covering(log, k)
        cause = "check" if c and c.accepted else "asr" if asr_states[k] == "correct" else "matcher"
        row("hidden", k, target[k], labels[k], states[k], cause, c)
    for group in items["hidden_groups"]:
        b = group[0][0]
        words = " ".join(w for _, w in group)
        c = covering(log, b)
        in_asr = any(abs(eb - b) <= SLACK + len(group) and ew in words.split() for eb, ew in asr_extras)
        cause = ("check" if c and c.accepted else "matcher") if in_asr else "asr"
        row("hidden extra", b, "+" + words, "extra", "-", cause, c)
    for k in items["fa_words"]:
        c = covering(log, k)
        cause = "check" if c and not c.accepted and asr_states[k] == "correct" else \
            "asr" if asr_states[k] != "correct" else "matcher"
        row("false alarm", k, target[k], "correct", states[k], cause, c)
    for b, w in items["fa_extras"]:
        c = covering(log, b)
        in_asr = any(ew == w and abs(eb - b) <= SLACK for eb, ew in asr_extras)
        row("false alarm extra", b, "+" + w, "-", "extra", "asr" if in_asr else "matcher", c)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--limit", type=int, help="first N clips only")
    p.add_argument("--out", default=str(ROOT / "benchmarks" / "error_analysis.csv"))
    args = p.parse_args()
    clips = load(["practice"])[: args.limit] if args.limit else load(["practice"])

    from asr.engine import Recognizer

    rec = Recognizer("large-v3-turbo", "int8_float16")
    seg = Segmenter()
    rows, totals = [], Counter()
    for _, name, wav, variants in clips:
        for _, text, truth in variants:
            seg.reset()
            sess = Session(text, rec, mode=DEFAULT_MODE, live=True, clock=SimClock())
            log, words = [], sess.checker.words

            def logged(matcher, audio, words=words, log=log):  # the final (re-align) passes
                pointer = matcher.pointer
                res = words(matcher, audio)
                log.append((pointer, res))
                return res

            sess.checker.words = logged
            for _ in sess.run(from_wav(wav), seg):
                pass
            sess.finish()
            states = ["missed" if x == "pending" else x for x in sess.matcher.states()]
            extras = sess.matcher.extras()
            r = score(states, extras, truth)
            totals.update(hidden=r["hidden"], fa=r["fa"])
            got = analyse(name, text, truth, log, states, extras, r["items"])
            rows += got
            print(f"{name:32} hidden {r['hidden']} FA {r['fa']}", flush=True)
            for x in got:
                print(f"    {x['kind']:18} {x['word']:14} app {x['app']:10} asr-only {x['asr_only']:10} -> {x['cause']:8}"
                      f" {x['check']} {x['check_margin']}")
    print(f"\n{len(clips)} clips: {totals['hidden']} hidden, {totals['fa']} false alarms")
    for kind in ("hidden", "hidden extra", "false alarm", "false alarm extra"):
        c = Counter(x["cause"] for x in rows if x["kind"] == kind)
        if c:
            print(f"  {kind:18} " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["clip", "kind", "index", "word", "truth", "app", "asr_only", "cause",
                                          "check_margin", "check", "target_near", "heard"])
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
