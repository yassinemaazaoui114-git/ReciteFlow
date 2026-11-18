"""Live mode on WAV files (Task 04 step 4): display latency + accuracy.

    python tools/live_latency.py                              # practice set, simulated real time
    python tools/live_latency.py --sets practice --realtime --limit 6   # true real-time replay
    python tools/live_latency.py --sets personal --realtime   # final exam (run ONCE)

Display latency = time from the end of a spoken word (Whisper word timestamp of the committed
word) to the moment its colour is drawn (after the acoustic check and the matcher). --realtime
replays the WAV at real speed with the wall clock; without it, asr.live.SimClock gives the times a
real-time run would have (audio arrives in real time, each pass/check adds its measured duration).
Backlog: lag_max = the most the processing was behind the audio; skipped = passes skipped to
catch up. Accuracy is scored like tools/benchmark_verify.py (hidden / right / other / FA).
Re-align (default; --no-realign = live words only): at each pause the whole utterance is
transcribed again and overrides the live colours. override = s from the end of the utterance audio
(speech + 0.2 s VAD padding) to the override being drawn.
"""

import argparse
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from asr.audio import from_wav  # noqa: E402
from asr.live import RealClock, SimClock  # noqa: E402
from asr.vad import Segmenter  # noqa: E402
from asr.verify import TAU  # noqa: E402
from core.session import HOLD_TIMEOUT, LIVE_INTERVAL, Session  # noqa: E402
from tools.benchmark_verify import load, score  # noqa: E402


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--sets", default="practice")
    p.add_argument("--mode", default="C", choices=["B", "C"])
    p.add_argument("--tau", type=float, default=TAU)
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--interval", type=float, default=LIVE_INTERVAL, help="live pass interval (s)")
    p.add_argument("--limit", type=int, help="first N clips only")
    p.add_argument("--hold-timeout", type=float, default=-1 if HOLD_TIMEOUT is None else HOLD_TIMEOUT,
                   help="s of silence before held words are committed; negative = wait for the next utterance")
    p.add_argument("--no-realign", action="store_true", help="keep the live words (no re-align at pauses)")
    p.add_argument("--out", default=str(ROOT / "tasks" / "TASK_04_live.csv"))
    args = p.parse_args()
    clips = load(args.sets.split(","))
    if args.limit:
        clips = clips[: args.limit]

    from asr.engine import Recognizer

    rec = Recognizer("large-v3-turbo", "int8_float16")
    seg = Segmenter()
    rows, all_lags, all_over = [], [], []
    for s, name, wav, variants in clips:
        for variant, text, truth in variants:
            if variant == "mistakes":  # injected text edits: the utterance benchmark covers them
                continue
            seg.reset()
            clock = RealClock() if args.realtime else SimClock()
            sess = Session(text, rec, mode=args.mode, live=True, tau=args.tau, clock=clock, live_interval=args.interval,
                           hold_timeout=None if args.hold_timeout < 0 else args.hold_timeout,
                           realign=not args.no_realign)
            for _ in sess.run(from_wav(wav, realtime=args.realtime), seg):
                pass
            sess.finish()
            states = ["missed" if x == "pending" else x for x in sess.matcher.states()]
            extras = sess.matcher.extras()
            r = score(states, extras, truth)
            r.pop("items")  # per-item lists: see tools/error_analysis.py
            want = set((truth or {}).get("recovered", []))  # restart set: mistakes re-recited correctly
            r["recovered_ok"] = len(want & sess.matcher.recovered()) if hasattr(sess.matcher, "recovered") else 0
            r["recovered_want"] = len(want)
            st = sess.live_stats
            lags = sess.display_lags
            all_lags += lags
            all_over += sess.realign_lags
            row = {"set": s, "clip": name, "variant": variant, "words": len(states), **r,
                   "lat_median_s": round(pct(lags, 0.5), 2), "lat_p90_s": round(pct(lags, 0.9), 2),
                   "lat_max_s": round(max(lags, default=0), 2), "passes": st.get("passes"),
                   "skipped": st.get("skipped_passes"), "pass_ms": round(st.get("pass_ms_mean", 0)),
                   "lag_max_s": round(st.get("lag_max_s", 0), 2), "checks": len(sess.checks),
                   "realign_changed": sess.realign_changed,
                   "override_median_s": round(pct(sess.realign_lags, 0.5), 2) if sess.realign_lags else "",
                   "flags": " ".join([f"{x}:{sess.matcher.target[k]}@{k}" for k, x in enumerate(states) if x != "correct"]
                                     + [f"+{w}@{b}" for b, w in extras])}
            rows.append(row)
            print(f"{name:42} hidden {r['hidden']:2} right {r['right']:2} other {r['other']:2} FA {r['fa']:2} | "
                  f"lat med {row['lat_median_s']:.2f} p90 {row['lat_p90_s']:.2f} max {row['lat_max_s']:.2f} | "
                  f"passes {row['passes']} skipped {row['skipped']} lag_max {row['lag_max_s']:.2f} "
                  f"realign changed {sess.realign_changed}"
                  + (f" | recovered {r['recovered_ok']}/{r['recovered_want']}" if r["recovered_want"] else ""), flush=True)
            if s == "personal":
                print(f"   flags: {row['flags']}")
    tot = {k: sum(r[k] for r in rows) for k in ("mistakes", "right", "other", "hidden", "fa", "passes", "skipped",
                                                 "recovered_ok", "recovered_want")}
    print(f"\nALL ({len(rows)} clips, {'real time' if args.realtime else 'simulated real time'}): {tot}")
    print(f"display latency: median {pct(all_lags, 0.5):.2f} s, p90 {pct(all_lags, 0.9):.2f} s, "
          f"max {max(all_lags, default=0):.2f} s over {len(all_lags)} words; "
          f"max backlog {max((r['lag_max_s'] for r in rows), default=0):.2f} s")
    if all_over:
        print(f"override after a pause: median {pct(all_over, 0.5):.2f} s, p90 {pct(all_over, 0.9):.2f} s, "
              f"max {max(all_over):.2f} s over {len(all_over)} pauses")
    rows.append({"set": "ALL", "clip": "ALL", **tot, "lat_median_s": round(pct(all_lags, 0.5), 2),
                 "lat_p90_s": round(pct(all_lags, 0.9), 2), "lat_max_s": round(max(all_lags, default=0), 2),
                 "lag_max_s": max((r["lag_max_s"] for r in rows), default=0)})
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
