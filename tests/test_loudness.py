"""Evening out loudness between creators (compilation/loudness.py)."""

import math
import subprocess
from pathlib import Path

import pytest

from compilation import loudness, recipe
from compilation.loudness import Measure, audio_chain, gain_db, parse_loudnorm, trim_db

# ---- the rules --------------------------------------------------------------------


def test_gain_matches_the_target_and_trim_rides_on_top():
    assert gain_db(Measure(-25.0, -10.0, 5.0), -14.0) == pytest.approx(11.0)
    assert gain_db(Measure(-8.0, -1.0, 5.0), -14.0) == pytest.approx(-6.0)
    # The segment's own volume is kept on top of the match, not undone by it.
    assert gain_db(Measure(-25.0, -10.0, 5.0), -14.0, trim_db(2.0)) == pytest.approx(11.0 + 6.02, abs=0.01)


def test_silence_is_never_boosted_and_boosts_are_capped():
    assert gain_db(Measure(-70.0, -70.0, 0.0), -14.0) == 0.0
    assert gain_db(None, -14.0) == 0.0
    assert gain_db(Measure(-45.0, -30.0, 3.0), -14.0) == loudness.MAX_BOOST_DB


def test_volume_as_trim():
    assert trim_db(1.0) == 0.0
    assert trim_db(0.5) == pytest.approx(-6.02, abs=0.01)
    assert trim_db(0) == -math.inf
    assert audio_chain(-math.inf) == "volume=0"
    assert "alimiter" in audio_chain(6.0)  # a boost is always limited


def test_parse_loudnorm_output():
    text = 'blah\n{\n\t"input_i" : "-23.41",\n\t"input_tp" : "-4.02",\n\t"input_lra" : "6.10",\n\t"input_thresh" : "-33.9"\n}\n'
    assert parse_loudnorm(text) == Measure(-23.41, -4.02, 6.10)
    silent = '{ "input_i" : "-inf", "input_tp" : "-inf", "input_lra" : "0.00" }'
    assert parse_loudnorm(silent).lufs == -70.0
    assert parse_loudnorm("no json here") is None


def test_recipe_target_and_volume_range():
    r = recipe.parse({"segments": [{"video_id": "a", "start": 0, "end": 5, "volume": 9}]}, check_files=False)
    assert r.loudness_target == -14.0 and r.segments[0].volume == recipe.MAX_VOLUME
    assert recipe.parse({"loudness_target": -16}, check_files=False, require_segments=False).loudness_target == -16.0
    with pytest.raises(recipe.RecipeError, match="loudness target"):
        recipe.parse({"loudness_target": -30}, check_files=False, require_segments=False)
    assert "loudness_target" in recipe.TEMPLATE_KEYS


# ---- the real thing ---------------------------------------------------------------


def _clip(path: Path, gain_db: float) -> Path:
    """Three seconds of picture with speech-band noise at a given level."""
    from core.binaries import ffmpeg

    subprocess.run(
        [ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=3",
         "-f", "lavfi", "-i", "anoisesrc=d=3:c=pink:a=1",
         "-af", f"highpass=f=200,lowpass=f=4000,volume={gain_db}dB",
         "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def quiet_and_loud(tmp_path):
    # About -27 LUFS (a quiet stream VOD) and -10 LUFS (a loud vlog): the
    # real-world spread this exists for.
    return _clip(tmp_path / "quiet.mp4", -9), _clip(tmp_path / "loud.mp4", 8)


def _render(src: Path, out: Path, rec, volume: float = 1.0) -> Path:
    from compilation import render

    render.render_part(src, out, start=0.0, duration=3.0, recipe=rec, has_audio=True, volume=volume)
    return out


def test_creators_come_out_matched(tmp_path, quiet_and_loud):
    quiet, loud = quiet_and_loud
    before = [loudness.measure(p, 0, 3).lufs for p in (quiet, loud)]
    assert before[1] - before[0] > 12  # really far apart to begin with
    rec = recipe.parse({"segments": [{"video_id": "a", "start": 0, "end": 3}]}, check_files=False)
    after = [loudness.measure(_render(p, tmp_path / f"o{i}.mp4", rec), 0, 3) for i, p in enumerate((quiet, loud))]
    for m in after:
        assert m.lufs == pytest.approx(-14.0, abs=1.0)
        assert m.peak <= -0.9  # the limiter held the ceiling (a little encoder overshoot allowed)


def test_a_segment_volume_is_kept_when_evening_out(tmp_path, quiet_and_loud):
    quiet, _ = quiet_and_loud
    rec = recipe.parse({"segments": [{"video_id": "a", "start": 0, "end": 3}]}, check_files=False)
    plain = loudness.measure(_render(quiet, tmp_path / "plain.mp4", rec), 0, 3).lufs
    quieter = loudness.measure(_render(quiet, tmp_path / "trim.mp4", rec, volume=0.5), 0, 3).lufs
    # Before, the normaliser undid this; now a -6 dB trim comes out ~6 dB down.
    assert plain - quieter == pytest.approx(6.0, abs=1.0)


def test_measurements_are_cached_to_disk(tmp_path, quiet_and_loud, monkeypatch):
    quiet, _ = quiet_and_loud
    cache = tmp_path / "loud.json"
    first = loudness.measure(quiet, 0, 3, cache)
    assert cache.exists()
    loudness._memory.clear()

    def boom(*a, **k):
        raise AssertionError("measured again instead of using the cache")

    monkeypatch.setattr(loudness.subprocess, "run", boom)
    assert loudness.measure(quiet, 0, 3, cache) == first


def test_measure_route_reports_each_creator_and_the_spread(tmp_path, quiet_and_loud):
    pytest.importorskip("httpx")
    import shutil

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from compilation import store
    from core.state import StateDB
    from server import compilations_api

    quiet, loud = quiet_and_loud
    downloads = tmp_path / "data" / "downloads"
    downloads.mkdir(parents=True)
    shutil.copy(quiet, downloads / "q.mp4")
    shutil.copy(loud, downloads / "l.mp4")
    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("q", title="Quiet", channel_name="Ann", duration=3)
    d.upsert_video("l", title="Loud", channel_name="Bob", duration=3)
    cid = store.create(d, "Mix", {"segments": [{"video_id": "q", "start": 0, "end": 3},
                                               {"video_id": "l", "start": 0, "end": 3, "volume": 0.5}]})
    d.close()

    class _W:
        running: dict = {}  # noqa: RUF012 (test fake)

        def notify(self):
            pass

    class _B:
        def publish(self, _):
            pass

    app = FastAPI()
    compilations_api.install(app, config={"paths": {"data_dir": str(tmp_path / "data")}},
                             db=lambda: StateDB(db_path), data_dir=tmp_path / "data", worker=_W(), broadcaster=_B())
    got = TestClient(app, base_url="http://127.0.0.1").post(f"/compilations/{cid}/loudness").json()
    q, l = sorted((p for p in got["parts"] if p["kind"] == "segment"), key=lambda p: p["index"])
    assert got["target"] == -14.0 and got["spread"] > 12
    assert q["match_db"] > 10 and l["match_db"] < 0          # quiet raised, loud lowered
    assert l["trim_db"] == pytest.approx(-6.0, abs=0.1)       # its own -6 dB kept on top
    assert (tmp_path / "data" / "cache" / "loudness.json").exists()  # shared with the render
