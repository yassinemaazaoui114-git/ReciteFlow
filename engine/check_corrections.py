"""Checks that the window saves corrections exactly as the old window does.

  py -3.12 engine/check_corrections.py snapshot <history.db> <copy.db>
      a copy of the history right after the recitation was saved, before any click
  py -3.12 engine/check_corrections.py compare <copy.db> <history.db> <clicks.json>
      applies the same clicks ({rec_id, clicks: [[pos, state or null], ...]}) to the copy with the old window's own
      `History.correct` (loaded from the ReciteFlow folder, read only), then compares every row of both databases;
      `corrected_at` is compared as set / not set (the clicks happen at different times). Exit code 0 = identical.

Only temporary histories are used; nothing is written in the ReciteFlow folder.
"""

import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.dont_write_bytecode = True  # no __pycache__ in the ReciteFlow folder
OLD_HISTORY = Path(__file__).resolve().parents[2] / "ReciteFlow" / "core" / "history.py"


def snapshot(src, dst):
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(src)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)


def rows(path):
    """Every row of every table, in a fixed order, with corrected_at as set / not set."""
    out = {}
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            got = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for r in got:
                if "corrected_at" in r:
                    r["corrected_at"] = r["corrected_at"] is not None
            out[table] = got
    return out


def compare(before, after, clicks_path):
    spec = importlib.util.spec_from_file_location("old_history", OLD_HISTORY)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    clicks = json.loads(Path(clicks_path).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "history.db"
        shutil.copyfile(before, ref)
        history = old.History(ref)
        for pos, state in clicks["clicks"]:
            history.correct(clicks["rec_id"], pos, state)
        want, got = rows(ref), rows(after)
    same = True
    for table in sorted(set(want) | set(got)):
        a, b = want.get(table, []), got.get(table, [])
        diff = [i for i in range(max(len(a), len(b))) if (a[i:i + 1] or [None]) != (b[i:i + 1] or [None])]
        print(f"{table}: {len(b)} rows, {'identical' if not diff else f'{len(diff)} differ'}")
        for i in diff[:10]:
            print(f"  old window: {a[i] if i < len(a) else None}\n  new window: {b[i] if i < len(b) else None}")
        same &= not diff
    rec = [r for r in got.get("tokens", []) if r["recitation_id"] == clicks["rec_id"] and r["user_state"]]
    print(f"{len(clicks['clicks'])} clicks, {len(rec)} corrections left: "
          + ", ".join(f"{r['pos']} {r['app_state']}->{r['user_state']}" for r in rec))
    print("IDENTICAL" if same else "DIFFERENT")
    return 0 if same else 1


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "snapshot":
        snapshot(*args)
    else:
        sys.exit(compare(*args))
