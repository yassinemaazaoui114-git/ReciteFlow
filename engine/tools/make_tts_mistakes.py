"""Practice set with REAL spoken mistakes: Windows TTS speaks a modified text, the target stays
the original text (Task 04). TTS is clean speech: a practice set for tuning, not proof.

    python tools/make_tts_mistakes.py            # -> clips/practice_tts/
    python tools/make_tts_mistakes.py --seed 3 --list-only
    python tools/make_tts_mistakes.py --restarts  # -> clips/practice_restart/ (re-recite set)

Texts: the 13 public clip texts (clips/public/**/NAME.txt) + EVERYDAY paragraphs below.
Per clip NAME it writes (same layout as the personal clips: NAME.txt is the TARGET):
    NAME.wav          16 kHz mono, what the TTS voice said (NAME.spoken.txt)
    NAME.txt          the target text (original, unchanged)
    NAME.spoken.txt   the text the voice actually spoke (with the mistakes)
    NAME.truth.json   labels[i] per normalized target word (correct | wrong | misspelled | missed),
                      extras [{"before": i, "word": w}], edits [{type, index, target, spoken, expect}]
Mistake types: swap (plausible in context: numbers, time words, opposites, close sounds; label
`wrong`, or `misspelled` when the matcher's near-miss rule accepts the pair), morph (plural/tense,
`misspelled`), delete (target word not said: `missed`), insert (extra word said: `extra`),
selfcorrect (a wrong word, then the phrase again correctly: "when you close, when you open";
the first attempt is `extra`).
About 30% of the clips have no mistake (false alarms), incl. texts with names and rare words.
Voice: every installed English SAPI voice, rate and pitch varied per clip.

--restarts (re-recite set, R12/R14): the voice makes a mistake, may go on for 0-3 words, pauses,
goes BACK (to the sentence start, or 3-12 words back) and says everything again correctly, then
continues. Truth: the mistaken word keeps its label (wrong/misspelled), the repeated words are
not extras, and truth["recovered"] lists the target indices that should be shown as recovered.
EVERYDAY texts only: r1 = one restart, r2 = two restarts in different sentences.
"""

import argparse
import json
import random
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from matcher import normalize  # noqa: E402
from matcher.align import _is_near  # noqa: E402

OUT = ROOT / "clips" / "practice_tts"
OUT_RESTART = ROOT / "clips" / "practice_restart"

EVERYDAY = {
    "walk": "Every Monday at seven in the morning, Maria walks her two dogs around the small lake near her "
            "house. The walk takes about forty minutes. On the way back she buys fresh bread and a dozen eggs "
            "at the corner shop, and then she starts work at nine.",
    "train": "The train to Edinburgh leaves at half past three from platform six. If you miss it, the next one "
             "is at five, but it is often late. Buy your ticket before Friday, because the price goes up by "
             "twenty percent at the weekend.",
    "soup": "To make the soup, cut three carrots and one large onion into small pieces. Heat a little oil in a "
            "big pot, add the vegetables and cook them for ten minutes. Then pour in two cups of water and let "
            "it boil slowly for half an hour.",
    "doctor": "Doctor Okafor told me to drink more water and to sleep at least eight hours every night. She also "
              "said I should walk for thirty minutes a day and eat less sugar. I will see her again in four "
              "weeks, on a Tuesday afternoon.",
    "meeting": "Our team meeting moved from Wednesday to Thursday this week. It starts at ten and should finish "
               "before noon. Please bring the first draft of the report and the new budget, which is about "
               "fifteen thousand dollars higher than last year.",
    "village": "Last summer we drove north to a quiet village in the mountains. The road was narrow and steep, "
               "and it took us almost six hours. We stayed in an old cottage with a red door, and every evening "
               "we watched the sun go down behind the hills.",
    "bakery": "Siobhan opened her small bakery in Galway twelve years ago. At first she sold only bread, but now "
              "she makes cakes, cookies and coffee too. The shop is open from eight to six, and it is closed on "
              "Sundays and public holidays.",
    "plants": "Remember to water the plants twice a week while I am away. The big ones near the window need more "
              "water than the small ones in the kitchen. If the leaves turn yellow, move them out of the sun for "
              "a few days.",
    "museum": "The museum has three floors. The first floor is about ancient Egypt, the second shows paintings "
              "from the nineteenth century, and the top floor has a cafe with a view of the river. Entry is free "
              "for children under twelve.",
    "violin": "Thomas started learning the violin when he was nine. He practiced for an hour every afternoon, "
              "even on the weekends. Ten years later he played his first concert in Vienna, and his grandmother "
              "cried in the front row.",
    "reading": "When you open a new book, read the first page slowly. This small step helps you learn the names "
               "and the place. After a week, you will read faster than before, and you will remember more of "
               "each chapter.",
}

# Plausible swaps (target word -> words a reciter might say instead). Pure homophones
# (their/there, two/too) are left out: the audio cannot tell them apart.
SWAPS = {
    # numbers
    "one": ["two"], "two": ["three", "four"], "three": ["four", "two"], "four": ["five", "three"],
    "five": ["four", "six"], "six": ["seven", "five"], "seven": ["eight", "six"], "eight": ["nine", "six"],
    "nine": ["eight", "ten"], "ten": ["six", "eight", "twelve"], "eleven": ["twelve"], "twelve": ["ten", "eleven"],
    "fifteen": ["fifty", "sixteen"], "twenty": ["thirty"], "thirty": ["forty", "thirteen"], "forty": ["fourteen", "fifty"],
    "hundred": ["thousand"], "thousand": ["hundred"], "dozen": ["couple"], "half": ["quarter"], "twice": ["three times"],
    # time words
    "day": ["week"], "week": ["month", "day"], "weeks": ["months", "days"], "month": ["year"], "year": ["month"],
    "years": ["months"], "morning": ["evening"], "evening": ["morning"], "afternoon": ["morning", "evening"],
    "night": ["day"], "monday": ["sunday", "tuesday"], "tuesday": ["thursday"], "wednesday": ["tuesday"],
    "thursday": ["tuesday", "friday"], "friday": ["sunday"], "sundays": ["mondays"], "summer": ["winter"],
    "winter": ["summer"], "today": ["tomorrow"], "before": ["after"], "after": ["before"], "early": ["late"],
    "late": ["early"], "first": ["last"], "last": ["first"], "noon": ["midnight"], "weekend": ["week"],
    "later": ["earlier"],
    # opposites
    "big": ["small"], "small": ["big"], "large": ["small"], "open": ["close"], "opened": ["closed"],
    "closed": ["open"], "hot": ["cold"], "cold": ["warm"], "more": ["less"], "less": ["more"], "up": ["down"],
    "down": ["up"], "left": ["right"], "right": ["left"], "always": ["never"], "never": ["always"],
    "old": ["new"], "new": ["old"], "high": ["low"], "higher": ["lower"], "long": ["short"], "north": ["south"],
    "south": ["north"], "quiet": ["busy"], "narrow": ["wide"], "free": ["cheap"], "buy": ["sell"], "buys": ["sells"],
    "start": ["stop"], "starts": ["ends"], "started": ["stopped"], "finish": ["start"], "slowly": ["quickly"],
    "fresh": ["old"], "faster": ["slower"], "many": ["few"], "few": ["many"], "good": ["bad"], "young": ["old"],
    "front": ["back"], "back": ["front"], "top": ["bottom"], "under": ["over"], "inside": ["outside"],
    "learning": ["teaching"], "learn": ["teach"], "remember": ["forget"], "yellow": ["brown"], "red": ["blue"],
    "black": ["white"], "white": ["black"], "great": ["small"], "little": ["lot of"], "near": ["far from"],
    "full": ["empty"], "light": ["dark"], "dark": ["light"], "happy": ["sad"], "same": ["other"],
    # close sounds / function words
    "this": ["these"], "these": ["this"], "then": ["than"], "than": ["then"], "a": ["the"], "the": ["a"],
    "in": ["on"], "on": ["in"], "at": ["in"], "his": ["her"], "her": ["his"], "he": ["she"], "she": ["he"],
    "was": ["is"], "is": ["was"], "were": ["are"], "will": ["would"], "would": ["will"], "can": ["could"],
    "could": ["can"], "some": ["all"], "all": ["some"], "every": ["each"], "each": ["every"], "and": ["but"],
    "but": ["and"], "him": ["them"], "them": ["him"], "with": ["without"], "into": ["onto"], "from": ["for"],
}
INSERT_WORDS = ["very", "really", "just", "also", "then", "still", "all", "the", "so", "always", "only", "quite"]
TYPE_WEIGHTS = [("swap", 45), ("morph", 15), ("delete", 15), ("insert", 10), ("selfcorrect", 15)]
GAP = 3  # at least this many untouched target words between two edits


FUNCTION = {"a", "the", "and", "but", "in", "on", "at", "his", "her", "he", "she", "was", "is", "were", "will",
            "would", "can", "could", "some", "all", "every", "each", "him", "them", "with", "into", "from", "this",
            "these", "then", "than", "only", "less", "more", "very", "also", "just", "even", "much", "miss",
            "there", "over", "good", "here", "where"}


def _common_word():
    """word -> True if ' word' is one Whisper token (a common real English word)."""
    from tokenizers import Tokenizer as HF

    hub = Path.home() / ".cache" / "huggingface" / "hub"
    path = next(hub.glob("models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/*/tokenizer.json"), None)
    if path is None:
        return lambda w: True
    tok = HF.from_file(str(path))
    return lambda w: len(tok.encode(" " + w, add_special_tokens=False).ids) == 1


COMMON = _common_word()


def morph(word):
    """Plural/tense change the matcher counts as a near-miss and that is a common word, or None."""
    if word in FUNCTION or word.endswith(("us", "is", "ss", "y", "x", "sh", "ch")):
        return None
    if word.endswith("ed") and len(word) >= 6:
        cand = word[:-2] + "ing"
    elif word.endswith("ing") and len(word) >= 6:
        cand = word[:-3] + "ed"
    elif word.endswith("s") and not word.endswith("ss") and len(word) >= 5:
        cand = word[:-1]
    elif word.isalpha() and len(word) >= 4:
        cand = word + "s"
    else:
        return None
    return cand if _is_near(cand, word) and COMMON(cand) else None


class Tokens:
    """Raw words of a text with the normalized index range of each (punctuation kept on raw words)."""

    def __init__(self, text):
        self.raw = text.split()
        self.spans, n = [], 0
        for w in self.raw:
            k = len(normalize(w))
            self.spans.append((n, n + k))
            n += k
        self.n = n

    def single(self, k):
        """Normalized word if raw word k is exactly one plain normalized word, else None."""
        a, b = self.spans[k]
        if b - a != 1:
            return None
        norm = normalize(self.raw[k])[0]
        core = self.raw[k].strip(".,;:!?\"'()").lower()
        return norm if core == norm else None


def _with_punct(raw, new):
    """Replace the word in raw token `raw` by `new`, keeping case of the first letter and punctuation."""
    lead = len(raw) - len(raw.lstrip("\"'("))
    trail = len(raw) - len(raw.rstrip(".,;:!?\"')"))
    core = raw[lead: len(raw) - trail]
    if core[:1].isupper():
        new = new[:1].upper() + new[1:]
    return raw[:lead] + new + raw[len(raw) - trail:]


def plan(tok, n_edits, rng):
    """Pick edits: [(raw index, type, spoken word or None)], spread over the text."""
    usable = range(2, len(tok.raw) - 1)  # never the first two words / the last word
    lo, hi = 2, len(tok.raw) - 1
    seg = (hi - lo) / max(1, n_edits)
    chosen = []
    for q in range(n_edits):
        a, b = int(lo + q * seg), int(lo + (q + 1) * seg)
        kinds = [k for k, w in TYPE_WEIGHTS for _ in range(w)]
        for _ in range(40):
            kind = rng.choice(kinds)
            cands = []
            for k in range(a, b):
                if k not in usable or any(abs(k - j) < GAP for j, _, _ in chosen):
                    continue
                w = tok.single(k)
                if w is None:
                    continue
                # function-word swaps (the/a, and/but) are real but should not dominate the set
                weight = 1 if w in FUNCTION else 4
                if kind in ("swap", "selfcorrect") and w in SWAPS:
                    cands += [(k, kind, s) for s in SWAPS[w]] * weight
                elif kind == "morph" and len(w) >= 4 and tok.raw[k][:1].islower() and morph(w):
                    cands.append((k, kind, morph(w)))
                elif kind == "delete" and len(w) >= 2:
                    cands.append((k, kind, None))
                elif kind == "insert":
                    prev = tok.single(k - 1)
                    cands += [(k, kind, s) for s in INSERT_WORDS if s not in (w, prev)]
            if cands:
                chosen.append(rng.choice(cands))
                break
    return sorted(chosen)


def apply(tok, edits):
    """Spoken text + truth for the target text `tok` with `edits`."""
    labels = ["correct"] * tok.n
    extras, out_edits, spoken = [], [], []
    by_k = {k: (kind, s) for k, kind, s in edits}
    for k, raw in enumerate(tok.raw):
        a, _ = tok.spans[k]
        if k not in by_k:
            spoken.append(raw)
            continue
        kind, s = by_k[k]
        w = tok.single(k)
        e = {"type": kind, "index": a, "target": w, "spoken": s}
        if kind in ("swap", "morph"):
            expect = "misspelled" if _is_near(s, w) else "wrong"
            spoken.append(_with_punct(raw, s))
            labels[a] = expect
        elif kind == "delete":
            expect = "missed"
            labels[a] = expect
            e["spoken"] = None
            # keep sentence punctuation by attaching it to the previous spoken word
            trail = raw[len(raw.rstrip(".,;:!?")):]
            if trail and spoken:
                spoken[-1] = spoken[-1].rstrip(",") + trail
        elif kind == "insert":
            expect = "extra"
            extras.append({"before": a, "word": s})
            spoken += [s, raw]
            e["target"] = None
        else:  # selfcorrect: "<2 words before> <wrong word>, <2 words before> <right word>"
            expect = "extra"
            start = max(0, k - 2)
            redo = [x.rstrip(".,;:!?") for x in tok.raw[start:k]]
            first = " ".join(redo + [_with_punct(raw, s).rstrip(".,;:!?")]) + ","
            spoken[len(spoken) - len(redo):] = [first] + spoken[len(spoken) - len(redo):]
            spoken.append(raw)
            before = tok.spans[start][0]
            words = normalize(" ".join(redo)) + normalize(s)
            e["extras"] = words
            extras += [{"before": before, "word": x} for x in words]
            e["index"] = before
        e["expect"] = expect
        out_edits.append(e)
    return " ".join(spoken), {"labels": labels, "extras": extras, "edits": out_edits}


def sentence_start(tok, k):
    """Raw index of the first word of the sentence that contains raw word k."""
    j = k
    while j > 0 and not tok.raw[j - 1].rstrip("\"')").endswith((".", "!", "?")):
        j -= 1
    return j


def apply_restarts(tok, plan_, rng):
    """Spoken text + truth for restarts. plan_ = [(raw index k, wrong word)]."""
    labels = ["correct"] * tok.n
    spoken, out_edits, recovered = [], [], []
    by_k = dict(plan_)
    k = 0
    while k < len(tok.raw):
        if k not in by_k:
            spoken.append(tok.raw[k])
            k += 1
            continue
        w, s = tok.single(k), by_k.pop(k)
        a = tok.spans[k][0]
        labels[a] = "misspelled" if _is_near(s, w) else "wrong"
        on = min(rng.choice([0, 0, 1, 2, 3]), len(tok.raw) - k - 2)  # words said after the mistake
        spoken.append(_with_punct(tok.raw[k], s))
        spoken += tok.raw[k + 1: k + 1 + on]
        spoken[-1] = spoken[-1].rstrip(".,;:!?") + "."  # a pause before going back
        back = sentence_start(tok, k) if rng.random() < 0.6 else max(0, k - rng.randint(3, 12))
        if back == k:
            back = max(0, k - rng.randint(3, 6))
        recovered.append(a)
        out_edits.append({"type": "restart", "index": a, "target": w, "spoken": s, "expect": labels[a],
                          "back_to": tok.spans[back][0], "words_back": a - tok.spans[back][0], "went_on": on})
        spoken += tok.raw[back:k]
        # k itself is then said correctly by the loop
    return " ".join(spoken), {"labels": labels, "extras": [], "edits": out_edits, "recovered": recovered}


def restart_jobs(seed):
    out = []
    for name, text in EVERYDAY.items():
        tok = Tokens(text)
        rng = random.Random(seed * 7_000_003 + zlib.crc32(name.encode()))
        swappable = [k for k in range(3, len(tok.raw) - 3) if tok.single(k) in SWAPS and tok.single(k) not in FUNCTION]
        for tag, n in (("r1", 1), ("r2", 2)):
            picks = []
            for k in rng.sample(swappable, len(swappable)):
                if all(sentence_start(tok, k) != sentence_start(tok, j) for j, _ in picks):
                    picks.append((k, rng.choice(SWAPS[tok.single(k)])))
                if len(picks) == n:
                    break
            spoken, truth = apply_restarts(tok, sorted(picks), rng)
            out.append((f"daily_{name}_{tag}", text, spoken, truth))
    return out


def sources():
    """[(name, text, rare)] rare = has names / rare words (public LibriSpeech texts do)."""
    out = [(f"daily_{k}", v, k in ("bakery", "doctor", "train", "violin")) for k, v in EVERYDAY.items()]
    for txt in sorted((ROOT / "clips" / "public").rglob("*.txt")):
        if txt.name.endswith(".mistakes.txt"):
            continue
        out.append((("pub_" + txt.stem).replace("-", ""), " ".join(txt.read_text(encoding="utf-8").split()), True))
    return out


def jobs(seed):
    """[(clip name, target text, spoken text, truth)]"""
    out = []
    for name, text, _ in sources():
        tok = Tokens(text)
        rng = random.Random(seed * 1_000_003 + zlib.crc32(name.encode()))
        long_text = tok.n >= 200
        if name.startswith("daily_"):
            variants = [("ok", 0), ("m1", 4), ("m2", 4)]
        elif long_text:
            variants = [("m1", 8)]
        else:
            # every public short text with mistakes; half of them also clean
            variants = [("m1", 4)] + ([("ok", 0)] if zlib.crc32(name.encode()) % 2 == 0 else [])
        for tag, n in variants:
            edits = plan(tok, n, rng) if n else []
            spoken, truth = apply(tok, edits)
            out.append((f"{name}_{tag}", text, spoken, truth))
    return out


PS_SCRIPT = r"""
param([string]$jobs)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($j in (Get-Content -Raw -Encoding UTF8 $jobs | ConvertFrom-Json)) {
  $s.SelectVoice($j.voice)
  $s.SetOutputToWaveFile($j.wav, $fmt)
  $s.SpeakSsml($j.ssml)
  $s.SetOutputToNull()
}
"""


def english_voices():
    cmd = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
           ".GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'en-*' } | "
           "ForEach-Object { $_.VoiceInfo.Name }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout
    return [v.strip() for v in out.splitlines() if v.strip()]


def ssml(text, rate, pitch):
    from xml.sax.saxutils import escape

    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<prosody rate="{rate:+d}%" pitch="{pitch:+d}%">{escape(text)}</prosody></speak>')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--list-only", action="store_true", help="print the plan, write nothing")
    parser.add_argument("--restarts", action="store_true", help="re-recite set -> clips/practice_restart/")
    args = parser.parse_args()
    out = Path(args.out) if not args.restarts or args.out != str(OUT) else OUT_RESTART
    todo = restart_jobs(args.seed) if args.restarts else jobs(args.seed)
    n_m = sum(len(t["edits"]) for *_, t in todo)
    n_ok = sum(1 for *_, t in todo if not t["edits"])
    for name, _, spoken, truth in todo:
        desc = ", ".join(f"{e['type']}@{e['index']} {e['target']}->{e['spoken']}"
                         + (f" back {e['words_back']}" if "words_back" in e else "") for e in truth["edits"]) or "no mistake"
        print(f"{name}: {desc}")
        if args.list_only and args.restarts:
            print(f"    {spoken}")
    kinds = {}
    for *_, t in todo:
        for e in t["edits"]:
            kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    print(f"\n{len(todo)} clips, {n_ok} without mistakes ({100 * n_ok / len(todo):.0f}%), {n_m} mistakes: {kinds}")
    if args.list_only:
        return
    voices = english_voices()
    if not voices:
        sys.exit("No English SAPI voice installed.")
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    ps_jobs = []
    for i, (name, text, spoken, truth) in enumerate(todo):
        voice = voices[i % len(voices)]
        rate, pitch = rng.choice([-15, -5, 0, 5, 10, 20]), rng.choice([-10, -5, 0, 5, 10])
        truth["voice"] = {"name": voice, "rate_pct": rate, "pitch_pct": pitch}
        wav = out / f"{name}.wav"
        (out / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
        (out / f"{name}.spoken.txt").write_text(spoken + "\n", encoding="utf-8")
        (out / f"{name}.truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
        ps_jobs.append({"voice": voice, "wav": str(wav), "ssml": ssml(spoken, rate, pitch)})
    jobs_path, script = out / "_tts_jobs.json", out / "_tts.ps1"
    jobs_path.write_text(json.dumps(ps_jobs), encoding="utf-8")
    script.write_text(PS_SCRIPT, encoding="utf-8")
    print(f"Speaking {len(ps_jobs)} clips with {voices}...", flush=True)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-jobs", str(jobs_path)], check=True)
    jobs_path.unlink()
    script.unlink()
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
