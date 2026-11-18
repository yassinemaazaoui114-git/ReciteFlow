"""Fetch a few LibriSpeech test utterances and join them into paragraph clips.

    python tools/fetch_samples.py                  # -> clips/public/clean/*.wav|txt, clips/public/other/*.wav|txt
    python tools/fetch_samples.py --clips-per-set 4
    python tools/fetch_samples.py --long           # -> clips/public/long/: 2 clean + 1 other, 280-400 words each

Reads openslr/librispeech_asr (Hugging Face) parquet by HTTP range requests, one 100-row
row group at a time (~13 MB each), so the 350 MB split is never downloaded in full.
From each row group it takes 4-6 consecutive utterances of one speaker/chapter whose total
length is 20-60 s, and joins them with a short silence (a natural pause between sentences).
With --long it instead joins as many consecutive utterances of one speaker/chapter as needed
for 280-400 words (Task 03: texts longer than Whisper's ~223-token prompt limit).
"""

import argparse
import io
import os
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from asr.audio import SAMPLE_RATE  # noqa: E402

REPO = "datasets/openslr/librispeech_asr"
SETS = {"clean": "clean/test/0000.parquet", "other": "other/test/0000.parquet"}
MIN_UTTS, MAX_UTTS = 4, 6
MIN_S, MAX_S = 20.0, 60.0
LONG_MIN_WORDS, LONG_MAX_WORDS = 280, 400  # ~300 words: well past the prompt limit
LONG_SETS = {"clean": 2, "other": 1}
GAP_S = 0.7  # silence between joined utterances; just over the VAD's 0.6 s end-of-utterance pause


def open_parquet(path):
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    return pq.ParquetFile(HfFileSystem().open(f"{REPO}/{path}", "rb"))


def decode(audio_struct):
    data, sr = sf.read(io.BytesIO(audio_struct["bytes"]), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise ValueError(f"unexpected sample rate {sr}")
    return data


def pick_run(rows):
    """First run of MIN_UTTS..MAX_UTTS consecutive utterances (same speaker and chapter)
    with total duration MIN_S..MAX_S. Durations come from the FLAC headers."""
    durs = [sf.info(io.BytesIO(r["audio"]["bytes"])).duration for r in rows]
    for i in range(len(rows)):
        total = 0.0
        for k in range(i, min(i + MAX_UTTS, len(rows))):
            if (rows[k]["speaker_id"], rows[k]["chapter_id"]) != (rows[i]["speaker_id"], rows[i]["chapter_id"]):
                break
            total += durs[k] + (GAP_S if k > i else 0)
            n = k - i + 1
            if total > MAX_S:
                break
            if n >= MIN_UTTS and total >= MIN_S:
                return rows[i:k + 1]
    return None


def pick_long_run(rows):
    """First run of consecutive utterances (same speaker and chapter) with LONG_MIN_WORDS..LONG_MAX_WORDS words."""
    for i in range(len(rows)):
        words = 0
        for k in range(i, len(rows)):
            if (rows[k]["speaker_id"], rows[k]["chapter_id"]) != (rows[i]["speaker_id"], rows[i]["chapter_id"]):
                break
            words += len(rows[k]["text"].split())
            if words > LONG_MAX_WORDS:
                break
            if words >= LONG_MIN_WORDS:
                return rows[i:k + 1]
    return None


def fetch_set(name, path, n_clips, out_dir, long=False):
    print(f"[{name}] opening {path} ...", flush=True)
    pf = open_parquet(path)
    groups = pf.metadata.num_row_groups
    # Spread the clips over the file so they come from different speakers.
    offset = 0.5 if long else 0.0  # long clips: row groups between the short clips' ones
    picks = sorted({min(groups - 1, round((g + offset) * groups / n_clips)) for g in range(n_clips)})
    out_dir.mkdir(parents=True, exist_ok=True)
    made, used_speakers = 0, set()
    g_iter = iter(picks + [g for g in range(groups) if g not in picks])
    for g in g_iter:
        if made >= n_clips:
            break
        rows = pf.read_row_group(g, columns=["id", "speaker_id", "chapter_id", "text", "audio"]).to_pylist()
        rows = [r for r in rows if r["speaker_id"] not in used_speakers]
        run = pick_long_run(rows) if long else pick_run(rows)
        if not run:
            continue
        gap = np.zeros(int(GAP_S * SAMPLE_RATE), dtype=np.float32)
        parts = []
        for k, r in enumerate(run):
            if k:
                parts.append(gap)
            parts.append(decode(r["audio"]))
        audio = np.concatenate(parts)
        text = " ".join(r["text"].strip().lower() for r in run)
        stem = f"{'long_' if long else ''}{name}_{run[0]['id']}_{len(run)}u"
        sf.write(out_dir / f"{stem}.wav", audio, SAMPLE_RATE, subtype="PCM_16")
        (out_dir / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
        used_speakers.add(run[0]["speaker_id"])
        made += 1
        print(f"  {stem}: {len(audio) / SAMPLE_RATE:.1f} s, {len(run)} utterances, {len(text.split())} words",
              flush=True)
    return made


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "clips" / "public"))
    parser.add_argument("--clips-per-set", type=int, default=5, help="5 clips x 4-6 utterances = ~20-25 utterances per set")
    parser.add_argument("--long", action="store_true", help="fetch the long (200-400 word) clips instead")
    args = parser.parse_args()
    if args.long:
        for name, n in LONG_SETS.items():
            made = fetch_set(name, SETS[name], n, Path(args.out) / "long", long=True)
            if made < n:
                print(f"[long {name}] only {made} clip(s) found")
        return
    for name, path in SETS.items():
        made = fetch_set(name, path, args.clips_per_set, Path(args.out) / name)
        if made < args.clips_per_set:
            print(f"[{name}] only {made} clip(s) found")


if __name__ == "__main__":
    main()
