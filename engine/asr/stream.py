"""Glue: audio blocks -> VAD utterances -> Whisper words.

Live smoke run:   python -m asr.stream [--model small.en] [--compute float16]
From a WAV file:  python -m asr.stream --wav path.wav [--realtime]
"""

import argparse
import itertools
import time

from .audio import BLOCK_SECONDS, MicSource, from_wav
from .vad import Segmenter


def stream(blocks, recognizer, segmenter=None):
    """Yield (words, start_time, end_time, latency_seconds) per utterance.

    start/end are seconds from stream start. latency is the time from the VAD closing the
    utterance to the words being ready (ASR time). The user-perceived delay after they stop
    speaking is roughly latency + the VAD pause (min_silence).
    """
    segmenter = segmenter or Segmenter()

    def utterances():
        for block in blocks:
            yield from segmenter.push(block)
        yield from segmenter.flush()

    for utt in utterances():
        t0 = time.perf_counter()
        words = recognizer.transcribe(utt.audio)
        yield words, utt.start, utt.end, time.perf_counter() - t0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--model", default="small.en")
    parser.add_argument("--compute", default="float16")
    parser.add_argument("--wav", help="read this WAV instead of the microphone")
    parser.add_argument("--realtime", action="store_true", help="pace WAV blocks like a mic")
    parser.add_argument("--prompt", help="initial_prompt text (e.g. the target paragraph)")
    parser.add_argument("--silence", type=float, default=0.6, help="pause that ends an utterance (s)")
    parser.add_argument("--device", help="sounddevice input device id or name")
    parser.add_argument("--seconds", type=float, help="stop listening after this many seconds of audio")
    args = parser.parse_args()

    from .engine import Recognizer

    print(f"Loading {args.model} ({args.compute})...", flush=True)
    rec = Recognizer(args.model, args.compute, initial_prompt=args.prompt)
    seg = Segmenter(min_silence=args.silence)

    def show(results):
        for words, start, end, latency in results:
            print(f"[{start:6.2f}-{end:6.2f}s  asr {latency * 1000:5.0f} ms] {' '.join(words)}", flush=True)

    if args.wav:
        show(stream(from_wav(args.wav, realtime=args.realtime), rec, seg))
        return
    print("Listening. Speak, pause, repeat. Ctrl+C to stop.", flush=True)
    try:
        device = int(args.device) if args.device and args.device.isdigit() else args.device
        with MicSource(device=device) as mic:
            blocks = mic.blocks()
            if args.seconds:
                blocks = itertools.islice(blocks, round(args.seconds / BLOCK_SECONDS))
            show(stream(blocks, rec, seg))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
