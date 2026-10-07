"""The monetization linter: what it flags, what it fixes, what it leaves."""

import pytest

from publish import compliance as c


@pytest.fixture(autouse=True)
def default_rules():
    c.configure({})
    yield
    c.configure({})


def codes(findings):
    return {f.code for f in findings}


def test_a_plain_post_is_clean():
    out = c.check("Rex loses the clutch on the last round",
                  "Rex throws the last round after a bad peek. #wardogs", ["rex clutch fail"])
    assert out == []


def test_hashtag_in_title_is_flagged_and_stripped():
    assert "hashtag-in-title" in codes(c.check("Rex loses it #wardogs", ""))
    got = c.sanitize("Rex loses it | Rex #wardogs", "")
    assert got.title == "Rex loses it | Rex"
    assert c.check(got.title, got.description) == []


def test_hashtags_are_capped_and_deduped_keeping_the_first():
    got = c.sanitize("T", "Body\n\n#a #b #a #c #d #e")
    assert got.description == "Body\n\n#a #b #c"
    assert "too-many-hashtags" not in codes(c.check("T", got.description))


def test_a_rank_is_not_a_hashtag():
    assert c.hashtags_in("He is #1 on the ladder") == []


def test_title_hashtags_count_against_the_cap_when_allowed():
    c.configure({"hashtags_in_title": True})
    got = c.sanitize("Big win #a", "Body #b #c #d")
    assert c.hashtags_in(got.title + " " + got.description) == ["#a", "#b", "#c"]


def test_tags_are_capped_deduped_and_not_echoes():
    tags = ["rex clutch", "Rex Clutch", "rex", "wardogs", "wardogs gameplay", "a", "b", "c", "d", "e", "f", "g"]
    got = c.sanitize("Rex clutch", "Body #wardogs", tags)
    assert got.tags[0] != "rex clutch"          # echo of the title dropped
    assert "wardogs" not in got.tags            # repeats a hashtag
    assert len(got.tags) <= 8
    assert len({t.lower() for t in got.tags}) == len(got.tags)


def test_keyword_stuffing_is_flagged_but_not_silently_rewritten():
    text = ("Best gaming clips. Gaming highlights, gaming moments, gaming fails, "
            "more gaming content every day for gaming fans.")
    out = c.check("Gaming clip", text)
    assert "keyword-repeat" in codes(out)
    assert any(not f.fixable for f in out)
    assert c.sanitize("Gaming clip", text).remaining


def test_keyword_lists_are_flagged():
    text = "tags: clutch, fail, rex, wardogs, funny, gaming, shooter, moments, highlights, best"
    assert "keyword-list" in codes(c.check("Rex", text))


@pytest.mark.parametrize("text", [
    "Dive into the game-changing world of Rex.",
    "Buckle up, you won't believe what happens next, a must-watch moment.",
])
def test_machine_tone_is_flagged(text):
    assert "machine-tone" in codes(c.check("Rex", text))


def test_tone_strictness_changes_the_bar():
    one = "Rex finds a seamless route through the map."
    assert "machine-tone" not in codes(c.check("Rex", one))
    c.configure({"tone": "strict"})
    assert "machine-tone" in codes(c.check("Rex", one))
    c.configure({"tone": "off"})
    assert "machine-tone" not in codes(c.check("Rex", "Dive into the game-changing world."))


def test_genre_override_applies_only_to_that_genre():
    c.configure({"genre_overrides": {"gaming": {"tone": "strict"}}})
    one = "Rex finds a seamless route through the map."
    assert "machine-tone" in codes(c.check("Rex", one, genre="gaming"))
    assert "machine-tone" not in codes(c.check("Rex", one, genre="music"))


def test_shouting_and_stacked_punctuation():
    out = codes(c.check("REX LOSES THE WHOLE ROUND AGAIN!!!", ""))
    assert {"title-caps", "punctuation"} <= out


def test_sanitize_never_empties_a_title():
    assert c.sanitize("#wardogs", "").title == "#wardogs"


def test_bad_config_values_fall_back_to_defaults():
    r = c.configure({"max_hashtags": "lots", "tone": "loud", "max_tags": 4})
    assert r.max_hashtags == 3 and r.tone == "standard" and r.max_tags == 4


def test_score_drops_with_findings():
    assert c.score([]) == 100
    assert c.score(c.check("Rex #a", "Dive into the game-changing world.")) < 70
