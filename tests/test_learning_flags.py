"""What flagged clips teach the next run: penalties, wider edges, default crop.

Real StateDB throughout (see conftest). The guardrails are the point: nothing
moves below its minimum count, and nothing moves past its cap.
"""

from core.models import ClipCandidate, Segment
from creator import learning


def _flag(db, creator, reasons, *, n=1, scoring=None, crop="track", tag="v1"):
    db.upsert_video(tag, title="t", channel_name="c", duration=600)
    db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = ?", (creator, tag))
    db.conn.commit()
    for _ in range(n):
        db.add_clip_flag(None, tag, reasons, "", {"scoring": scoring or {}, "crop": crop})


def _clip(db, creator, scores, start=0, tag="v1"):
    import json

    db.upsert_video(tag, title="t", channel_name="c", duration=600)
    db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = ?", (creator, tag))
    db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, scores, path, created_at)"
        " VALUES (?, ?, ?, 50, ?, '', '')", (tag, start, start + 30, json.dumps(scores)),
    )
    db.conn.commit()


def test_nothing_learned_below_the_minimum(db, creator):
    _flag(db, creator, ["starts_late"], n=learning.MIN_FLAGS - 1)
    assert learning.preferences(db, creator) is None


def test_starts_late_widens_the_lead_in_and_is_capped(db, creator):
    _flag(db, creator, ["starts_late"], n=learning.MIN_FLAGS)
    assert learning.preferences(db, creator)["boundary"] == {"lead": learning.PAD_STEP, "tail": 0.0}
    _flag(db, creator, ["starts_late"], n=30)
    assert learning.preferences(db, creator)["boundary"]["lead"] == learning.MAX_PAD


def test_runs_long_cancels_ends_early(db, creator):
    _flag(db, creator, ["ends_early"], n=4)
    _flag(db, creator, ["runs_long"], n=3)
    assert learning.preferences(db, creator) is None  # net 1, below the minimum


def test_crop_needs_a_clear_winner_on_default_crop_clips(db, creator):
    _flag(db, creator, ["needs_wide"], n=3, crop="letterbox")  # user already chose it
    assert learning.preferences(db, creator) is None
    _flag(db, creator, ["needs_wide"], n=3)
    assert learning.preferences(db, creator)["crop"] == "letterbox"
    _flag(db, creator, ["crop_jumps"], n=3)  # tie -> no opinion
    assert learning.preferences(db, creator) is None


def test_not_a_moment_shaves_channels_that_overrated_them(db, creator):
    for i in range(4):
        _clip(db, creator, {"text": 50, "audio": 50}, start=i * 100)
    _flag(db, creator, ["not_a_moment"], n=learning.MIN_MOMENT_FLAGS,
          scoring={"text": 90, "audio": 40})
    bias = learning.preferences(db, creator)["weight_bias"]
    assert bias["text"] == 1 - learning.MAX_PENALTY  # 50/90 would be lower; capped
    assert bias["audio"] == 1.0                      # never boosted by a flag
    assert learning.apply_bias({"text": 0.5, "audio": 0.5}, bias)["text"] < 0.5


def test_learning_off_and_kill_switch(db, creator):
    _flag(db, creator, ["starts_late"], n=5)
    assert learning.preferences(db, creator, use_flags=False) is None
    db.conn.execute("UPDATE creators SET learning_enabled = 0")
    db.conn.commit()
    assert learning.preferences(db, creator) is None


def _segs():
    return [Segment(start=float(i * 5), end=float(i * 5 + 5), text=f"s{i}") for i in range(20)]


def test_adjust_boundaries_snaps_and_records_the_pad():
    c = ClipCandidate(start=21.0, end=41.0, score=80)
    learning.adjust_boundaries([c], {"lead": 1.5, "tail": 1.5}, _segs(), 100.0, 60.0)
    assert (c.start, c.end) == (15.0, 45.0)          # outward to whole segments
    assert c.subscores["learned_pad"] == [6.0, 4.0]
    assert c.proposed == (21.0, 41.0)


def test_adjust_boundaries_respects_neighbours_video_end_and_max():
    a = ClipCandidate(start=10.0, end=30.0, score=80)
    b = ClipCandidate(start=32.0, end=98.0 - 40, score=80)
    learning.adjust_boundaries([a, b], {"lead": 3.0, "tail": 3.0}, _segs(), 100.0, 60.0)
    assert a.end <= b.start and b.start >= 30.0
    assert b.end - b.start <= 60.0

    tail_end = ClipCandidate(start=80.0, end=99.0, score=80)
    learning.adjust_boundaries([tail_end], {"lead": 0.0, "tail": 3.0}, _segs(), 100.0, 60.0)
    assert tail_end.end <= 100.0


def test_adjust_boundaries_is_a_noop_without_a_pad():
    c = ClipCandidate(start=21.0, end=41.0, score=80)
    learning.adjust_boundaries([c], {"lead": 0.0, "tail": 0.0}, _segs(), 100.0, 60.0)
    assert (c.start, c.end) == (21.0, 41.0) and c.subscores is None


# ---- what a flag tells the user, and fixing the flagged clip -----------------


def test_flag_feedback_counts_down_then_reports_applied(db, creator):
    _flag(db, creator, ["starts_late"], n=1)
    fb = learning.flag_feedback(db, creator, ["starts_late"])
    assert not fb["applied"] and "2 more" in fb["pending"][0]
    _flag(db, creator, ["starts_late"], n=2)
    fb = learning.flag_feedback(db, creator, ["starts_late"])
    assert fb["applied"] and not fb["pending"]


def test_flag_feedback_says_when_nothing_carries_over(db, creator):
    assert "no creator profile" in learning.flag_feedback(db, None, ["starts_late"])["recorded"][0]
    _flag(db, creator, ["captions"], n=1)
    assert "saved for review" in learning.flag_feedback(db, creator, ["captions"])["recorded"][0]


def test_recut_moves_edges_onto_segments_and_sets_framing():
    segs = _segs()   # 5s segments
    plan = learning.recut_plan({"starts_late", "ends_early", "needs_wide"}, 22.0, 41.0, {}, segs, 100.0, 60.0)
    assert plan["start"] == 20.0 and plan["end"] == 45.0     # snapped outward to whole segments
    assert plan["render_opts"] == {"crop": "letterbox"}
    assert len(plan["changes"]) == 3


def test_recut_does_nothing_it_cannot_do():
    plan = learning.recut_plan({"captions", "not_a_moment"}, 22.0, 41.0, {}, _segs(), 100.0, 60.0)
    assert plan["changes"] == [] and (plan["start"], plan["end"]) == (22.0, 41.0)
    # Already letterboxed: asking for wide again changes nothing.
    again = learning.recut_plan({"needs_wide"}, 22.0, 41.0, {"crop": "letterbox"}, _segs(), 100.0, 60.0)
    assert again["changes"] == []


def test_recut_respects_the_video_end_and_max_duration():
    plan = learning.recut_plan({"ends_early"}, 60.0, 98.0, {}, _segs(), 99.0, 60.0)
    assert plan["end"] <= 99.0
    long = learning.recut_plan({"starts_late", "ends_early"}, 40.0, 95.0, {}, _segs(), 100.0, 60.0)
    assert long["end"] - long["start"] <= 60.0


def test_recut_route_queues_a_render_and_resolves_the_flags(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("httpx")
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import flags_api

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T", channel_name="c", duration=100)
    clip_id = d.add_clip("v1", 22, 41, 70, "hook", path=str(tmp_path / "c.mp4"), title="A clip")
    d.close()
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text(json.dumps(
        {"segments": [{"start": i * 5.0, "end": i * 5.0 + 5, "text": f"s{i}."} for i in range(20)]}))

    class W:
        notified = 0

        def notify(self):
            W.notified += 1

    app = FastAPI()
    flags_api.install(app, config={"clips": {"max_duration": 60}, "llm": {}},
                      db=lambda: StateDB(db_path), data_dir=tmp_path, worker=W())
    c = TestClient(app, base_url="http://127.0.0.1")

    assert c.post(f"/clips/{clip_id}/recut").status_code == 409          # nothing flagged
    r = c.post(f"/clips/{clip_id}/flag", json={"reasons": ["starts_late", "needs_wide"]})
    assert r.status_code == 200 and "learning" in r.json()
    r = c.post(f"/clips/{clip_id}/recut")
    assert r.status_code == 200 and r.json()["changes"]
    assert W.notified == 1
    d = StateDB(db_path)
    job = d.conn.execute("SELECT payload FROM jobs WHERE type = 'render' ORDER BY id DESC").fetchone()
    payload = json.loads(job["payload"])
    assert payload["start"] < 22 and payload["render_opts"] == {"crop": "letterbox"}
    assert d.list_clip_flags(status="open", clip_id=clip_id) == []
    d.close()
    assert c.post(f"/clips/{clip_id}/recut").status_code == 409          # flags are resolved now


def test_recut_leaves_edges_alone_on_a_hand_edited_clip(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("httpx")
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import flags_api

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T", channel_name="c", duration=100)
    clip_id = d.add_clip("v1", 22, 41, 70, "hook", path=str(tmp_path / "c.mp4"), title="A clip")
    d.conn.execute("UPDATE clips SET render_opts = ? WHERE id = ?",
                   (json.dumps({"edit": {"cuts": [[1, 2]]}}), clip_id))
    d.conn.commit()
    d.close()
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text(json.dumps(
        {"segments": [{"start": i * 5.0, "end": i * 5.0 + 5, "text": f"s{i}."} for i in range(20)]}))

    class W:
        def notify(self):
            pass

    app = FastAPI()
    flags_api.install(app, config={"clips": {"max_duration": 60}, "llm": {}},
                      db=lambda: StateDB(db_path), data_dir=tmp_path, worker=W())
    c = TestClient(app, base_url="http://127.0.0.1")
    c.post(f"/clips/{clip_id}/flag", json={"reasons": ["starts_late"]})
    r = c.post(f"/clips/{clip_id}/recut")
    assert r.status_code == 409 and "hand edits" in r.json()["detail"]
    # Framing does not depend on the start, so it is still fixed.
    c.post(f"/clips/{clip_id}/flag", json={"reasons": ["needs_wide"]})
    r = c.post(f"/clips/{clip_id}/recut")
    assert r.status_code == 200 and r.json()["changes"] == ["framing set to letterbox"]
    d = StateDB(db_path)
    payload = json.loads(d.conn.execute("SELECT payload FROM jobs WHERE type = 'render'").fetchone()["payload"])
    assert payload["start"] == 22 and payload["end"] == 41      # the edges did not move
    d.close()


def test_a_framing_complaint_steps_toward_a_framing_that_shows_more():
    faults = {"subject_cut_off"}
    a = learning.recut_plan(faults, 22.0, 41.0, {}, _segs(), 100.0, 60.0)
    assert a["render_opts"] == {"crop": "letterbox"} and a["changes"] == ["framing set to letterbox"]
    b = learning.recut_plan(faults, 22.0, 41.0, {"crop": "letterbox"}, _segs(), 100.0, 60.0)
    assert b["render_opts"] == {"crop": "center"}                       # press again: the next step
    c = learning.recut_plan(faults, 22.0, 41.0, {"crop": "center"}, _segs(), 100.0, 60.0)
    assert c["changes"] == []                                           # out of steps: says so, does not loop
    assert learning.recut_plan({"wrong_angle"}, 22.0, 41.0, {}, _segs(), 100.0, 60.0)["render_opts"]
    # A specific ask beats the guess.
    d = learning.recut_plan({"subject_cut_off", "needs_tight"}, 22.0, 41.0, {"crop": "letterbox"}, _segs(), 100.0, 60.0)
    assert d["render_opts"] == {"crop": "track"}


def test_the_flag_reply_says_whether_recut_can_do_anything(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("httpx")
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import flags_api

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T", channel_name="c", duration=100)
    clip_id = d.add_clip("v1", 22, 41, 70, "hook", path=str(tmp_path / "c.mp4"), title="A clip")
    d.close()

    class W:
        def notify(self):
            pass

    app = FastAPI()
    flags_api.install(app, config={"clips": {"max_duration": 60}, "llm": {}},
                      db=lambda: StateDB(db_path), data_dir=tmp_path, worker=W())
    c = TestClient(app, base_url="http://127.0.0.1")
    none = c.post(f"/clips/{clip_id}/flag", json={"reasons": ["captions"]}).json()
    assert none["recut"] == {"available": False, "changes": []}         # nothing to offer: the dialog hides the button
    some = c.post(f"/clips/{clip_id}/flag", json={"reasons": ["subject_cut_off"]}).json()
    assert some["recut"] == {"available": True, "changes": ["framing set to letterbox"]}
    r = c.post(f"/clips/{clip_id}/recut")
    assert r.status_code == 200 and r.json()["changes"] == ["framing set to letterbox"]
