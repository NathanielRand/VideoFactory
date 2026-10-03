"""Logo frames and the timed call to action (video_editor/watermark.py)."""

from video_editor import watermark

CTA = {"enabled": True, "text": "Upload your clips to our Discord!", "at": 2, "duration": 3}


def test_cta_counts_from_the_start_by_default():
    assert watermark.cta_windows(CTA, 30) == [(2.0, 5.0)]


def test_cta_can_count_back_from_the_end():
    assert watermark.cta_windows({**CTA, "anchor": "end"}, 30) == [(25.0, 28.0)]


def test_cta_repeats_and_stays_inside_the_clip():
    assert watermark.cta_windows({**CTA, "repeat": 10}, 24) == [(2.0, 5.0), (12.0, 15.0), (22.0, 24.0)]


def test_cta_repeat_never_overlaps_itself():
    wins = watermark.cta_windows({**CTA, "repeat": 1}, 20)
    assert all(b[0] >= a[1] for a, b in zip(wins, wins[1:]))


def test_disabled_or_empty_cta_burns_nothing():
    assert not watermark.has_cta({"cta": {**CTA, "enabled": False}})
    assert not watermark.has_cta({"cta": {**CTA, "text": "  {} "}})
    assert not watermark.has_overlay_text({"type": "image"})


def test_cta_alone_writes_an_ass_file(tmp_path):
    cfg = {"type": "image", "cta": {**CTA, "text": "Vote {\\b1}A or B"}}
    assert watermark.has_overlay_text(cfg)
    out = watermark.ensure_text(None, tmp_path / "c.ass", cfg, (1080, 1920), duration=10)
    ass = out.read_text(encoding="utf-8")
    assert "Style: CTA," in ass and "Style: Watermark" not in ass
    # the user's own override tags are stripped; only ours remain
    assert "Vote b1A or B" in ass
    assert ass.count("Dialogue: 3,0:00:02.00,0:00:05.00,CTA") == 1


def test_compilations_can_leave_the_cta_out(tmp_path):
    cfg = {"type": "text", "text": "@me", "cta": CTA}
    ass = watermark.ensure_text(None, tmp_path / "c.ass", cfg, (1080, 1920), 10, with_cta=False)
    assert "CTA" not in ass.read_text(encoding="utf-8")


def test_frames_crop_the_logo():
    assert "crop" not in watermark._frame_chain("free", 200)
    assert "crop" not in watermark._frame_chain(None, 200)
    square = watermark._frame_chain("square", 200)
    assert "crop=" in square and "scale=200:200" in square and "geq" not in square
    assert "geq=" in watermark._frame_chain("circle", 200)


def test_line_breaks_cannot_break_the_ass_file(tmp_path):
    cfg = {"type": "text", "text": "@me\nsecond", "cta": {**CTA, "text": "Join\r\nthe Discord"}}
    ass = watermark.ensure_text(None, tmp_path / "c.ass", cfg, (1080, 1920), 10).read_text(encoding="utf-8")
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == 2
    assert events[0].endswith("@me second") and events[1].endswith("Join the Discord")
