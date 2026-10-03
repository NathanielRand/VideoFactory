"""Copy is written ABOUT the source creator, in the connected audience's voice."""

import json
from datetime import datetime, timedelta, timezone

from analysis import audience
from analysis import metadata as md
from core.models import ClipCandidate, Segment

TRANSCRIPT = "I got him, I can't believe I hit that shot. My team is going to lose it."


class _LLM:
    supports_schema = False

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        return self.replies.pop(0)


class _DB:
    def __init__(self):
        self.flags = {}

    def get_flag(self, key, default=""):
        return self.flags.get(key, default)

    def set_flag(self, key, value):
        self.flags[key] = value


def _cand():
    return ClipCandidate(start=0.0, end=10.0, score=90, hook="I got him")


def _batch(title, desc="Sadelity lands the shot.", comment="Did you see it?"):
    return json.dumps({"items": [{"index": 0, "title": title, "description": desc, "hashtags": ["#wardogs"],
                                  "keywords": ["shot"], "first_comment": comment, "alt_titles": []}]})


# ---- first person -----------------------------------------------------------------------

def test_first_person_is_found_but_a_credited_quote_is_not():
    assert md.first_person("I got him")
    assert md.first_person("Why my team lost, and I'm not sorry")
    assert md.first_person("Watch me clutch it")
    assert not md.first_person("Sadelity gets him")
    assert not md.first_person('Sadelity: "I got him"')
    assert not md.first_person("Can we talk about that shot?")           # "we" talks to the community
    assert not md.first_person("Mike's setup")                           # not "me"/"my" inside a word


def test_a_hook_that_speaks_as_i_is_credited_to_the_channel():
    meta = md.generate_metadata_batch([_cand()], [Segment(start=0, end=10, text=TRANSCRIPT)], "Stream",
                                      _LLM(["not json"]), channel="Sadelity")[0]
    assert meta.title.startswith('Sadelity: "I got him"')
    assert not md.first_person(meta.title)


# ---- slang is not a hallucination -------------------------------------------------------

def test_slang_is_not_flagged_as_unsupported_but_hype_still_is():
    text = "He walked into the room and the whole team just fell apart."
    assert md.unsupported_words("Team Is Cooked After Lowkey Fumbled Room", text) == []
    assert "insane" in md.unsupported_words("Insane Room Moment", text)
    assert md.unsupported_words("Insane Room", text + " That was insane.") == []   # said, so it is a quote
    assert "dragon" in md.unsupported_words("Cooked By A Dragon", text)


def test_a_slangy_title_survives_the_grounding_check():
    llm = _LLM([_batch("Sadelity is cooked after that shot")])
    meta = md.generate_metadata_batch([_cand()], [Segment(start=0, end=10, text=TRANSCRIPT)], "Stream",
                                      llm, channel="Sadelity")[0]
    assert "cooked" in meta.title
    assert len(llm.prompts) == 1                                          # no rewrite was asked for


# ---- voice repair -----------------------------------------------------------------------

def test_a_title_that_speaks_as_i_is_rewritten_once():
    llm = _LLM([_batch("I hit the shot nobody believed"), json.dumps({"title": "Sadelity hits the shot nobody believed"})])
    meta = md.generate_metadata_batch([_cand()], [Segment(start=0, end=10, text=TRANSCRIPT)], "Stream",
                                      llm, channel="Sadelity")[0]
    assert not md.first_person(meta.title) and "Sadelity" in meta.title
    assert "speaks as" in llm.prompts[1]


def test_a_description_and_comment_that_speak_as_i_are_fixed_or_dropped():
    meta = md.ClipMetadata(title="Sadelity lands it", description="I hit the shot.", first_comment="Did I get him?")
    fixed = json.dumps({"description": "Sadelity hits the shot.", "first_comment": "Did Sadelity get him?"})
    assert md._unvoiced(meta, _LLM([fixed]), "Sadelity", "")
    assert meta.description == "Sadelity hits the shot." and meta.first_comment == "Did Sadelity get him?"

    stubborn = md.ClipMetadata(title="x", description="I hit the shot.", first_comment="My best shot?")
    md._unvoiced(stubborn, _LLM([fixed.replace("Sadelity", "I")]), "Sadelity", "")
    assert stubborn.description == "" and stubborn.first_comment == ""    # dropped, never shipped

    nomodel = md.ClipMetadata(title="x", description="I hit it.", first_comment="Fine?")
    assert not md._unvoiced(nomodel, _LLM([]), "Sadelity", "", ask_model=False)
    assert nomodel.description == "" and nomodel.first_comment == "Fine?"


def test_a_suggested_comment_is_asked_for_again_then_dropped():
    good = json.dumps({"first_comment": "Did Sadelity see that coming?"})
    bad = json.dumps({"first_comment": "Did I see that coming?"})
    llm = _LLM([bad, good])
    out = md.suggest_first_comment(title="t", description="d", content="c", llm=llm, channel="Sadelity")
    assert out == "Did Sadelity see that coming?" and len(llm.prompts) == 2
    assert md.suggest_first_comment(title="t", description="d", content="c", llm=_LLM([bad, bad])) == ""


# ---- the prompts ------------------------------------------------------------------------

def test_every_prompt_carries_the_voice_and_the_audience():
    line = "Viewers of this channel: 80% male; mostly ages 18-24 (60%) and 25-34 (30%); top countries: US 70%."
    for run in (
        lambda llm: md.generate_metadata_batch([_cand()], [Segment(start=0, end=10, text=TRANSCRIPT)], "Stream",
                                               llm, channel="Sadelity", audience=line, creator_context="Plays Wardogs"),
        lambda llm: md.generate_metadata(_cand(), [Segment(start=0, end=10, text=TRANSCRIPT)], "Stream", llm,
                                         channel="Sadelity", audience=line, creator_context="Plays Wardogs"),
    ):
        llm = _LLM(["not json"])
        run(llm)
        prompt = llm.prompts[0]
        assert "{voice}" not in prompt and "VOICE AND AUDIENCE" in prompt
        assert "ABOUT Sadelity" in prompt and line in prompt and "Plays Wardogs" in prompt


def test_without_audience_data_the_prompt_says_so_instead_of_inventing_one():
    rules = md.voice_rules("Sadelity", "")
    assert "No audience data yet" in rules and "Viewers of this channel" not in rules


# ---- the audience profile ---------------------------------------------------------------

def test_the_audience_is_described_from_the_two_reports():
    text = audience.describe(
        [{"ageGroup": "age18-24", "gender": "male", "viewerPercentage": 40.0},
         {"ageGroup": "age25-34", "gender": "male", "viewerPercentage": 30.0},
         {"ageGroup": "age18-24", "gender": "female", "viewerPercentage": 20.0},
         {"ageGroup": "age35-44", "gender": "female", "viewerPercentage": 10.0}],
        [{"country": "US", "views": 600}, {"country": "GB", "views": 300}, {"country": "CA", "views": 100}],
    )
    assert "70% male, 30% female" in text and "18-24 (60%)" in text and "25-34 (30%)" in text
    assert "US 60%, UK 30%, Canada 10%" in text
    assert audience.describe([], []) == "" and audience.describe(None, None) == ""


class _Pub:
    def __init__(self, report=None, error=None):
        self.report, self.error, self.calls = report, error, 0

    def audience_report(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.report


def test_a_failed_or_empty_fetch_is_no_audience_never_an_error():
    assert audience.fetch(_Pub(error=RuntimeError("Reconnect: no analytics permission"))) == ""
    assert audience.fetch(_Pub({"age_gender": [], "countries": []})) == ""
    db = _DB()

    def broken():
        raise RuntimeError("no channel connected")

    assert audience.cached(db, broken) == ""


def test_the_audience_is_cached_for_a_week_and_a_stale_one_survives_a_failed_refresh():
    db = _DB()
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    report = {"age_gender": [{"ageGroup": "age18-24", "gender": "male", "viewerPercentage": 100.0}],
              "countries": [{"country": "US", "views": 10}]}
    pub = _Pub(report)
    first = audience.cached(db, lambda: pub, now)
    assert "100% male" in first and pub.calls == 1
    assert audience.cached(db, lambda: pub, now + timedelta(days=3)) == first and pub.calls == 1
    failing = _Pub(error=RuntimeError("quota"))
    assert audience.cached(db, lambda: failing, now + timedelta(days=8)) == first and failing.calls == 1
