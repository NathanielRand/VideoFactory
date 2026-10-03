"""Flagging a clip that came out wrong keeps the reasons and what was decided."""

import json
from pathlib import Path

import pytest

from core.state import StateDB


@pytest.fixture
def client(tmp_path: Path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import flags_api

    db_path, data_dir = tmp_path / "state.db", tmp_path / "data"
    (data_dir / "transcripts").mkdir(parents=True)
    d = StateDB(db_path)
    d.conn.execute("INSERT INTO videos (video_id, title, channel_name, status, created_at, updated_at)"
                   " VALUES ('v1', 'A Stream', 'Some Streamer', 'done', 'x', 'x')")
    d.conn.commit()
    clip_id = d.add_clip("v1", 100.0, 130.0, 80, "hook",
                         scores=json.dumps({"proposed_start": 104.0, "refined_from": [104.0, 128.0]}),
                         render_opts=json.dumps({"crop": "bias_left"}))
    d.close()
    (data_dir / "transcripts" / "v1.json").write_text(json.dumps({"segments": [
        {"start": 90.0, "end": 99.0, "text": "before the clip"},
        {"start": 101.0, "end": 110.0, "text": "inside the clip"},
        {"start": 140.0, "end": 150.0, "text": "after the clip"},
    ]}), encoding="utf-8")
    app = FastAPI()
    flags_api.install(app, config={"model": "test-model"}, db=lambda: StateDB(db_path), data_dir=data_dir)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.clip_id, c.data_dir = clip_id, data_dir
    return c


def test_a_flag_keeps_reasons_and_what_the_pipeline_decided(client):
    r = client.post(f"/clips/{client.clip_id}/flag",
                    json={"reasons": ["starts_late", "wrong_person", "starts_late"], "note": " missed the question "})
    assert r.status_code == 200
    report = json.loads((client.data_dir / "flags" / str(r.json()["id"]) / "report.json").read_text(encoding="utf-8"))

    assert report["reasons"] == ["starts_late", "wrong_person"]
    assert report["note"] == "missed the question"
    assert report["crop"] == "bias_left" and report["model"] == "test-model"
    assert report["scoring"]["refined_from"] == [104.0, 128.0]
    assert report["video"]["channel"] == "Some Streamer"
    assert ">> [101.0-110.0] inside the clip" in report["transcript"]
    assert "   [90.0-99.0] before the clip" in report["transcript"]


def test_flags_are_listed_and_can_be_resolved(client):
    fid = client.post(f"/clips/{client.clip_id}/flag", json={"reasons": ["other"]}).json()["id"]
    assert [f["id"] for f in client.get("/flags", params={"status": "open"}).json()["flags"]] == [fid]
    client.post(f"/flags/{fid}/resolve")
    assert client.get("/flags", params={"status": "open"}).json()["flags"] == []


def test_bad_input_is_refused(client):
    assert client.post(f"/clips/{client.clip_id}/flag", json={"reasons": ["nonsense"]}).status_code == 400
    assert client.post(f"/clips/{client.clip_id}/flag", json={"reasons": []}).status_code == 422
    assert client.post("/clips/9999/flag", json={"reasons": ["other"]}).status_code == 404
    assert {r["group"] for r in client.get("/flags/reasons").json()["reasons"]} == {"moment", "framing", "other"}
