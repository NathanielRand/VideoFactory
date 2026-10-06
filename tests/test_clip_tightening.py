"""Dead-air cutting, crossfaded joins, game profiles and the HUD event signal.

These pin the rules that keep a tightened clip watchable and keep its captions
and length honest. Whether the framing looks right is a question for real
footage, not for these.
"""

import numpy as np

from analysis import tighten
from genres import events, profiles
from video_editor.captions import remap_lines
from video_editor.cuts import concat_graph
from video_editor.timeline import EditList

# ---- game profiles -----------------------------------------------------------


def test_wardogs_is_found_by_title_or_channel_as_whole_words():
    assert profiles.detect("I Crashed WARDOGS Economy With This...", "TheTacticalBrit").id == "wardogs"
    assert profiles.detect("funniest bug in Wardogs") is not None
    assert profiles.detect("War Dogs: the movie review") is not None   # the phrase, spelled apart
    assert profiles.detect("Sneaking up on snipers", "Hudi") is None
    assert profiles.detect("Wardogsfans compilation") is None           # whole words only
    assert profiles.detect("", None) is None


def test_a_profile_can_be_forced_or_switched_off(tmp_path):
    cfg = {"paths": {"data_dir": str(tmp_path)}}                        # no database: nothing to detect from
    assert profiles.for_source(tmp_path / "abc.mp4", cfg) is None
    assert profiles.for_source(tmp_path / "abc.mp4", cfg, "wardogs").id == "wardogs"
    assert profiles.for_source(tmp_path / "abc.mp4", cfg, "none") is None


def test_every_profile_region_is_a_valid_box_and_activity_regions_exist():
    for p in profiles.PROFILES.values():
        for name, (x0, y0, x1, y1) in p.regions.items():
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, name
        assert set(p.activity) <= set(p.regions)
        assert 0 <= p.focus_x <= 1


# ---- the HUD signal ----------------------------------------------------------


def test_a_popup_is_a_spike_and_an_idle_region_is_quiet():
    rng = np.random.default_rng(1)
    idle = rng.integers(60, 90, size=(120, 18, 40)).astype(np.uint8)     # a dim, busy-ish world
    popup = idle.copy()
    popup[60:64, 8:12, 10:30] = 250                                       # white text appears
    quiet = events.region_signal(idle)
    spiky = events.region_signal(popup)
    assert quiet.max() < 0.35
    assert spiky[60:64].max() > 0.6
    assert spiky[:55].max() < 0.35


def test_a_one_sample_clip_has_no_signal_and_does_not_crash():
    assert events.region_signal(np.zeros((1, 4, 4), np.uint8)).tolist() == [0.0]


# ---- choosing cuts -----------------------------------------------------------


def test_dead_air_needs_no_speech_and_nothing_happening():
    speech = [True] * 3 + [False] * 8 + [True] * 3
    active = [False] * 14
    assert tighten.dead_ranges(speech, active, 14.0) == [[3 + tighten.DEAD_LEAD, 11 - tighten.DEAD_LEAD]]
    active[6] = True                                                      # a kill lands mid-stretch
    cuts = tighten.dead_ranges(speech, active, 14.0)
    assert len(cuts) == 2                                                 # the dead stretch either side goes
    assert not any(a < 7.0 and b > 6.0 for a, b in cuts)                  # the second it happened in stays


def test_a_dead_stretch_at_the_start_goes_entirely():
    cuts = tighten.dead_ranges([False] * 4 + [True] * 8, [False] * 12, 12.0)
    assert cuts == [[0.0, 4 - tighten.DEAD_LEAD]]


def test_a_clip_is_never_chopped_up_or_emptied():
    many = [[i * 6.0, i * 6.0 + 2.5] for i in range(1, 9)]
    chosen = tighten.choose_cuts(many, 60.0)
    assert len(chosen) <= tighten.AUTO_MAX_CUTS
    assert sum(b - a for a, b in chosen) <= 60.0 * tighten.AUTO_MAX_FRACTION
    # Everything dead: refuse, rather than render a stub.
    assert tighten.choose_cuts([[0.0, 18.0]], 20.0) == []


def test_no_cut_leaves_a_sliver_of_video_between_joins():
    chosen = tighten.choose_cuts([[5.0, 8.0], [9.0, 12.0]], 40.0)        # 1.0s between them
    keep = tighten.keep_from_cuts(chosen, 40.0)
    assert all(b - a >= tighten.AUTO_MIN_KEEP for a, b in keep)


def test_a_short_clip_is_left_alone():
    assert tighten.auto_plan(__file__, 0.0, 6.0, []) is None


def test_keep_ranges_cover_exactly_what_is_not_cut():
    keep = tighten.keep_from_cuts([[3.0, 5.0], [10.0, 12.0]], 20.0)
    assert keep == [[0.0, 3.0], [5.0, 10.0], [12.0, 20.0]]


# ---- crossfaded joins --------------------------------------------------------


def _edit(keep, transition=0.2, duration=30.0):
    return EditList.from_dict({"keep": keep, "transition": transition}, duration=duration)


def test_a_crossfade_shortens_the_clip_by_one_overlap_per_join():
    e = _edit([[0, 5], [8, 14], [20, 26]])
    assert e.overlap() == 0.2
    assert abs(e.final_duration() - (5 + 6 + 6 - 2 * 0.2)) < 1e-6


def test_the_overlap_can_never_swallow_a_short_section():
    e = _edit([[0, 5], [8, 8.9]], transition=0.6)                         # 0.9s section
    assert e.overlap() <= 0.9 / 3 + 1e-6
    assert _edit([[0, 5]], transition=0.5, duration=10.0) is not None
    assert _edit([[0, 5]]).overlap() == 0.0                               # one section: nothing to join


def test_a_hard_cut_still_means_no_overlap():
    e = _edit([[0, 5], [8, 14]], transition=0.0)
    assert e.overlap() == 0.0 and abs(e.final_duration() - 11.0) < 1e-6
    assert "concat=n=2" in concat_graph(e.keep, "0:a", e.overlap())[0]
    assert "xfade" not in concat_graph(e.keep, "0:a", 0.0)[0]


def test_remap_follows_the_overlap_so_captions_land_where_the_picture_is():
    e = _edit([[0, 5], [8, 14]])
    assert e.remap(2.0) == 2.0
    assert abs(e.remap(8.0) - (5 - 0.2)) < 1e-6                           # second section starts one overlap early
    assert abs(e.remap(11.0) - (5 - 0.2 + 3)) < 1e-6
    assert e.remap(6.0) is None                                           # in the cut
    lines = remap_lines([{"start": 9.0, "end": 10.0, "text": "hi"}], e)
    assert abs(lines[0]["start"] - (5 - 0.2 + 1)) < 0.011


def test_the_filter_graph_chains_one_xfade_and_one_acrossfade_per_join():
    graph, v, a = concat_graph([(0, 5), (8, 14), (20, 26)], "0:a", 0.2)
    assert (v, a) == ("[vcut]", "[acut]")
    assert graph.count("xfade") == 2 and graph.count("acrossfade") == 2
    # First join starts one overlap before the first section ends; the second
    # one overlap before everything joined so far ends.
    assert "offset=4.800" in graph
    assert "offset=10.600" in graph


def test_an_edit_without_a_transition_is_unchanged():
    e = EditList.from_dict({"keep": [[0, 5], [8, 14]]}, duration=30.0)
    assert e.transition == 0.0 and e.overlap() == 0.0


def test_cutting_never_takes_a_long_clip_below_the_job_minimum():
    """Long-clips jobs exist to make clips OVER a minute. Dead air still goes,
    but only as much as leaves the clip at or above that floor."""
    dead = [[10.0, 14.0], [20.0, 24.0], [30.0, 34.0], [40.0, 44.0]]      # 16s of candidates
    out = tighten.choose_cuts(dead, 66.0, floor=61.0)
    removed = sum(b - a + tighten.TRANSITION for a, b in out)
    assert 66.0 - removed >= 61.0
    assert out                                                           # some dead air did go
    assert tighten.choose_cuts(dead, 62.0, floor=61.0) == []              # no room at all
    # Without a floor the same clip is tightened further.
    loose = tighten.choose_cuts(dead, 66.0)
    assert sum(b - a for a, b in loose) > sum(b - a for a, b in out)
    assert tighten.auto_plan(__file__, 0.0, 61.5, [], floor=61.0) is None   # no room: not even analysed


def test_a_game_clip_with_no_framing_chosen_is_locked_and_an_explicit_track_is_not(tmp_path):
    """The renderer treats an absent framing as "use the profile" and an
    explicit "track" as the plain tracker; the preview cache must too."""
    from server.media import TrackingCache

    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    auto = TrackingCache.key(1, src, 0, 5, None, "auto", False, "m", 8)
    track = TrackingCache.key(1, src, 0, 5, None, "track", False, "m", 8)
    assert auto != track
