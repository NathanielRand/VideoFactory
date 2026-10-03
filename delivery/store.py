"""Export destinations, and the transfers that fill them.

A destination is somewhere finished videos go: a folder on this PC, a cloud
provider's sync folder, or an rclone remote. Each can take new clips and new
compilation renders on its own as they finish ("auto"), or only what is sent
to it by hand.

A transfer is one file on its way to one place. They are rows, worked through
one at a time by TransferWorker, so a restart picks up where it stopped and
the Local and Cloud pages read the same list the worker writes.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from delivery import cloud

KINDS = ("folder", "cloud_folder", "rclone")
CHUNK = 8 * 1024 * 1024
# How often a copy in progress writes how far it has got.
REPORT_SECONDS = 0.5

SCHEMA = """
CREATE TABLE IF NOT EXISTS export_destinations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    kind              TEXT NOT NULL,              -- folder | cloud_folder | rclone
    provider          TEXT NOT NULL DEFAULT '',   -- cloud_folder: onedrive, dropbox...; rclone: the remote's type
    target            TEXT NOT NULL,              -- a folder, or rclone "remote:path"
    auto_clips        INTEGER NOT NULL DEFAULT 0,
    auto_compilations INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS export_transfers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    destination_id INTEGER NOT NULL DEFAULT 0,   -- 0: a one-off "Save to folder"
    via            TEXT NOT NULL DEFAULT 'copy', -- copy | rclone
    target         TEXT NOT NULL,
    item_kind      TEXT NOT NULL,                -- clip | compilation
    item_id        INTEGER NOT NULL,             -- clips.id | compilation_renders.id
    canvas         TEXT NOT NULL DEFAULT '',     -- which format of a compilation render
    source         TEXT NOT NULL,
    name           TEXT NOT NULL,                -- the file name it gets there
    state          TEXT NOT NULL DEFAULT 'queued',  -- queued | sending | done | failed
    bytes          INTEGER NOT NULL DEFAULT 0,
    sent           INTEGER NOT NULL DEFAULT 0,
    error          TEXT NOT NULL DEFAULT '',
    result         TEXT NOT NULL DEFAULT '',     -- where it landed
    created_at     TEXT NOT NULL,
    finished_at    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_export_transfers_state ON export_transfers(state, id);
"""

_wake = threading.Event()


def wake() -> None:
    """Tell the worker there is something new to send."""
    _wake.set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(db) -> None:
    db.conn.executescript(SCHEMA)


# ---- destinations ---------------------------------------------------------------------


def _destination(row) -> dict:
    d = dict(row)
    d["auto_clips"] = bool(d["auto_clips"])
    d["auto_compilations"] = bool(d["auto_compilations"])
    return d


def destinations(db) -> list[dict]:
    rows = db.conn.execute("SELECT * FROM export_destinations ORDER BY id").fetchall()
    return [_destination(r) for r in rows]


def get_destination(db, dest_id: int) -> dict | None:
    r = db.conn.execute("SELECT * FROM export_destinations WHERE id = ?", (dest_id,)).fetchone()
    return _destination(r) if r else None


def add_destination(db, *, name: str, kind: str, target: str, provider: str = "",
                    auto_clips: bool = False, auto_compilations: bool = False) -> int:
    if kind not in KINDS:
        raise ValueError(f"unknown destination kind {kind!r}")
    if kind != "rclone" and not Path(target).is_absolute():
        raise ValueError("Choose a full folder path.")
    # Unnamed, it goes by what a person would call it: the cloud, the rclone
    # remote, or the folder.
    fallback = (cloud.PROVIDERS.get(provider) if kind == "cloud_folder" else None) or (
        target.split(":", 1)[0] if kind == "rclone" else Path(target).name
    ) or target
    cur = db.conn.execute(
        "INSERT INTO export_destinations (name, kind, provider, target, auto_clips, auto_compilations,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name.strip() or fallback, kind, provider, target,
         int(auto_clips), int(auto_compilations), _now()),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def update_destination(db, dest_id: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in ("name", "auto_clips", "auto_compilations")
               and v is not None}
    if not allowed:
        return get_destination(db, dest_id) is not None
    sets = ", ".join(f"{k} = ?" for k in allowed)
    values = [int(v) if isinstance(v, bool) else v for v in allowed.values()]
    cur = db.conn.execute(f"UPDATE export_destinations SET {sets} WHERE id = ?", (*values, dest_id))
    db.conn.commit()
    return cur.rowcount > 0


def delete_destination(db, dest_id: int) -> bool:
    """Forget a destination. Files already sent there stay; transfers not yet
    sent are dropped, since there is nowhere for them to go."""
    db.conn.execute(
        "DELETE FROM export_transfers WHERE destination_id = ? AND state IN ('queued', 'failed')",
        (dest_id,),
    )
    cur = db.conn.execute("DELETE FROM export_destinations WHERE id = ?", (dest_id,))
    db.conn.commit()
    return cur.rowcount > 0


def check_destination(dest: dict, *, rclone_run=None) -> str:
    """"" when the destination can be written to now, else why not."""
    if dest["kind"] == "rclone":
        return cloud.check_rclone(dest["target"], run=rclone_run)
    return cloud.check_folder(Path(dest["target"]))


# ---- what there is to send -------------------------------------------------------------


def _size(path: str | None) -> int:
    try:
        return Path(path).stat().st_size if path else 0
    except OSError:
        return 0


def _slug(text: str, fallback: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", (text or "").lower()).strip()
    slug = re.sub(r"[\s_]+", "-", slug)[:60].strip("-")
    return slug or fallback


def _sent_to(db, kind: str) -> dict[tuple[int, str], list[int]]:
    """{(item_id, canvas): [destination ids it reached]}."""
    out: dict[tuple[int, str], list[int]] = {}
    for r in db.conn.execute(
        "SELECT item_id, canvas, destination_id FROM export_transfers "
        "WHERE item_kind = ? AND state = 'done' AND destination_id > 0", (kind,),
    ):
        out.setdefault((r["item_id"], r["canvas"]), []).append(r["destination_id"])
    return out


def files(db, kind: str, limit: int = 500) -> list[dict]:
    """Finished videos on this PC, newest first. `kind` is clips or
    compilations; a compilation render is one row per format."""
    if kind == "clips":
        sent = _sent_to(db, "clip")
        rows = db.conn.execute(
            "SELECT c.id, c.title, c.hook, c.path, c.created_at, c.exported_at, c.score, c.start_s, c.end_s,"
            " c.video_id, v.title AS video_title, v.channel_name "
            "FROM clips c LEFT JOIN videos v ON v.video_id = c.video_id "
            "WHERE c.path IS NOT NULL AND c.path != '' ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            size = _size(r["path"])
            out.append({
                "kind": "clip", "id": r["id"], "canvas": "",
                "title": r["title"] or r["hook"] or Path(r["path"]).stem,
                "parent": r["video_title"] or r["video_id"], "channel": r["channel_name"] or "",
                "path": r["path"], "bytes": size, "exists": size > 0,
                "duration": round(float(r["end_s"]) - float(r["start_s"]), 1),
                "created_at": r["created_at"], "saved": bool(r["exported_at"]),
                "sent_to": sent.get((r["id"], ""), []),
            })
        return out
    if kind == "compilations":
        sent = _sent_to(db, "compilation")
        rows = db.conn.execute(
            "SELECT r.id, r.compilation_id, r.version, r.outputs, r.created_at, c.title "
            "FROM compilation_renders r LEFT JOIN compilations c ON c.id = r.compilation_id "
            "ORDER BY r.created_at DESC, r.id DESC LIMIT ?", (limit,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                outputs = json.loads(r["outputs"] or "{}")
            except ValueError:
                outputs = {}
            for canvas, path in outputs.items():
                size = _size(path)
                out.append({
                    "kind": "compilation", "id": r["id"], "canvas": canvas,
                    "title": f"{r['title'] or 'Compilation'} v{r['version']}",
                    "parent": r["title"] or "", "compilation_id": r["compilation_id"], "channel": "",
                    "path": path, "bytes": size, "exists": size > 0, "duration": None,
                    "created_at": r["created_at"], "saved": False,
                    "sent_to": sent.get((r["id"], canvas), []),
                })
        return out
    raise ValueError("kind must be clips or compilations")


def _source(db, item_kind: str, item_id: int, canvas: str = "") -> tuple[str, str]:
    """(path on disk, the file name it gets at a destination)."""
    if item_kind == "clip":
        r = db.conn.execute("SELECT title, hook, path FROM clips WHERE id = ?", (item_id,)).fetchone()
        if r is None or not r["path"]:
            raise LookupError(f"clip {item_id} has no file")
        return r["path"], _slug(r["title"] or r["hook"] or "", Path(r["path"]).stem) + ".mp4"
    if item_kind == "compilation":
        r = db.conn.execute("SELECT outputs FROM compilation_renders WHERE id = ?", (item_id,)).fetchone()
        outputs = json.loads(r["outputs"] or "{}") if r else {}
        path = outputs.get(canvas) or (next(iter(outputs.values()), None) if not canvas else None)
        if not path:
            raise LookupError(f"render {item_id} has no {canvas or 'file'}")
        return path, Path(path).name
    raise ValueError(f"unknown item kind {item_kind!r}")


# ---- transfers ------------------------------------------------------------------------


def queue_transfers(db, items: list[dict], *, destination: dict | None = None,
                    folder: str = "") -> list[int]:
    """One transfer per item, to a destination or (a one-off) a folder.
    Items are {"kind": "clip"|"compilation", "id", "canvas"?}. Items whose
    file is gone are passed over."""
    if destination is None and not folder:
        raise ValueError("send to a destination or a folder")
    if destination is None and not Path(folder).is_absolute():
        raise ValueError("Choose a full folder path.")
    via = "rclone" if destination and destination["kind"] == "rclone" else "copy"
    target = destination["target"] if destination else folder
    ids = []
    for item in items:
        kind, item_id, canvas = item.get("kind"), int(item.get("id") or 0), item.get("canvas") or ""
        try:
            source, name = _source(db, kind, item_id, canvas)
        except (LookupError, ValueError):
            continue
        cur = db.conn.execute(
            "INSERT INTO export_transfers (destination_id, via, target, item_kind, item_id, canvas, source,"
            " name, bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (destination["id"] if destination else 0, via, target, kind, item_id, canvas, source, name,
             _size(source), _now()),
        )
        ids.append(int(cur.lastrowid))
    db.conn.commit()
    if ids:
        wake()
    return ids


def transfers(db, limit: int = 100) -> list[dict]:
    rows = db.conn.execute(
        "SELECT t.*, d.name AS destination_name, d.kind AS destination_kind "
        "FROM export_transfers t LEFT JOIN export_destinations d ON d.id = t.destination_id "
        "ORDER BY CASE t.state WHEN 'sending' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, t.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        t = dict(r)
        t["percent"] = round(100 * t["sent"] / t["bytes"]) if t["bytes"] else (100 if t["state"] == "done" else 0)
        out.append(t)
    return out


def retry_transfer(db, transfer_id: int) -> bool:
    cur = db.conn.execute(
        "UPDATE export_transfers SET state = 'queued', error = '', sent = 0 WHERE id = ? AND state = 'failed'",
        (transfer_id,),
    )
    db.conn.commit()
    if cur.rowcount:
        wake()
    return cur.rowcount > 0


def clear_transfers(db) -> int:
    cur = db.conn.execute("DELETE FROM export_transfers WHERE state IN ('done', 'failed')")
    db.conn.commit()
    return cur.rowcount


def cancel_transfer(db, transfer_id: int) -> bool:
    """Drop one that has not started."""
    cur = db.conn.execute("DELETE FROM export_transfers WHERE id = ? AND state = 'queued'", (transfer_id,))
    db.conn.commit()
    return cur.rowcount > 0


# ---- sending new work on its own ------------------------------------------------------------


def after_job(db, job, payload: dict) -> int:
    """When a job finishes, queue what it made for every destination set to
    take it. Returns how many transfers were queued."""
    queued = 0
    if job["type"] == "process" and not payload.get("import_only"):
        dests = [d for d in destinations(db) if d["auto_clips"]]
        row = db.get_job(job["id"])
        video_id = (row["video_id"] if row else "") or ""
        if dests and video_id:
            items = [{"kind": "clip", "id": int(c["id"])} for c in db.clips_for_video(video_id) if c["path"]]
            already = _sent_to(db, "clip")
            for d in dests:
                todo = [i for i in items if d["id"] not in already.get((i["id"], ""), [])]
                queued += len(queue_transfers(db, todo, destination=d))
    elif job["type"] == "compile":
        dests = [d for d in destinations(db) if d["auto_compilations"]]
        comp_id = int(payload.get("compilation_id") or 0)
        r = db.conn.execute(
            "SELECT id, outputs FROM compilation_renders WHERE compilation_id = ? ORDER BY version DESC LIMIT 1",
            (comp_id,),
        ).fetchone()
        if dests and r:
            items = [{"kind": "compilation", "id": r["id"], "canvas": c}
                     for c in json.loads(r["outputs"] or "{}")]
            for d in dests:
                queued += len(queue_transfers(db, items, destination=d))
    return queued


# ---- the worker -------------------------------------------------------------------------


def _unique(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix = target.stem, target.suffix
    n = 2
    while target.exists():
        target = folder / f"{stem}-{n}{suffix}"
        n += 1
    return target


def copy_file(source: Path, folder: Path, name: str, on_bytes: Callable[[int], None]) -> Path:
    """Copy in chunks, reporting bytes as they go, into a part file renamed at
    the end: a sync client never uploads half a video, and a copy cut short
    leaves no file that looks finished."""
    import shutil

    folder.mkdir(parents=True, exist_ok=True)
    target = _unique(folder, name)
    part = target.with_name(target.name + ".part")
    sent = 0
    try:
        with open(source, "rb") as src, open(part, "wb") as dst:
            while chunk := src.read(CHUNK):
                dst.write(chunk)
                sent += len(chunk)
                on_bytes(sent)
        shutil.copystat(source, part)
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return target


class TransferWorker(threading.Thread):
    """Sends queued transfers one at a time. One at a time on purpose: two
    multi-gigabyte copies to the same disk go no faster together, and a sync
    client uploads in its own order anyway."""

    def __init__(self, db: Callable, *, broadcaster, rclone_copy=cloud.rclone_copy, clock=time.monotonic):
        super().__init__(daemon=True, name="export-transfers")
        self._db = db
        self._broadcaster = broadcaster
        self._rclone_copy = rclone_copy
        self._clock = clock
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        _wake.set()

    def recover(self) -> None:
        """A transfer cut off by a restart goes back to the front."""
        d = self._db()
        try:
            ensure_schema(d)
            d.conn.execute("UPDATE export_transfers SET state = 'queued', sent = 0 WHERE state = 'sending'")
            d.conn.commit()
        finally:
            d.close()

    def run(self) -> None:
        self.recover()
        while not self._stop.is_set():
            try:
                while self.step():
                    if self._stop.is_set():
                        return
            except Exception:
                traceback.print_exc()  # the worker must outlive any one failure
            _wake.wait(timeout=10)
            _wake.clear()

    def step(self) -> bool:
        """Send the next transfer. False when there was none."""
        d = self._db()
        try:
            row = d.conn.execute(
                "SELECT * FROM export_transfers WHERE state = 'queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            d.conn.execute("UPDATE export_transfers SET state = 'sending', sent = 0 WHERE id = ?", (row["id"],))
            d.conn.commit()
            self._broadcaster.publish({"type": "exports"})
            last = {"at": 0.0}

            def report(sent: int) -> None:
                now = self._clock()
                if now - last["at"] < REPORT_SECONDS:
                    return
                last["at"] = now
                d.conn.execute("UPDATE export_transfers SET sent = ? WHERE id = ?", (sent, row["id"]))
                d.conn.commit()
                self._broadcaster.publish({"type": "exports", "transfer": row["id"],
                                           "sent": sent, "bytes": row["bytes"]})

            try:
                source = Path(row["source"])
                if not source.is_file():
                    raise FileNotFoundError("The file isn't on this PC any more.")
                if row["via"] == "rclone":
                    result = self._rclone_copy(
                        source, row["target"], row["name"],
                        on_fraction=lambda f: report(int(f * row["bytes"])),
                    )
                else:
                    result = str(copy_file(source, Path(row["target"]), row["name"], report))
            except Exception as e:
                d.conn.execute(
                    "UPDATE export_transfers SET state = 'failed', error = ?, finished_at = ? WHERE id = ?",
                    (str(e)[:500] or type(e).__name__, _now(), row["id"]),
                )
                d.conn.commit()
            else:
                d.conn.execute(
                    "UPDATE export_transfers SET state = 'done', sent = bytes, result = ?, error = '',"
                    " finished_at = ? WHERE id = ?", (result, _now(), row["id"]),
                )
                if row["item_kind"] == "clip":
                    # Saved somewhere: the same "kept" mark the old Export set.
                    d.conn.execute(
                        "UPDATE clips SET exported_at = ? WHERE id = ? AND (exported_at IS NULL OR exported_at = '')",
                        (_now(), row["item_id"]),
                    )
                d.conn.commit()
            self._broadcaster.publish({"type": "exports"})
            return True
        finally:
            d.close()
