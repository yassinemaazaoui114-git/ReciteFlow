"""ReciteFlow (terminal): recite a text from memory, the app marks every mistake as you go.

    python reciteflow.py --file text.txt
    python reciteflow.py                      # paste the text, then press Enter on an empty line
    python reciteflow.py --file t.txt --wav clip.wav --realtime   # test run from a recording
    python reciteflow.py --file t.txt --mode A --no-live          # Task 03 behaviour

Default (Task 04): the text is NOT given to Whisper (mode C: no prompt + acoustic check of every
disagreement), and words are coloured while you speak (live, ~1-2 s after each word).
Modes: A prompt, B no prompt, C no prompt + acoustic check, D prompt + double check (A/D: not live).

History: every microphone recitation is saved in data/history.db (text + result per word) and,
unless --no-audio, its recording in data/recordings/ (kept for training later; local only).
Replays (--wav) are only saved with --save. --no-save saves nothing.

Colors: correct green, wrong red, [missed] red, misspelled yellow, not yet said grey,
{extra} words red at the place they were said; a mistake re-recited correctly stays red and is
underlined (go back as far as the sentence start, up to 25 words). Keeps going after mistakes; ends when the
text is finished or on Ctrl+C.
"""

import argparse
import os
import shutil
import sys
import time

from colorama import Fore, Style, just_fix_windows_console

from asr.prompt import MODES
from asr.verify import MODES as CHECK_MODES

DEFAULT_PROMPT_MODE = "auto:around"  # Task 03 Part B benchmark (modes A and D)
STYLE = {
    "correct": Fore.GREEN,
    "wrong": Fore.RED,
    "missed": Fore.RED,
    "misspelled": Fore.YELLOW,
    "pending": Style.DIM + Fore.WHITE,
}
UNDERLINE = "[4m"  # recovered words (R14)


def read_text(path):
    if path:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    print("Paste the text to recite, then press Enter on an empty line:")
    lines = []
    for line in sys.stdin:
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


def colored_tokens(words, tail):
    """(visible text, colored text) per token, in reading order."""
    out = []
    for w in words:
        for x in w.extras_before:
            out.append(("{" + x + "}", Fore.RED + "{" + x + "}" + Style.RESET_ALL))
        shown = f"[{w.text}]" if w.state == "missed" else w.text
        out.append((shown, STYLE[w.state] + (UNDERLINE if w.recovered else "") + shown + Style.RESET_ALL))
    for x in tail:
        out.append(("{" + x + "}", Fore.RED + "{" + x + "}" + Style.RESET_ALL))
    return out


def render(session, status):
    words, tail = session.display()
    width = max(20, shutil.get_terminal_size((100, 30)).columns - 1)
    lines, line, used = [], [], 0
    for visible, colored in colored_tokens(words, tail):
        if line and used + 1 + len(visible) > width:
            lines.append(" ".join(line))
            line, used = [], 0
        used += len(visible) + (1 if line else 0)
        line.append(colored)
    if line:
        lines.append(" ".join(line))
    sys.stdout.write("\033[2J\033[H" + "\n".join(lines) + "\n\n" + Style.DIM + status + Style.RESET_ALL + "\n")
    sys.stdout.flush()


def print_summary(s, silence):
    print()
    print(f"Words: {s['words']}   " + Fore.GREEN + f"correct {s['correct']}" + Style.RESET_ALL + "   "
          + Fore.YELLOW + f"misspelled {s['misspelled']}" + Style.RESET_ALL + "   "
          + Fore.RED + f"wrong {s['wrong']}   missed {s['missed']}   extra {s['extra']}" + Style.RESET_ALL
          + f"   not reached {s['pending']}")
    if s["extras"]:
        print("Extra words: " + ", ".join(s["extras"]))
    if s.get("recovered"):
        print(f"Fixed by re-reciting (underlined): {s['recovered']}")
    if s.get("passes") is not None:
        print(f"Time: {s['total_s']:.1f} s, live: {s['passes']} passes ({s['pass_ms_mean']:.0f} ms each, "
              f"{s['skipped_passes']} skipped), word shown {s['display_median_s']:.1f} s after it was said "
              f"(median; 90%: {s['display_p90_s']:.1f} s). Acoustic checks: {s['checks']}, {s['flipped']} accepted.")
        if s["realigns"]:
            print(f"Re-aligned at {s['realigns']} pause(s) ({s['realign_ms_mean']:.0f} ms each), "
                  f"{s['realign_changed']} colour(s) changed.")
    else:
        print(f"Time: {s['total_s']:.1f} s, {s['utterances']} utterance(s), mean ASR latency "
              f"{s['latency_mean_ms']:.0f} ms (+ {silence:.1f} s pause before each update)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--file", help="text file to recite (UTF-8); without it, paste the text")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute", default="int8_float16")
    parser.add_argument("--prompt-mode", default=DEFAULT_PROMPT_MODE,
                        help=f"one of {', '.join(MODES)}, or auto:<mode> (full if the text fits under "
                             f"~200 tokens, else <mode>), used by modes A/D. Default {DEFAULT_PROMPT_MODE}")
    parser.add_argument("--mode", default=None, choices=CHECK_MODES,
                        help="A prompt, B no prompt, C no prompt + acoustic check (default), D prompt + double check")
    parser.add_argument("--no-live", action="store_true", help="update after each pause instead of while speaking")
    parser.add_argument("--tau", type=float, help="acoustic check threshold (modes C/D)")
    parser.add_argument("--silence", type=float, default=0.6, help="pause that ends an utterance (s)")
    parser.add_argument("--device", help="microphone: sounddevice input device id or name")
    parser.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    parser.add_argument("--wav", help="read this WAV instead of the microphone (testing)")
    parser.add_argument("--realtime", action="store_true", help="with --wav: pace the audio like a mic")
    parser.add_argument("--no-save", action="store_true", help="do not save this recitation in the history")
    parser.add_argument("--save", action="store_true", help="with --wav: save the replay in the history too")
    parser.add_argument("--no-audio", action="store_true", help="save the result but not the recording")
    parser.add_argument("--db", help="history database (default data/history.db)")
    args = parser.parse_args()
    just_fix_windows_console()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    text = read_text(args.file)
    if not text:
        sys.exit("No text given.")

    from contextlib import nullcontext

    from asr import MicSource, Segmenter, from_wav
    from asr.engine import Recognizer
    from asr.verify import TAU
    from core import AudioTap, Session
    from core.session import DEFAULT_MODE, LIVE_MODES

    mode = args.mode or DEFAULT_MODE
    live = not args.no_live
    if live and mode not in LIVE_MODES:
        print(f"Mode {mode} is not live (the live passes use no prompt); updating after each pause.")
        live = False

    print(f"Loading {args.model} ({args.compute})...", flush=True)
    rec = Recognizer(args.model, args.compute)  # warms up the GPU on load
    segmenter = Segmenter(min_silence=args.silence)
    from asr.live import SimClock

    # A WAV read faster than real time: time it as a real-time run would be (asr.live.SimClock).
    clock = SimClock() if args.wav and not args.realtime else None
    session = Session(text, rec, args.prompt_mode, mode=mode, live=live,
                      tau=TAU if args.tau is None else args.tau, clock=clock)
    prompt_info = f"mode {mode}" + (f", prompt {session.prompts.mode}" if mode in "AD" else "") +         (", live" if live else "")
    device = int(args.device) if args.device and args.device.isdigit() else args.device

    status = f"Listening ({prompt_info}). Recite the text; Ctrl+C to stop."
    blocks = AudioTap(())
    try:
        with nullcontext() if args.wav else MicSource(device=device) as mic:
            blocks = AudioTap(from_wav(args.wav, realtime=args.realtime) if args.wav else mic.blocks())
            session.started = time.perf_counter()
            render(session, status)
            for update in session.run(blocks, segmenter):
                status = (f"Listening ({prompt_info}). Last update: {update.latency * 1000:.0f} ms. "
                          "Ctrl+C to stop.")
                render(session, status)
    except KeyboardInterrupt:
        pass
    session.finish()
    render(session, "Finished." if session.finished else "Stopped.")
    print_summary(session.summary(), args.silence)
    if not args.no_save and (args.save or not args.wav):
        save_history(args, session, blocks, mode)


def save_history(args, session, tap, mode):
    from core import History

    try:
        history = History(args.db) if args.db else History()
        keep = not args.no_audio and not args.wav and history.keep_audio
        words, tail = session.display()
        summary = session.summary()
        rec_id = history.save(text=session.text, words=words, tail=tail, summary=summary,
                              started_at=time.time() - summary["total_s"],
                              heard=session.heard, audio=tap.audio(), keep_audio=keep,
                              source=f"replay:{os.path.basename(args.wav)}" if args.wav else "mic",
                              mode=mode, model=args.model, finished=session.finished)
        print(f"Saved in the history as #{rec_id}" + (" (with the recording)." if keep else "."))
    except Exception as e:
        print(f"Could not save the history: {e}")


if __name__ == "__main__":
    main()
