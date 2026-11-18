"""Audio sources. Every source yields the same block format:
a 1-D float32 numpy array of BLOCK_SIZE samples, mono, 16 kHz, values in [-1, 1].
"""

import queue
import time

import numpy as np

SAMPLE_RATE = 16000
BLOCK_SIZE = 512  # 32 ms; the block size Silero VAD expects at 16 kHz
BLOCK_SECONDS = BLOCK_SIZE / SAMPLE_RATE


def to_blocks(audio):
    """Split a 1-D float32 array into BLOCK_SIZE blocks; the last one is zero-padded."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    for i in range(0, len(audio), BLOCK_SIZE):
        block = audio[i : i + BLOCK_SIZE]
        if len(block) < BLOCK_SIZE:
            block = np.pad(block, (0, BLOCK_SIZE - len(block)))
        yield np.ascontiguousarray(block)


def load_wav(path):
    """Read a WAV file as mono float32 at 16 kHz (averages channels, resamples if needed)."""
    import soundfile as sf

    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(round(len(audio) * SAMPLE_RATE / rate))
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio
        ).astype(np.float32)
    return audio


def from_wav(path, realtime=False):
    """Yield blocks from a WAV file. realtime=True paces them like a live microphone."""
    start = time.perf_counter()
    for i, block in enumerate(to_blocks(load_wav(path))):
        if realtime:
            delay = start + (i + 1) * BLOCK_SECONDS - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        yield block


class MicSource:
    """Non-blocking microphone input. The sounddevice callback pushes blocks into a
    thread-safe queue; blocks() pulls them with a blocking get (no busy loop).

        with MicSource() as mic:
            for block in mic.blocks():
                ...
    """

    def __init__(self, device=None):
        self.device = device
        self.queue = queue.Queue()
        self.overflows = 0
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        if status and status.input_overflow:
            self.overflows += 1
        self.queue.put(np.ascontiguousarray(indata[:, 0], dtype=np.float32).copy())

    def start(self):
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        return self

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def blocks(self, timeout=1.0):
        """Yield blocks until the stream is stopped."""
        while self._stream is not None:
            try:
                yield self.queue.get(timeout=timeout)
            except queue.Empty:
                continue
