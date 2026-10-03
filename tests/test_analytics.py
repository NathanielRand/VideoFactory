from datetime import datetime, timezone

from server.analytics_api import kind_of, summarize_videos
from server.thumbnails_api import clean_copy


def video(vid, views, publish_id=None, state="live", short=False, via="manual"):
    return {
        "video_id": vid, "title": vid, "thumbnail": "", "url": "u", "state": state,
        "views": views, "likes": 1, "comments": 0, "short": short,
        "published_at": "2026-09-28T10:00:00Z", "publish_id": publish_id, "via": via, "channel": "c",
    }


def test_kinds():
    assert (kind_of(3), kind_of(-3), kind_of(None)) == ("clip", "compilation", "other")


def test_summary_splits_by_type_and_ignores_unseen():
    s = summarize_videos(
        [video("a", 100, 1, short=True), video("b", 50, -2), video("c", 10), video("d", 0, 4, state="scheduled")],
        today=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )
    assert s["totals"]["views"] == 160 and s["totals"]["videos"] == 3
    assert s["by_type"]["clip"]["views"] == 100 and s["by_type"]["compilation"]["views"] == 50
    assert s["by_format"]["short"]["videos"] == 1
    assert s["states"]["scheduled"] == 1
    assert s["top"][0]["video_id"] == "a"
    assert next(t for t in s["timeline"] if t["date"] == "2026-09-28")["views"] == 160


def test_clean_copy_drops_overlong_pieces():
    sets = clean_copy([
        {"headline": "He Lost It", "kicker": "and then this happened", "badge": "REAL", "emoji": "😱", "mood": "shock"},
        {"headline": "far too many words for a thumbnail", "kicker": "", "badge": "", "emoji": "", "mood": ""},
        {"headline": "Wait", "kicker": "one two three four five six seven", "badge": "x", "emoji": "zz", "mood": "odd"},
    ])
    assert len(sets) == 2
    assert sets[0]["emoji"] == "😱" and sets[0]["mood"] == "shock"
    assert sets[1]["kicker"] == "" and sets[1]["emoji"] == "" and sets[1]["mood"] == "hype"


def test_summary_route_without_youtube(tmp_path):
    import pytest

    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import analytics_api

    db_path = tmp_path / "s.db"
    StateDB(db_path).close()
    app = FastAPI()
    analytics_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    got = TestClient(app, base_url="http://127.0.0.1").get("/analytics/summary").json()
    assert got["youtube"]["connected"] is False and "totals" not in got
    assert got["thumbnails"] == {"with": 0, "without": 0, "total": 0}


def test_subscribers_credit_videos_and_survive_missing_permission(tmp_path, monkeypatch):
    import json

    import pytest

    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import analytics_api
    from server import youtube_service as yt

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.set_flag(yt.SETTINGS_KEY, json.dumps({"enabled": True}))
    d.close()

    class Pub:
        def channel_videos(self, limit):
            return []

        def daily_report(self, days):
            return []

        def subscriber_count(self):
            return {"count": 1230, "hidden": False}

        def video_subscribers(self, days, limit):
            return [{"video": "aaaaaaaaaaa", "views": 900, "subscribersGained": 14, "subscribersLost": 2},
                    {"video": "bbbbbbbbbbb", "views": 5, "subscribersGained": 0, "subscribersLost": 1}]

    monkeypatch.setattr(yt, "make_publisher", lambda *a, **k: Pub())
    monkeypatch.setattr(yt, "load_accounts", lambda db, *a, **k: [{"id": "UC1", "title": "Chan"}])
    app = FastAPI()
    analytics_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    got = TestClient(app, base_url="http://127.0.0.1").get("/analytics/summary", params={"fresh": "true"}).json()
    subs = got["subscribers"]
    assert subs["total"] == 1230 and subs["days"] == 90
    # Only videos that actually brought subscribers are listed.
    assert [v["video_id"] for v in subs["top"]] == ["aaaaaaaaaaa"] and subs["top"][0]["gained"] == 14
