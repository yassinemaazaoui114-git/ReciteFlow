"""faster-whisper wrapper: one utterance of audio in, raw words out."""

import numpy as np

from ._cuda import add_nvidia_dll_dirs
from .words import Word

add_nvidia_dll_dirs()

import ctranslate2  # noqa: E402
import torch  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402
from faster_whisper.audio import pad_or_trim  # noqa: E402
from faster_whisper.tokenizer import Tokenizer  # noqa: E402


class Recognizer:
    """Whisper recognizer tuned for short, already-segmented utterances.

    Drops a whole Whisper segment when its no_speech_prob > no_speech_max or its mean word
    probability < min_avg_word_prob (typical of hallucinations on noise). No word blacklist.
    `initial_prompt` can be changed between calls (e.g. to the current target text).
    """

    def __init__(
        self,
        model_name="small.en",
        compute_type="float16",
        initial_prompt=None,
        device="cuda",
        no_speech_max=0.6,
        min_avg_word_prob=0.3,
    ):
        self.model_name = model_name
        self.compute_type = compute_type
        self.initial_prompt = initial_prompt
        self.no_speech_max = no_speech_max
        self.min_avg_word_prob = min_avg_word_prob
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.dropped = []  # (text, no_speech_prob, avg_word_prob) of dropped segments
        self._tok = None
        # Keep the encoder output of the last transcribed utterance for sequence_logprobs.
        self._encode, self._captured, self._last_encoded = self.model.encode, [], None
        self.model.encode = self._capture_encode
        # The first GPU call is slow (CUDA/cuDNN init); pay it here, not on the first utterance.
        self.transcribe(np.zeros(16000, dtype=np.float32))
        self.dropped.clear()

    def count_tokens(self, text: str) -> int:
        """Whisper tokens in `text` as a prompt (faster-whisper prepends a space)."""
        return len(self.model.hf_tokenizer.encode(" " + text.strip(), add_special_tokens=False).ids)

    def transcribe(self, audio) -> list[str]:
        return [w.word for w in self.transcribe_words(audio)]

    def transcribe_words(self, audio, initial_prompt=False) -> list[Word]:
        """Words with start/end seconds (relative to `audio`) and probability.
        initial_prompt=False uses self.initial_prompt; None or a string overrides it."""
        audio = np.asarray(audio, dtype=np.float32)
        prompt = self.initial_prompt if initial_prompt is False else initial_prompt
        self._captured.clear()
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=5,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,
            word_timestamps=True,
            initial_prompt=prompt or None,
            # Filtering is done below with our own rule, not faster-whisper's combined one.
            no_speech_threshold=None,
        )
        words = []
        for seg in segments:
            seg_words = seg.words or []
            if not seg_words:
                continue
            avg_prob = sum(w.probability for w in seg_words) / len(seg_words)
            if seg.no_speech_prob > self.no_speech_max or avg_prob < self.min_avg_word_prob:
                self.dropped.append((seg.text.strip(), seg.no_speech_prob, avg_prob))
                continue
            words.extend(Word(w.word.strip(), float(w.start), float(w.end), float(w.probability))
                         for w in seg_words if w.word.strip())
        # One encoder pass means the audio fit in one 30 s window: reusable for scoring.
        self._last_encoded = (audio, self._captured[0]) if len(self._captured) == 1 else None
        self._captured.clear()
        return words

    def sequence_logprobs(self, audio, texts: list[str]) -> list[float]:
        """Teacher-forced log P(text | audio) for each text, no prompt: sum of the token
        log-probabilities (end-of-text token included) from ctranslate2's Whisper.align, which
        scores given tokens on the audio in one decoder pass. Used by asr.verify.
        If `audio` is the array just given to transcribe_words, its encoder output is reused."""
        tok = self._tokenizer()
        tokens = [tok.encode(" " + t.strip()) for t in texts]
        live = [k for k, t in enumerate(tokens) if t]
        out = [0.0] * len(texts)
        if not live:
            return out
        enc, num_frames = self._encoded(audio)
        # align() wants one encoder output per sequence: repeat the single audio along the batch.
        batch = torch.as_tensor(enc, device="cuda") if enc.device == "cuda" else torch.as_tensor(np.asarray(enc))
        batch = ctranslate2.StorageView.from_array(batch.expand(len(live), -1, -1).contiguous())
        results = self.model.model.align(batch, tok.sot_sequence, [tokens[k] for k in live], [num_frames] * len(live))
        for k, r in zip(live, results):
            probs = np.clip(np.asarray(r.text_token_probs, dtype=np.float64), 1e-12, 1.0)
            out[k] = float(np.log(probs).sum())
        return out

    def _encoded(self, audio):
        """(encoder output, content frames) for an utterance under 30 s."""
        fe = self.model.feature_extractor
        audio = np.asarray(audio, dtype=np.float32)
        num_frames = min(len(audio) // fe.hop_length, fe.nb_max_frames)
        last = self._last_encoded
        if last is not None and last[0] is audio:
            return last[1], num_frames
        features = fe(audio)
        return self.model.encode(pad_or_trim(features[..., : fe.nb_max_frames])), num_frames

    def _capture_encode(self, features):
        out = self._encode(features)
        self._captured.append(out)
        return out

    def _tokenizer(self):
        if self._tok is None:
            self._tok = Tokenizer(self.model.hf_tokenizer, self.model.model.is_multilingual, task="transcribe",
                                  language="en")
        return self._tok
