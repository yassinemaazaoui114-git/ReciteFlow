"""The recitation logic, with no UI code: the engine worker, saving to the history,
the view helpers, ready-to-draw frames and corrections. The server (`server.py`) and tests use it; the
Electron window only draws what it sends.

Events go through `post(event, data)` (same shape as `server.emit`):
  "frame"  {tokens, counts, beep, at}        on every engine frame (live passes ~0.6 s, pause re-check overrides); a word token
                                             has heard=True while heard by a pass but not coloured yet;
                                             at = time.perf_counter() when sent (to measure delivery)
  "done"   {finished, rec_id, note, tokens, counts, result, stats}   after the last frame; stats = the session's
           display_lags and realign_lags (s), for latency measurements
  "error"  {message}

History: `data\\history.db` (+ `data\\recordings\\`) in the project folder,
opened with `open_history()`. Tests and replays pass their own `History` (or none) and never touch it.
"""

import os
import threading
import time
from contextlib import nullcontext
from pathlib import Path

from core.history import History, corrected_counts, labels_for, token_list

# The history lives in this project's data folder (off git)
HISTORY_DB = Path(__file__).resolve().parents[1] / "data" / "history.db"
MISTAKES = ("wrong", "missed", "misspelled", "extra")
HIDDEN_MARK = "…"  # shown after the last said word while the rest of the text is hidden


# --- view helpers -----------------------------------------------------------------------------------------

def line_breaks(text, words):
    """Indexes of the words that end a line of `text`, so the coloured view keeps the paragraphs.
    Empty when the lines do not add up to `words` (then the view is one flowing paragraph)."""
    breaks, n = set(), 0
    for line in text.splitlines():
        k = len(line.split())
        if k:
            n += k
            breaks.add(n - 1)
    return breaks if n == len(words) else set()


def shown(tok):
    """How a core.history.Token is written in the view: [missed], {extra}."""
    if tok.kind == "extra":
        return "{" + tok.text + "}"
    return f"[{tok.text}]" if tok.state == "missed" else tok.text


def separator(tok, breaks):
    return "\n" if tok.kind == "word" and tok.word_index in breaks else " "


def tokens(words, tail, breaks=()):
    """(shown text, state, separator after it) per token, in reading order."""
    return [(shown(t), t.state, separator(t, breaks)) for t in token_list(words, tail)]


def visible(toks, hide):
    """The tokens to draw. hide=True (reciting from memory): words not yet said are left out, so
    the text appears word by word as it is said; extras and said words are always shown."""
    return [t for t in toks if not (hide and t.kind == "word" and t.state == "pending")]


def new_mistakes(toks, seen):
    """Keys of mistakes in `toks` not in `seen` (and adds them to `seen`). A mistake that flashes
    live and is cleared at the pause keeps its key, so it never beeps twice."""
    keys = {(t.kind, t.word_index, t.text) for t in toks if t.state in MISTAKES}
    new = keys - seen
    seen |= new
    return new


def input_devices():
    """(id, name) of the microphones of the default Windows audio API."""
    try:
        import sounddevice as sd

        api = sd.default.hostapi
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and d["hostapi"] == api]
    except Exception:
        return []


# --- ready-to-draw data ------------------------------------------------------------------------------------

def heard_words(session):
    """Indexes of the target words the last live pass heard that are not coloured yet: the words held back by
    the matcher plus the pass's words that wait for the next pass to agree, laid on the next target words.
    They are shown plain (not checked) so a word appears as soon as it is heard; the colours do not change."""
    live = getattr(session, "_live", None)
    hyp = live._prev if live is not None else None
    if not hyp:
        return set()
    from matcher import normalize

    m = session.matcher
    lo = m.pointer
    hi = min(len(m.target), lo + len(m.held()) + sum(len(normalize(w.word)) for w in hyp))
    return {k for k, (a, b) in enumerate(session._spans) if (a < hi and b > lo) or (a == b and lo <= a < hi)}


def draw_tokens(toks, breaks, heard=()):
    """The tokens as plain dicts for the window. `underline` = recovered and not corrected by the user; `corrected` = the user labelled it (tinted); `labels` = the correction menu's choices; `heard` = not yet said for the matcher but heard by the last pass (shown plain)."""
    return [{"pos": t.pos, "kind": t.kind, "text": shown(t), "state": t.state, "sep": separator(t, breaks),
             "app_state": t.app_state, "corrected": t.user_state is not None,
             "underline": bool(t.recovered and not t.user_state), "labels": list(labels_for(t.kind)),
             "heard": t.kind == "word" and t.state == "pending" and t.word_index in heard}
            for t in toks]


def result(summary, toks):
    """The numbers of the done line, with the user's corrections applied:
    "{correct} of {total} words correct ({said} said). Extra words: {extras}. {fixed} fixed by re-reciting"."""
    counts = corrected_counts(summary, toks)
    total = summary.get("words", 0)
    return {"correct": counts["correct"], "total": total,
            "said": total - counts["pending"] if counts["pending"] else None,
            "extras": [t.text for t in toks if t.kind == "extra" and t.state == "extra"],
            "fixed": sum(1 for t in toks if t.recovered), "corrections": sum(1 for t in toks if t.user_state)}


def open_history(path=HISTORY_DB):
    """The history, or None when it cannot be opened (the window then says "History is off")."""
    try:
        return History(path)
    except Exception:
        return None


# --- the engine --------------------------------------------------------------------------------------------

class Engine:
    """Runs one core.Session per recitation in a worker thread, with a loaded recognizer (asr.engine.Recognizer,
    loaded by the server, or by `load()`). After a saved recitation, `correct()` labels its tokens."""

    def __init__(self, post, rec=None, model="large-v3-turbo", compute="int8_float16", history=None,
                 save_replay=False):
        self.post = post
        self.rec, self.model, self.compute = rec, model, compute
        self.history, self.save_replay = history, save_replay
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running = False  # cleared just before "done" / "error" is posted, so a new start is allowed then
        self.text, self.breaks, self.toks, self.summary = "", set(), [], {}
        self.rec_id = None  # history id of the finished recitation (corrections go there)
        self._beeped = set()  # mistake keys already announced (new_mistakes)
        self._heard_of = None  # the live pass words the last frame showed as heard
        self.progress_at = time.perf_counter()  # last sign of life of the running recitation (an audio block, a step)

    def load(self):
        """Load the recognizer here (the server loads it itself). Raises on failure."""
        from asr.engine import Recognizer

        self.rec = Recognizer(self.model, self.compute)  # warms up the GPU on load

    @property
    def busy(self):
        return self._running

    def stalled(self, limit):
        """True when a recitation runs but has not moved for `limit` seconds: audio blocks arrive every few tens of
        ms and a pass takes well under a second, so this means the audio or the speech model stopped answering."""
        return self._running and time.perf_counter() - self.progress_at > limit

    def start(self, text, device=None, wav=None, keep_audio=True):
        """Start a recitation of `text` from the mic `device` (None = default), or a WAV replayed in real time.
        Only one at a time: raises RuntimeError while one runs."""
        with self._lock:
            if self._running:
                raise RuntimeError("A recitation is already running.")
            self._running = True
        self._stop.clear()
        self.progress_at = time.perf_counter()
        self.text, self.breaks, self.toks, self.summary, self.rec_id = text, set(), [], {}, None
        self._beeped = set()
        threading.Thread(target=self._run, args=(text, device, wav, keep_audio), daemon=True).start()

    def stop(self):
        self._stop.set()

    def _end(self, event, data):
        self._running = False
        self.post(event, data)

    def _until_stop(self, blocks, session=None):
        for block in blocks:
            self.progress_at = time.perf_counter()
            if self._stop.is_set():
                return
            yield block
            # The session asks for the next block right after handling this one: a live pass that heard new
            # words without committing any gets its frame here, before waiting for more audio
            live = getattr(session, "_live", None)
            if live is not None and live._prev is not self._heard_of:
                self._frame(session)

    def _frame(self, session, first=False, live=True):
        words, tail = session.display()
        if first:
            self.breaks = line_breaks(self.text, words)
        self.toks, self.summary = token_list(words, tail), session.summary()
        passes = getattr(session, "_live", None)
        self._heard_of = passes._prev if passes is not None else None
        heard = heard_words(session) if live and not session.finished else set()
        beep = bool(new_mistakes(self.toks, self._beeped))
        self.post("frame", {"tokens": draw_tokens(self.toks, self.breaks, heard), "counts": self.summary,
                            "beep": beep, "at": time.perf_counter()})

    def _run(self, text, device, wav, keep_audio):
        try:
            from asr import MicSource, Segmenter, from_wav
            from core import AudioTap, Session
            from core.session import DEFAULT_MODE

            session = Session(text, self.rec, mode=DEFAULT_MODE, live=True)
            segmenter = Segmenter()
            with nullcontext() if wav else MicSource(device=device) as mic:
                blocks = AudioTap(from_wav(wav, realtime=True) if wav else mic.blocks())
                session.started = time.perf_counter()
                self._frame(session, first=True)
                for _ in session.run(self._until_stop(blocks, session), segmenter):
                    self._frame(session)
            self.progress_at = time.perf_counter()
            session.finish()
            self.progress_at = time.perf_counter()
            self._frame(session, live=False)
            rec_id, note = self._save(session, blocks, wav, keep_audio and not wav, DEFAULT_MODE)
            self.rec_id = rec_id
            self._end("done", {"finished": session.finished, "rec_id": rec_id, "note": note,
                               "tokens": draw_tokens(self.toks, self.breaks),
                               "counts": corrected_counts(self.summary, self.toks),
                               "result": result(self.summary, self.toks),
                               "stats": {"display_lags": session.display_lags, "realign_lags": session.realign_lags}})
        except Exception as e:
            self._end("error", {"message": f"{type(e).__name__}: {e}"})

    def _save(self, session, tap, wav, keep_audio, mode):
        """Save the recitation in the history. Returns (id or None, note for the status line)."""
        if self.history is None or (wav and not self.save_replay):
            return None, ""
        try:
            words, tail = session.display()
            summary = session.summary()
            rec_id = self.history.save(text=session.text, words=words, tail=tail, summary=summary,
                                       started_at=time.time() - summary["total_s"], heard=session.heard,
                                       audio=tap.audio(), keep_audio=keep_audio,
                                       source=f"replay:{os.path.basename(wav)}" if wav else "mic", mode=mode,
                                       model=self.model, finished=session.finished)
            return rec_id, f"saved #{rec_id}" + (" with the recording" if keep_audio else "")
        except Exception as e:
            return None, f"not saved ({e})"

    # --- corrections (after a saved recitation) ----------------------------------------------------------

    def correct(self, pos, state):
        """Label token `pos` of the finished recitation with `state` (None = undo). Saved at once
        (History.correct). Returns the redrawn {tokens, counts, result}; raises when it cannot be saved."""
        if self.busy or self.rec_id is None or self.history is None:
            raise RuntimeError("There is no saved recitation to correct.")
        if not 0 <= pos < len(self.toks):
            raise ValueError(f"no word at position {pos}")
        self.toks[pos] = self.history.correct(self.rec_id, pos, state)
        return {"tokens": draw_tokens(self.toks, self.breaks), "counts": corrected_counts(self.summary, self.toks),
                "result": result(self.summary, self.toks)}
