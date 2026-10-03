"""Exports: finished files go to folders and clouds, once, whole, and a
failure is a row you can retry rather than a file that looks finished."""

import json
import subprocess
from pathlib import Path

import pytest

from core.state import StateDB
from delivery import cloud
from delivery import store as exports


class Broadcaster:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    d = StateDB(path)
    exports.ensure_schema(d)
    d.close()
    return lambda: StateDB(path)


def add_clip(db, tmp_path, name="a", size=3 * 1024 * 1024 + 7, title="Best Moment!"):
    f = tmp_path / f"{name}.mp4"
    f.write_bytes(b"x" * size)
    d = db()
    try:
        d.upsert_video("v1", title="Source video", channel_name="Ann")
        clip_id = d.add_clip("v1", 1.0, 9.0, 90, "hook")
        d.conn.execute("UPDATE clips SET path = ?, title = ? WHERE id = ?", (str(f), title, clip_id))
        d.conn.commit()
    finally:
        d.close()
    return clip_id, f


def run_all(db, **kwargs):
    worker = exports.TransferWorker(db, broadcaster=Broadcaster(), **kwargs)
    while worker.step():
        pass
    return worker


def test_a_clip_is_copied_whole_under_a_readable_name_and_marked_saved(db, tmp_path):
    clip_id, f = add_clip(db, tmp_path)
    out = tmp_path / "out"
    d = db()
    exports.queue_transfers(d, [{"kind": "clip", "id": clip_id}], folder=str(out))
    d.close()
    run_all(db)
    copied = out / "best-moment.mp4"
    assert copied.read_bytes() == f.read_bytes()
    assert not list(out.glob("*.part"))
    d = db()
    t = exports.transfers(d)[0]
    assert t["state"] == "done" and t["percent"] == 100 and t["result"] == str(copied)
    assert d.get_clip(clip_id)["exported_at"]
    d.close()


def test_the_same_name_twice_is_kept_side_by_side(db, tmp_path):
    clip_id, _ = add_clip(db, tmp_path)
    out = tmp_path / "out"
    d = db()
    exports.queue_transfers(d, [{"kind": "clip", "id": clip_id}] * 2, folder=str(out))
    d.close()
    run_all(db)
    assert sorted(p.name for p in out.iterdir()) == ["best-moment-2.mp4", "best-moment.mp4"]


def test_a_missing_file_fails_with_a_reason_and_can_be_retried(db, tmp_path):
    clip_id, f = add_clip(db, tmp_path)
    d = db()
    [tid] = exports.queue_transfers(d, [{"kind": "clip", "id": clip_id}], folder=str(tmp_path / "out"))
    d.close()
    moved = f.with_suffix(".bak")
    f.rename(moved)
    run_all(db)
    d = db()
    t = exports.transfers(d)[0]
    assert t["state"] == "failed" and "isn't on this PC" in t["error"]
    moved.rename(f)
    assert exports.retry_transfer(d, tid)
    d.close()
    run_all(db)
    d = db()
    assert exports.transfers(d)[0]["state"] == "done"
    d.close()


def test_a_restart_mid_copy_sends_it_again(db, tmp_path):
    clip_id, _ = add_clip(db, tmp_path)
    d = db()
    [tid] = exports.queue_transfers(d, [{"kind": "clip", "id": clip_id}], folder=str(tmp_path / "out"))
    d.conn.execute("UPDATE export_transfers SET state = 'sending', sent = 99 WHERE id = ?", (tid,))
    d.conn.commit()
    d.close()
    worker = exports.TransferWorker(db, broadcaster=Broadcaster())
    worker.recover()
    while worker.step():
        pass
    d = db()
    assert exports.transfers(d)[0]["state"] == "done"
    d.close()


def test_new_clips_go_to_auto_destinations_once(db, tmp_path):
    clip_id, _ = add_clip(db, tmp_path)
    d = db()
    auto = exports.add_destination(d, name="Auto", kind="folder", target=str(tmp_path / "auto"),
                                   auto_clips=True)
    exports.add_destination(d, name="Manual", kind="folder", target=str(tmp_path / "manual"))
    job_id = d.add_job("process", json.dumps({"url": "x"}), video_id="v1")
    job = d.get_job(job_id)
    assert exports.after_job(d, job, {"url": "x"}) == 1
    d.close()
    run_all(db)
    d = db()
    # Sent already: running the same video again does not send it twice.
    assert exports.after_job(d, job, {"url": "x"}) == 0
    assert exports.files(d, "clips")[0]["sent_to"] == [auto]
    d.close()
    assert len(list((tmp_path / "auto").iterdir())) == 1
    assert not (tmp_path / "manual").exists()


def test_an_import_sends_nothing(db, tmp_path):
    add_clip(db, tmp_path)
    d = db()
    exports.add_destination(d, name="Auto", kind="folder", target=str(tmp_path / "auto"), auto_clips=True)
    job = d.get_job(d.add_job("process", json.dumps({"url": "x"}), video_id="v1"))
    assert exports.after_job(d, job, {"url": "x", "import_only": True}) == 0
    d.close()


def test_a_compilation_render_sends_every_format(db, tmp_path):
    from compilation import store as compilations

    d = db()
    comp = compilations.create(d, "Best of")
    outs = {}
    for canvas in ("9:16", "16:9"):
        p = tmp_path / f"Best of [1] v1 {canvas.replace(':', 'x')}.mp4"
        p.write_bytes(b"v")
        outs[canvas] = str(p)
    compilations.record_render(d, comp, 1, {}, outs)
    exports.add_destination(d, name="Cloud", kind="folder", target=str(tmp_path / "c"),
                            auto_compilations=True)
    job = d.get_job(d.add_job("compile", json.dumps({"compilation_id": comp})))
    assert exports.after_job(d, job, {"compilation_id": comp}) == 2
    listed = exports.files(d, "compilations")
    assert {f["canvas"] for f in listed} == {"9:16", "16:9"}
    d.close()


# ---- cloud ---------------------------------------------------------------------------


def test_sync_folders_are_found_where_each_client_keeps_them(tmp_path):
    home = tmp_path / "home"
    (home / "OneDrive").mkdir(parents=True)
    (home / "Box").mkdir()
    dropbox = tmp_path / "Dropbox (Personal)"
    dropbox.mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text(json.dumps({"personal": {"path": str(dropbox)}}))
    found = cloud.sync_folders(home=home, env={"APPDATA": str(appdata), "OneDrive": str(home / "OneDrive")},
                               platform="linux")
    got = {(f.provider, Path(f.path).name) for f in found}
    assert got == {("onedrive", "OneDrive"), ("dropbox", "Dropbox (Personal)"), ("box", "Box")}


def test_rclone_remotes_are_listed_with_their_types():
    def run(cmd):
        out = "rclone v1.68.0\n" if cmd[1] == "version" else "r2:           s3\nbackblaze: b2\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    info = cloud.rclone_info(binary="rclone", run=run)
    assert info["available"] and info["version"] == "rclone v1.68.0"
    assert info["remotes"] == [{"name": "r2", "type": "s3"}, {"name": "backblaze", "type": "b2"}]
    assert cloud.rclone_info(binary="")["available"] is False


@pytest.mark.parametrize("remote, path", [("bad;name", "x"), ("r2", "../escape")])
def test_rclone_targets_refuse_what_is_not_a_remote_path(remote, path):
    with pytest.raises(ValueError):
        cloud.rclone_target(remote, path)


def test_rclone_upload_reports_progress_and_where_it_went(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    seen = []

    class Proc:
        stderr = iter(["Transferred: 1 MiB / 4 MiB, 25%, ...\n", "Transferred: 4 MiB / 4 MiB, 100%\n"])

        def wait(self):
            return 0

    dest = cloud.rclone_copy(src, "r2:videos", "a.mp4", binary="rclone", on_fraction=seen.append,
                             popen=lambda *a, **k: Proc())
    assert dest == "r2:videos/a.mp4" and seen == [0.25, 1.0]


def test_a_folder_that_cannot_be_written_says_why(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert cloud.check_folder(tmp_path / "ok") == ""
    assert cloud.check_folder(blocker / "sub") != ""


def test_the_routes_add_a_destination_send_to_it_and_refuse_a_bad_one(db, tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import exports_api

    clip_id, _ = add_clip(db, tmp_path)
    app = FastAPI()
    worker = exports_api.install(app, db=db, broadcaster=Broadcaster(), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")

    dest = c.post("/exports/destinations", json={"kind": "cloud_folder", "provider": "onedrive",
                                                  "target": str(tmp_path / "OneDrive"),
                                                  "path": "Video Factory"}).json()
    assert dest["target"] == str(tmp_path / "OneDrive" / "Video Factory")
    assert c.post("/exports/destinations", json={"kind": "folder", "target": "relative/path"}).status_code == 400
    assert c.post("/exports/destinations", json={"kind": "folder", "target": str(tmp_path),
                                                 "path": "../out"}).status_code == 400

    assert c.post("/exports/send", json={"items": [{"kind": "clip", "id": clip_id}],
                                         "destination_id": dest["id"]}).status_code == 200
    while worker.step():
        pass
    [t] = c.get("/exports/transfers").json()
    assert t["state"] == "done" and t["destination_name"] == "OneDrive"
    assert c.get("/exports/files", params={"kind": "clips"}).json()[0]["sent_to"] == [dest["id"]]
    assert c.post("/exports/send", json={"items": [{"kind": "clip", "id": 999}],
                                         "folder": str(tmp_path)}).status_code == 409
    summary = c.get("/exports/summary").json()
    assert set(summary["folders"]) == {"downloads", "clips", "compilations", "transcripts"}
