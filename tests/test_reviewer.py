"""Flag reviews: only menu items get through, nothing applies until approved."""

import json

import pytest

from creator import learning, reviewer


def _flag(db, creator, reasons, note="", n=1, tag="v1", crop="track"):
    db.upsert_video(tag, title="t", channel_name="c", duration=600)
    db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = ?", (creator, tag))
    db.conn.commit()
    for _ in range(n):
        db.add_clip_flag(None, tag, reasons, note, {
            "clip": {"duration": 30, "score": 70}, "crop": crop,
            "transcript": "  [1.0-2.0] before\n>> [3.0-5.0] the question that started it\n  [6.0-7.0] after"})


def test_only_menu_items_survive_and_values_are_clamped():
    got = reviewer.clean_proposals([
        {"kind": "pad_lead", "value": "9", "rationale": "x"},                 # clamped to 3
        {"kind": "pad_tail", "value": "0", "rationale": "x"},                 # nothing to add
        {"kind": "crop", "value": "Letterbox", "rationale": "y"},
        {"kind": "crop", "value": "zoom", "rationale": "y"},                  # not a mode
        {"kind": "min_score_delta", "value": "-40", "rationale": "z"},        # clamped to -10
        {"kind": "min_score_delta", "value": "0", "rationale": "z"},
        {"kind": "guidance", "value": "Skip stretches where he only reads chat.", "rationale": "g"},
        {"kind": "guidance", "value": "short", "rationale": "g"},             # too short to mean anything
        {"kind": "delete_everything", "value": "yes", "rationale": "!"},      # not on the menu
        {"kind": "crop", "value": "letterbox", "rationale": "dup"},           # duplicate
        "junk",
    ])
    assert [(p["kind"], p["value"]) for p in got] == [
        ("pad_lead", "3"), ("crop", "letterbox"), ("min_score_delta", "-10"),
        ("guidance", "Skip stretches where he only reads chat."),
    ]
    assert reviewer.clean_proposals("nope") == []
    assert len(reviewer.clean_proposals(
        [{"kind": "pad_lead", "value": str(i / 10 + 0.5), "rationale": ""} for i in range(20)])) <= reviewer.MAX_PROPOSALS


def test_guidance_is_capped():
    long = "Do this " + "x" * 500
    got = reviewer.clean_proposals([{"kind": "guidance", "value": long, "rationale": ""}])
    assert len(got[0]["value"]) <= reviewer.GUIDANCE_CHARS


def test_evidence_needs_enough_flags_and_shows_notes_and_speech(db, creator):
    _flag(db, creator, ["starts_late"])
    assert reviewer.evidence(db, creator) is None
    _flag(db, creator, ["starts_late", "needs_wide"], note="it missed the question")
    text = reviewer.evidence(db, creator)
    assert "starts_late x2" in text and "it missed the question" in text
    assert "the question that started it" in text and "before" not in text.split("said")[1].split("\n")[0]


class _LLM:
    supports_schema = False

    def __init__(self, reply):
        self.reply = reply

    def generate(self, prompt, *, json_mode=False):
        self.prompt = prompt
        return self.reply


def test_review_stores_pending_proposals_without_applying_them(db, creator):
    _flag(db, creator, ["starts_late"], n=2)
    reply = json.dumps({"proposals": [
        {"kind": "pad_lead", "value": "1.5", "rationale": "2 of 2 flags say it starts late"},
        {"kind": "bogus", "value": "1", "rationale": ""}]})
    out = reviewer.review(db, creator, _LLM(reply))
    assert len(out["created"]) == 1 and out["created"][0]["status"] == "pending"
    assert reviewer.approved_overrides(db, creator) == {"guidance": []}          # nothing applied yet
    # Asking again does not stack the same proposal.
    again = reviewer.review(db, creator, _LLM(reply))
    assert again["created"] == [] and again["skipped"] == 1


def test_review_says_why_when_there_is_nothing(db, creator):
    assert "at least" in reviewer.review(db, creator, _LLM("{}"))["reason"]
    _flag(db, creator, ["captions"], n=2)
    assert "not suggest" in reviewer.review(db, creator, _LLM('{"proposals": []}'))["reason"]
    assert "not suggest" in reviewer.review(db, creator, _LLM("not json"))["reason"]


def test_approving_changes_the_next_run_and_rejecting_takes_it_back(db, creator):
    _flag(db, creator, ["captions"], n=1)
    a = reviewer.add_proposal(db, creator, "pad_lead", "2", "why")
    b = reviewer.add_proposal(db, creator, "guidance", "Skip stretches where he only reads chat.", "why")
    c = reviewer.add_proposal(db, creator, "min_score_delta", "5", "why")
    d = reviewer.add_proposal(db, creator, "crop", "letterbox", "why")
    assert learning.preferences(db, creator) is None                          # pending: no effect
    for p in (a, b, c, d):
        reviewer.decide(db, p["id"], "approved")
    prefs = learning.preferences(db, creator)
    assert prefs["boundary"]["lead"] == 2.0 and prefs["crop"] == "letterbox"
    assert prefs["overrides"] == {"min_score_delta": 5, "guidance": ["Skip stretches where he only reads chat."]}
    reviewer.decide(db, a["id"], "rejected")
    assert learning.preferences(db, creator)["boundary"]["lead"] == 0.0        # taken back


def test_an_approval_overrides_the_counted_value(db, creator):
    _flag(db, creator, ["starts_late"], n=6)                                   # counting alone: 2.0s
    assert learning.preferences(db, creator)["boundary"]["lead"] == 2.0
    p = reviewer.add_proposal(db, creator, "pad_lead", "0.5", "why")
    reviewer.decide(db, p["id"], "approved")
    assert learning.preferences(db, creator)["boundary"]["lead"] == 0.5


def test_review_routes(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import review_api

    path = tmp_path / "s.db"
    d = StateDB(path)
    cur = d.conn.execute("INSERT INTO creators (display_name, aliases, learning_enabled, created_at)"
                         " VALUES ('C', '[]', 1, 'now')")
    d.conn.commit()
    cid = cur.lastrowid
    p = reviewer.add_proposal(d, cid, "crop", "center", "because")
    d.close()
    app = FastAPI()
    review_api.install(app, config={"llm": {}}, db=lambda: StateDB(path))
    c = TestClient(app, base_url="http://127.0.0.1")

    assert c.get("/creators/999/proposals").status_code == 404
    assert c.post(f"/creators/{cid}/review").status_code == 409               # no flags yet
    assert c.get(f"/creators/{cid}/proposals").json()["proposals"][0]["status"] == "pending"
    assert c.post(f"/proposals/{p['id']}/approve").json()["status"] == "approved"
    assert c.get(f"/creators/{cid}/proposals?status=approved").json()["proposals"][0]["value"] == "center"
    assert c.post(f"/proposals/{p['id']}/reject").json()["status"] == "rejected"
    assert c.post("/proposals/9999/approve").status_code == 404


def test_guidance_about_framing_is_refused():
    # It would sit in the moment-picking prompt, where it does nothing.
    got = reviewer.clean_proposals([
        {"kind": "guidance", "value": "Keep the cursor centered and avoid zooming too far.", "rationale": ""},
        {"kind": "guidance", "value": "Skip stretches where he only reads chat.", "rationale": ""},
    ])
    assert [p["value"] for p in got] == ["Skip stretches where he only reads chat."]
