"""ReciteFlow engine sidecar: a small local server the Electron window talks to.
Listens only on 127.0.0.1, on a port the system picks, and answers only requests that carry the token Electron
gave it in the RECITEFLOW_TOKEN environment variable (`Authorization: Bearer <token>`). Standard library only.
On start it prints one line to stdout: {"port": <port>}, then loads the speech model in the background
(asr.engine.Recognizer, which also warms up the GPU), sending "loading", then "ready" or "error" on the event stream.
It stops on POST /shutdown, and by itself when the Electron process (RECITEFLOW_PARENT_PID) ends.
Routes: GET /health (state), GET /events (event stream, server-sent events), POST /shutdown,
GET /devices (microphones), POST /recite {text, device, wav, keep_audio} (one at a time), POST /recite/stop,
POST /corrections {rec_id, pos, state} (state null = undo), GET/POST /settings {keep_audio}.
Recitation events (recite.py): "frame", "done", "error". Errors answer {"error": "<plain words>"} with 400 / 409 / 503.
Model and compute type: RECITEFLOW_MODEL / RECITEFLOW_COMPUTE (default large-v3-turbo, int8_float16).
History: RECITEFLOW_DB (default ReciteFlow/data/history.db; tests give a temporary one).
Replays (a "wav" in /recite) are saved only with RECITEFLOW_SAVE_REPLAY=1.
"""
import ctypes
import hmac
import json
import os
import queue
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import recite

HEARTBEAT_S = 15   # an empty comment line on the event stream this often, so a dead connection is noticed
MAX_BODY = 1_000_000   # bytes; a pasted text is far smaller

TOKEN = os.environ.get("RECITEFLOW_TOKEN", "")
MODEL = os.environ.get("RECITEFLOW_MODEL", "large-v3-turbo")
COMPUTE = os.environ.get("RECITEFLOW_COMPUTE", "int8_float16")
DB = os.environ.get("RECITEFLOW_DB") or recite.HISTORY_DB
SAVE_REPLAY = os.environ.get("RECITEFLOW_SAVE_REPLAY") == "1"

# state: "loading" | "ready" | "error"; load_s: seconds the model took to load (with the GPU warm-up)
_state = {"state": "loading", "error": None, "pid": os.getpid(), "model": MODEL, "compute": COMPUTE,
          "gpu": None, "load_s": None}
_clients = []
_clients_lock = threading.Lock()
recognizer = None   # the loaded asr.engine.Recognizer, used by the recitations
history = None      # core.history.History, or None when it could not be opened
engine = None       # recite.Engine


def emit(event, data):
    """Send one event to every open event stream."""
    with _clients_lock:
        for q in _clients:
            q.put((event, data))


def _set_state(**changes):
    _state.update(changes)
    emit("state", dict(_state))


def _gpu_name():
    """The graphics card's name from nvidia-smi, or None."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        return out.stdout.strip().splitlines()[0] or None
    except Exception:
        return None


def _load():
    """Load the speech model in the background, so /health answers right away."""
    global recognizer
    _set_state(gpu=_gpu_name())
    try:
        t0 = time.perf_counter()
        from asr.engine import Recognizer

        recognizer = Recognizer(MODEL, COMPUTE)   # warms up the GPU on load
        engine.rec = recognizer
        _set_state(state="ready", load_s=round(time.perf_counter() - t0, 2))
    except Exception as e:   # shown in the window as a message, never a crash
        _set_state(state="error", error=f"Could not load the speech model: {type(e).__name__}: {e}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):   # keep stderr for real errors only
        pass

    def _allowed(self):
        given = self.headers.get("Authorization", "")
        if TOKEN and hmac.compare_digest(given, "Bearer " + TOKEN):
            return True
        self._json({"error": "forbidden"}, 403)
        return False

    def _error(self, message, code=400):
        self._json({"error": message}, code)

    def _body(self):
        """The request's JSON object, or None after answering 400."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("not an object")
            return data
        except Exception:
            self.close_connection = True
            self._error("Bad request: the body must be a JSON object.")
            return None

    def _json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._allowed():
            return
        if self.path == "/health":
            self._json(_state)
        elif self.path == "/events":
            self._events()
        elif self.path == "/devices":
            self._json({"devices": [{"id": i, "name": name} for i, name in recite.input_devices()]})
        elif self.path == "/settings":
            self._settings()
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._allowed():
            return
        if self.path == "/shutdown":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.1), os._exit(0)), daemon=True).start()
            return
        routes = {"/recite": self._recite, "/recite/stop": self._stop, "/corrections": self._correct,
                  "/settings": self._settings}
        if self.path not in routes:
            return self._json({"error": "not found"}, 404)
        data = self._body()
        if data is not None:
            routes[self.path](data)

    def _recite(self, data):
        text, device, wav = data.get("text"), data.get("device"), data.get("wav")
        if not isinstance(text, str) or not text.strip():
            return self._error("Paste a text first.")
        if device is not None and (isinstance(device, bool) or not isinstance(device, int)):
            return self._error("Bad request: device must be a microphone id or null.")
        if wav is not None and not (isinstance(wav, str) and os.path.isfile(wav)):
            return self._error(f"Bad request: no recording at {wav!r}.")
        if engine.rec is None:
            return self._error("The speech model is not ready yet.", 503)
        keep = data.get("keep_audio")
        keep = bool(keep) if keep is not None else bool(history and history.keep_audio)
        try:
            engine.start(text.strip(), device=device, wav=wav, keep_audio=keep)
        except RuntimeError as e:
            return self._error(str(e), 409)
        self._json({"ok": True})

    def _stop(self, data):
        running = engine.busy
        engine.stop()
        self._json({"ok": True, "running": running})

    def _correct(self, data):
        rec_id, pos, state = data.get("rec_id"), data.get("pos"), data.get("state")
        if not isinstance(pos, int) or isinstance(pos, bool) or not (state is None or isinstance(state, str)):
            return self._error("Bad request: pos must be a number and state a label or null.")
        if engine.busy or engine.rec_id is None or rec_id != engine.rec_id:
            return self._error("Could not save the correction: this recitation is not the last saved one.", 409)
        try:
            self._json(engine.correct(pos, state))
        except (ValueError, KeyError) as e:
            self._error(f"Could not save the correction: {e}")
        except Exception as e:
            self._error(f"Could not save the correction: {e}", 500)

    def _settings(self, data=None):
        if history is None:
            return self._error("History is off (could not open data/history.db)", 503)
        if data is not None and "keep_audio" in data:
            if not isinstance(data["keep_audio"], bool):
                return self._error("Bad request: keep_audio must be true or false.")
            history.keep_audio = data["keep_audio"]
        self._json({"keep_audio": history.keep_audio})

    def _events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        q = queue.Queue()
        q.put(("state", dict(_state)))   # a new listener first gets the current state
        with _clients_lock:
            _clients.append(q)
        try:
            while True:
                try:
                    event, data = q.get(timeout=HEARTBEAT_S)
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": alive\n\n")
                self.wfile.flush()
        except OSError:   # the window closed the stream
            pass
        finally:
            with _clients_lock:
                _clients.remove(q)


def _exit_when_parent_goes(pid):
    """Wait for the Electron process to end (quit or crash), then stop.
    Not done by reading stdin: on Windows a waiting read on a pipe blocks other calls on it, and imports can hang."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE: only the right to wait for it
    if handle:
        kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)     # INFINITE
    os._exit(0)


def main():
    global history, engine
    if not TOKEN:
        sys.exit("RECITEFLOW_TOKEN is not set; the engine only runs when started by the ReciteFlow window.")
    history = recite.open_history(DB)
    engine = recite.Engine(emit, model=MODEL, compute=COMPUTE, history=history, save_replay=SAVE_REPLAY)
    _state["history"] = str(DB) if history else None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    parent = int(os.environ.get("RECITEFLOW_PARENT_PID") or 0)
    if parent:
        threading.Thread(target=_exit_when_parent_goes, args=(parent,), daemon=True).start()
    threading.Thread(target=_load, daemon=True).start()
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
