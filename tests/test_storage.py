"""Moving the library: the pointer, the refusals, and the stored-path rewrite.

The move is the dangerous part of storage management, so these pin down what
must never happen: a copy that quietly becomes the library while incomplete,
paths left pointing at the old folder, or a location created on a drive that
is not there.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from core import storage


@pytest.fixture
def pointer(monkeypatch, tmp_path):
    p = tmp_path / "appdata" / "storage.json"
    monkeypatch.setattr(storage, "pointer_path", lambda: p)
    return p


def _library(root: Path, old_prefix: str | None = None) -> Path:
    """A small library whose database stores absolute paths under `root`."""
    (root / "clips" / "a").mkdir(parents=True)
    (root / "downloads").mkdir()
    (root / "clips" / "a" / "c1.mp4").write_bytes(b"x" * 100)
    (root / "downloads" / "v1.mp4").write_bytes(b"y" * 300)
    prefix = old_prefix or str(root)
    conn = sqlite3.connect(root / "state.db")
    conn.executescript(
        "CREATE TABLE clips (id INTEGER PRIMARY KEY, path TEXT, hook TEXT);"
        "CREATE TABLE compilations (id INTEGER PRIMARY KEY, outputs TEXT, n INTEGER);"
    )
    conn.execute("INSERT INTO clips (path, hook) VALUES (?, ?)",
                 (str(Path(prefix) / "clips" / "a" / "c1.mp4"), "no path here"))
    conn.execute("INSERT INTO compilations (outputs, n) VALUES (?, 7)",
                 (json.dumps({"9:16": str(Path(prefix) / "clips" / "a" / "c1.mp4")}),))
    conn.commit()
    conn.close()
    return root


# ---- pointer ---------------------------------------------------------------


def test_pointer_round_trip(pointer, tmp_path):
    assert storage.read_pointer() is None
    storage.write_pointer(tmp_path / "lib")
    assert storage.read_pointer() == tmp_path / "lib"
    storage.write_pointer(None)
    assert storage.read_pointer() is None


def test_relative_or_corrupt_pointer_is_ignored(pointer):
    pointer.parent.mkdir(parents=True)
    pointer.write_text(json.dumps({"data_dir": "relative/dir"}))
    assert storage.read_pointer() is None
    pointer.write_text("not json")
    assert storage.read_pointer() is None


# ---- missing drive ---------------------------------------------------------


def test_missing_drive_is_unavailable_and_not_created(tmp_path):
    target = tmp_path / "gone-drive" / "library"
    assert storage.unavailable_reason(target) is not None
    assert not target.parent.exists()


def test_new_library_folder_on_a_real_drive_is_fine(tmp_path):
    assert storage.unavailable_reason(tmp_path / "library") is None


def test_a_file_is_not_a_location(tmp_path):
    f = tmp_path / "f"
    f.write_text("x")
    assert storage.unavailable_reason(f) is not None


# ---- refusals --------------------------------------------------------------


def test_move_refuses_target_inside_source(tmp_path):
    src = _library(tmp_path / "lib")
    problems = storage.check_target(src, src / "sub", 400, "move")
    assert any("inside" in p for p in problems)


def test_move_refuses_source_inside_target(tmp_path):
    src = _library(tmp_path / "outer" / "lib")
    problems = storage.check_target(src, tmp_path / "outer", 400, "move")
    assert problems


def test_move_refuses_non_empty_target(tmp_path):
    src = _library(tmp_path / "lib")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "something").write_text("x")
    assert any("not empty" in p for p in storage.check_target(src, dst, 400, "move"))


def test_move_refuses_when_there_is_no_room(tmp_path, monkeypatch):
    src = _library(tmp_path / "lib")
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 10**12 - 1000, 1000))
    problems = storage.check_target(src, tmp_path / "dst", 5 * 1024**3, "move")
    assert any("free space" in p for p in problems)


def test_move_allowed_when_there_is_room(tmp_path, monkeypatch):
    src = _library(tmp_path / "lib")
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 0, 10**12))
    assert storage.check_target(src, tmp_path / "dst", 400, "move") == []


def test_existing_mode_needs_a_database(tmp_path):
    src = _library(tmp_path / "lib")
    empty = tmp_path / "other"
    empty.mkdir()
    assert storage.check_target(src, empty, 0, "existing")
    other = _library(tmp_path / "real")
    assert storage.check_target(src, other, 0, "existing") == []


def test_same_folder_is_refused(tmp_path):
    src = _library(tmp_path / "lib")
    assert storage.check_target(src, src, 0, "fresh")


# ---- rewriting stored paths ------------------------------------------------


def test_rewrite_changes_plain_and_json_paths_only(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _library(old)
    changed = storage.rewrite_paths(old / "state.db", old, new)
    assert changed == 2

    conn = sqlite3.connect(old / "state.db")
    path, hook = conn.execute("SELECT path, hook FROM clips").fetchone()
    outputs, n = conn.execute("SELECT outputs, n FROM compilations").fetchone()
    conn.close()
    assert path.startswith(str(new))
    assert json.loads(outputs)["9:16"].startswith(str(new))
    assert hook == "no path here" and n == 7


def test_rewrite_handles_json_escaped_windows_paths(tmp_path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (json.dumps({"p": "C:\\Data\\clips\\a.mp4"}),))
    conn.commit()
    conn.close()

    # Exercise the variant builder directly: the match itself is case-folded on
    # Windows only, but the escaped form must be produced on any platform.
    pairs = dict(storage._variants("C:\\Data", "D:\\Lib"))
    assert "C:\\\\Data" in pairs and pairs["C:\\\\Data"] == "D:\\\\Lib"
    assert pairs["C:/Data"] == "D:/Lib"


def test_rewrite_is_all_or_nothing(tmp_path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    _library(old)
    db = old / "state.db"
    real_connect = sqlite3.connect

    class Boom:
        def __init__(self, conn):
            self._c, self._n = conn, 0

        def execute(self, sql, *a):
            if sql.startswith("UPDATE"):
                self._n += 1
                if self._n == 2:
                    raise sqlite3.OperationalError("disk full")
            return self._c.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._c, name)

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: Boom(real_connect(*a, **k)))
    with pytest.raises(sqlite3.OperationalError):
        storage.rewrite_paths(db, old, new)
    monkeypatch.undo()

    conn = real_connect(db)
    (path,) = conn.execute("SELECT path FROM clips").fetchone()
    conn.close()
    assert path.startswith(str(old))


# ---- the move --------------------------------------------------------------


def test_move_copies_repoints_and_keeps_the_old_library(pointer, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 0, 10**12))
    src = _library(tmp_path / "old")
    dst = tmp_path / "new"
    state = storage.MoveState()

    storage.move_library(src, dst, state)

    snap = state.snapshot()
    assert snap["status"] == "done", snap
    assert (dst / "clips" / "a" / "c1.mp4").read_bytes() == b"x" * 100
    assert (dst / "downloads" / "v1.mp4").stat().st_size == 300
    assert (src / "clips" / "a" / "c1.mp4").exists()          # old copy untouched
    assert storage.read_pointer() == dst.resolve()

    conn = sqlite3.connect(dst / "state.db")
    (path,) = conn.execute("SELECT path FROM clips").fetchone()
    conn.close()
    assert path.startswith(str(dst.resolve()))
    # ...and the old database still points at the old folder.
    conn = sqlite3.connect(src / "state.db")
    (old_path,) = conn.execute("SELECT path FROM clips").fetchone()
    conn.close()
    assert old_path.startswith(str(src))


def test_failed_move_changes_nothing(pointer, tmp_path, monkeypatch):
    src = _library(tmp_path / "old")
    dst = tmp_path / "new"
    monkeypatch.setattr(storage, "rewrite_paths", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    state = storage.MoveState()

    storage.move_library(src, dst, state)

    assert state.snapshot()["status"] == "error"
    assert "boom" in state.snapshot()["error"]
    assert storage.read_pointer() is None     # still on the old library
    assert not dst.exists()                    # half-copy removed


def test_incomplete_copy_is_not_accepted(pointer, tmp_path, monkeypatch):
    src = _library(tmp_path / "old")
    dst = tmp_path / "new"
    real = storage._copy_library

    def lossy(s, d, on_bytes):
        real(s, d, on_bytes)
        (d / "downloads" / "v1.mp4").unlink()

    monkeypatch.setattr(storage, "_copy_library", lossy)
    state = storage.MoveState()
    storage.move_library(src, dst, state)

    assert state.snapshot()["status"] == "error"
    assert storage.read_pointer() is None


# ---- the drives ------------------------------------------------------------


def test_breakdown_sums_known_and_other(tmp_path):
    lib = _library(tmp_path / "lib")
    got = storage.breakdown(lib)
    names = {p["name"]: p["bytes"] for p in got["parts"]}
    assert names["downloads"] == 300 and names["clips"] == 100
    assert names["other"] > 0                      # state.db
    assert got["total_bytes"] == sum(names.values())


def test_describe_reports_the_drive_under_a_path(tmp_path):
    info = storage.describe(tmp_path)
    assert info["volume"] is not None
    assert info["volume"]["total_bytes"] >= info["volume"]["free_bytes"] > 0


# ---- the second round: things only a real install trips over ---------------


def test_rewrite_does_not_touch_a_sibling_folder_with_the_same_prefix(tmp_path):
    old, sibling, new = tmp_path / "data", tmp_path / "data2", tmp_path / "new"
    _library(old)
    conn = sqlite3.connect(old / "state.db")
    conn.execute("INSERT INTO clips (path) VALUES (?)", (str(sibling / "clips" / "x.mp4"),))
    conn.commit()
    conn.close()

    storage.rewrite_paths(old / "state.db", old, new)

    conn = sqlite3.connect(old / "state.db")
    rows = [r[0] for r in conn.execute("SELECT path FROM clips ORDER BY id")]
    conn.close()
    assert rows[0].startswith(str(new))
    assert rows[1] == str(sibling / "clips" / "x.mp4")


def test_models_and_caches_are_not_copied_or_counted_as_moving(pointer, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 0, 10**12))
    src = _library(tmp_path / "old")
    (src / "models").mkdir()
    (src / "models" / "blob").write_bytes(b"m" * 5000)
    (src / "previews").mkdir()
    (src / "previews" / "p.mp4").write_bytes(b"p" * 50)

    info = storage.breakdown(src)
    assert info["models_bytes"] == 5000
    assert "models" not in {p["name"] for p in info["parts"]}
    assert info["move_bytes"] == info["total_bytes"] - 50

    dst = tmp_path / "new"
    state = storage.MoveState()
    storage.move_library(src, dst, state)

    assert state.snapshot()["status"] == "done", state.snapshot()
    assert not (dst / "models").exists()
    assert not (dst / "previews").exists()
    assert (src / "models" / "blob").exists()


def test_a_file_growing_during_the_copy_does_not_fail_the_move(pointer, tmp_path, monkeypatch):
    """Logs and downloads are written while a copy runs. Verifying against the
    live source would reject every real library."""
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 0, 10**12))
    src = _library(tmp_path / "old")
    (src / "logs").mkdir()
    log = src / "logs" / "job_1.log"
    log.write_text("start")
    real = storage._copy_library

    def copy_then_grow(s, d, on_bytes):
        manifest = real(s, d, on_bytes)
        log.write_text("start" + "x" * 1000)  # the app kept logging
        return manifest

    monkeypatch.setattr(storage, "_copy_library", copy_then_grow)
    state = storage.MoveState()
    storage.move_library(src, tmp_path / "new", state)
    assert state.snapshot()["status"] == "done", state.snapshot()


def test_the_engine_is_held_during_a_move_and_released_on_failure(pointer, tmp_path, monkeypatch):
    storage.hold.clear()
    src = _library(tmp_path / "old")
    seen = {}

    def spy(s, d, on_bytes):
        seen["held"] = storage.hold.is_set()
        raise RuntimeError("stop")

    monkeypatch.setattr(storage, "_copy_library", spy)
    storage.move_library(src, tmp_path / "new", storage.MoveState())
    assert seen["held"] is True
    assert storage.hold.is_set() is False


def test_a_successful_move_keeps_the_engine_held_until_restart(pointer, tmp_path, monkeypatch):
    storage.hold.clear()
    monkeypatch.setattr(storage, "_usage", lambda p: (10**12, 0, 10**12))
    src = _library(tmp_path / "old")
    storage.move_library(src, tmp_path / "new", storage.MoveState())
    assert storage.hold.is_set()
    storage.hold.clear()


def test_chosen_location_must_exist_at_startup(tmp_path):
    """An unmounted /mnt/data is an empty directory's worth of nothing: its
    parent exists, so only the folder itself can tell it is gone."""
    gone = tmp_path / "mnt-data"
    assert storage.unavailable_reason(gone) is None                     # fine for a NEW library
    assert storage.unavailable_reason(gone, must_exist=True) is not None  # not for a chosen one
    gone.mkdir()
    assert storage.unavailable_reason(gone, must_exist=True) is None


def test_a_checkout_keeps_its_pointer_beside_the_code_not_in_the_user_folder():
    """Moving a developer's library must not repoint an installed copy."""
    from core import host

    got = storage.pointer_path()
    assert got.parent == Path(storage.__file__).resolve().parent.parent
    assert host.user_data_root() not in got.parents
