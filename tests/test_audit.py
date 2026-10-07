"""Auditing videos already on the channel, and fixing them safely
(publish/audit.py, server/audit_api.py), plus the upload gate and the
generators that keep new text inside the same rules."""

import json
from pathlib import Path

import pytest

from core.state import StateDB
from publish import audit, compliance


@pytest.fixture(autouse=True)
def default_rules():
    compliance.configure({})
    yield
    compliance.configure({})


def video(vid="aaaaaaaaaaa", title="Rex loses the clutch", description="Rex throws the last round.",
          tags=None, category="20"):
    return {
        "id": vid,
        "snippet": {"title": title, "description": description, "tags": tags or [],
                    "categoryId": category, "publishedAt": "2026-09-01T10:00:00Z",
                    "defaultLanguage": "en", "channelId": "UC1", "thumbnails": {}},
        "status": {"privacyStatus": "public"},
    }


# ---- the audit ----------------------------------------------------------------


def test_a_clean_video_has_nothing_to_fix():
    got = audit.audit_item(video())
    assert got["findings"] == [] and got["proposed"] is None and got["score"] == 100


def test_a_spammy_video_gets_a_deterministic_fix():
    got = audit.audit_item(video(
        title="Rex loses the clutch #wardogs #gaming",
        description="Rex throws the last round.\n\n#a #b #c #d #e #f",
        tags=["rex clutch", "rex clutch", "wardogs"] + [f"t{i}" for i in range(20)]))
    assert set(got["changes"]) == {"title", "description", "tags"}
    fixed = got["proposed"]
    assert "#" not in fixed["title"]
    assert fixed["description"].count("#") <= 3
    assert compliance.check(fixed["title"], fixed["description"], fixed["tags"]) == []


def test_machine_tone_is_reported_not_silently_rewritten():
    got = audit.audit_item(video(description="Dive into the game-changing world of Rex. Buckle up."))
    assert got["proposed"] is None
    assert any(f["code"] == "machine-tone" for f in got["manual"])


def test_report_orders_worst_first_and_summarises():
    report = audit.audit_all([video("aaaaaaaaaaa"), video("bbbbbbbbbbb", title="X #y"),
                              video("ccccccccccc", description="Dive into the game-changing world.")])
    assert report["summary"] == {"checked": 3, "clean": 1, "fixable": 1, "needs_rewrite": 1}
    assert report["videos"][-1]["video_id"] == "aaaaaaaaaaa"


def test_genre_strictness_applies_to_the_videos_category():
    compliance.configure({"genre_overrides": {"gaming": {"tone": "strict"}}})
    one = "Rex finds a seamless route through the map."
    assert audit.audit_item(video(description=one, category="20"))["manual"]
    assert not audit.audit_item(video(description=one, category="10"))["manual"]


# ---- rewrites -----------------------------------------------------------------


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate(self, prompt, json_mode=True):
        self.prompts.append(prompt)
        return json.dumps(self.reply)


def stuffed():
    return audit.audit_item(video(description=(
        "Dive into the game-changing world of Rex. Buckle up.\n\n"
        "Watch the full stream: https://twitch.tv/rex\n\n0:00 Start\n1:00 Clutch")))


def test_a_rewrite_touches_only_prose_and_keeps_links_and_chapters():
    llm = FakeLLM({"paragraphs": [{"index": 0, "text": "Rex throws the last round."}]})
    got = audit.rewrite_entry(stuffed(), llm)
    desc = got["proposed"]["description"]
    assert desc.startswith("Rex throws the last round.")
    assert "https://twitch.tv/rex" in desc and "0:00 Start" in desc
    assert got["manual"] == [] and got["rewritten"]
    assert "twitch.tv" not in llm.prompts[0], "protected paragraphs are never shown to the model"


def test_a_rewrite_that_still_fails_the_check_is_discarded():
    llm = FakeLLM({"paragraphs": [{"index": 0, "text": "Buckle up for this game-changer."}]})
    got = audit.rewrite_entry(stuffed(), llm)
    assert got["proposed"] is None and got["manual"]


def test_a_rewrite_that_adds_hashtags_or_runs_long_is_discarded():
    for text in ("Rex throws the last round #wardogs", "Rex throws the last round. " * 20):
        got = audit.rewrite_entry(stuffed(), FakeLLM({"paragraphs": [{"index": 0, "text": text}]}))
        assert got["proposed"] is None


def test_a_model_failure_changes_nothing():
    class Boom:
        def generate(self, *a, **k):
            raise RuntimeError("offline")

    entry = stuffed()
    assert audit.rewrite_entry(entry, Boom()) == entry


# ---- the upload gate ----------------------------------------------------------


def test_build_insert_body_holds_every_upload_to_the_rules():
    from publish.base import PublishRequest
    from publish.metadata import build_insert_body

    req = PublishRequest(video_path=Path("x.mp4"), title="Big clutch #wardogs",
                         description="Body\n\n#a #b #c #d #e", tags=["big clutch", "rex"] + [f"t{i}" for i in range(30)],
                         localizations={"es": {"title": "Gran jugada #wardogs", "description": "Cuerpo"}})
    body = build_insert_body(req)
    sn = body["snippet"]
    assert "#" not in sn["title"] and sn["description"].count("#") <= 3 and len(sn["tags"]) <= 8
    assert "#" not in body["localizations"]["es"]["title"]
    assert sn["categoryId"] == str(req.category_id)


# ---- the generators -----------------------------------------------------------


def test_generated_metadata_stays_inside_the_limits():
    from analysis import metadata as md

    meta = md.ClipMetadata(
        title="Rex loses it #wardogs",
        description="Rex loses it.", hashtags=[f"#t{i}" for i in range(9)],
        keywords=["rex loses it", "wardogs"] + [f"phrase {i}" for i in range(15)])
    got = md._anchored(meta, "Rex", ["#wardogs"], "")
    assert "#" not in got.title
    assert len(got.hashtags) <= 3 and len(got.keywords) <= 8
    assert "rex loses it" not in got.keywords          # an echo of the title


def test_a_machine_sounding_title_falls_back_to_the_hook_when_the_rewrite_fails():
    from analysis import metadata as md

    meta = md.ClipMetadata(title="Dive into the game-changing clutch", description="Rex wins the round.")
    asked = md._naturalized(meta, FakeLLM({"title": "Buckle up for the must-watch clutch", "description": ""}),
                            "Rex", "", fallback_title="Rex wins the round", text="Rex wins the round")
    assert asked and meta.title == "Rex wins the round"


def test_a_machine_sounding_description_is_dropped_rather_than_shipped():
    from analysis import metadata as md

    meta = md.ClipMetadata(title="Rex wins", description="Buckle up, this is a game-changer you won't believe.",
                           first_comment="Don't forget to like and subscribe!")
    md._naturalized(meta, FakeLLM({"title": "Rex wins", "description": "Another game-changer."}), "Rex", "")
    assert meta.description == "" and meta.first_comment == ""


# ---- the routes ---------------------------------------------------------------


@pytest.fixture
def api(tmp_path: Path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import audit_api
    from server import youtube_service as service

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.set_flag(service.SETTINGS_KEY, json.dumps({"enabled": True}))
    service.add_account(d, {"id": "UC1", "title": "Rex"}, [])
    d.close()

    live = {
        "aaaaaaaaaaa": video("aaaaaaaaaaa", title="Clutch #wardogs #gaming",
                             description="Rex throws the last round.\n\n#a #b #c #d", tags=["clutch", "rex"]),
        "bbbbbbbbbbb": video("bbbbbbbbbbb"),
    }
    sent: list[dict] = []

    class Pub:
        def channel_videos(self, limit):
            return list(live.values())

        def video_details(self, vid):
            return live[vid]

        def update_metadata(self, vid, *, title, description, tags, current=None):
            sent.append({"id": vid, "title": title, "description": description, "tags": tags})
            sn = live[vid]["snippet"]
            sn.update(title=title, description=description, tags=list(tags))

    monkeypatch.setattr(service, "make_publisher", lambda *a, **k: Pub())
    app = FastAPI()
    audit_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.live, c.sent, c.db_path = live, sent, db_path
    return c


def test_audit_reports_without_changing_anything(api):
    got = api.post("/youtube/audit", json={}).json()
    assert got["report"]["summary"]["fixable"] == 1 and api.sent == []
    assert api.get("/youtube/audit").json()["report"]["summary"] == got["report"]["summary"]


def test_apply_needs_confirmation_and_a_report(api):
    ids = {"video_ids": ["aaaaaaaaaaa"]}
    assert api.post("/youtube/audit/apply", json={**ids, "confirm": False}).status_code == 400
    assert api.post("/youtube/audit/apply", json={**ids, "confirm": True}).status_code == 409


def test_apply_sends_only_the_reported_fix_and_undo_restores_the_original(api):
    api.post("/youtube/audit", json={})
    got = api.post("/youtube/audit/apply", json={"video_ids": ["aaaaaaaaaaa", "bbbbbbbbbbb"], "confirm": True}).json()
    status = {r["video_id"]: r["status"] for r in got["results"]}
    assert status == {"aaaaaaaaaaa": "updated", "bbbbbbbbbbb": "skipped"}
    assert [s["id"] for s in api.sent] == ["aaaaaaaaaaa"]
    assert "#" not in api.sent[0]["title"] and api.sent[0]["description"].count("#") <= 3
    assert got["applied"] == ["aaaaaaaaaaa"]

    again = api.post("/youtube/audit/apply", json={"video_ids": ["aaaaaaaaaaa"], "confirm": True}).json()
    assert again["results"][0]["status"] == "skipped", "a second apply has nothing left to do"

    back = api.post("/youtube/audit/undo", json={"video_ids": ["aaaaaaaaaaa"], "confirm": True}).json()
    assert back["results"][0]["status"] == "restored"
    assert api.live["aaaaaaaaaaa"]["snippet"]["title"] == "Clutch #wardogs #gaming"
    assert back["applied"] == []


def test_a_video_edited_since_the_audit_is_not_overwritten(api):
    api.post("/youtube/audit", json={})
    api.live["aaaaaaaaaaa"]["snippet"]["title"] = "Edited by hand"
    got = api.post("/youtube/audit/apply", json={"video_ids": ["aaaaaaaaaaa"], "confirm": True}).json()
    assert got["results"][0]["status"] == "skipped" and api.sent == []


def test_apply_stops_when_the_day_runs_out_of_quota(api):
    from publish.quota import DAILY_UNITS
    from server import youtube_service as service

    api.post("/youtube/audit", json={})
    d = StateDB(api.db_path)
    ledger = service.load_ledger(d)
    ledger.units = DAILY_UNITS - 10
    service.save_ledger(d, ledger)
    d.close()
    got = api.post("/youtube/audit/apply", json={"video_ids": ["aaaaaaaaaaa"], "confirm": True}).json()
    assert got["results"][0]["status"] == "deferred" and api.sent == []


def test_routes_look_absent_while_youtube_is_off(api):
    from server import youtube_service as service

    d = StateDB(api.db_path)
    d.set_flag(service.SETTINGS_KEY, json.dumps({"enabled": False}))
    d.close()
    assert api.get("/youtube/audit").status_code == 404
