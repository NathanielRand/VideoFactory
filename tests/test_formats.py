"""Output formats: the canvas table, the cropper at every canvas, multi-format
compilations, and AI-clip variants."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from compilation import recipe, render
from core.state import StateDB
from formats import profiles, variants

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not available")


def _src(path: Path, size: str = "1280x720", seconds: int = 3) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        capture_output=True, check=True,
    )
    return path


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


# ---- the table ---------------------------------------------------------------------


def test_every_profile_names_a_known_canvas():
    for p in profiles.PROFILES.values():
        assert p["canvas"] in profiles.CANVASES


def test_normalize_dedupes_keeps_order_and_refuses_unknowns():
    assert profiles.normalize(["4:5", "9:16", "4:5"]) == ["4:5", "9:16"]
    with pytest.raises(ValueError, match="21:9"):
        profiles.normalize(["21:9"])


def test_length_warnings_name_the_platform_and_its_cap():
    w = profiles.length_warnings("9:16", 200)
    assert any("YouTube Shorts" in x and "180" in x for x in w)
    assert not any("TikTok" in x for x in w)  # TikTok allows 600s
    assert profiles.length_warnings("9:16", 30) == []
    assert profiles.tag("4:5") == "4x5"


# ---- the cropper at every canvas ------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("size", [(1080, 1080), (1080, 1350), (1920, 1080), (1080, 1920)])
@pytest.mark.parametrize("mode", ["track", "fit_blur", "split"])
def test_cropper_renders_every_canvas_without_distortion(tmp_path, size, mode):
    from video.cropper import render_vertical

    clip = _src(tmp_path / "src.mp4")
    tracking = {
        "track": {"mode": "track", "path": [(0.0, 0.3), (3.0, 0.7)]},
        "fit_blur": {"mode": "fit_blur", "region": None},
        "split": {"mode": "split", "webcam_box": (0.0, 0.0, 0.3, 0.3)},
    }[mode]
    out = render_vertical(clip, tracking, tmp_path / "out.mp4", size=size)
    assert _size(out) == size


@needs_ffmpeg
def test_a_narrow_source_is_padded_not_stretched_into_a_wider_canvas(tmp_path):
    from video.cropper import render_vertical

    tall = _src(tmp_path / "tall.mp4", size="360x640")
    out = render_vertical(tall, {"mode": "track", "path": [(0.0, 0.5)]}, tmp_path / "sq.mp4", size=(1080, 1080))
    assert _size(out) == (1080, 1080)


# ---- compilations in several formats -------------------------------------------------------


def test_recipe_outputs_default_to_the_canvas_and_lead_with_it():
    r = recipe.parse({"canvas": "9:16", "segments": [{"video_id": "a", "start": 0, "end": 3}]}, check_files=False)
    assert r.outputs == ["9:16"]
    r = recipe.parse(
        {"outputs": ["4:5", "16:9", "4:5"], "segments": [{"video_id": "a", "start": 0, "end": 3}]},
        check_files=False,
    )
    assert r.outputs == ["4:5", "16:9"] and r.canvas == "4:5"
    with pytest.raises(recipe.RecipeError, match="output format"):
        recipe.parse({"outputs": ["3:2"], "segments": [{"video_id": "a", "start": 0, "end": 3}]}, check_files=False)


@needs_ffmpeg
def test_render_all_writes_one_file_per_format(tmp_path):
    src = _src(tmp_path / "s.mp4", seconds=4)
    r = recipe.parse({
        "outputs": ["9:16", "16:9", "1:1"],
        "segments": [{"video_id": "s", "start": 0, "end": 2}, {"video_id": "s", "start": 2, "end": 4}],
        "transition": {"type": "fade", "duration": 0.3},
    })
    labels = []
    outs = render.render_all(
        r, {"s": render.SourceInfo(path=src, channel="Ann")}, tmp_path / "out", "Best of [1]",
        on_progress=lambda i, t, label: labels.append(label), parallel=3,
    )
    assert set(outs) == {"9:16", "16:9", "1:1"}
    assert _size(outs["9:16"]) == (1080, 1920)
    assert _size(outs["16:9"]) == (1920, 1080)
    assert _size(outs["1:1"]) == (1080, 1080)
    assert outs["1:1"].name == "Best of [1] 1x1.mp4"
    assert any(label.startswith("[16:9]") for label in labels)


# ---- AI clip variants ---------------------------------------------------------------------------


def _clip_fixture(tmp_path: Path, render_opts: dict | None = None) -> tuple[StateDB, int, dict]:
    data = tmp_path / "data"
    (data / "downloads").mkdir(parents=True)
    (data / "transcripts").mkdir()
    (data / "downloads" / "v1.mp4").write_bytes(b"src")
    (data / "transcripts" / "v1.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    clip_dir = data / "clips" / "x"
    clip_dir.mkdir(parents=True)
    (clip_dir / "clip_00001-00005.mp4").write_bytes(b"clip")
    db = StateDB(data / "state.db")
    db.upsert_video("v1", title="V")
    clip_id = db.add_clip("v1", 1, 5, 80, "hook", path=str(clip_dir / "clip_00001-00005.mp4"))
    if render_opts:
        db.set_clip(clip_id, render_opts=json.dumps(render_opts))
    config = {"paths": {"data_dir": str(data)}, "video": {"parallel_renders": 3}}
    return db, clip_id, config


def test_variants_skip_the_clips_own_shape_and_share_one_tracking(tmp_path, monkeypatch):
    import core.pipeline as pipeline

    db, clip_id, config = _clip_fixture(tmp_path)
    calls = []

    def fake_render(source, candidate, segments, clip_dir, cfg, opts, lang, tracking_cache=None):
        calls.append((opts["canvas"], id(tracking_cache), "tracking" in tracking_cache))
        tracking_cache.setdefault("tracking", {"mode": "track", "path": [(0, 0.5)]})
        path = clip_dir / f"clip_00001-00005.{profiles.tag(opts['canvas'])}.mp4"
        path.write_bytes(b"v")
        return path, ""

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_: "en")
    done = variants.render(db, clip_id, ["9:16", "4:5", "1:1", "16:9"], config)

    assert set(done) == {"4:5", "1:1", "16:9"}          # 9:16 is the clip itself
    assert len({c[1] for c in calls}) == 1              # one shared cache
    assert calls[0] == ("4:5", calls[0][1], False)      # first render computes tracking
    assert all(had for _, _, had in calls[1:])          # the rest reuse it
    rows = variants.list_for(db, clip_id)
    assert [r["canvas"] for r in rows] == ["16:9", "1:1", "4:5"] and all(r["exists"] for r in rows)


def test_a_landscape_clip_counts_16x9_as_its_own_shape(tmp_path, monkeypatch):
    import core.pipeline as pipeline

    db, clip_id, config = _clip_fixture(tmp_path, {"profile": "short_clips"})
    monkeypatch.setattr(pipeline, "_render_files", lambda *a, **k: pytest.fail("nothing to render"))
    assert variants.render(db, clip_id, ["16:9"], config) == {}


def test_rerendering_a_clip_keeps_its_variants_but_flags_them(tmp_path):
    db, clip_id, _ = _clip_fixture(tmp_path)
    db.conn.execute(
        "INSERT INTO clip_variants (clip_id, canvas, path, created_at) VALUES (?, '4:5', 'x.mp4', 'now')", (clip_id,)
    )
    saved = db.detach_clip_rows(clip_id)
    assert "clip_variants" in saved
    db.reattach_clip_rows(clip_id + 100, saved)
    assert db.conn.execute("SELECT clip_id FROM clip_variants").fetchone()["clip_id"] == clip_id + 100


def test_variants_api_queues_a_job_and_validates_formats(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import formats_api

    db, clip_id, config = _clip_fixture(tmp_path)
    db_path = Path(config["paths"]["data_dir"]) / "state.db"

    class _W:
        def notify(self):
            pass

    class _B:
        def publish(self, _):
            pass

    app = FastAPI()
    formats_api.install(app, db=lambda: StateDB(db_path), data_dir=Path(config["paths"]["data_dir"]),
                        worker=_W(), broadcaster=_B())
    c = TestClient(app, base_url="http://127.0.0.1")
    assert "youtube_shorts" in c.get("/formats").json()["profiles"]
    info = c.get(f"/clips/{clip_id}/variants").json()
    assert info["canvas"] == "9:16" and info["variants"] == []
    assert c.post(f"/clips/{clip_id}/variants", json={"canvases": ["3:2"]}).status_code == 400
    assert c.post(f"/clips/{clip_id}/variants", json={"canvases": []}).status_code == 400
    r = c.post(f"/clips/{clip_id}/variants", json={"canvases": ["4:5", "1:1"]})
    assert r.status_code == 200
    job = StateDB(db_path).conn.execute("SELECT * FROM jobs WHERE id = ?", (r.json()["job_id"],)).fetchone()
    assert job["type"] == "variants" and json.loads(job["payload"])["canvases"] == ["4:5", "1:1"]
    assert job["video_id"] == ""  # must not trip the "video already queued" guard
