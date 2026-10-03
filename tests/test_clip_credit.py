"""Clip credits: a branding profile's "credit" block, filled from the source video."""

import shutil
import sqlite3
import subprocess

import pytest

from core.binaries import ffmpeg
from video_editor import credit

def _have_ffmpeg() -> bool:
    try:
        return bool(ffmpeg())
    except Exception:
        return False


needs_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="FFmpeg not available")


def test_credit_is_off_unless_enabled_and_valid():
    assert credit.style_of(None) is None
    assert credit.style_of({"type": "image"}) is None
    assert credit.style_of({"credit": {"enabled": False}}) is None
    # a corrupt stored block reads as off, it must never fail a render
    assert credit.style_of({"credit": {"enabled": True, "font": "Nope"}}) is None
    assert credit.style_of({"credit": {"enabled": True, "template": "Via {channel}"}}).template == "Via {channel}"


def test_source_info_reads_the_channel_of_the_source_video(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE videos (video_id TEXT, title TEXT, channel_name TEXT, channel_url TEXT, source_url TEXT)")
    conn.execute("INSERT INTO videos VALUES ('abc', 'A Title', 'Some Streamer', 'https://x/c', 'https://x/v')")
    conn.commit()
    conn.close()
    cfg = {"paths": {"data_dir": str(tmp_path)}}

    assert credit.source_info(tmp_path / "downloads" / "abc.mp4", cfg) == {
        "channel": "Some Streamer", "title": "A Title", "url": "https://x/c"}
    assert credit.source_info(tmp_path / "downloads" / "zzz.mp4", cfg)["channel"] == ""
    assert credit.source_info(tmp_path / "x.mp4", {"paths": {"data_dir": str(tmp_path / "nowhere")}})["channel"] == ""


def _clip(path, size="1080x1920", seconds=3):
    subprocess.run(
        [ffmpeg(), "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=duration={seconds}", "-shortest", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True)


def _frame_diff(a, b) -> float:
    """Mean pixel difference at 1.0s between two clips."""
    import numpy as np

    def grab(p):
        r = subprocess.run([ffmpeg(), "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.uint8).astype(float)

    return float(abs(grab(a) - grab(b)).mean())


@needs_ffmpeg
def test_a_credit_is_burned_onto_the_clip(tmp_path):
    clip, ref = tmp_path / "clip.mp4", tmp_path / "ref.mp4"
    _clip(clip)
    shutil.copy(clip, ref)
    style = credit.style_of({"credit": {"enabled": True, "template": "Clip: {channel}", "seconds": 3}})

    added = credit.apply(clip, style, (1080, 1920), channel="Some Streamer", title="", url="",
                         duration=3.0, asset_dir=tmp_path)

    assert added and clip.exists()
    assert _frame_diff(clip, ref) > 0.05
    assert not list(tmp_path.glob("*.credit.*")), "scratch files must be cleaned up"


@needs_ffmpeg
def test_a_banner_image_sits_under_the_text(tmp_path):
    clip, ref = tmp_path / "clip.mp4", tmp_path / "ref.mp4"
    _clip(clip)
    shutil.copy(clip, ref)
    subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", "color=c=red@0.9:s=600x160,format=rgba",
                    "-frames:v", "1", str(tmp_path / "plate.png")], capture_output=True, check=True)
    style = credit.style_of({"credit": {"enabled": True, "bg_image": "plate.png", "seconds": 3}})

    assert credit.apply(clip, style, (1080, 1920), channel="Some Streamer", title="", url="",
                        duration=3.0, asset_dir=tmp_path)
    assert _frame_diff(clip, ref) > 1.0  # a red plate is far more than text alone


@needs_ffmpeg
def test_nothing_to_credit_leaves_the_clip_alone(tmp_path):
    clip = tmp_path / "clip.mp4"
    _clip(clip)
    before = clip.read_bytes()
    style = credit.style_of({"credit": {"enabled": True}})

    assert not credit.apply(clip, style, (1080, 1920), channel="", title="", url="",
                            duration=3.0, asset_dir=tmp_path)
    assert clip.read_bytes() == before
