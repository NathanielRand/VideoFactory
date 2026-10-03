"""What is happening to a clip right now, and a re-render that keeps the clip
in place until its replacement exists."""

import json
from datetime import datetime, timedelta

import pytest

from core.state import StateDB


def _job(db, type_, payload, status, updated=None, error=""):
    jid = db.add_job(type_, json.dumps(payload))
    db.conn.execute("UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    (status, error, updated or datetime.now().isoformat(timespec="seconds"), jid))
    db.conn.commit()
    return jid


def test_queued_running_and_failed_jobs_show_and_finished_ones_do_not(db):
    q = _job(db, "render", {"clip_id": 1}, "queued")
    r = _job(db, "render", {"clip_id": 2}, "running")
    f = _job(db, "render", {"clip_id": 3}, "failed", error="ffmpeg exploded")
    _job(db, "render", {"clip_id": 4}, "done")
    work = db.clip_work([1, 2, 3, 4, 5])
    assert work[1] == {"state": "queued", "kind": "render", "job_id": q, "error": ""}
    assert work[2]["state"] == "running" and work[2]["job_id"] == r
    assert work[3]["state"] == "failed" and work[3]["error"] == "ffmpeg exploded" and work[3]["job_id"] == f
    assert 4 not in work and 5 not in work


def test_only_the_newest_job_for_a_clip_counts(db):
    _job(db, "render", {"clip_id": 1}, "failed", error="old")
    _job(db, "render", {"clip_id": 1}, "done")               # retried and it worked
    assert db.clip_work([1]) == {}
    _job(db, "render", {"clip_id": 2}, "done")
    _job(db, "render", {"clip_id": 2}, "running")            # now being redone
    assert db.clip_work([2])[2]["state"] == "running"


def test_an_old_failure_stops_flagging_the_card(db):
    long_ago = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    _job(db, "render", {"clip_id": 1}, "failed", updated=long_ago, error="x")
    assert db.clip_work([1]) == {}


def test_translations_and_format_jobs_reach_every_clip_they_name(db):
    _job(db, "translate", {"clip_ids": [1, 2]}, "running")
    _job(db, "variants", {"clip_id": 3, "canvases": []}, "queued")
    work = db.clip_work([1, 2, 3])
    assert work[1]["kind"] == work[2]["kind"] == "translate" and work[1]["state"] == "running"
    assert work[3]["kind"] == "formats" and work[3]["state"] == "queued"
    _job(db, "process", {"url": "x", "clip_id": 9}, "running")     # video processing is not a clip job
    assert 9 not in db.clip_work([9])


def test_no_ids_no_work(db):
    assert db.clip_work([]) == {}


# ---- the render job keeps the clip until the new one exists ---------------------------


def _worker_setup(tmp_path):
    from core.models import ClipCandidate  # noqa: F401
    from server.jobs import Worker

    data = tmp_path
    (data / "downloads").mkdir()
    (data / "downloads" / "v1.mp4").write_bytes(b"x")
    (data / "transcripts").mkdir()
    (data / "transcripts" / "v1.json").write_text(json.dumps(
        {"segments": [{"start": 0.0, "end": 5.0, "text": "hi."}]}))
    db = StateDB(data / "state.db")
    db.upsert_video("v1", title="T", channel_name="c", duration=100)
    clip_id = db.add_clip("v1", 1.0, 5.0, 70, "hook", path=str(data / "old.mp4"), title="Keep me")
    worker = Worker({"paths": {"data_dir": str(data)}, "llm": {}, "clips": {}})
    return worker, db, clip_id


def test_the_old_clip_is_still_there_while_the_render_runs(tmp_path, monkeypatch):
    import core.pipeline as pipeline

    worker, db, clip_id = _worker_setup(tmp_path)
    seen = {}

    def fake_render(*a, **k):
        seen["during"] = db.get_clip(clip_id) is not None      # the card must still exist
        return tmp_path / "new.mp4", "{}"

    def fake_register(db_, video_id, candidate, path, meta, opts, config=None):
        seen["at_register"] = db.get_clip(clip_id) is None     # replaced only now
        db_.add_clip(video_id, candidate.start, candidate.end, candidate.score, candidate.hook,
                     path=str(path), title="new")
        return object()

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    monkeypatch.setattr(pipeline, "_register_clip", fake_register)
    worker._rerender_clip(db, {"clip_id": clip_id})
    assert seen == {"during": True, "at_register": True}


def test_a_failed_render_no_longer_costs_the_clip(tmp_path, monkeypatch):
    import core.pipeline as pipeline

    worker, db, clip_id = _worker_setup(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(pipeline, "_render_files", boom)
    with pytest.raises(RuntimeError):
        worker._rerender_clip(db, {"clip_id": clip_id})
    assert db.get_clip(clip_id)["title"] == "Keep me"


# ---- through the real app ---------------------------------------------------------------


def test_a_queued_rerender_shows_on_the_clip_through_the_api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.upsert_video("v1", title="T", channel_name="c", duration=100)
    clip_id = db.add_clip("v1", 1.0, 5.0, 70, "hook", path=str(tmp_path / "c.mp4"), title="A clip")
    other = db.add_clip("v1", 10.0, 15.0, 70, "hook", path=str(tmp_path / "d.mp4"), title="Another")
    client = TestClient(app, base_url="http://127.0.0.1")

    assert client.get(f"/clip-work?ids={clip_id},{other}").json() == {}          # nothing going on: Ready
    job = client.post(f"/clips/{clip_id}/render", json={"start": 0.5}).json()["job_id"]
    got = client.get(f"/clip-work?ids={clip_id},{other},junk,").json()
    assert got == {str(clip_id): {"state": "queued", "kind": "render", "job_id": job, "error": ""}}
    db.conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job,))
    db.conn.commit()
    assert client.get(f"/clip-work?ids={clip_id}").json()[str(clip_id)]["state"] == "running"
    db.conn.execute("UPDATE jobs SET status = 'done' WHERE id = ?", (job,))
    db.conn.commit()
    assert client.get(f"/clip-work?ids={clip_id}").json() == {}                  # finished: badge clears
    assert client.get("/clip-work").json() == {}
    db.conn.close()


def test_a_rerender_moves_the_thumbnail_to_the_new_clip_and_never_asks_for_another(tmp_path, monkeypatch):
    import core.pipeline as pipeline

    worker, db, clip_id = _worker_setup(tmp_path)
    folder = tmp_path / "thumbnails"
    folder.mkdir()
    (folder / f"clip_{clip_id}_chosen.jpg").write_bytes(b"jpg")
    (folder / f"clip_{clip_id}_design.json").write_text("{}", encoding="utf-8")

    def fake_render(*a, **k):
        return tmp_path / "new.mp4", "{}"

    def fake_register(db_, video_id, candidate, path, meta, opts, config=None):
        db_.add_clip(video_id, candidate.start, candidate.end, candidate.score, candidate.hook,
                     path=str(path), title="new")
        return object()

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    monkeypatch.setattr(pipeline, "_register_clip", fake_register)
    worker._rerender_clip(db, {"clip_id": clip_id})               # same start: the design stays too
    new_id = db.conn.execute("SELECT id FROM clips WHERE video_id = 'v1'").fetchone()["id"]
    assert (folder / f"clip_{new_id}_chosen.jpg").exists() and (folder / f"clip_{new_id}_design.json").exists()
    assert (folder / f"clip_{new_id}_auto.done").exists()
    assert not (folder / f"clip_{clip_id}_chosen.jpg").exists()

    # Trimmed: the picture follows, the design (laid out on the old frames) does not.
    worker._rerender_clip(db, {"clip_id": new_id, "start": 2.0})
    newer = db.conn.execute("SELECT id FROM clips WHERE video_id = 'v1'").fetchone()["id"]
    assert (folder / f"clip_{newer}_chosen.jpg").exists() and not (folder / f"clip_{newer}_design.json").exists()
