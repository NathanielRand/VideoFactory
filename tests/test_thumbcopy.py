"""Thumbnail words are quoted from the clip's peak; the badge is an always-on tag."""

import json

from analysis import peaks, thumbcopy
from core.models import Segment


def S(start, end, text):
    return Segment(start=start, end=end, text=text)


SEGS = [
    S(0, 4, "So we're sitting outside the enemy safe zone waiting for someone to leave with something expensive."),
    S(4, 6, "Ready? Go behind the building."),
    S(6, 8, "Shoot it, shoot it, shoot it, shoot it!"),
    S(8, 9, "It's a rocket heli!"),
    S(9, 11, "Oh my god."),
    S(11, 14, "Is it blowed up? I got him, I got him."),
    S(14, 18, "Alright so we're gonna go over there and loot everything we can find."),
]


def test_lines_are_split_into_sentences_and_scored_by_what_is_charged():
    lines = peaks.split_lines(SEGS, 0, 18)
    assert any(ln.text == "Is it blowed up?" for ln in lines)
    assert all(any(c.isalnum() for c in ln.text) for ln in lines)               # no lone punctuation
    assert peaks.emotion("Shoot it, shoot it, shoot it, shoot it!") > peaks.emotion("We are gonna go loot everything we can find.")
    assert peaks.emotion("") == 0


def test_the_peak_is_a_real_quote_and_distinct():
    top = peaks.peak(SEGS, 0, 18, 4)
    assert top[0]["text"].lower().startswith("shoot it")
    texts = [t["text"].lower() for t in top]
    assert len(set(texts)) == len(texts)
    assert all(any(t["text"].lower().rstrip(".!?") in s.text.lower() for s in SEGS) for t in top)   # said in the clip


def test_snippets_keep_the_strongest_run_and_drop_filler():
    assert peaks.trim_snippet("So um it's a rocket heli!") == "it's a rocket heli!"
    long = "we were all just standing there and then out of nowhere he yelled get out of the humvee right now!"
    out = peaks.trim_snippet(long, 6)
    assert len(out.split()) <= 6 and out in long


def test_lines_with_blocked_words_never_reach_a_thumbnail_or_a_title():
    segs = [S(0, 3, "Holy shit that was insane!"), S(3, 6, "Oh my god, he's behind you!")]
    assert peaks.is_clean("Oh my god") and not peaks.is_clean("Holy shit")
    assert [p["text"] for p in peaks.peak(segs, 0, 6, 3)] == ["Oh my god, he's behind you!"]


def test_a_two_sided_exchange_needs_close_marked_different_lines():
    lines = peaks.split_lines([S(0, 2, "Is it blowed up?"), S(2.2, 4, "I got him!")], 0, 4)
    a, b = peaks.best_exchange(lines)
    assert (a.text, b.text) == ("Is it blowed up?", "I got him!")
    far = peaks.split_lines([S(0, 2, "Is it blowed up?"), S(9, 11, "I got him!")], 0, 11)
    assert peaks.best_exchange(far) is None                                       # too far apart
    flat = peaks.split_lines([S(0, 2, "we walked over."), S(2.2, 4, "then we sat down.")], 0, 4)
    assert peaks.best_exchange(flat) is None                                      # nothing marked
    same = peaks.split_lines([S(0, 2, "Run!"), S(2.1, 4, "Run!")], 0, 4)
    assert peaks.best_exchange(same) is None                                      # repeating is not a conversation


def test_the_badge_is_an_always_on_hashtag_without_its_hash():
    b = thumbcopy.pick_badge(["#Shorts", "#Wardogs", "#Gaming"], ["#rocket"], ["rocket heli"], "Sadelity")
    assert b in ("wardogs", "gaming") and "#" not in b
    assert thumbcopy.pick_badge([], ["#rocket", "#fyp"], ["k"], "Sadelity") == "rocket"       # then the clip's own
    assert thumbcopy.pick_badge([], [], ["rocket heli"], "Sadelity") == "rocket heli"          # then a keyword
    assert thumbcopy.pick_badge([], [], [], "Sadelity") == "Sadelity"                          # then the channel
    assert thumbcopy.pick_badge([], [], [], "A Very Long Channel Name Indeed") == ""          # too long for a badge
    assert thumbcopy.pick_badge(["#" + "x" * 30], [], [], "") == ""


def test_the_sets_are_quotes_with_a_supporting_line_an_exchange_and_an_alternative():
    sets = thumbcopy.quote_sets(SEGS, 0, 18, badge="wardogs")
    assert 2 <= len(sets) <= 3
    first = sets[0]
    assert first["headline"].lower().startswith("shoot it") and first["kicker"] and first["badge"] == "wardogs"
    assert first["mood"] in thumbcopy.MOODS and not first["exchange"]
    heads = [s["headline"].lower() for s in sets]
    assert len(set(heads)) == len(heads)
    every = " ".join(x.text.lower() for x in SEGS)
    for s in sets:
        for part in (s["headline"], s["kicker"]):
            if part:
                assert part.lower().rstrip(".!?, ") in every                 # nothing is invented


def test_an_exchange_becomes_a_two_sided_set():
    segs = [S(0, 2, "Is it blowed up?"), S(2.3, 4, "I got him!"), S(4, 6, "Oh my god, he's behind you!")]
    sets = thumbcopy.quote_sets(segs, 0, 6, badge="wardogs")
    ex = [s for s in sets if s["exchange"]]
    assert ex and ex[0]["headline"] == "Is it blowed up?" and ex[0]["kicker"] == "I got him!"


def test_the_models_ranking_reorders_and_a_bad_answer_changes_nothing():
    cands = peaks.peak(SEGS, 0, 18, 8)
    n = len(cands)
    last = n - 1
    choice = thumbcopy.parse_choice(json.dumps({"order": [last, 0], "mood": "funny", "emoji": "😂"}), n)
    assert choice["order"][:2] == [last, 0] and sorted(choice["order"]) == list(range(n))
    assert choice["mood"] == "funny" and choice["emoji"] == "😂"
    sets = thumbcopy.quote_sets(SEGS, 0, 18, order=choice["order"], mood="funny", emoji="😂", candidates=cands)
    assert sets[0]["mood"] == "funny" and sets[0]["headline"].lower() == peaks.trim_snippet(cands[last]["text"]).lower()
    bad = thumbcopy.parse_choice("the second one", n)
    assert bad == {"order": [], "mood": "", "emoji": ""}
    junk = thumbcopy.parse_choice(json.dumps({"order": [99, "x"], "mood": "sad", "emoji": "🐙"}), n)
    assert junk == {"order": [], "mood": "", "emoji": ""}                                      # unknown values dropped


def test_no_speech_no_sets():
    assert thumbcopy.quote_sets([S(0, 3, "...")], 0, 3) == []
    assert thumbcopy.quote_sets([], 0, 3) == []
