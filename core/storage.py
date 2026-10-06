"""Where the library lives, which drive that is, and moving it somewhere else.

The library (downloads, clips, transcripts, the database) is one folder, the
data directory. This module answers "what drive is that on and how full is it",
remembers a different choice, and moves the library there.

The choice is stored in a small pointer file in the per-user app folder, NOT in
settings.yaml: in a checkout settings.yaml is tracked by git, and the pointer
has to be readable before the data directory (which holds everything else)
is known. VIDEO_FACTORY_DATA_DIR still wins over it, so Docker is unchanged.

Switching never happens live. `data_dir` is resolved once at startup and handed
to every part of the engine, so a switch writes the pointer and the app restarts.
A move copies, never deletes: the old folder stays until the user removes it.

Stdlib plus psutil, like core/paths.py, so none of it needs the pipeline.
"""

import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from core import host

POINTER_NAME = "storage.json"

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Set once the library has been switched (or a move is under way) and cleared
# only by a restart. While set, the engine claims no new work: anything it
# wrote to the old database after the copy was taken would be lost when the app
# comes back up on the new one.
hold = threading.Event()

# Folders inside the data dir, in the order the UI lists them. Anything else
# (state.db, credentials, branding, logs...) is reported as "other".
_KNOWN = ("downloads", "clips", "transcripts", "previews", "posters", "branding", "logs")

# Inside the data dir but not part of a library that moves:
#  * models/ is the bundled Ollama's model store. The desktop app points Ollama
#    at <default data dir>/models whatever the library location is, so moving
#    it would only copy gigabytes the app would not read.
#  * previews/ and posters/ are rebuilt on demand.
_NOT_COPIED = ("models", "previews", "posters")

# Free space to keep on the target beyond the library itself: the copy of the
# database, filesystem overhead, and room for the next download.
_MARGIN_BYTES = 2 * 1024**3


# ---- the pointer -------------------------------------------------------------


def pointer_path() -> Path:
    """Installed builds keep the pointer in the per-user app folder. A checkout
    keeps it beside the code (gitignored), so moving a developer's library never
    repoints an installed copy of the app on the same machine; that collision is
    the one secrets._account() already guards against."""
    if getattr(sys, "frozen", False):
        return host.user_data_root() / POINTER_NAME
    return _REPO_ROOT / f".{POINTER_NAME}"


def read_pointer() -> Path | None:
    """The library location the user chose, or None for the default."""
    try:
        raw = json.loads(pointer_path().read_text(encoding="utf-8")).get("data_dir")
    except (OSError, ValueError, AttributeError):
        return None
    if not raw or not Path(raw).is_absolute():
        return None
    return Path(raw)


def write_pointer(data_dir: Path | None) -> None:
    """Remember `data_dir`, or forget the choice (back to the default) for None."""
    target = pointer_path()
    if data_dir is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"data_dir": str(data_dir)}), encoding="utf-8")
    os.replace(tmp, target)


def unavailable_reason(path: Path, must_exist: bool = False) -> str | None:
    """Why `path` cannot be used right now, or None if it can.

    The case this exists for is an unplugged drive. On Windows a missing drive
    is obvious, but on macOS and Linux `mkdir -p /Volumes/Archive/x` happily
    CREATES /Volumes/Archive on the system disk, and the app would start a
    fresh empty library there without a word. So a location is only usable if
    everything above its last folder already exists: the library folder itself
    may be new (first run), its drive may not.

    `must_exist` is for a location that was already chosen and used: its folder
    has to be there itself. An unmounted /mnt/data is an empty directory, so its
    parent exists and the lenient rule above would happily build a library on
    the system disk.
    """
    path = Path(path)
    if must_exist and not path.is_dir():
        return "The folder is not there. Its drive may be disconnected."
    if path.exists():
        if not path.is_dir():
            return "It is not a folder."
        return None if os.access(path, os.W_OK) else "This account cannot write to it."
    parent = path.parent
    if parent == path or not parent.exists():
        return "The drive or folder it sits on is not connected."
    return None if os.access(parent, os.W_OK) else "This account cannot write there."


# ---- drives ------------------------------------------------------------------


def _usage(path: str) -> tuple[int, int, int] | None:
    try:
        u = shutil.disk_usage(path)
        return u.total, u.used, u.free
    except OSError:
        return None  # an empty card reader or optical drive raises on Windows


def _label(mount: str) -> str:
    """A human name for a volume: the Windows volume label, else the mount folder."""
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(261)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
                ctypes.c_wchar_p(mount), buf, 261, None, None, None, None, 0
            )
            if ok and buf.value:
                return buf.value
        except Exception:
            pass
        return ""
    p = Path(mount)
    return "" if mount == "/" else p.name


def _kind(part, mount: str) -> dict:
    """removable / network flags from what the OS reports about the mount."""
    opts = (getattr(part, "opts", "") or "").lower()
    fstype = (getattr(part, "fstype", "") or "").lower()
    network = fstype in {"nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "sshfs", "fuse.sshfs", "webdav"}
    if sys.platform == "win32":
        removable = "removable" in opts
        try:
            import ctypes

            dt = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(mount))  # type: ignore[attr-defined]
            removable = removable or dt == 2
            network = network or dt == 4
        except Exception:
            pass
    else:
        removable = mount.startswith(("/Volumes/", "/media/", "/run/media/"))
    return {"removable": removable, "network": network}


def volumes() -> list[dict]:
    """Every drive the app could keep the library on, with its free space."""
    import psutil

    out, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        mount = part.mountpoint
        if mount in seen:
            continue
        seen.add(mount)
        # Windows lists CD drives with no disc and card readers with no card.
        if sys.platform == "win32" and "cdrom" in (part.opts or "").lower():
            continue
        usage = _usage(mount)
        if usage is None:
            continue
        total, used, free = usage
        out.append({
            "mount": mount,
            "label": _label(mount),
            "device": part.device,
            "filesystem": part.fstype,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "writable": os.access(mount, os.W_OK),
            **_kind(part, mount),
        })
    return out


def volume_for(path: Path) -> dict | None:
    """The volume `path` is on: the volume with the longest matching mount."""
    path = Path(path)
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        resolved = str(probe.resolve())
    except OSError:
        return None
    best = None
    for v in volumes():
        mount = v["mount"]
        if _is_under(resolved, mount) and (best is None or len(mount) > len(best["mount"])):
            best = v
    return best


def _is_under(path: str, mount: str) -> bool:
    a, b = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(mount))
    return a == b or a.startswith(b.rstrip("\\/") + os.sep)


# ---- what is in the library --------------------------------------------------


def _tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass  # vanished mid-scan
    return total


def breakdown(data_dir: Path) -> dict:
    """Bytes per top-level folder of the library, biggest first.

    `total_bytes` is the library; `models_bytes` (the AI models, which stay put)
    is reported apart and `move_bytes` is what a move would actually copy.
    """
    data_dir = Path(data_dir)
    parts: dict[str, int] = {}
    other = 0
    models = 0
    if data_dir.is_dir():
        for entry in data_dir.iterdir():
            size = _tree_bytes(entry) if entry.is_dir() else _safe_size(entry)
            if entry.is_dir() and entry.name == "models":
                models = size
            elif entry.is_dir() and entry.name in _KNOWN:
                parts[entry.name] = size
            else:
                other += size
    if other:
        parts["other"] = other
    rows = [{"name": k, "bytes": v} for k, v in sorted(parts.items(), key=lambda kv: -kv[1])]
    total = sum(parts.values())
    skipped = sum(parts.get(n, 0) for n in _NOT_COPIED)
    return {
        "parts": rows,
        "total_bytes": total,
        "models_bytes": models,
        "move_bytes": total - skipped,
    }


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def describe(path: Path) -> dict:
    """The location and the drive under it, in one answer."""
    path = Path(path)
    vol = volume_for(path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "problem": unavailable_reason(path),
        "volume": vol,
    }


# ---- can we move there? ------------------------------------------------------


def _same_or_inside(child: Path, parent: Path) -> bool:
    return _is_under(str(child.resolve()), str(parent.resolve()))


def check_target(src: Path, dst: Path, library_bytes: int, mode: str) -> list[str]:
    """Reasons `dst` cannot take the library, in plain words. Empty means go ahead.

    `mode` is "move" (copy the library there), "fresh" (start empty there) or
    "existing" (use a library already in that folder). Space and overlap only
    matter when copying.
    """
    problems: list[str] = []
    src, dst = Path(src), Path(dst)
    if not dst.is_absolute():
        return ["Choose a full folder path."]

    bad = unavailable_reason(dst)
    if bad:
        problems.append(bad)
        return problems

    if dst.exists() and _same_or_inside(dst, src) and _same_or_inside(src, dst):
        problems.append("That is the folder the library is already in.")
        return problems

    if mode == "existing":
        if not (dst / "state.db").is_file():
            problems.append("There is no Video Factory library in that folder (no state.db).")
        return problems

    if mode == "move":
        if _same_or_inside(dst, src):
            problems.append("The new folder is inside the current one. Pick a folder outside it.")
        if _same_or_inside(src, dst):
            problems.append("The current library is inside that folder. Pick a different folder.")
        if dst.is_dir() and any(dst.iterdir()):
            problems.append("That folder is not empty. Pick an empty one, or a new folder.")
        probe = dst
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        usage = _usage(str(probe))
        if usage and usage[2] < library_bytes + _MARGIN_BYTES:
            need = (library_bytes + _MARGIN_BYTES) / 1e9
            have = usage[2] / 1e9
            problems.append(f"Not enough free space there: needs about {need:.1f} GB, has {have:.1f} GB.")
    return problems


# ---- rewriting stored paths --------------------------------------------------


def _variants(old: str, new: str) -> list[tuple[str, str]]:
    """The ways one folder prefix appears inside stored text.

    Plain, forward-slashed (yt-dlp and ffmpeg both produce these), and
    JSON-escaped, which is what a path looks like inside a JSON column on
    Windows (every backslash doubled).
    """
    pairs = [(old, new)]
    if "\\" in old or "\\" in new:
        pairs.append((old.replace("\\", "/"), new.replace("\\", "/")))
        pairs.append((json.dumps(old)[1:-1], json.dumps(new)[1:-1]))
    seen, out = set(), []
    for pair in pairs:
        if pair[0] and pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def rewrite_paths(db_path: Path, old: Path, new: Path) -> int:
    """Point every stored path under `old` at `new`. Returns how many values changed.

    Clips, formats, compilations and job logs all store absolute paths, and the
    compilation endpoints refuse any path outside the data dir, so a library
    that moved without this would hit hard failures, not cosmetic ones.

    Every TEXT column of every table is swept, rather than a hand-kept list of
    columns: paths also sit inside JSON columns, and a list is exactly the
    thing that goes stale when someone adds a table. A match needs the whole
    absolute prefix, so ordinary text is not touched. One transaction: it all
    changes or none of it does. Case-insensitive on Windows, where C:\\Data and
    c:\\data are the same folder.
    """
    flags = re.IGNORECASE if sys.platform == "win32" else 0
    pairs = _variants(str(old), str(new))
    # The prefix must end at a separator, a quote or the end of the value, so
    # moving ".../data" does not also rewrite ".../data2/...".
    boundary = r"(?=[\\/\"']|$)"
    patterns = [(re.compile(re.escape(a) + boundary, flags), b) for a, b in pairs]
    changed = 0
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            for col in cols:
                for needle, _ in pairs:
                    rows = conn.execute(
                        f'SELECT rowid, "{col}" FROM "{table}" '
                        f'WHERE typeof("{col}")=\'text\' AND instr(lower("{col}"), lower(?)) > 0',
                        (needle,),
                    ).fetchall()
                    for rowid, value in rows:
                        fixed = value
                        for pat, repl in patterns:
                            fixed = pat.sub(lambda _m, r=repl: r, fixed)
                        if fixed != value:
                            conn.execute(f'UPDATE "{table}" SET "{col}"=? WHERE rowid=?', (fixed, rowid))
                            changed += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return changed


# ---- credentials -------------------------------------------------------------


def carry_credentials(old: Path, new: Path, db_path: Path) -> None:
    """Re-key stored credentials for the new location.

    On Windows and Linux they are files under the data dir and the copy already
    brought them. macOS keeps them in the Keychain under an account NAME that
    includes the data dir path, so a moved library would silently look
    signed-out everywhere. Copy each known credential across to the new name.
    """
    from core import secrets

    if secrets._keyring() is None:
        return

    names = ["youtube_token", "youtube_client", "uploadpost_key", "woopsocial_key"]
    try:
        from publish.youtube_shorts import token_name_for
        from server import youtube_service

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (youtube_service.ACCOUNTS_KEY,)
            ).fetchone()
        finally:
            conn.close()
        for account in json.loads(row[0]) if row else []:
            if isinstance(account, dict) and account.get("id"):
                names.append(token_name_for(account["id"]))
    except Exception:
        pass
    try:
        from llm.providers import catalog, keys

        names += [keys._name(p) for p in catalog.PROVIDERS]
    except Exception:
        pass

    for name in dict.fromkeys(names):
        payload = secrets.load(old, name)
        if payload:
            secrets.save(new, name, payload)


# ---- the move ----------------------------------------------------------------


class MoveState:
    """Progress of the one move that can be running. Polled by the UI."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.status = "idle"        # idle | running | done | error
        self.phase = ""
        self.done_bytes = 0
        self.total_bytes = 0
        self.error = ""
        self.new_path = ""
        self.old_path = ""
        self.rewritten = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status, "phase": self.phase,
                "done_bytes": self.done_bytes, "total_bytes": self.total_bytes,
                "error": self.error, "new_path": self.new_path,
                "old_path": self.old_path, "rewritten": self.rewritten,
            }

    def set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)


def _copy_library(src: Path, dst: Path, on_bytes: Callable[[int], None]) -> dict[str, int]:
    """Copy the tree and return what was written (relative path -> size).

    The live database goes through SQLite's backup API, which is consistent while
    the app is using it; copying state.db and its -wal file by hand can produce
    a database that is missing recent writes.

    The returned manifest, not the source, is what _verify checks against: logs
    and downloads keep growing while a copy that can take hours is running, so a
    comparison with the live source would fail on any real library.
    """
    manifest: dict[str, int] = {}
    dst.mkdir(parents=True, exist_ok=True)
    for dirpath, dirs, files in os.walk(src):
        rel = Path(dirpath).relative_to(src)
        if rel == Path("."):
            dirs[:] = [d for d in dirs if d not in _NOT_COPIED]
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            if rel == Path(".") and name.startswith("state.db"):
                continue  # handled below
            s, d = Path(dirpath) / name, dst / rel / name
            try:
                shutil.copy2(s, d)
            except FileNotFoundError:
                continue  # a scratch file vanished while we walked
            size = d.stat().st_size
            manifest[str(rel / name)] = size
            on_bytes(size)
    db = src / "state.db"
    if db.is_file():
        s_conn = sqlite3.connect(db)
        d_conn = sqlite3.connect(dst / "state.db")
        try:
            s_conn.backup(d_conn)
        finally:
            d_conn.close()
            s_conn.close()
        on_bytes(_safe_size(dst / "state.db"))
    return manifest


def _verify(manifest: dict[str, int], dst: Path) -> str | None:
    """A copy that is missing or truncated must never become the library."""
    missing = 0
    for rel, size in manifest.items():
        d = dst / rel
        if not d.is_file() or d.stat().st_size != size:
            missing += 1
    if missing:
        return f"{missing} file(s) did not copy correctly."
    try:
        conn = sqlite3.connect(dst / "state.db")
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return f"The copied database could not be opened: {e}"
    return None if ok == "ok" else f"The copied database failed its integrity check ({ok})."


def move_library(src: Path, dst: Path, state: MoveState) -> None:
    """Copy `src` to `dst`, repoint it, and switch. Runs on a worker thread.

    Order matters: copy, rewrite the COPY's database, verify, carry credentials,
    and only then write the pointer. Until that last step the app still runs on
    the old library, so a failure anywhere leaves nothing changed (the partial
    copy is removed). The old folder is never deleted here.
    """
    src, dst = Path(src).resolve(), Path(dst).resolve()
    created_dst = not dst.exists()
    hold.set()  # claim no new work until the app has restarted or the move failed
    state.set(status="running", phase="Copying files", done_bytes=0,
              total_bytes=breakdown(src)["move_bytes"], error="",
              new_path=str(dst), old_path=str(src), rewritten=0)
    try:
        def add(n: int) -> None:
            state.set(done_bytes=state.done_bytes + n)

        manifest = _copy_library(src, dst, add)
        state.set(phase="Updating the library's file paths")
        n = rewrite_paths(dst / "state.db", src, dst)
        state.set(phase="Checking the copy", rewritten=n)
        problem = _verify(manifest, dst)
        if problem:
            raise RuntimeError(problem)
        carry_credentials(src, dst, dst / "state.db")
        write_pointer(dst)
        state.set(status="done", phase="Done. Restart to use the new location.")
    except Exception as e:
        # Leave the old library as the live one and clean up our half-copy,
        # but only what we created: never remove a folder that was already there.
        if created_dst:
            shutil.rmtree(dst, ignore_errors=True)
        hold.clear()
        state.set(status="error", phase="", error=str(e) or e.__class__.__name__)
