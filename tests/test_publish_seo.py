"""Reach and search: per-platform captions, keywords, chapters, the SEO
check, best times, generated search fields, and compilations as publishable."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.state import StateDB
from publish import seo, timing

# ---- captions -----------------------------------------------------------------


def test_caption_leads_with_the_title_where_there_is_no_title_field():
    text = seo.caption_for("tiktok", title="He did WHAT", description="Watch to the end.",
                           hashtags=["gaming", "#fails"])
    assert text.startswith("He did WHAT\n\nWatch to the end.")
    assert text.endswith("#gaming #fails")


def test_youtube_caption_does_not_repeat_the_title():
    text = seo.caption_for("youtube", title="Big title", description="Body.", hashtags=[])
    assert "Big title" not in text


def test_x_caption_fits_280_and_keeps_title_and_tags():
    body = "word " * 200
    text = seo.caption_for("x", title="Hook line", description=body, hashtags=["a", "b", "c"],
                           footer="Join the Discord")
    assert len(text) <= 280
    assert text.startswith("Hook line") and text.endswith("#a #b")
    assert "Join the Discord" not in text  # the footer goes before the clip's words
    assert "…" in text


def test_hashtags_are_capped_and_deduplicated_per_platform():
    text = seo.caption_for("threads", title="t", hashtags=["One", "#one", "two"])
    assert text.endswith("#One") and "#two" not in text


# ---- keywords -----------------------------------------------------------------


def test_keywords_put_search_phrases_first_and_fit_the_budget():
    tags = seo.keywords_for(keywords=["speedrun world record", "mario 64"], creator="Some Streamer",
                            channel_keywords=["gaming clips"], hashtags=["#mario", "#Mario 64"])
    assert tags[:4] == ["speedrun world record", "mario 64", "Some Streamer", "gaming clips"]
    assert tags.count("mario 64") == 1  # the hashtag duplicate folds away
    big = seo.keywords_for(keywords=[f"phrase number {i}" for i in range(100)])
    assert sum(len(t) + 2 for t in big) <= 520


# ---- chapters and credits --------------------------------------------------------


def test_chapters_start_at_zero_and_fold_short_parts():
    text = seo.chapters([("Intro", 3), ("First fight", 40), ("Second", 30), ("Blink", 4), ("Third", 25)])
    lines = text.splitlines()
    assert lines[0] == "0:00 First fight"  # the 3 s intro takes the next part's name
    assert lines[1] == "0:43 Second"
    assert lines[2] == "1:17 Third"  # the 4 s part joined "Second"


def test_too_few_chapters_gives_none():
    assert seo.chapters([("A", 30), ("B", 30)]) == ""


def test_hour_long_timestamps():
    assert seo.timestamp(3725) == "1:02:05"


def test_credits_list_each_channel_once():
    text = seo.credits([
        {"channel": "Ann", "channel_url": "https://y/@ann"},
        {"channel": "ann", "channel_url": "https://y/@ann"},
        {"channel": "Bob"},
    ])
    assert text == "Featuring:\n• Ann https://y/@ann\n• Bob"


# ---- the check ------------------------------------------------------------------


def test_check_rewards_a_well_built_long_video():
    good = seo.check(
        title="Best speedrun fails compilation",
        description="The best speedrun fails of the year, all in one place. " * 5 + "\n0:00 Start\n1:00 Mid\n2:00 End",
        keywords=["speedrun fails", "mario speedrun", "gaming compilation"] * 20,
        hashtags=["speedrun", "fails", "gaming"],
        long_form=True, has_thumbnail=True, has_playlist=True,
    )
    assert good.score >= 90
    bad = seo.check(title="x" * 90, long_form=True)
    assert bad.score < 50
    assert any("chapters" in t.message for t in bad.tips)


def test_check_flags_a_title_without_the_main_keyword():
    report = seo.check(title="You won't believe this", description="speedrun fails", keywords=["speedrun fails"])
    assert any("in the title" in t.message for t in report.tips)


# ---- best times ------------------------------------------------------------------


def test_defaults_avoid_the_small_hours():
    grid = timing.default_grid("tiktok")
    assert grid[2][20] == 1.0 and grid[2][4] < 0.1


def test_best_slots_spread_across_days_and_skip_the_next_two_hours():
    grid = timing.default_grid("youtube")
    slots = timing.best_slots(grid, now_weekday=0, now_hour=19, count=6, per_day=2, min_gap_hours=2)
    assert len(slots) == 6
    assert all(not (day == 0 and hour < 21) for day, hour in slots)
    per_day: dict[int, list[int]] = {}
    for day, hour in slots:
        per_day.setdefault(day, []).append(hour)
    assert all(len(h) <= 2 for h in per_day.values())
    assert all(abs(a - b) >= 2 for hs in per_day.values() for a in hs for b in hs if a != b)


def test_best_slots_plan_around_posts_already_booked():
    grid = timing.default_grid("youtube")
    slots = timing.best_slots(grid, now_weekday=0, now_hour=0, count=4, per_day=2,
                              taken={(0, 19), (0, 20)})
    assert all(day != 0 for day, _ in slots)  # today's two are already spoken for


def test_learning_moves_toward_hours_that_did_well():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)  # a Monday
    posts = []
    for week in range(1, 11):
        day = now - timedelta(weeks=week)
        posts.append({"published_at": day.replace(hour=8).isoformat(), "views": 50_000})  # 08:00 hits
        posts.append({"published_at": day.replace(hour=19).isoformat(), "views": 200})    # 19:00 flops
    grid, confidence = timing.blended_grid("youtube", posts, 0, now=now)
    base = timing.default_grid("youtube")
    assert confidence == 0.5
    assert grid[0][8] > base[0][8]
    assert grid[0][19] < base[0][19]


def test_posts_too_young_to_judge_are_ignored():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    _, n = timing.learned_grid([{"published_at": now.isoformat(), "views": 9}], 0, now=now)
    assert n == 0


def test_local_slot_applies_the_offset():
    assert timing.local_slot("2026-09-28T02:00:00Z", -300) == (6, 21)  # Sunday 21:00 in UTC-5


# ---- generated search fields ------------------------------------------------------


def test_generated_search_fields_are_cleaned():
    from analysis.metadata import ClipMetadata, _from_parsed

    meta = _from_parsed(
        {"title": "Big win", "description": "d", "hashtags": ["#a"],
         "keywords": ["#Speed Run", "speed run", "  "], "first_comment": '"Who won?"',
         "alt_titles": ["Big win", "Another <angle>"]},
        ClipMetadata(title="fallback", description=""),
    )
    assert meta.keywords == ["speed run"]
    assert meta.first_comment == "Who won?"
    assert meta.alt_titles == ["Another angle"]


def test_a_model_that_skips_the_search_fields_still_works():
    from analysis.metadata import ClipMetadata, _from_parsed

    meta = _from_parsed({"title": "T", "description": "D", "hashtags": []}, ClipMetadata(title="f", description=""))
    assert meta.title == "T" and meta.keywords == [] and meta.first_comment == ""


# ---- compilations as publishable ----------------------------------------------------


def _compilation(d: StateDB, tmp_path: Path) -> int:
    from compilation import store

    d.upsert_video("v1", title="Stream one", channel_name="Ann", channel_url="https://y/@ann")
    d.upsert_video("v2", title="Stream two", channel_name="Bob")
    d.add_clip("v1", 100, 140, 80, "hook", title="The clutch play")
    recipe = {"canvas": "16:9", "segments": [
        {"video_id": "v1", "start": 100, "end": 140},
        {"video_id": "v2", "start": 0, "end": 30},
        {"video_id": "v1", "start": 500, "end": 530},
    ]}
    comp_id = store.create(d, "My comp", recipe)
    video = tmp_path / "comp.mp4"
    video.write_bytes(b"x")
    d.conn.execute("UPDATE compilations SET outputs = ?, status = 'done' WHERE id = ?",
                   (json.dumps({"16:9": str(video)}), comp_id))
    d.conn.commit()
    return comp_id


def test_a_compilation_publishes_under_its_negative_id(tmp_path):
    from compilation import store

    d = StateDB(tmp_path / "s.db")
    comp_id = _compilation(d, tmp_path)
    store.set_publish_meta(d, comp_id, {"title": "Best of", "keywords": ["best of ann"], "canvas": "16:9"})
    row = d.get_publishable(-comp_id)
    assert row["title"] == "Best of" and row["path"].endswith("comp.mp4")
    assert json.loads(row["keywords"]) == ["best of ann"]
    assert d.get_publishable(-999) is None
    assert d.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 1  # nothing faked


def test_compilation_facts_build_chapters_and_credits(tmp_path):
    from compilation import metadata as comp_meta
    from compilation import store

    d = StateDB(tmp_path / "s.db")
    comp_id = _compilation(d, tmp_path)
    meta = comp_meta.generate(d, store.get(d, comp_id), llm=None, footer="Discord: x")
    assert meta["chapters"].splitlines() == ["0:00 The clutch play", "0:40 Stream two", "1:10 Stream one"]
    assert "• Ann https://y/@ann" in meta["credits"] and "• Bob" in meta["credits"]
    assert meta["description"].endswith("Discord: x")
    assert "ann" in meta["keywords"] and "#compilation" in meta["hashtags"]


def test_compilation_youtube_uploads_skip_the_clip_foreign_key(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.record_clip_publish(-3, "youtube", {"provider": "youtube", "state": "published", "post_id": "abc"})
    assert d.clip_publishes(-3)[0]["post_id"] == "abc"


# ---- helpers the publishers call ------------------------------------------------------


def test_caption_overrides_fill_caption_platforms_only(tmp_path):
    from server import publishing_api

    d = StateDB(tmp_path / "s.db")
    out = publishing_api.caption_overrides(
        d, platforms=["tiktok", "youtube", "x"], title="Hook", description="Body",
        hashtags=["#a"], given={"x": {"title": "mine"}},
    )
    assert out["tiktok"]["title"].startswith("Hook\n\nBody")
    assert "youtube" not in out
    assert out["x"]["title"] == "mine"
    publishing_api.save_settings(d, {"platform_captions": False})
    assert publishing_api.caption_overrides(d, platforms=["tiktok"], title="t", description="",
                                            hashtags=[]) == {}


def test_tags_and_first_comment_use_clip_and_channel_defaults(tmp_path):
    from server import publishing_api

    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="T", channel_name="Ann")
    clip_id = d.add_clip("v1", 0, 30, 70, "h", hashtags='["#clutch"]',
                         keywords='["clutch play"]', first_comment="Who won?")
    publishing_api.save_settings(d, {"channel_keywords": ["ann clips"], "first_comment": "Discord: x",
                                     "hashtags": ["#annfam"]})
    clip = d.get_clip(clip_id)
    assert publishing_api.youtube_tags(d, clip) == ["clutch play", "Ann", "ann clips", "clutch"]
    assert publishing_api.youtube_tags(d, clip, ["typed"]) == ["typed"]
    # The video's own comment REPLACES the standing one; it does not stack.
    assert publishing_api.first_comment_for(d, clip) == "Who won?"
    d.set_clip(clip_id, first_comment="")
    assert publishing_api.first_comment_for(d, d.get_clip(clip_id)) == "Discord: x"
    # Always-on hashtags lead, then the video's own.
    assert publishing_api.hashtags_for(d, clip) == ["#annfam", "#clutch"]
    # What is in the publish box replaces the stored list: a deleted tag stays gone.
    assert publishing_api.hashtags_for(d, clip, ["#win", "annfam"]) == ["#annfam", "#win"]


def test_provider_standing_comment_is_the_fallback(tmp_path):
    from server import publishing_api

    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="T")
    clip = d.get_clip(d.add_clip("v1", 0, 30, 70, "h"))
    assert publishing_api.first_comment_for(d, clip, "from upload-post") == "from upload-post"
    publishing_api.save_settings(d, {"first_comment": "from publish page"})
    assert publishing_api.first_comment_for(d, clip, "from upload-post") == "from publish page"


def test_generated_comments_are_suggestions_not_the_comment(tmp_path):
    from compilation import metadata as comp_meta
    from compilation import store

    d = StateDB(tmp_path / "s.db")
    comp_id = _compilation(d, tmp_path)
    meta = comp_meta.generate(d, store.get(d, comp_id), llm=None)
    assert meta["first_comment"] == ""
    assert "suggested_comment" in meta


def test_youtube_description_carries_always_on_hashtags(tmp_path):
    from server import publishing_api
    from server.publisher import _describe

    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="T", channel_name="Ann")
    clip = d.get_clip(d.add_clip("v1", 0, 30, 70, "h", hashtags='["#clutch"]'))
    publishing_api.save_settings(d, {"hashtags": ["#annfam"]})
    assert _describe(d, clip, "Body").endswith("#Ann #annfam #clutch")


def test_playlist_rules_and_auto_creation(tmp_path):
    from server import publishing_api

    d = StateDB(tmp_path / "s.db")

    class Pub:
        made: list = []  # noqa: RUF012 (test fake)

        def credentials(self, scopes=None):
            return object()  # connected with the playlist permission

        def create_playlist(self, title, description=""):
            self.made.append(title)
            return "PL1"

    cid = d.conn.execute("INSERT INTO creators (display_name, created_at) VALUES ('Ann', 'x')").lastrowid
    d.upsert_video("v1", title="T", channel_name="ann_live")
    d.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = 'v1'", (cid,))
    clip = d.get_clip(d.add_clip("v1", 0, 30, 70, "h"))
    key = publishing_api.playlist_key(d, clip)
    assert key == f"creator:{cid}"
    assert publishing_api.resolve_playlist(d, Pub(), key) is None  # off by default
    publishing_api.save_settings(d, {"auto_playlists": True})
    assert publishing_api.resolve_playlist(d, Pub(), key) == "PL1"
    assert publishing_api.resolve_playlist(d, Pub(), key) == "PL1"
    assert Pub.made == ["Ann clips"]  # named for the creator, made once, then remembered
    assert publishing_api.playlist_key(d, {"id": -4, "video_id": "compilation:4"}) == "__compilations__"


@pytest.fixture
def client(tmp_path: Path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import publishing_api

    db_path = tmp_path / "state.db"
    app = FastAPI()
    publishing_api.install(app, config={"llm": {}}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path = db_path
    return c


def test_best_times_endpoint(client):
    got = client.get("/publishing/best-times", params={
        "platform": "tiktok", "offset": -240, "weekday": 2, "hour": 10, "count": 5,
    }).json()
    assert len(got["slots"]) == 5 and got["confidence"] == 0
    assert client.get("/publishing/best-times", params={"hour": 30}).status_code == 400


def test_compilation_publish_meta_round_trip(client, tmp_path):
    d = StateDB(client.db_path)
    comp_id = _compilation(d, tmp_path)
    d.close()
    got = client.put(f"/compilations/{comp_id}/publish-meta",
                     json={"title": "T", "keywords": ["#A b"], "hashtags": ["x"], "canvas": "16:9"}).json()
    assert got["meta"]["keywords"] == ["a b"] and got["meta"]["hashtags"] == ["#x"]
    bad = client.put(f"/compilations/{comp_id}/publish-meta", json={"canvas": "9:16"})
    assert bad.status_code == 400
    info = client.get(f"/compilations/{comp_id}/publish-meta").json()
    assert info["publish_id"] == -comp_id and info["outputs"] == ["16:9"]


def test_suggest_comment_stores_a_suggestion(client, tmp_path, monkeypatch):
    import analysis.metadata as am
    import llm.registry

    monkeypatch.setattr(llm.registry, "create_backend", lambda cfg: object())
    monkeypatch.setattr(am, "suggest_first_comment", lambda **kw: "Did Ann see that coming?")
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="T")
    clip_id = d.add_clip("v1", 0, 30, 70, "h")
    comp_id = _compilation(d, tmp_path)
    d.close()
    got = client.post(f"/publishing/suggest-comment/{clip_id}").json()
    assert got["suggested_comment"] == "Did Ann see that coming?"
    d = StateDB(client.db_path)
    row = d.get_clip(clip_id)
    assert row["suggested_comment"] == "Did Ann see that coming?" and row["first_comment"] == ""
    d.close()
    assert client.post(f"/publishing/suggest-comment/{-comp_id}").status_code == 200
    assert client.post("/publishing/suggest-comment/-999").status_code == 404


def test_a_playlist_rule_never_costs_an_upload_without_the_scope(tmp_path):
    from publish.errors import AuthRequired
    from server import publishing_api

    d = StateDB(tmp_path / "s.db")
    publishing_api.save_settings(d, {"auto_playlists": True})

    class NoScope:
        def credentials(self, scopes=None):
            raise AuthRequired("needs a permission you have not granted")

        def create_playlist(self, *a, **k):
            raise AssertionError("must not try")

    assert not publishing_api.can_use_playlists(NoScope())
    assert publishing_api.resolve_playlist(d, NoScope(), "creator:1") is None


def test_a_new_compilation_render_gets_a_new_upload_key(tmp_path):
    d = StateDB(tmp_path / "s.db")
    comp_id = _compilation(d, tmp_path)
    first = d.get_publishable(-comp_id)["created_at"]
    assert d.get_publishable(-comp_id)["created_at"] == first  # a retry is caught
    newer = tmp_path / "comp_v2.mp4"
    newer.write_bytes(b"x")
    d.conn.execute("UPDATE compilations SET outputs = ? WHERE id = ?",
                   (json.dumps({"16:9": str(newer)}), comp_id))
    assert d.get_publishable(-comp_id)["created_at"] != first  # a new render is new


def test_stats_refresh_explains_when_there_is_nothing_to_learn_from(client):
    got = client.post("/publishing/stats/refresh").json()
    assert got["updated"] == 0 and "YouTube direct" in got["reason"]


def test_fast_draft_skips_the_model(client, tmp_path, monkeypatch):
    import llm.registry

    def boom(cfg):
        raise AssertionError("the fast draft must not load a model")

    monkeypatch.setattr(llm.registry, "create_backend", boom)
    d = StateDB(client.db_path)
    comp_id = _compilation(d, tmp_path)
    d.close()
    got = client.post(f"/compilations/{comp_id}/publish-meta/generate", params={"ai": "false"}).json()
    assert got["meta"]["chapters"].startswith("0:00 ")


def test_a_draft_is_not_saved_unless_asked(client, tmp_path):
    d = StateDB(client.db_path)
    comp_id = _compilation(d, tmp_path)
    d.close()
    client.post(f"/compilations/{comp_id}/publish-meta/generate", params={"ai": "false", "save": "false"})
    assert client.get(f"/compilations/{comp_id}/publish-meta").json()["meta"] == {}


def test_youtube_questions_reach_every_provider_that_can_carry_them():
    from publish.uploadpost import platform_overrides
    from publish.woopsocial import build_post

    fields = dict(platform_overrides({"youtube": {"ai_disclosure": "false", "paid_promotion": "true",
                                                   "made_for_kids": "false"}}))
    # "false" travels as an answer; it is not dropped as empty.
    assert fields == {"containsSyntheticMedia": "false", "hasPaidProductPlacement": "true",
                      "selfDeclaredMadeForKids": "false"}
    post = build_post(media_id="m", text="t", title="T",
                      accounts=[{"platform": "YOUTUBE", "id": "a1"}],
                      overrides={"youtube": {"madeForKids": False}})
    assert post["socialAccounts"][0]["madeForKids"] is False
    plain = build_post(media_id="m", text="t", title="T", accounts=[{"platform": "YOUTUBE", "id": "a1"}])
    assert "madeForKids" not in plain["socialAccounts"][0]
