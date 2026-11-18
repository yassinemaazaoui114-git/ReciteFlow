"""Write a mistake version of each public clip's reference text, with known labels.

    python tools/make_mistakes.py                 # all clips in clips/public/**
    python tools/make_mistakes.py --seed 7

For clip NAME (NAME.wav + NAME.txt, the audio says NAME.txt) it writes:
  NAME.mistakes.txt  the target text with 4 edits, one per quarter of the paragraph:
                     swap (word -> different real word), delete, insert, misspell (plural/tense).
                     Texts of LONG_WORDS+ words get 2 rounds (8 edits, one per eighth).
  NAME.truth.json    expected matcher result when the audio (the ORIGINAL text) is recited
                     against NAME.mistakes.txt:
                       labels[i]  for each normalized target word of NAME.mistakes.txt
                                  (correct | wrong | missed | misspelled)
                       extras     [{"before": i, "word": w}] spoken words not in the target
                       edits      what was changed, where
The audio is unchanged. Edits are done on normalized words (matcher.normalize), so the
indices in truth.json are the same indices as Matcher.states().
"""

import argparse
import json
import random
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matcher import normalize  # noqa: E402
from matcher.align import _is_near  # noqa: E402  (same near-miss rule the matcher uses)

# Swapped-in / inserted words: common real words. One is picked that is not a near-miss of
# the word it replaces and not equal to the neighbours (that would make the alignment ambiguous).
SWAP_WORDS = ["house", "river", "green", "money", "window", "garden", "letter", "summer",
              "doctor", "silver", "market", "winter", "paper", "bridge", "orange", "island"]
INSERT_WORDS = ["very", "quietly", "really", "suddenly", "small", "almost", "certainly",
                "perhaps", "gently", "bright"]
EDIT_TYPES = ["swap", "delete", "insert", "misspell"]
LONG_WORDS = 200
EDGE = 2  # never edit the first/last 2 words of the paragraph
# Not misspelled: "that" -> "thats" is not a plausible recitation slip.
FUNCTION_WORDS = {"that", "this", "these", "those", "just", "never", "were", "with", "from", "have",
                  "they", "them", "then", "than", "what", "when", "which", "there", "their", "would",
                  "could", "should", "been", "into", "upon", "very", "some", "such", "only", "also",
                  "will", "shall", "your", "here", "where", "while", "about", "after", "before"}


def misspell(word):
    """Plural/tense change that the matcher's near-miss rule accepts, or None."""
    if word.endswith("ed") and len(word) >= 6:
        cand = word[:-2] + "ing"
    elif word.endswith("s") and not word.endswith("ss") and len(word) >= 5:
        cand = word[:-1]
    else:
        cand = word + "s"
    return cand if cand != word and _is_near(cand, word) else None


def editable(tokens, i):
    w = tokens[i]
    return w.isalpha() and len(w) >= 4


def make(tokens, rng):
    """Returns (modified tokens, truth dict)."""
    n = len(tokens)
    if n < 4 * 6:
        raise ValueError(f"paragraph too short for 4 spread edits ({n} words)")
    types = []
    for _ in range(2 if n >= LONG_WORDS else 1):
        round_types = EDIT_TYPES[:]
        rng.shuffle(round_types)
        types += round_types
    quarter = (n - 2 * EDGE) / len(types)  # one edit per segment
    chosen = []  # (index in original tokens, type)
    for q, kind in enumerate(types):
        lo, hi = int(EDGE + q * quarter) + 1, int(EDGE + (q + 1) * quarter) - 1
        cands = [i for i in range(lo, hi) if editable(tokens, i)
                 and tokens[i] not in (tokens[i - 1], tokens[i + 1])]
        if kind == "misspell":
            cands = [i for i in cands if tokens[i] not in FUNCTION_WORDS and misspell(tokens[i])]
        if not cands:
            raise ValueError(f"no editable word for {kind} in quarter {q}")
        chosen.append((rng.choice(cands), kind))
    chosen.sort()

    out, labels, extras, edits = [], [], [], []
    for i, w in enumerate(tokens):
        kind = next((k for j, k in chosen if j == i), None)
        prev_w = out[-1] if out else ""
        next_w = tokens[i + 1] if i + 1 < n else ""
        if kind == "swap":
            word = rng.choice([s for s in SWAP_WORDS
                               if s not in (w, prev_w, next_w) and not _is_near(s, w)])
            edits.append({"type": "swap", "index": len(out), "original": w, "target": word, "expect": "wrong"})
            out.append(word)
            labels.append("wrong")
        elif kind == "delete":
            edits.append({"type": "delete", "index": len(out), "original": w, "target": None, "expect": "extra"})
            extras.append({"before": len(out), "word": w})
        elif kind == "insert":
            word = rng.choice([s for s in INSERT_WORDS if s not in (w, prev_w) and not _is_near(s, w)])
            edits.append({"type": "insert", "index": len(out), "original": None, "target": word, "expect": "missed"})
            out.append(word)
            labels.append("missed")
            out.append(w)
            labels.append("correct")
        elif kind == "misspell":
            word = misspell(w)
            edits.append({"type": "misspell", "index": len(out), "original": w, "target": word,
                          "expect": "misspelled"})
            out.append(word)
            labels.append("misspelled")
        else:
            out.append(w)
            labels.append("correct")
    return out, {"labels": labels, "extras": extras, "edits": edits}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--clips", default=str(ROOT / "clips" / "public"))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    txts = sorted(p for p in Path(args.clips).rglob("*.txt") if not p.name.endswith(".mistakes.txt"))
    if not txts:
        print(f"No reference texts in {args.clips}; run tools/fetch_samples.py first")
        sys.exit(1)
    for txt in txts:
        stem = txt.stem
        tokens = normalize(txt.read_text(encoding="utf-8"))
        # Per-clip seed so re-running gives the same edits regardless of which clips exist.
        rng = random.Random(args.seed * 1_000_003 + zlib.crc32(stem.encode()))
        out, truth = make(tokens, rng)
        assert len(normalize(" ".join(out))) == len(truth["labels"]) == len(out)
        txt.with_name(stem + ".mistakes.txt").write_text(" ".join(out) + "\n", encoding="utf-8")
        txt.with_name(stem + ".truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
        desc = ", ".join(f"{e['type']}@{e['index']} {e['original']}->{e['target']}" for e in truth["edits"])
        print(f"{stem}: {desc}")


if __name__ == "__main__":
    main()
