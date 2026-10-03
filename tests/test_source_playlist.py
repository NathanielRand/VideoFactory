import json

import pytest

from publish.metadata import playlist_url, source_credit_line, timestamped_url, with_links


def test_source_link_lands_on_the_moment_the_clip_starts():
    assert timestamped_url("https://www.youtube.com/watch?v=abc", 75.6) == "https://www.youtube.com/watch?v=abc&t=75s"
    assert timestamped_url("https://youtu.be/abc", 75) == "https://youtu.be/abc?t=75s"
    assert timestamped_url("https://youtu.be/abc?t=9", 75) == "https://youtu.be/abc?t=9"
    assert timestamped_url("https://twitch.tv/videos/1", 75) == "https://twitch.tv/videos/1"
    assert timestamped_url("https://youtu.be/abc", 0) == "https://youtu.be/abc"


def test_credit_line_needs_a_real_web_link_never_a_local_path():
    assert source_credit_line("Streamer", "https://youtu.be/abc", 5) == "Source: Streamer - https://youtu.be/abc?t=5s"
    assert source_credit_line("Streamer", "C:\videos\a.mp4") == "Source: Streamer"
    assert source_credit_line("", "") == ""


def test_links_are_added_once():
    text = with_links("Hello", ["Source: A - https://x.test/v", "Playlist: " + playlist_url("PL1")])
    assert text.endswith("Playlist: https://www.youtube.com/playlist?list=PL1")
    assert with_links(text, ["Source: A - https://x.test/v", "Playlist: " + playlist_url("PL1")]) == text
    assert with_links("Hi", ["", "  "]) == "Hi"


def _clip(db, playlist=""):
    db.upsert_video("v1", title="T", channel_name="Some Streamer", source_url="https://youtu.be/v1")
    return db.add_clip("v1", 65, 90, 70, "hook", path="x.mp4", title="A")


def test_the_description_credits_the_source_and_links_the_playlist(db):
    from server import publishing_api
    from server.publisher import _describe

    clip = db.get_clip(_clip(db))
    text = _describe(db, clip, "A great moment.", "PLabc")
    assert "Source: Some Streamer - https://youtu.be/v1?t=65s" in text
    assert "Playlist: https://www.youtube.com/playlist?list=PLabc" in text
    # Off means off, and no playlist means no line.
    publishing_api.save_settings(db, {"link_source": False, "link_playlist": False})
    text = _describe(db, clip, "A great moment.", "PLabc")
    assert "Source:" not in text and "Playlist:" not in text
    publishing_api.save_settings(db, {"link_source": True, "link_playlist": True})
    assert "Playlist:" not in _describe(db, clip, "A great moment.", "")


def test_a_compilation_gets_no_source_line(db):
    from server import publishing_api

    assert publishing_api.source_line(db, {"id": -3, "video_id": "compilation:3", "start_s": 0}) == ""


def test_a_clip_can_hold_its_own_playlist(db):
    clip = _clip(db)
    db.set_clip(clip, playlist_id="PLkeep")
    assert db.get_clip(clip)["playlist_id"] == "PLkeep"


def test_stored_metadata_carries_the_source_channel(db):
    from server import publishing_api

    _clip(db)
    kw = dict(title="Wild clutch", description="A great moment.", hashtags=["#funny"], keywords=["clutch"])
    got = publishing_api.enrich_clip_metadata(db, "v1", 65, **kw)
    assert got["title"] == "Wild clutch | Some Streamer"
    assert "Source: Some Streamer - https://youtu.be/v1?t=65s" in got["description"]
    assert got["hashtags"] == ["#SomeStreamer", "#funny"]
    assert got["keywords"] == ["Some Streamer", "clutch"]
    # A re-render runs it again on its own output: nothing is added twice.
    again = publishing_api.enrich_clip_metadata(
        db, "v1", 65, title=got["title"], description=got["description"],
        hashtags=got["hashtags"], keywords=got["keywords"],
    )
    assert again == got


def test_enrichment_leaves_a_compilation_and_empty_fields_alone(db):
    from server import publishing_api

    got = publishing_api.enrich_clip_metadata(
        db, "compilation:3", 0, title="Best of", description="", hashtags=[], keywords=[]
    )
    assert got == {"title": "Best of", "description": "", "hashtags": [], "keywords": []}
