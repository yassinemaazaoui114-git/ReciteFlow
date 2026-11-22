"""Whisper initial_prompt built from the target text and the matcher pointer.

Whisper keeps only the last ~223 prompt tokens, so a long `full` prompt loses the START of the
text (the part recited first). Modes:
    none    no prompt
    full    the whole target text
    back    the last BACK_WORDS target words before the pointer (already recited)
    around  from AROUND_BEFORE words before the pointer to AROUND_AFTER words after it,
            trimmed from the end to stay under max_tokens
    auto:M  `full` if the whole text fits under max_tokens, else mode M (`back` or `around`)

Not tied to the GPU: pass `count_tokens` (Recognizer.count_tokens) for exact token counts;
without it tokens are estimated from the word count.
"""

from matcher import normalize

MODES = ["none", "full", "back", "around"]
MAX_TOKENS = 200
BACK_WORDS = 40
AROUND_BEFORE = 30
AROUND_AFTER = 100


def estimate_tokens(text: str) -> int:
    """Rough Whisper token count for English (about 1.4 tokens per word, with punctuation)."""
    return round(len(text.split()) * 1.4) + 1


class PromptBuilder:
    """prompt(pointer) -> initial_prompt for the next utterance (pointer = Matcher.pointer,
    an index into the normalized target words)."""

    def __init__(self, text: str, mode: str = "auto:around", max_tokens: int = MAX_TOKENS, count_tokens=None):
        self.count = count_tokens or estimate_tokens
        self.max_tokens = max_tokens
        self.text = " ".join(text.split())
        self.words = self.text.split()  # raw words, punctuation and case kept
        # norm_end[k] = number of normalized target words up to and including raw word k.
        self._norm_end, total = [], 0
        for w in self.words:
            total += len(normalize(w))
            self._norm_end.append(total)
        self.requested = mode
        if mode.startswith("auto:"):
            long_mode = mode.split(":", 1)[1]
            self.mode = "full" if self.count(self.text) <= max_tokens else long_mode
        else:
            self.mode = mode
        if self.mode not in MODES:
            raise ValueError(f"unknown prompt mode {mode!r}; use one of {MODES} or auto:<mode>")

    def _raw_index(self, pointer: int) -> int:
        """Raw word index of the first raw word not fully recited."""
        for k, end in enumerate(self._norm_end):
            if end > pointer:
                return k
        return len(self.words)

    def prompt(self, pointer: int = 0) -> str | None:
        if self.mode == "none":
            return None
        if self.mode == "full":
            return self.text or None
        k = self._raw_index(pointer)
        if self.mode == "back":
            words = self.words[max(0, k - BACK_WORDS) : k]
        else:
            words = self.words[max(0, k - AROUND_BEFORE) : k + AROUND_AFTER]
            while len(words) > 1 and self.count(" ".join(words)) > self.max_tokens:
                words = words[: max(1, len(words) - 5)]
        return " ".join(words) or None
