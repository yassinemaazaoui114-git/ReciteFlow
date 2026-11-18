"""Text normalization shared by the target paragraph and the spoken words."""

import re

from num2words import num2words

# Whole-word contractions that do not follow the generic suffix rules below.
_SPECIAL = {
    "won't": "will not",
    "can't": "can not",
    "cannot": "can not",
    "shan't": "shall not",
    "ain't": "is not",
    "let's": "let us",
}

# "'s" is only expanded after these words; elsewhere it is a possessive
# ("john's") and the apostrophe is simply dropped ("johns").
_IS_WORDS = {
    "it", "that", "what", "he", "she", "there", "here", "who", "where",
    "how", "when", "why", "this", "everyone", "everybody", "nobody", "someone",
}

_SUFFIXES = [
    ("n't", " not"),
    ("'re", " are"),
    ("'ve", " have"),
    ("'ll", " will"),
    ("'m", " am"),
    ("'d", " would"),
]

_NUMBER = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)(st|nd|rd|th)?(%?)")
_WORD = re.compile(r"[a-z']+")
_SPLIT_NUMBER = re.compile(r"(\d)\s+([.,]\d)")
_SPLIT_PERCENT = re.compile(r"(\d)\s+%")
_PER_CENT = re.compile(r"\bper cent\b")
_CURRENCY = {"$": "dollars", "£": "pounds", "€": "euros"}
_MONEY = re.compile(r"([$£€])\s?(\d+(?:,\d{3})*(?:\.\d+)?)")
_KEEP_SINGLE = {"a", "i"}


def _number_to_words(match: re.Match) -> str:
    digits, ordinal, percent = match.groups()
    plain = digits.replace(",", "")
    if "." in plain:
        words = num2words(float(plain))
    else:
        value = int(plain)
        if ordinal:
            words = num2words(value, to="ordinal")
        elif "," not in digits and len(plain) == 4 and 1100 <= value <= 2099:
            words = num2words(value, to="year")
        else:
            words = num2words(value)
    if percent:
        words += " percent"
    return f" {words} "


def _expand(word: str) -> str:
    if word in _SPECIAL:
        return _SPECIAL[word]
    if word.endswith("'s") and word[:-2] in _IS_WORDS:
        return word[:-2] + " is"
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix):
            return word[: -len(suffix)] + replacement
    return word.replace("'", "")


def _currency(match: re.Match) -> str:
    symbol, amount = match.groups()
    unit = _CURRENCY[symbol]
    return f"{amount} {unit if amount != '1' else unit[:-1]}"


def normalize(text: str) -> list[str]:
    """Lowercase, expand numbers, currency and contractions, strip punctuation, tokenize."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    # Whisper word lists joined with spaces split numbers: "$15 ,000", "20 %", "3 .5".
    text = _SPLIT_NUMBER.sub(r"\1\2", text)
    text = _SPLIT_PERCENT.sub(r"\1%", text)
    text = _PER_CENT.sub("percent", text)
    text = _MONEY.sub(_currency, text)
    text = _NUMBER.sub(_number_to_words, text)
    # Hyphens, underscores and all other punctuation become separators;
    # apostrophes survive until contraction expansion.
    tokens = []
    for word in _WORD.findall(text):
        word = word.strip("'")
        if not word:
            continue
        for token in _expand(word).split():
            if len(token) > 1 or token in _KEEP_SINGLE:
                tokens.append(token)
    return tokens
