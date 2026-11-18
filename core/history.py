"""Recitation history: every recitation, its result per word, the user's corrections and
(optionally) the audio, stored locally. UI-free; the GUI and the terminal app both use it.

    history = History()                                  # data/history.db
    rec_id = history.save(text=..., words=words, tail=tail, summary=session.summary(),
                          heard=session.heard, audio=tap.audio(), keep_audio=True)
    history.correct(rec_id, pos, "correct")              # user: "this word was actually right"

Storage: one SQLite file (Python's built-in sqlite3, no server) + WAV files in data/recordings/.
Nothing leaves the PC. `keep_audio` is the user's consent to keep the recording for training; the
text and the per-word results are always kept (they are small and contain no voice).

Tokens are the words of the coloured view in reading order (extras before the word they were said
before, then the target word; extras after the last word at the end), the same order as
gui.tokens. pos = index in that list. A user correction (user_state) is the label for training:
    target word:  correct | misspelled | wrong | missed
    extra word:   extra (really said) | not_said (the app invented it)
"""

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "history.db"
SCHEMA_VERSION = 2  # 2: tokens.recovered
WORD_LABELS = ("correct", "misspelled", "wrong", "missed")
EXTRA_LABELS = ("extra", "not_said")
COUNTED = ("correct", "misspelled", "wrong", "missed", "pending")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recitations (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,          -- local time, ISO format
    duration_s REAL,
    source TEXT NOT NULL,              -- 'mic' or 'replay:<wav name>'
    text TEXT NOT NULL,                -- the target text
    heard TEXT,                        -- JSON list of ASR word lists, one per utterance (final passes)
    mode TEXT, model TEXT,
    finished INTEGER,                  -- 1 if the whole text was recited
    words INTEGER, correct INTEGER, misspelled INTEGER, wrong INTEGER, missed INTEGER,
    extra INTEGER, pending INTEGER,    -- the app's counts (normalized words)
    audio_path TEXT,                   -- relative to data/, NULL if not kept
    keep_for_training INTEGER NOT NULL,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
    recitation_id INTEGER NOT NULL REFERENCES recitations(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL,
    kind TEXT NOT NULL,                -- 'word' or 'extra'
    word_index INTEGER,                -- target word index (word), or the word it came before (extra; -1 = end)
    text TEXT NOT NULL,
    app_state TEXT NOT NULL,
    recovered INTEGER NOT NULL DEFAULT 0,  -- said wrong, then re-recited right (R14)
    user_state TEXT,                   -- NULL = not reviewed
    corrected_at TEXT,
    PRIMARY KEY (recitation_id, pos)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass
class Token:
    pos: int
    kind: str  # "word" | "extra"
    word_index: int
    text: str
    app_state: str
    user_state: str | None = None
    recovered: bool = False

    @property
    def state(self) -> str:
        return self.user_state or self.app_state


def token_list(words, tail) -> list[Token]:
    """Session.display() output -> tokens in reading order (same order as gui.tokens)."""
    out = []
    for i, w in enumerate(words):
        out += [Token(len(out) + j, "extra", i, x, "extra") for j, x in enumerate(w.extras_before)]
        out.append(Token(len(out), "word", i, w.text, w.state, recovered=getattr(w, "recovered", False)))
    out += [Token(len(out) + j, "extra", -1, x, "extra") for j, x in enumerate(tail)]
    return out


def labels_for(kind: str) -> tuple[str, ...]:
    return WORD_LABELS if kind == "word" else EXTRA_LABELS


def corrected_counts(summary: dict, tokens: list[Token]) -> dict:
    """The app's counts with the user's corrections applied (one raw word = one count)."""
    out = {k: summary.get(k, 0) for k in COUNTED + ("extra",)}
    for t in tokens:
        if t.user_state is None or t.user_state == t.app_state:
            continue
        old = "extra" if t.kind == "extra" else t.app_state
        new = t.user_state if t.user_state != "not_said" else None
        if old in out:
            out[old] = max(0, out[old] - 1)
        if new in out:
            out[new] += 1
    return out


class AudioTap:
    """Pass audio blocks through and keep a copy (to save the recording at the end)."""

    def __init__(self, blocks):
        self._blocks = blocks
        self.parts: list[np.ndarray] = []

    def __iter__(self):
        for block in self._blocks:
            self.parts.append(block)
            yield block

    def audio(self) -> np.ndarray:
        return np.concatenate(self.parts) if self.parts else np.zeros(0, dtype=np.float32)


class History:
    def __init__(self, path: str | Path = DEFAULT_DB):
        self.path = Path(path)
        self.data_dir = self.path.parent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)
            cols = [r["name"] for r in db.execute("PRAGMA table_info(tokens)")]
            if "recovered" not in cols:  # version 1 database
                db.execute("ALTER TABLE tokens ADD COLUMN recovered INTEGER NOT NULL DEFAULT 0")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _db(self):
        """A new connection per call: the GUI saves from the engine thread and corrects from the
        window thread. Used as `with self._db() as db:` (commits on success)."""
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        db.row_factory = sqlite3.Row
        return _Closing(db)

    # --- settings ---------------------------------------------------------------

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._db() as db:
            db.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    @property
    def keep_audio(self) -> bool:
        """The user's choice: keep recordings for training (default yes; local only)."""
        return self.setting("keep_audio", "1") == "1"

    @keep_audio.setter
    def keep_audio(self, value: bool):
        self.set_setting("keep_audio", "1" if value else "0")

    # --- recitations ------------------------------------------------------------

    def save(self, *, text: str, words, tail, summary: dict, heard=None, audio=None, keep_audio: bool = True,
             source: str = "mic", mode: str = "", model: str = "", finished: bool = False,
             started_at: float | None = None, sample_rate: int = 16000) -> int:
        """Store one recitation; returns its id. The audio (1-D float32) is written as a WAV only
        when keep_audio is True."""
        started = time.localtime(started_at if started_at is not None else time.time())
        tokens = token_list(words, tail)
        slim = {k: v for k, v in summary.items() if isinstance(v, (int, float, str, bool, list))}
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO recitations(started_at, duration_s, source, text, heard, mode, model, finished, words, "
                "correct, misspelled, wrong, missed, extra, pending, audio_path, keep_for_training, summary_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S", started), summary.get("total_s"), source, text,
                 json.dumps(heard or []), mode, model, int(finished), summary.get("words", 0),
                 *(summary.get(k, 0) for k in ("correct", "misspelled", "wrong", "missed", "extra", "pending")),
                 None, int(keep_audio), json.dumps(slim)))
            rec_id = cur.lastrowid
            db.executemany(
                "INSERT INTO tokens(recitation_id, pos, kind, word_index, text, app_state, recovered) "
                "VALUES(?,?,?,?,?,?,?)",
                [(rec_id, t.pos, t.kind, t.word_index, t.text, t.app_state, int(t.recovered)) for t in tokens])
        if keep_audio and audio is not None and len(audio):
            import soundfile as sf

            rel = Path("recordings") / time.strftime(f"%Y%m%d_%H%M%S_{rec_id}.wav", started)
            (self.data_dir / rel.parent).mkdir(parents=True, exist_ok=True)
            sf.write(str(self.data_dir / rel), np.asarray(audio, dtype=np.float32), sample_rate, subtype="PCM_16")
            with self._db() as db:
                db.execute("UPDATE recitations SET audio_path = ? WHERE id = ?", (rel.as_posix(), rec_id))
        return rec_id

    def correct(self, rec_id: int, pos: int, user_state: str | None) -> Token:
        """Set (or with None, clear) the user's label for one token. Returns the token."""
        with self._db() as db:
            row = db.execute("SELECT * FROM tokens WHERE recitation_id = ? AND pos = ?", (rec_id, pos)).fetchone()
            if row is None:
                raise KeyError(f"recitation {rec_id} has no token {pos}")
            if user_state is not None and user_state not in labels_for(row["kind"]):
                raise ValueError(f"{user_state!r} is not a label for a {row['kind']} token")
            if user_state == row["app_state"]:
                user_state = None  # agreeing with the app = no correction
            db.execute("UPDATE tokens SET user_state = ?, corrected_at = ? WHERE recitation_id = ? AND pos = ?",
                       (user_state, time.strftime("%Y-%m-%dT%H:%M:%S") if user_state else None, rec_id, pos))
        return Token(pos, row["kind"], row["word_index"], row["text"], row["app_state"], user_state,
                     bool(row["recovered"]))

    def tokens(self, rec_id: int) -> list[Token]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM tokens WHERE recitation_id = ? ORDER BY pos", (rec_id,)).fetchall()
        return [Token(r["pos"], r["kind"], r["word_index"], r["text"], r["app_state"], r["user_state"],
                      bool(r["recovered"])) for r in rows]

    def recitation(self, rec_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM recitations WHERE id = ?", (rec_id,)).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 20) -> list[dict]:
        """Newest first, with the number of corrected tokens."""
        with self._db() as db:
            rows = db.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM tokens t WHERE t.recitation_id = r.id AND t.user_state IS NOT NULL)"
                " AS corrections FROM recitations r ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


class _Closing:
    """sqlite3 connection as a context manager that also closes it (sqlite3's own only commits)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, *exc):
        try:
            if exc_type is None:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
