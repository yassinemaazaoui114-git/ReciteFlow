"""Server check: drive engine/server.py the way Electron does (start it with a token, read the port line, call the
routes with `Authorization: Bearer`, read the event stream) and check recitations, stop, corrections and refusals.
Uses a temporary history (RECITEFLOW_DB); the real history is never opened. Loads the speech model once: the GPU must
be free.

    py -3.12 engine/check_server.py
"""
import json
import os
import queue
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIPS = HERE.parents[1] / "ReciteFlow" / "clips"
WAV, TEXT = CLIPS / "this_step.wav", (CLIPS / "this_step.txt").read_text(encoding="utf-8").strip()

failures = []


def check(ok, what):
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        failures.append(what)


class Client:
    def __init__(self, port, token):
        self.base, self.token = f"http://127.0.0.1:{port}", token
        self.events = queue.Queue()

    def call(self, path, body=None, token=True, raw=None):
        """(status code, JSON answer)."""
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request(self.base + path, data=data, method="GET" if data is None else "POST")
        if token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def listen(self):
        req = urllib.request.Request(self.base + "/events", headers={"Authorization": f"Bearer {self.token}"})

        def run():
            event = None
            try:
                with urllib.request.urlopen(req) as res:
                    for line in res:
                        line = line.decode("utf-8").rstrip("\n")
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            self.events.put((event, json.loads(line[6:]), time.perf_counter()))
            except OSError:  # the server shut down
                pass
        threading.Thread(target=run, daemon=True).start()

    def until(self, kinds, timeout=180):
        """Events until one of `kinds` arrives: (that event, its data, frames seen before it)."""
        frames, end = [], time.perf_counter() + timeout
        while True:
            event, data, at = self.events.get(timeout=max(0.1, end - time.perf_counter()))
            if event == "frame":
                frames.append(data)
            if event in kinds:
                return event, data, frames, at


def main():
    tmp = Path(tempfile.mkdtemp(prefix="reciteflow_check_"))
    token = secrets.token_hex(32)
    env = {**os.environ, "RECITEFLOW_TOKEN": token, "RECITEFLOW_DB": str(tmp / "history.db"),
           "RECITEFLOW_SAVE_REPLAY": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
    server = subprocess.Popen([sys.executable, "-u", str(HERE / "server.py")], cwd=HERE, env=env,
                              stdout=subprocess.PIPE, text=True)
    try:
        port = json.loads(server.stdout.readline())["port"]
        c = Client(port, token)
        print(f"server on 127.0.0.1:{port}, temporary history {tmp}")

        print("refusals")
        for path, body in (("/health", None), ("/devices", None), ("/recite", {"text": TEXT}), ("/recite/stop", {}),
                           ("/corrections", {"rec_id": 1, "pos": 0, "state": "wrong"}), ("/settings", None)):
            check(c.call(path, body, token=False)[0] == 403, f"no token: {path} refused (403)")
        bad = Client(port, "x" * 64)
        check(bad.call("/health")[0] == 403, "wrong token refused (403)")
        check(c.call("/recite", raw=b"{not json")[0] == 400, "bad JSON refused (400)")
        check(c.call("/recite", raw=b"[1, 2]")[0] == 400, "a JSON list refused (400)")
        code, ans = c.call("/recite", {"text": "   "})
        check(code == 400 and ans["error"] == "Paste a text first.", f"empty text refused (400): {ans}")
        check(c.call("/recite", {"text": TEXT, "wav": str(tmp / "missing.wav")})[0] == 400, "missing WAV refused (400)")
        check(c.call("/recite", {"text": TEXT, "device": "mic"})[0] == 400, "bad device refused (400)")
        check(c.call("/nowhere", {})[0] == 404, "unknown route (404)")

        c.listen()
        t0 = time.perf_counter()
        while c.call("/health")[1]["state"] == "loading":
            time.sleep(0.5)
        state = c.call("/health")[1]
        check(state["state"] == "ready", f"model ready in {state['load_s']} s on {state['gpu']}")
        check(state["history"] == str(tmp / "history.db"), "server uses the temporary history")

        print("devices and settings")
        code, ans = c.call("/devices")
        check(code == 200 and isinstance(ans["devices"], list), f"{len(ans['devices'])} microphones listed")
        check(c.call("/settings")[1] == {"keep_audio": True}, "keep_audio defaults to on")
        check(c.call("/settings", {"keep_audio": False})[1] == {"keep_audio": False}, "keep_audio set off")
        check(c.call("/settings")[1] == {"keep_audio": False}, "keep_audio remembered")
        check(c.call("/settings", {"keep_audio": "no"})[0] == 400, "bad keep_audio refused (400)")

        print("full replay of this_step.wav")
        t0 = time.perf_counter()
        check(c.call("/recite", {"text": TEXT, "wav": str(WAV)}) == (200, {"ok": True}), "recitation started")
        code, ans = c.call("/recite", {"text": TEXT, "wav": str(WAV)})
        check(code == 409, f"a second recitation refused (409): {ans.get('error')}")
        event, done, frames, at = c.until(("done", "error"))
        r = done.get("result", {})
        check(event == "done" and done["finished"], f"done after {at - t0:.1f} s, {len(frames)} frames")
        check((r.get("correct"), r.get("total")) == (55, 55), f"{r.get('correct')} of {r.get('total')} words correct")
        check(all(not f["beep"] for f in frames), "no beep")
        check(done["rec_id"] is not None and done["note"] == f"saved #{done['rec_id']}",
              f"replay saved in the temporary history: {done['note']}")
        rec_id = done["rec_id"]

        print("corrections")
        code, ans = c.call("/corrections", {"rec_id": rec_id, "pos": 0, "state": "wrong"})
        check(code == 200 and ans["counts"]["correct"] == 54 and ans["counts"]["wrong"] == 1
              and ans["tokens"][0]["corrected"], "word 0 corrected to wrong: counts 54 correct, 1 wrong")
        import sqlite3

        db = sqlite3.connect(tmp / "history.db")
        row = db.execute("SELECT app_state, user_state, corrected_at FROM tokens WHERE recitation_id = ? AND pos = 0",
                         (rec_id,)).fetchone()
        check(row[:2] == ("correct", "wrong") and row[2], f"saved row: {row}")
        code, ans = c.call("/corrections", {"rec_id": rec_id, "pos": 0, "state": None})
        row = db.execute("SELECT user_state FROM tokens WHERE recitation_id = ? AND pos = 0", (rec_id,)).fetchone()
        check(code == 200 and ans["counts"]["correct"] == 55 and row == (None,), "undo clears it")
        db.close()
        check(c.call("/corrections", {"rec_id": rec_id, "pos": 0, "state": "extra"})[0] == 400,
              "a label not allowed for a word refused (400)")
        check(c.call("/corrections", {"rec_id": rec_id, "pos": 999, "state": "wrong"})[0] == 400,
              "a position out of range refused (400)")
        check(c.call("/corrections", {"rec_id": rec_id + 1, "pos": 0, "state": "wrong"})[0] == 409,
              "another recitation id refused (409)")

        print("stop mid-way")
        t0 = time.perf_counter()
        c.call("/recite", {"text": TEXT, "wav": str(WAV)})
        time.sleep(6)
        code, ans = c.call("/recite/stop", {})
        check(code == 200 and ans["running"], "stop accepted while running")
        event, done, frames, at = c.until(("done", "error"))
        r = done.get("result", {})
        check(event == "done" and not done["finished"],
              f"stopped: done {at - t0:.1f} s after start, {r.get('correct')} of {r.get('total')} correct, "
              f"{r.get('said')} said")
        check(c.call("/recite/stop", {})[1] == {"ok": True, "running": False}, "stop when idle is harmless")

        c.call("/shutdown", {})
        server.wait(10)
        check(server.returncode == 0, "shutdown")
    finally:
        if server.poll() is None:
            server.kill()
    print("PASSED" if not failures else f"FAILED: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
