"""The rubric, per-stage models, and the offline evaluation of raters."""

import json

from analysis import evaluation, rubric
from llm.stages import StageModels

# ---- rubric --------------------------------------------------------------------


def _rating(index, **kw):
    base = {"hook": 5, "payoff": 5, "standalone": 5, "clean_edges": 5, "energy": 5}
    return {"index": index, **{**base, **kw}}


def test_rubric_score_weights_the_hook_and_payoff_most():
    flat = dict.fromkeys(rubric.DIMENSIONS, 5)
    assert rubric.rubric_score(flat) == 50
    strong_open = {**flat, "hook": 10}
    strong_edges = {**flat, "clean_edges": 10}
    assert rubric.rubric_score(strong_open) > rubric.rubric_score(strong_edges)
    assert rubric.rubric_score(dict.fromkeys(rubric.DIMENSIONS, 10)) == 100
    assert rubric.rubric_score(dict.fromkeys(rubric.DIMENSIONS, 0)) == 0


def test_parse_ratings_keeps_complete_entries_and_clamps():
    raw = json.dumps({"ratings": [
        _rating(0, hook=15, payoff=-3),                   # out of range: clamped
        {"index": 1, "hook": 5},                          # incomplete: dropped
        _rating(7),                                       # not in the batch: dropped
        _rating(2, hook=7.6),                             # a float is rounded
    ]})
    got = rubric.parse_ratings("```json\n" + raw + "\n```", n=3)
    assert set(got) == {0, 2}
    assert got[0]["hook"] == 10 and got[0]["payoff"] == 0
    assert got[2]["hook"] == 8
    assert 0 <= got[0]["score"] <= 100


def test_parse_ratings_survives_garbage():
    assert rubric.parse_ratings("no json here", 3) == {}
    assert rubric.parse_ratings('{"ratings": "nope"}', 3) == {}
    assert rubric.parse_ratings("", 3) == {}


class _Fake:
    name = "fake"
    supports_schema = False

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_rate_batches_and_a_failed_batch_costs_only_its_own_clips():
    good = json.dumps({"ratings": [_rating(i, hook=8) for i in range(6)]})
    llm = _Fake([good, RuntimeError("boom")])
    items = [(f"{i}s", f"text {i}") for i in range(9)]
    out = rubric.rate(items, llm, guidance="- no gameplay chatter")
    assert len(llm.prompts) == 2                        # 6 + 3
    assert all(r is not None for r in out[:6]) and all(r is None for r in out[6:])
    assert "no gameplay chatter" in llm.prompts[0]      # guidance reaches the model
    assert out[0]["hook"] == 8


def test_blend_moves_the_score_toward_the_rubric():
    assert rubric.blend(80, 40, share=0.5) == 60
    assert rubric.blend(80, 80) == 80
    assert 0 <= rubric.blend(100, 100) <= 100


# ---- per-stage models ------------------------------------------------------------


def test_stages_fall_back_to_the_default_and_ignore_unknown_ones(capsys):
    default = _Fake([])
    s = StageModels({"stage_models": {"bogus": "x", "rerank": ""}}, default)
    assert s.for_stage("propose") is default
    assert s.for_stage("rerank") is default            # blank means default
    assert "unknown stage 'bogus'" in capsys.readouterr().out


def test_a_stage_that_cannot_be_built_uses_the_default(capsys):
    default = _Fake([])
    s = StageModels({"stage_models": {"refine": "nosuchprovider/model"}}, default)
    assert s.for_stage("refine") is default
    assert "using the default model" in capsys.readouterr().out


# ---- evaluation --------------------------------------------------------------------


def test_auc_is_order_only_and_counts_ties_half():
    assert evaluation.auc([9, 8], [1, 2]) == 1.0
    assert evaluation.auc([1, 2], [9, 8]) == 0.0
    assert evaluation.auc([5], [5]) == 0.5
    assert evaluation.auc([], [1]) is None


def test_precision_at_k():
    items = [{"label": 1}, {"label": 0}, {"label": 1}, {"label": 0}]
    assert evaluation.precision_at(items, [4, 3, 2, 1], 2) == 0.5
    assert evaluation.precision_at(items, [4, 1, 3, 2], 2) == 1.0


def test_evaluate_compares_raters_and_flags_thin_data():
    items = [
        {"label": 1, "source": "exported", "score": 80, "scores": {"text": 30, "rubric": 90}},
        {"label": 0, "source": "flagged", "score": 70, "scores": {"text": 60, "rubric": 20}},
        {"label": 1, "source": "views", "score": 75, "scores": {"text": 20, "rubric": 80}},
    ]
    res = evaluation.evaluate(items, evaluation.default_raters(items))
    assert res["reliable"] is False
    assert res["raters"]["stored score"]["auc"] == 1.0
    assert res["raters"]["text channel"]["auc"] == 0.0        # anti-correlated: it would rank them backwards
    assert res["raters"]["rubric (stored)"]["auc"] == 1.0
    text = evaluation.report(res)
    assert "Too few to trust" in text and "rubric (stored)" in text
    # A model-backed rater is a list of scores in item order.
    assert evaluation.evaluate(items, {"m": [1, 9, 1]})["raters"]["m"]["auc"] == 0.0


def test_labelled_clips_read_views_exports_and_flags(db, creator):
    from core.state import _now

    db.upsert_video("v1", title="t", channel_name="c", duration=600)
    db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = 'v1'", (creator,))
    ids = [db.add_clip("v1", i * 100, i * 100 + 30, 60 + i, "h", path="", title="t") for i in range(4)]
    db.conn.commit()
    # Views on three clips (one platform): above / at / below the median.
    for cid, views in zip(ids[:3], (500, 100, 10)):
        db.conn.execute(
            "INSERT INTO clip_publishes (clip_id, platform, provider, video_id, start_s, end_s, state, post_id,"
            " created_at, updated_at) VALUES (?, 'youtube', 'x', 'v1', 0, 30, 'published', ?, ?, ?)",
            (cid, f"p{cid}", _now(), _now()))
        db.conn.execute("INSERT INTO publish_stats (platform, post_id, published_at, views, likes, comments,"
                        " checked_at) VALUES ('youtube', ?, '', ?, 0, 0, '')", (f"p{cid}", views))
    db.conn.execute("INSERT INTO clip_feedback (creator_id, clip_id, action, clip_meta, created_at)"
                    " VALUES (?, ?, 'exported', '{}', ?)", (creator, ids[3], _now()))
    db.add_clip_flag(None, "v1", ["not_a_moment"], "", {"clip": {"start": 900, "end": 930, "score": 66},
                                                       "scoring": {"text": 40}})
    db.add_clip_flag(None, "v1", ["captions"], "", {"clip": {"start": 1, "end": 2, "score": 1}})
    db.conn.commit()

    got = {(i["source"], i["label"]) for i in evaluation.labelled_clips(db, creator)}
    assert ("views", 1) in got and ("views", 0) in got       # median splits the three
    assert ("exported", 1) in got and ("flagged", 0) in got
    assert not any(i["start"] == 1 for i in evaluation.labelled_clips(db))   # only not_a_moment counts


# ---- the vision second opinion ---------------------------------------------------


def test_frame_order_is_completed_and_garbage_is_refused():
    from video import frame_judge

    assert frame_judge.parse_order('{"order": [2, 0, 1]}', 3) == [2, 0, 1]
    assert frame_judge.parse_order('{"order": [2]}', 3) == [2, 0, 1]          # favourite first, rest as they were
    assert frame_judge.parse_order('{"order": [5, 2, 2]}', 3) == [2, 0, 1]    # out of range and repeats dropped
    assert frame_judge.parse_order("I like the first", 3) is None
    assert frame_judge.parse_order('{"order": []}', 3) is None


def test_judge_needs_a_model_that_can_see_and_sends_the_frames():
    from video import frame_judge

    class Eyes:
        def __init__(self, sees):
            self.sees, self.sent = sees, None

        def can_see(self):
            return self.sees

        def generate(self, prompt, *, json_mode=False, images=None):
            self.sent = images
            return '{"order": [1, 0]}'

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), "red").save(buf, "JPEG")
    frames = [buf.getvalue(), buf.getvalue()]
    blind, sighted = Eyes(False), Eyes(True)
    assert frame_judge.judge(blind, frames) is None and blind.sent is None
    assert frame_judge.judge(sighted, frames) == [1, 0]
    assert len(sighted.sent) == 2 and max(Image.open(io.BytesIO(sighted.sent[0])).size) <= frame_judge.MAX_SIDE
    assert frame_judge.judge(sighted, frames[:1]) is None                       # nothing to compare


def test_vision_backend_is_off_by_default_and_refuses_the_cloud(capsys):
    from video import frame_judge

    assert frame_judge.vision_backend({"llm": {}}) is None
    assert frame_judge.vision_backend({"llm": {"vision_model": "openrouter/some/model"}}) is None
    assert "only sent to local models" in capsys.readouterr().out


# ---- shadow vs on, inside fusion ---------------------------------------------------


def _finalists():
    from core.models import ClipCandidate, Segment

    segs = [Segment(start=float(i * 10), end=float(i * 10 + 10), text=f"line {i}.") for i in range(6)]
    cands = [ClipCandidate(start=0, end=20, score=80, subscores={}),
             ClipCandidate(start=30, end=50, score=70, subscores={})]
    return cands, segs


def test_shadow_mode_records_the_grade_and_changes_no_score():
    from analysis import fusion

    cands, segs = _finalists()
    reply = json.dumps({"ratings": [_rating(0, hook=2, payoff=2), _rating(1, hook=10, payoff=10)]})
    fusion._grade_finalists(cands, segs, _Fake([reply]), guidance="", apply=False)
    assert [c.score for c in cands] == [80, 70]                      # untouched
    assert cands[1].subscores["rubric"] > cands[0].subscores["rubric"]
    assert set(cands[0].subscores["rubric_detail"]) == set(rubric.DIMENSIONS)


def test_on_mode_blends_the_grade_into_the_score():
    from analysis import fusion

    cands, segs = _finalists()
    reply = json.dumps({"ratings": [_rating(0, hook=2, payoff=2), _rating(1, hook=10, payoff=10)]})
    fusion._grade_finalists(cands, segs, _Fake([reply]), guidance="", apply=True)
    assert cands[0].score < 80 and cands[1].score > 70               # the rubric moved them apart
    assert cands[0].subscores["rubric"] < cands[1].subscores["rubric"]


def test_a_model_that_cannot_grade_leaves_the_clips_alone():
    from analysis import fusion

    cands, segs = _finalists()
    fusion._grade_finalists(cands, segs, _Fake(["not json"]), guidance="", apply=True)
    assert [c.score for c in cands] == [80, 70] and "rubric" not in cands[0].subscores


def test_rerank_only_shows_the_rubric_when_it_is_on():
    from analysis import fusion

    cands, segs = _finalists()
    for c in cands:
        c.subscores["rubric_detail"] = {"hook": 7, "payoff": 3}
    off, on = _Fake(['{"order": [0, 1]}']), _Fake(['{"order": [0, 1]}'])
    fusion._rerank(list(cands), segs, off, use_rubric=False)
    fusion._rerank(list(cands), segs, on, use_rubric=True)
    assert "hook=7" not in off.prompts[0] and "hook=7" in on.prompts[0]


def test_on_mode_never_drops_a_kept_clip_below_the_bar():
    from analysis import fusion

    cands, segs = _finalists()                                         # scores 80 and 70
    reply = json.dumps({"ratings": [_rating(0, hook=0, payoff=0, standalone=0, clean_edges=0, energy=0),
                                    _rating(1, hook=0, payoff=0, standalone=0, clean_edges=0, energy=0)]})
    fusion._grade_finalists(cands, segs, _Fake([reply]), guidance="", apply=True, floor=60)
    assert [c.score for c in cands] == [60, 60]                        # pulled down, but only to the bar
