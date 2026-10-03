"""Titles and descriptions are checked against what was said, and anchored."""

import json

from analysis import metadata as md
from core.models import ClipCandidate, Segment


def _seg(text, start=0.0, end=10.0):
    return Segment(start=start, end=end, text=text)


TRANSCRIPT = ("Shoot it, shoot it, shoot it! It's a rocket heli. Oh my god, we're getting shot up "
              "from behind the building. Nice, you got him.")


def test_words_the_clip_never_says_are_found_and_vague_hype_always_counts():
    bad = md.unsupported_words("Insane Dragon Ambush Near Castle", TRANSCRIPT)
    assert {"insane", "dragon", "castle"} <= set(bad)
    assert md.unsupported_words("Shooting the Rocket Heli", TRANSCRIPT) == []       # 'shooting' ~ 'shoot'
    # Names from outside the clip (the channel, the video's title) are known.
    assert md.unsupported_words("Wardogs Rocket Heli", TRANSCRIPT, ["Wardogs"]) == []


def test_only_a_mostly_unsupported_title_is_called_ungrounded():
    ok = "Rocket Heli Chase Behind the Building"
    one_creative = "Rocket Heli Takedown Behind the Building"          # 'takedown' is one word of five
    assert md._ungrounded(ok, TRANSCRIPT, "", []) == []
    assert md._ungrounded(one_creative, TRANSCRIPT, "", []) == []
    assert md._ungrounded("Dragon Slaying Adventure Begins", TRANSCRIPT, "", [])


def test_a_long_clip_shows_its_start_and_its_end():
    text = ("start " * 300) + ("middle " * 600) + ("finale " * 300)
    out = md.budget_text(text, 500)
    assert len(out) <= 520 and out.startswith("start") and out.endswith("finale") and " ... " in out
    assert md.budget_text("short", 500) == "short"


def test_an_always_on_tag_is_picked_by_relevance_then_by_turn_and_never_a_generic_one():
    tags = ["#Shorts", "#Wardogs", "#Gaming"]
    assert md.pick_always_on(tags, "playing gaming today") == "gaming"          # the clip is about it
    assert md.pick_always_on(tags, "nothing relevant", 0) == "wardogs"
    assert md.pick_always_on(tags, "nothing relevant", 1) == "gaming"           # they take turns
    assert md.pick_always_on(["#shorts", "#fyp"], "x") == ""                    # only generic ones: none
    assert md.pick_always_on([], "x") == ""


def test_the_channel_and_a_standing_tag_are_put_on_in_code():
    meta = md.ClipMetadata(title="Rocket Heli Chase", description="A helicopter gets chased.",
                           hashtags=["#rocket", "#chase"])
    md._anchored(meta, "Sadelity", ["#Wardogs"], TRANSCRIPT)
    assert meta.title == "Rocket Heli Chase | Sadelity"
    assert meta.description.startswith("Sadelity: ")
    assert meta.hashtags[0] == "#wardogs" and "#rocket" in meta.hashtags and len(meta.hashtags) <= 5


def test_nothing_is_added_twice_or_forced_into_a_full_title():
    meta = md.ClipMetadata(title="Sadelity Snipes a Rocket Heli", description="Sadelity gets a kill.",
                           hashtags=["#wardogs", "#kill"])
    md._anchored(meta, "Sadelity", ["#Wardogs"], "")
    assert meta.title == "Sadelity Snipes a Rocket Heli" and meta.description == "Sadelity gets a kill."
    assert meta.hashtags.count("#wardogs") == 1
    tagged = md.ClipMetadata(title="The Wardogs Ambush", description="", hashtags=[])
    md._anchored(tagged, "Sadelity", ["#Wardogs"], "")
    assert "Sadelity" not in tagged.title                                       # a standing tag is already in it
    long = md.ClipMetadata(title="x" * 94, description="", hashtags=[])
    md._anchored(long, "Sadelity", [], "")
    assert len(long.title) <= 100
    empty = md.ClipMetadata(title="Clip", description="", hashtags=[])
    md._anchored(empty, "Sadelity", [], "")
    assert empty.description == ""                                              # an empty description stays empty


class _LLM:
    supports_schema = False

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def _batch(title, desc="Sadelity chases a helicopter.", tags=("#wardogs",)):
    return json.dumps({"items": [{"index": 0, "title": title, "description": desc, "hashtags": list(tags),
                                  "keywords": ["rocket heli"], "first_comment": "Did you see it?",
                                  "alt_titles": []}]})


def test_a_bad_title_is_rewritten_with_the_unsupported_words_named():
    cand = ClipCandidate(start=0, end=10, score=80, hook="hook")
    llm = _LLM([_batch("Insane Dragon Ambush Near Castle"), json.dumps({"title": "Shooting Down the Rocket Heli"})])
    out = md.generate_metadata_batch([cand], [_seg(TRANSCRIPT)], "Some Stream", llm,
                                     channel="Sadelity", always_on=["#Wardogs"])[0]
    assert out.title.startswith("Shooting Down the Rocket Heli") and out.title.endswith("| Sadelity")
    assert "dragon" in llm.prompts[1] and "castle" in llm.prompts[1]           # the model was told what was wrong


def test_if_the_rewrite_is_also_wrong_the_clips_own_strongest_line_is_the_title():
    cand = ClipCandidate(start=0, end=10, score=80, hook="hook")
    llm = _LLM([_batch("Insane Dragon Ambush Near Castle"), json.dumps({"title": "Epic Dragon Battle"})])
    out = md.generate_metadata_batch([cand], [_seg(TRANSCRIPT)], "Some Stream", llm, channel="Sadelity")[0]
    assert "dragon" not in out.title.lower()
    assert out.title.lower().startswith(("shoot it", "nice", "oh my god"))      # a real quotation


def test_a_good_title_costs_no_extra_model_call_and_the_prompt_carries_the_anchors():
    cand = ClipCandidate(start=0, end=10, score=80, hook="hook")
    llm = _LLM([_batch("Rocket Heli Chase Behind the Building")])
    out = md.generate_metadata_batch([cand], [_seg(TRANSCRIPT)], "Some Stream", llm,
                                     channel="Sadelity", always_on=["#Wardogs"])[0]
    assert len(llm.prompts) == 1
    assert "SOURCE CHANNEL: Sadelity" in llm.prompts[0] and "#wardogs" in llm.prompts[0]
    assert out.hashtags[0] == "#wardogs" and out.title.endswith("| Sadelity")


def test_a_failed_model_still_gets_the_anchors():
    cand = ClipCandidate(start=0, end=10, score=80, hook="Sniping a rocket heli")
    out = md.generate_metadata_batch([cand], [_seg(TRANSCRIPT)], "Some Stream", _LLM(["not json"]),
                                     channel="Sadelity", always_on=["#Wardogs"])[0]
    assert out.title == "Sniping a rocket heli | Sadelity" and out.hashtags == ["#wardogs"]


def test_the_whole_clip_reaches_the_model_not_its_first_900_characters():
    cand = ClipCandidate(start=0, end=10, score=80, hook="h")
    long = ("filler words here " * 120) + "THE PAYOFF IS AT THE VERY END"
    llm = _LLM([_batch("Filler Words Here")])
    md.generate_metadata_batch([cand], [_seg(long)], "Some Stream", llm)
    assert "THE PAYOFF IS AT THE VERY END" in llm.prompts[0]
