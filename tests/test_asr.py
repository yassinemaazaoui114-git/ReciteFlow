"""ASR pipeline tests that need no GPU and no microphone.

VAD grouping is tested with synthetic audio (tones + silence) and an energy-based speech
detector in place of Silero, so the grouping rules are tested exactly and deterministically.
"""

import numpy as np
import pytest

from asr import BLOCK_SIZE, SAMPLE_RATE, MicSource, Segmenter, from_wav, to_blocks
from asr.audio import BLOCK_SECONDS

SR = SAMPLE_RATE


def tone(seconds, amp=0.3, freq=220.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SR), dtype=np.float32)


def energy_prob(block):
    """Stand-in for Silero: tone amp 0.3 -> 1.0, amp 0.1 -> ~0.7, silence -> 0."""
    return min(1.0, float(np.sqrt(np.mean(block**2))) / 0.1)


def segment(audio, **kw):
    seg = Segmenter(speech_prob=energy_prob, **kw)
    return list(seg.feed(to_blocks(audio)))


# --- VAD grouping --------------------------------------------------------

def test_counts_utterances_separated_by_pauses():
    audio = np.concatenate([silence(0.5), tone(1), silence(1), tone(1.5), silence(1), tone(0.8), silence(1)])
    utts = segment(audio)
    assert len(utts) == 3
    assert not any(u.forced_cut for u in utts)


def test_pause_threshold_respected():
    short_gap = np.concatenate([tone(1), silence(0.4), tone(1), silence(1)])
    long_gap = np.concatenate([tone(1), silence(0.7), tone(1), silence(1)])
    assert len(segment(short_gap)) == 1  # 0.4 s < default 0.6 s: same utterance
    assert len(segment(long_gap)) == 2
    assert len(segment(short_gap, min_silence=0.3)) == 2  # configurable


def test_padding_before_and_after_speech():
    audio = np.concatenate([silence(1), tone(1), silence(1)])
    (u,) = segment(audio)
    assert u.start == pytest.approx(1.0 - 0.2, abs=BLOCK_SECONDS)
    assert u.end == pytest.approx(2.0 + 0.2, abs=2 * BLOCK_SECONDS)
    assert len(u.audio) == pytest.approx((u.end - u.start) * SR, abs=1)


def test_utterance_at_end_of_stream_is_flushed():
    utts = segment(np.concatenate([silence(0.5), tone(1)]))
    assert len(utts) == 1


def test_short_click_is_dropped():
    audio = np.concatenate([silence(1), tone(0.05), silence(1), tone(1), silence(1)])
    assert len(segment(audio)) == 1


def test_cap_cuts_at_quietest_point():
    # 40 s of continuous "speech" with a softer stretch around 20 s (still above threshold).
    audio = np.concatenate([tone(20), tone(0.3, amp=0.1), tone(19.7), silence(1)])
    utts = segment(audio)
    assert len(utts) == 2
    first, second = utts
    assert first.forced_cut and not second.forced_cut
    assert first.end - first.start <= 25.0
    assert 20.0 <= first.end <= 20.4  # cut inside the soft stretch
    assert second.start == pytest.approx(first.end)  # nothing lost or duplicated


def test_cap_on_long_uniform_speech():
    utts = segment(np.concatenate([tone(70), silence(1)]))
    assert all(u.end - u.start <= 25.0 + 1e-6 for u in utts)
    assert len(utts) >= 3
    for a, b in zip(utts, utts[1:]):
        assert b.start == pytest.approx(a.end)
    assert sum(len(u.audio) for u in utts) >= 70 * SR


def test_silence_only_gives_nothing():
    assert segment(silence(5)) == []


def test_silero_on_silence_and_tone():
    pytest.importorskip("silero_vad")
    from asr import SileroProb

    seg = Segmenter(speech_prob=SileroProb())
    audio = np.concatenate([silence(2), tone(1, freq=1000), silence(2)])
    assert list(seg.feed(to_blocks(audio))) == []  # a pure tone is not speech


# --- block format --------------------------------------------------------

def assert_block(block):
    assert isinstance(block, np.ndarray)
    assert block.dtype == np.float32
    assert block.shape == (BLOCK_SIZE,)
    assert block.flags["C_CONTIGUOUS"]


def test_from_wav_and_mic_blocks_have_same_format(tmp_path):
    sf = pytest.importorskip("soundfile")
    path = tmp_path / "clip.wav"
    audio = tone(1.01)  # not a multiple of BLOCK_SIZE: last block is padded
    sf.write(path, audio, SR, subtype="PCM_16")
    wav_blocks = list(from_wav(path))
    assert len(wav_blocks) == -(-len(audio) // BLOCK_SIZE)
    for b in wav_blocks:
        assert_block(b)

    # Mic path without hardware: call the sounddevice callback with what PortAudio passes.
    mic = MicSource()
    indata = audio[:BLOCK_SIZE].reshape(-1, 1).copy()
    mic._callback(indata, BLOCK_SIZE, None, None)
    mic_block = mic.queue.get_nowait()
    assert_block(mic_block)
    np.testing.assert_allclose(mic_block, wav_blocks[0], atol=1e-4)  # PCM_16 rounding
    indata[:] = 0  # the callback must copy, PortAudio reuses its buffer
    assert np.any(mic_block != 0)


def test_from_wav_resamples_and_downmixes(tmp_path):
    sf = pytest.importorskip("soundfile")
    path = tmp_path / "stereo44k.wav"
    t = np.arange(44100) / 44100
    stereo = np.stack([np.sin(2 * np.pi * 220 * t)] * 2, axis=1).astype(np.float32) * 0.3
    sf.write(path, stereo, 44100)
    blocks = list(from_wav(path))
    for b in blocks:
        assert_block(b)
    assert len(blocks) * BLOCK_SIZE == pytest.approx(SR, abs=BLOCK_SIZE)
