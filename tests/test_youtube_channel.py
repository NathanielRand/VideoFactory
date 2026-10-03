"""Where each video stands on YouTube (publish/youtube_status.py) and the
Publish page's list of the channel's videos (/youtube/channel-videos)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.state import StateDB
from publish.youtube_status import seconds, state_of, summarize, video_id_from

NOW = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def item(vid="abcdefghijk", upload="processed", processing="succeeded", privacy="public", **status):
    return {
        "id": vid,
        "snippet": {"title": "T", "publishedAt": "2026-09-20T10:00:00Z",
                    "thumbnails": {"medium": {"url": "https://i.ytimg.com/x.jpg"}}},
        "status": {"uploadStatus": upload, "privacyStatus": privacy, **status},
        "processingDetails": {"processingStatus": processing},
        "statistics": {"viewCount": "1200", "likeCount": "80"},
        "contentDetails": {"duration": "PT45S"},
    }


@pytest.mark.parametrize(
    "kwargs, state",
    [
        ({}, "live"),
        ({"privacy": "unlisted"}, "unlisted"),
        ({"privacy": "private"}, "private"),
        ({"privacy": "private", "publishAt": "2026-10-01T18:00:00Z"}, "scheduled"),
        # A publish time already passed: it is whatever it is now, not "scheduled".
        ({"privacy": "private", "publishAt": "2026-09-01T18:00:00Z"}, "private"),
        ({"upload": "uploaded", "processing": "processing"}, "processing"),
        ({"upload": "rejected", "rejectionReason": "duplicate"}, "rejected"),
        ({"upload": "failed", "failureReason": "codec"}, "failed"),
        ({"processing": "terminated"}, "failed"),
        ({"upload": "deleted"}, "deleted"),
    ],
)
def test_states(kwargs, state):
    assert state_of(item(**kwargs), NOW)[0] == state


def test_reasons_are_readable():
    assert state_of(item(upload="rejected", rejectionReason="duplicate"), NOW)[1] == (
        "Rejected for it duplicates another video."
    )
    assert "codec" in state_of(item(upload="failed", failureReason="codec"), NOW)[1]


def test_summary_counts_and_hidden_counts():
    v = summarize(item(), NOW)
    assert (v["views"], v["likes"], v["comments"]) == (1200, 80, None)  # comments hidden
    assert v["short"] and v["duration"] == 45 and v["thumbnail"].endswith("x.jpg")
    assert v["studio_url"].endswith("/abcdefghijk/edit")


def test_durations_and_ids():
    assert seconds("PT1H2M3S") == 3723 and seconds("P1DT1S") == 86401 and seconds("bad") == 0
    for link in ("https://www.youtube.com/watch?v=6vuPHtNb3oQ", "https://youtube.com/shorts/6vuPHtNb3oQ",
                 "https://youtu.be/6vuPHtNb3oQ", "6vuPHtNb3oQ"):
        assert video_id_from(link) == "6vuPHtNb3oQ"
    assert video_id_from("https://tiktok.com/@x/video/1") == ""


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import youtube_api
    from server import youtube_service as service

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.set_flag(service.SETTINGS_KEY, json.dumps({"enabled": True}))
    d.close()

    calls = []

    class Pub:
        def channel_videos(self, limit):
            calls.append(limit)
            return [item("aaaaaaaaaaa"), item("bbbbbbbbbbb", privacy="private",
                                                 publishAt="2099-01-01T00:00:00Z")]

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())

    class _W:
        def notify(self):
            pass

    app = FastAPI()
    youtube_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path,
                        worker=_W(), publish_worker=_W())
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path, c.calls = db_path, calls
    return c


def test_channel_videos_marks_what_this_app_made_and_learns_from_it(client):
    d = StateDB(client.db_path)
    # A WoopSocial post of clip 7, recorded with its YouTube link.
    d.record_clip_publish(7, "youtube", {"provider": "woopsocial", "state": "published",
                                         "post_url": "https://youtube.com/shorts/aaaaaaaaaaa"})
    d.close()
    got = client.get("/youtube/channel-videos").json()
    by_id = {v["video_id"]: v for v in got["videos"]}
    assert by_id["aaaaaaaaaaa"]["publish_id"] == 7 and by_id["aaaaaaaaaaa"]["state"] == "live"
    assert by_id["bbbbbbbbbbb"]["publish_id"] is None and by_id["bbbbbbbbbbb"]["state"] == "scheduled"
    # Live videos feed best times; the scheduled one has no audience yet.
    d = StateDB(client.db_path)
    rows = d.conn.execute("SELECT post_id, views FROM publish_stats").fetchall()
    d.close()
    assert [(r["post_id"], r["views"]) for r in rows] == [("aaaaaaaaaaa", 1200)]


def test_channel_videos_are_cached_briefly_unless_asked_fresh(client):
    client.get("/youtube/channel-videos")
    client.get("/youtube/channel-videos")
    assert len(client.calls) == 1
    client.get("/youtube/channel-videos", params={"fresh": "true"})
    assert len(client.calls) == 2


def test_an_unknown_channel_is_refused(client):
    assert client.get("/youtube/channel-videos", params={"channel_id": "UCnope"}).status_code == 400


def test_push_thumbnail_sends_the_saved_one_and_drops_the_cache(client, tmp_path, monkeypatch):
    from server import youtube_service as service

    sent = []

    class Pub:
        def channel_videos(self, limit):
            return [item("aaaaaaaaaaa")]

        def set_thumbnail(self, video_id, image):
            sent.append((video_id, image))

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())
    d = StateDB(client.db_path)
    d.record_clip_publish(7, "youtube", {"provider": "woopsocial", "state": "published",
                                         "post_url": "https://youtube.com/shorts/aaaaaaaaaaa"})
    d.close()
    # Nothing saved yet: refused, and nothing reaches YouTube.
    assert client.post("/youtube/videos/aaaaaaaaaaa/thumbnail", json={}).status_code == 404
    (tmp_path / "thumbnails").mkdir()
    (tmp_path / "thumbnails" / "clip_7_chosen.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    got = client.post("/youtube/videos/aaaaaaaaaaa/thumbnail", json={})
    assert got.status_code == 200 and got.json()["publish_id"] == 7
    assert sent and sent[0][0] == "aaaaaaaaaaa" and sent[0][1].endswith("clip_7_chosen.jpg")
    # A video this app did not make has no thumbnail here to send, and a bad id never gets that far.
    assert client.post("/youtube/videos/bbbbbbbbbbb/thumbnail", json={}).status_code == 400
    assert client.post("/youtube/videos/..%2Fx/thumbnail", json={}).status_code in (400, 404)


def test_unschedule_reaches_youtube_and_closes_this_apps_record(client, monkeypatch):
    from server import youtube_service as service

    gone = []

    class Pub:
        def channel_videos(self, limit):
            return []

        def unschedule(self, video_id):
            gone.append(video_id)

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())
    d = StateDB(client.db_path)
    d.record_clip_publish(-3, "youtube", {"provider": "youtube", "state": "queued", "scheduled_for": "2099-01-01T00:00:00Z",
                                          "post_url": "https://youtu.be/bbbbbbbbbbb"})
    d.close()
    assert client.post("/youtube/videos/bbbbbbbbbbb/unschedule", json={}).status_code == 200
    assert gone == ["bbbbbbbbbbb"]
    d = StateDB(client.db_path)
    row = d.clip_publishes(-3)[0]
    ledger_units = service.load_ledger(d).units
    d.close()
    assert row["state"] == "skipped" and row["scheduled_for"] == "" and ledger_units == 51


def test_delete_needs_confirmation_and_frees_the_clip_to_be_published_again(client, monkeypatch):
    from server import youtube_service as service

    gone = []

    class Pub:
        def channel_videos(self, limit):
            return []

        def delete_video(self, video_id):
            gone.append(video_id)

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="T")
    clip = d.add_clip("v1", 0, 3, 70, "hook", path="x.mp4", title="A")
    d.record_upload(clip, "bbbbbbbbbbb")
    d.close()
    assert client.post("/youtube/videos/bbbbbbbbbbb/delete", json={}).status_code == 400 and not gone
    assert client.post("/youtube/videos/bbbbbbbbbbb/delete", json={"confirm": True}).status_code == 200
    assert gone == ["bbbbbbbbbbb"]
    d = StateDB(client.db_path)
    status = d.get_clip(clip)["status"]
    left = d.conn.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
    d.close()
    assert status == "rendered" and left == 0


def test_replace_queues_a_render_then_an_upload_that_names_the_old_video(client, monkeypatch, tmp_path):
    from server import youtube_service as service

    deleted = []

    class Pub:
        def credentials(self, scopes):
            return object()

        def video_details(self, video_id):
            return {
                "snippet": {"title": "Old title", "description": "Old text", "tags": ["a"], "categoryId": "24"},
                "status": {"privacyStatus": "public", "embeddable": True, "license": "youtube"},
            }

        def delete_video(self, video_id):
            deleted.append(video_id)

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="T")
    clip = d.add_clip("v1", 0, 3, 70, "hook", path="x.mp4", title="A")
    d.record_upload(clip, "bbbbbbbbbbb")
    d.close()
    got = client.post("/youtube/videos/bbbbbbbbbbb/replace", json={})
    assert got.status_code == 200 and got.json()["render_job_id"]
    d = StateDB(client.db_path)
    job = d.conn.execute("SELECT * FROM publish_jobs").fetchone()
    d.close()
    req = json.loads(job["request"])
    # The old video is named for deletion AFTER the upload, never deleted now.
    assert req["replace_video_id"] == "bbbbbbbbbbb" and not deleted
    assert req["title"] == "Old title" and req["notify_subscribers"] is False
    assert job["after_job_id"] == got.json()["render_job_id"]
    # Not one of ours, or a compilation: nothing is queued.
    assert client.post("/youtube/videos/ccccccccccc/replace", json={}).status_code == 400


def test_retire_video_frees_a_clip_and_keeps_its_records(db):
    from server import youtube_service as service

    db.upsert_video("v1", title="T")
    clip = db.add_clip("v1", 0, 3, 70, "hook", path="x.mp4", title="A")
    db.record_upload(clip, "aaaaaaaaaaa")
    db.record_clip_publish(clip, "youtube", {"provider": "youtube", "state": "published",
                                             "post_url": "https://youtu.be/aaaaaaaaaaa"})
    service.retire_video(db, "aaaaaaaaaaa", "Replaced.", forget_upload=True)
    assert db.get_clip(clip)["status"] == "rendered"
    row = db.clip_publishes(clip)[0]
    assert row["state"] == "skipped" and row["post_url"] == "https://youtu.be/aaaaaaaaaaa"


def test_update_permissions_asks_for_every_scope_including_analytics():
    from publish.oauth import scopes_for
    from publish.youtube_shorts import ANALYTICS_SCOPE, SCOPE_FULL

    assert ANALYTICS_SCOPE not in scopes_for(False) and ANALYTICS_SCOPE not in scopes_for(True)
    full = scopes_for(True, analytics=True)
    assert ANALYTICS_SCOPE in full and SCOPE_FULL in full and len(set(full)) == len(full)
