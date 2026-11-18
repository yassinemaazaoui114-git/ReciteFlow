"""ReciteFlow (window): paste a text, press Start, recite it from memory; the words are coloured as
you say them. Same engine and defaults as the terminal app (mode C + live + re-align at pauses).

    python gui.py
    python gui.py --file text.txt                    # start with this text in the box
    python gui.py --file t.txt --wav clip.wav        # replay a recording instead of the mic (testing)

Colors (R15): correct green, wrong red, [missed] red, misspelled yellow, not yet said grey,
{extra} words red at the place they were said. A mistake you go back and re-recite correctly
stays red and is underlined (R14).

Reciting from memory (both on by default, checkboxes above the buttons): "Hide the text while I
recite" leaves out the words not yet said, so the text appears word by word as you say it (all of
it again after Stop); "Beep on a mistake" plays a short tone when a new mistake appears live, so
you can recite with your eyes closed. A live false alarm can beep before the pause re-check clears it.

History (core.history): every microphone recitation is saved in data/history.db, and its recording
in data/recordings/ while "Keep my recordings for training" is ticked. After a recitation, click a
word the app got wrong to correct it; the correction is saved as a label for training later.
Replays (--wav) are only saved with --save.

The engine runs in a worker thread (`Engine`) and posts messages to a queue; the window (`App`)
reads the queue on a timer, so the window never waits for the GPU.
"""

import argparse
import os
import queue
import threading
import time
from contextlib import nullcontext

import tkinter as tk

import customtkinter as ctk

from core.history import History, corrected_counts, labels_for, token_list

BG, CARD, BORDER = "#0B1220", "#111A2E", "#22304A"
TEXT, MUTED = "#E2E8F0", "#8B98AD"
PRIMARY, PRIMARY_HOVER = "#14B8A6", "#0D9488"
DANGER, DANGER_HOVER = "#EF4444", "#DC2626"
GREEN, RED, YELLOW, GREY = "#4ADE80", "#F87171", "#FACC15", "#7C8AA0"
WORD_COLORS = {"correct": GREEN, "wrong": RED, "missed": RED, "misspelled": YELLOW, "pending": GREY, "extra": RED,
               "not_said": GREY}
FIXED_BG = "#26344F"  # background of a word the user corrected
MENU = {  # correction menu labels per state
    "correct": "Correct (said right)", "misspelled": "Misspelled (close, not exact)",
    "wrong": "Wrong (said another word)", "missed": "Missed (not said)",
    "extra": "Really said (extra word)", "not_said": "Not said (the app invented it)",
}
# The counters under the text are also the legend: they show each colour and notation.
CHIPS = [("correct", "Correct"), ("misspelled", "Misspelled"), ("wrong", "Wrong"), ("missed", "[Missed]"),
         ("extra", "{Extra}"), ("pending", "Not yet said")]
FONT = "Segoe UI"
DEFAULT_MIC = "Default microphone"
POLL_MS = 50


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


MISTAKES = ("wrong", "missed", "misspelled", "extra")
HIDDEN_MARK = "…"  # shown after the last said word while the rest of the text is hidden


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


def beep():
    """Short tone in a background thread (winsound.Beep blocks while it plays)."""
    try:
        import winsound

        threading.Thread(target=winsound.Beep, args=(880, 160), daemon=True).start()
    except Exception:
        pass


def input_devices():
    """(id, name) of the microphones of the default Windows audio API."""
    try:
        import sounddevice as sd

        api = sd.default.hostapi
        return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                if d["max_input_channels"] > 0 and d["hostapi"] == api]
    except Exception:
        return []


class Engine:
    """Loads the recognizer once and runs one Session per recitation, each in a worker thread.
    Messages for the window go through `post`:
    ("ready",), ("frame", words, tail, summary), ("done", finished, recitation id or None, note),
    ("error", text)."""

    def __init__(self, post, model="large-v3-turbo", compute="int8_float16", history=None, save_replay=False):
        self.post = post
        self.model, self.compute = model, compute
        self.history, self.save_replay = history, save_replay
        self.rec = None
        self._stop = threading.Event()

    def load(self):
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        try:
            from asr.engine import Recognizer

            self.rec = Recognizer(self.model, self.compute)  # warms up the GPU on load
            self.post(("ready",))
        except Exception as e:
            self.post(("error", f"Could not load the speech model: {e}"))

    def start(self, text, device=None, wav=None, keep_audio=True):
        self._stop.clear()
        threading.Thread(target=self._run, args=(text, device, wav, keep_audio), daemon=True).start()

    def stop(self):
        self._stop.set()

    def _until_stop(self, blocks):
        for block in blocks:
            if self._stop.is_set():
                return
            yield block

    def _frame(self, session):
        words, tail = session.display()
        self.post(("frame", words, tail, session.summary()))

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
                self._frame(session)
                for _ in session.run(self._until_stop(blocks), segmenter):
                    self._frame(session)
            session.finish()
            self._frame(session)
            rec_id, note = self._save(session, blocks, wav, keep_audio and not wav, DEFAULT_MODE)
            self.post(("done", session.finished, rec_id, note))
        except Exception as e:
            self.post(("error", f"{type(e).__name__}: {e}"))

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


class App(ctk.CTk):
    def __init__(self, text="", wav=None, model="large-v3-turbo", compute="int8_float16", db=None,
                 save_replay=False):
        super().__init__(fg_color=BG)
        self.title("ReciteFlow")
        icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reciteflow.ico")
        if os.path.exists(icon):
            # CustomTkinter sets its own icon ~200 ms after start, so set ours after that
            self.after(300, lambda: self.iconbitmap(icon))
        self.geometry("980x680")
        self.minsize(640, 460)
        self.text, self.wav = text, wav
        self.phase = "edit"  # edit -> listening -> finishing -> done
        self.loaded = False
        self.breaks = set()
        self.summary = {}
        self._last = None  # last drawn tokens (skip identical redraws)
        self.toks = []  # core.history.Token list of the view (user corrections in user_state)
        self.rec_id = None  # history id of the finished recitation (corrections go there)
        self.beeped = set()  # mistake keys already announced by a beep (new_mistakes)
        try:
            self.history = History(db) if db else History()
        except Exception:
            self.history = None
        self._build()
        self.box.insert("1.0", text)
        self.events = queue.Queue()
        self.engine = Engine(self.events.put, model, compute, self.history, save_replay)
        self.engine.load()
        self._status("Loading the speech model...", YELLOW)
        self._buttons()
        self.bind("<Control-Return>", lambda e: self._start() if self.phase in ("edit", "done") else None)
        self.bind("<Escape>", lambda e: self._stop() if self.phase == "listening" else None)
        self.protocol("WM_DELETE_WINDOW", self._close)
        if self.history is None:
            self._status("History is off (could not open data/history.db)", RED)
        self.after(POLL_MS, self._poll)

    # --- layout -----------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="ReciteFlow", font=(FONT, 22, "bold"), text_color=TEXT).grid(row=0, column=0, sticky="w")
        self.status = ctk.CTkLabel(head, text="", font=(FONT, 14), text_color=MUTED)
        self.status.grid(row=0, column=1, sticky="e")

        self.hint = ctk.CTkLabel(self, text="", font=(FONT, 14), text_color=MUTED, anchor="w")
        self.hint.grid(row=1, column=0, sticky="ew", padx=24, pady=(2, 10))

        self.box = ctk.CTkTextbox(self, font=(FONT, 20), wrap="word", fg_color=CARD, text_color=TEXT,
                                  border_color=BORDER, border_width=1, corner_radius=12,
                                  spacing1=4, spacing2=8, spacing3=4)
        self.box.grid(row=2, column=0, sticky="nsew", padx=24)
        for state, color in WORD_COLORS.items():
            self.box.tag_config(state, foreground=color)
        self.box.tag_config("not_said", overstrike=True)
        self.box.tag_config("recovered", underline=True)
        self.box.tag_config("fixed", background=FIXED_BG)
        self.box.tag_bind("tok", "<Button-1>", self._click)
        self.box.tag_bind("tok", "<Enter>", lambda e: self._cursor("hand2" if self._can_correct() else ""))
        self.box.tag_bind("tok", "<Leave>", lambda e: self._cursor(""))

        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 0))
        self.chips = {}
        for col, (state, _) in enumerate(CHIPS):
            self.chips[state] = ctk.CTkLabel(chips, text="", font=(FONT, 14, "bold"), text_color=WORD_COLORS[state])
            self.chips[state].grid(row=0, column=col, padx=(0, 22))
        self._counts({})

        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.grid(row=4, column=0, sticky="ew", padx=24, pady=(12, 0))
        self.hide = tk.BooleanVar(value=True)
        self.sound = tk.BooleanVar(value=True)
        boxes = []
        for col, (label, var) in enumerate((("Hide the text while I recite", self.hide),
                                            ("Beep on a mistake", self.sound))):
            b = ctk.CTkCheckBox(opts, text=label, variable=var, font=(FONT, 14), text_color=MUTED, fg_color=PRIMARY,
                                hover_color=PRIMARY_HOVER, border_color=BORDER)
            b.grid(row=0, column=col, sticky="w", padx=(0, 22))
            boxes.append(b)
        self.hide_box, self.sound_box = boxes

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=5, column=0, sticky="ew", padx=24, pady=(10, 20))
        bar.grid_columnconfigure(1, weight=1)
        self.devices = dict((name, i) for i, name in input_devices())
        self.mic = ctk.CTkOptionMenu(bar, values=[DEFAULT_MIC] + list(self.devices), width=280, height=40,
                                     font=(FONT, 14), dropdown_font=(FONT, 14), dynamic_resizing=False,
                                     fg_color=CARD, button_color=BORDER, button_hover_color=BORDER, text_color=TEXT)
        self.mic.grid(row=0, column=0, sticky="w")
        self.keep = tk.BooleanVar(value=self.history.keep_audio if self.history else False)
        self.keep_box = ctk.CTkCheckBox(bar, text="Keep my recordings for training", variable=self.keep,
                                        font=(FONT, 14), text_color=MUTED, fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
                                        border_color=BORDER, command=self._keep_changed)
        self.keep_box.grid(row=0, column=1, sticky="w", padx=(18, 10))
        self.edit = ctk.CTkButton(bar, text="Edit text", width=120, height=40, font=(FONT, 15), fg_color="transparent",
                                  border_color=BORDER, border_width=1, hover_color=CARD, text_color=TEXT,
                                  command=self._edit)
        self.edit.grid(row=0, column=2, padx=(0, 10))
        self.go = ctk.CTkButton(bar, text="Start", width=160, height=40, font=(FONT, 15, "bold"), command=self._toggle)
        self.go.grid(row=0, column=3)

    def _status(self, text, color=MUTED):
        self.status.configure(text=text, text_color=color)

    def _counts(self, s):
        for state, label in CHIPS:
            self.chips[state].configure(text=f"{label}  {s.get(state, 0)}")

    def _buttons(self):
        """Button labels, colours and enabled states for the current phase."""
        idle = self.phase in ("edit", "done")
        if idle:
            self.go.configure(text="Recite again" if self.phase == "done" else "Start", fg_color=PRIMARY,
                              hover_color=PRIMARY_HOVER, text_color="#04201C",
                              state="normal" if self.loaded else "disabled")
        else:
            self.go.configure(text="Stop" if self.phase == "listening" else "Finishing...", fg_color=DANGER,
                              hover_color=DANGER_HOVER, text_color="#FFFFFF",
                              state="normal" if self.phase == "listening" else "disabled")
        self.mic.configure(state="normal" if idle and not self.wav else "disabled")
        self.keep_box.configure(state="normal" if idle and self.history and not self.wav else "disabled")
        self.hide_box.configure(state="normal" if idle else "disabled")  # the beep can be turned off any time
        if self.phase == "done":
            self.edit.grid()
        else:
            self.edit.grid_remove()
        self.hint.configure(text={
            "edit": "Paste the text you want to recite, then press Start (Ctrl+Enter).",
            "listening": ("Recite from memory: each word appears, coloured, about a second after you say it. "
                          if self.hide.get() else
                          "Recite the text from memory. Each word is coloured about a second after you say it. ")
                         + ("A beep means a mistake. " if self.sound.get() else "") + "Esc to stop.",
            "finishing": "Checking the last words...",
        }.get(self.phase, self.hint.cget("text")))

    # --- actions ----------------------------------------------------------------

    def _toggle(self):
        if self.phase == "listening":
            self._stop()
        else:
            self._start()

    def _start(self):
        if not self.loaded:
            return
        if self.phase == "edit":
            self.text = self.box.get("1.0", "end").strip()
        if not self.text:
            self._status("Paste a text first.", RED)
            return
        self.phase, self._last, self.rec_id = "listening", None, None
        self.beeped = set()
        self.box.configure(state="disabled")
        self._counts({})
        self._status("Starting the replay..." if self.wav else "Starting the microphone...", YELLOW)
        self._buttons()
        self.engine.start(self.text, self.devices.get(self.mic.get()), self.wav, self.keep.get())

    def _stop(self):
        self.phase = "finishing"
        self._status("Finishing...", YELLOW)
        self._buttons()
        self.engine.stop()

    def _edit(self):
        self.phase, self.rec_id = "edit", None
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", self.text)
        self._counts({})
        self._status("Ready" if self.loaded else "Loading the speech model...", MUTED if self.loaded else YELLOW)
        self._buttons()
        self.box.focus_set()

    def _keep_changed(self):
        if self.history:
            self.history.keep_audio = self.keep.get()

    # --- corrections (after a recitation) -------------------------------------------

    def _can_correct(self):
        return self.phase == "done" and self.rec_id is not None

    def _cursor(self, cursor):
        try:
            self.box._textbox.configure(cursor=cursor or "xterm")
        except Exception:
            pass

    def _click(self, event):
        if not self._can_correct():
            return
        index = self.box.index(f"@{event.x},{event.y}")
        pos = next((int(t[1:]) for t in self.box.tag_names(index) if t[0] == "t" and t[1:].isdigit()), None)
        if pos is None or pos >= len(self.toks):
            return
        tok = self.toks[pos]
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
                       disabledforeground=MUTED, font=(FONT, 12), bd=0)
        menu.add_command(label=f"The app said: {MENU.get(tok.app_state, tok.app_state)}", state="disabled")
        menu.add_separator()
        for state in labels_for(tok.kind):
            mark = "\u2713  " if state == tok.state else "     "
            menu.add_command(label=mark + MENU[state], command=lambda s=state: self._correct(pos, s))
        if tok.user_state:
            menu.add_separator()
            menu.add_command(label="Undo my correction", command=lambda: self._correct(pos, None))
        menu.tk_popup(event.x_root, event.y_root)

    def _correct(self, pos, state):
        try:
            self.toks[pos] = self.history.correct(self.rec_id, pos, state)
        except Exception as e:
            self._status(f"Could not save the correction: {e}", RED)
            return
        self._render()
        self._done_hint()
        fixed = sum(1 for t in self.toks if t.user_state)
        self._status(f"Saved #{self.rec_id}: {fixed} correction(s)", PRIMARY)

    def _done_hint(self):
        s = corrected_counts(self.summary, self.toks)
        total = self.summary.get("words", 0)
        extras = [t.text for t in self.toks if t.kind == "extra" and t.state == "extra"]
        fixed = sum(1 for t in self.toks if t.recovered)
        self._counts(s)
        self.hint.configure(text=f"{s['correct']} of {total} words correct"
                            + (f" ({total - s['pending']} said)" if s["pending"] else "")
                            + (". Extra words: " + ", ".join(extras) if extras else ".")
                            + (f" {fixed} fixed by re-reciting (underlined)." if fixed else "")
                            + (" Click a word the app got wrong to correct it." if self._can_correct() else ""))

    def _close(self):
        self.engine.stop()
        self.destroy()

    # --- engine messages ----------------------------------------------------------

    def _poll(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "ready":
            self.loaded = True
            if self.phase in ("edit", "done"):
                self._status("Ready")
        elif kind == "frame" and self.phase in ("listening", "finishing"):
            _, words, tail, summary = msg
            self._draw(words, tail)
            self._counts(summary)
            self.summary = summary
            if self.phase == "listening":
                self._status("Replaying the recording" if self.wav else "Listening", PRIMARY)
            return
        elif kind == "done" and self.phase in ("listening", "finishing"):
            _, finished, self.rec_id, note = msg
            self.phase = "done"
            self._render()  # reveal the whole text (hidden while reciting)
            self._status(("Finished" if finished else "Stopped") + (f" ({note})" if note else ""),
                         RED if note.startswith("not saved") else MUTED)
            self._done_hint()
        elif kind == "error":
            if self.phase in ("listening", "finishing"):
                self._edit()
            self._status(msg[1], RED)
        self._buttons()

    def _draw(self, words, tail):
        if self._last is None:
            self.breaks = line_breaks(self.text, words)
        toks = tokens(words, tail, self.breaks)
        if toks == self._last:
            return
        self._last = toks
        self.toks = token_list(words, tail)
        if new_mistakes(self.toks, self.beeped) and self.sound.get() and self.phase == "listening":
            beep()
        self._render()

    def _render(self):
        """Draw self.toks; each token is tagged with its state, "tok" and "t<pos>" (for clicks)."""
        box = self.box
        box.configure(state="normal")
        box.delete("1.0", "end")
        follow = None  # the first word not yet said: keep it in view
        hide = self.hide.get() and self.phase != "done"
        for tok in visible(self.toks, hide):
            if tok.state == "pending" and follow is None:
                follow = box.index("end-1c")
            tags = (tok.state, "tok", f"t{tok.pos}") + (("fixed",) if tok.user_state else ())                 + (("recovered",) if tok.recovered and not tok.user_state else ())
            box.insert("end", shown(tok), tags)
            box.insert("end", separator(tok, self.breaks))
        if hide and any(t.state == "pending" for t in self.toks):
            box.insert("end", HIDDEN_MARK, ("pending",))
        box.configure(state="disabled")
        if self.phase != "done":
            box.see(follow or "end")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--file", help="text file to put in the box (UTF-8)")
    parser.add_argument("--wav", help="replay this WAV in real time instead of the microphone (testing)")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--compute", default="int8_float16")
    parser.add_argument("--db", help="history database (default data/history.db)")
    parser.add_argument("--save", action="store_true", help="with --wav: save the replay in the history too")
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    text = ""
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read().strip()
    ctk.set_appearance_mode("dark")
    App(text, args.wav, args.model, args.compute, args.db, args.save).mainloop()


if __name__ == "__main__":
    main()
