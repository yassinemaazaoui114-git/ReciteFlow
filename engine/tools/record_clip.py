"""Record a benchmark clip from the microphone.

    python tools/record_clip.py NAME                 # NAME.wav + NAME.txt in clips/
    python tools/record_clip.py NAME --text ref.txt  # take the reference text from a file

Press Enter to start, Enter to stop, then paste the exact text you recited.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from asr.audio import SAMPLE_RATE, MicSource  # noqa: E402


def read_reference():
    print("Paste or type the exact reference text, then an empty line to finish:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("name", help="clip name (no extension)")
    parser.add_argument("--text", help="file with the reference text (skip the paste prompt)")
    parser.add_argument("--dir", default=str(ROOT / "clips"))
    parser.add_argument("--device", default=None, help="sounddevice input device id or name")
    parser.add_argument("--force", action="store_true", help="overwrite an existing clip")
    args = parser.parse_args()

    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    wav, txt = out / f"{args.name}.wav", out / f"{args.name}.txt"
    if wav.exists() and not args.force:
        sys.exit(f"{wav} already exists (use --force to overwrite)")

    import sounddevice as sd

    device = int(args.device) if args.device and args.device.isdigit() else args.device
    print(f"Input device: {sd.query_devices(device, 'input')['name']}")
    input("Press Enter to START recording...")
    mic = MicSource(device=device).start()
    input("Recording. Press Enter to STOP...")
    mic.stop()
    blocks = []
    while not mic.queue.empty():
        blocks.append(mic.queue.get_nowait())
    audio = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
    if mic.overflows:
        print(f"Warning: {mic.overflows} input overflows (audio dropped)")
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    print(f"Recorded {len(audio) / SAMPLE_RATE:.1f} s, peak level {peak:.2f}")
    if peak < 0.02:
        print("Warning: very quiet; check the microphone.")
    sf.write(wav, audio, SAMPLE_RATE, subtype="PCM_16")

    text = Path(args.text).read_text(encoding="utf-8").strip() if args.text else read_reference()
    if not text:
        print(f"Warning: empty reference text. Fill in {txt} before benchmarking.")
    txt.write_text(text + "\n", encoding="utf-8")
    print(f"Saved {wav} and {txt}")


if __name__ == "__main__":
    main()
