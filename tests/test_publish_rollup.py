"""One summary per clip, video and compilation of where its posting stands."""

from datetime import datetime, timedelta, timezone

from core.state import StateDB
from publish.rollup import item_states, video_rollups

NOW = datetime.now(timezone.utc)
LATER = (NOW + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
EARLIER = (NOW - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db(tmp_path):
    return StateDB(tmp_path / "s.db")


def _clip(d, video="v1", start=0.0):
    d.conn.execute(
        "INSERT OR IGNORE INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES (?, 'T', 'done', 'x', 'x')",
        (video,),
    )
    cur = d.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, created_at) VALUES (?, ?, ?, 5, 'x')",
        (video, start, start + 10),
    )
    d.conn.commit()
    return cur.lastrowid


def _post(d, clip, platform, state, when=""):
    d.conn.execute(
        "INSERT INTO clip_publishes (clip_id, platform, state, scheduled_for, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'x', 'x')",
        (clip, platform, state, when),
    )
    d.conn.commit()


def test_a_clip_with_nothing_posted_is_ready(tmp_path):
    d = _db(tmp_path)
    c = _clip(d)
    assert item_states(d, [c])[c]["state"] == "ready"


def test_headline_follows_the_destinations(tmp_path):
    d = _db(tmp_path)
    a, b, c, e = (_clip(d, start=i * 20.0) for i in range(4))
    _post(d, a, "tiktok", "published")
    _post(d, b, "tiktok", "published")
    _post(d, b, "instagram", "queued", LATER)
    _post(d, c, "tiktok", "queued", LATER)
    _post(d, e, "tiktok", "failed")
    got = item_states(d, [a, b, c, e])
    assert got[a]["state"] == "published"
    assert got[b]["state"] == "partial"
    assert got[c]["state"] == "scheduled" and got[c]["next_at"] == LATER
    assert got[e]["state"] == "failed"


def test_direct_youtube_upload_counts_and_a_past_time_is_published(tmp_path):
    d = _db(tmp_path)
    a, b = _clip(d, start=0.0), _clip(d, start=20.0)
    for clip, when in ((a, LATER), (b, EARLIER)):
        d.conn.execute(
            "INSERT INTO uploads (clip_id, youtube_id, uploaded_at, publish_at, state) "
            "VALUES (?, 'yt', 'x', ?, 'scheduled')",
            (clip, when),
        )
    d.conn.commit()
    got = item_states(d, [a, b])
    assert got[a]["state"] == "scheduled" and got[b]["state"] == "published"
    assert got[a]["platforms"] == ["youtube"]


def test_a_compilation_uses_its_negative_id(tmp_path):
    d = _db(tmp_path)
    d.conn.execute(
        "INSERT INTO clip_publishes (clip_id, platform, state, created_at, updated_at) "
        "VALUES (-7, 'youtube', 'published', 'x', 'x')"
    )
    d.conn.commit()
    assert item_states(d, [-7])[-7]["state"] == "published"


def test_video_stage_rolls_its_clips_up(tmp_path):
    d = _db(tmp_path)
    a, b = _clip(d, start=0.0), _clip(d, start=20.0)
    assert video_rollups(d)["v1"]["stage"] == "ready"
    _post(d, a, "tiktok", "published")
    assert video_rollups(d)["v1"]["stage"] == "partial"
    _post(d, b, "tiktok", "queued", LATER)
    assert video_rollups(d)["v1"]["stage"] == "queued"
    d.conn.execute("UPDATE clip_publishes SET state = 'published' WHERE clip_id = ?", (b,))
    d.conn.commit()
    r = video_rollups(d)["v1"]
    assert r["stage"] == "complete" and r["published"] == 2
