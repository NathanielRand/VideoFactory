"""Compilations: recipe validation, credits, graph building, storage, the API,
and one real render of a multi-source compilation."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from compilation import credits, recipe, render, store
from compilation.recipe import CreditStyle, RecipeError
from core.state import StateDB

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not available")


def _seg(vid="a", start=0.0, end=5.0, **kw):
    return {"video_id": vid, "start": start, "end": end, **kw}


# ---- recipe ----------------------------------------------------------------------


def test_minimal_recipe_gets_defaults():
    r = recipe.parse({"segments": [_seg()]}, check_files=False)
    assert r.canvas == "16:9" and r.size == (1920, 1080)
    assert r.fit == "blur" and r.transition == "none"
    assert r.credits.enabled and r.credits.template == "Clip: {channel}"
    assert r.segments[0].duration == 5.0


@pytest.mark.parametrize("bad, message", [
    ({"canvas": "21:9"}, "canvas"),
    ({"fit": "stretch"}, "fit"),
    ({"transition": {"type": "spin"}}, "transition"),
    ({"credits": {"position": "middle"}}, "position"),
    ({"segments": [{"start": 0, "end": 5}]}, "video_id"),
    ({"segments": [_seg(end=0.2)]}, "shorter"),
    ({"segments": [_seg(end="soon")]}, "number"),
    ({"banner": "logo"}, "banner"),
])
def test_bad_recipes_are_refused_with_a_reason(bad, message):
    data = {"segments": [_seg()], **bad}
    with pytest.raises(RecipeError, match=message):
        recipe.parse(data, check_files=False)


def test_no_segments_is_only_an_error_when_rendering():
    assert recipe.parse({}, check_files=False, require_segments=False).segments == []
    with pytest.raises(RecipeError, match="at least one segment"):
        recipe.parse({}, check_files=False)


def test_segments_must_exist_in_the_library_and_are_clamped_to_it():
    with pytest.raises(RecipeError, match="not in the library"):
        recipe.parse({"segments": [_seg("zzz")]}, durations={"a": 10.0}, check_files=False)
    r = recipe.parse({"segments": [_seg("a", 2, 99)]}, durations={"a": 10.0}, check_files=False)
    assert r.segments[0].end == 10.0


def test_transition_never_outlasts_half_the_shortest_part():
    r = recipe.parse(
        {"segments": [_seg(end=1.0), _seg(end=8)], "transition": {"type": "fade", "duration": 2}},
        check_files=False,
    )
    assert r.transition == "fade" and r.transition_duration <= 0.45
    tiny = recipe.parse(
        {"segments": [_seg(end=0.6), _seg(end=8)], "transition": {"type": "fade", "duration": 2}},
        check_files=False,
    )
    assert tiny.transition == "fade" and tiny.transition_duration <= 0.25


def test_blur_regions_are_clamped_inside_the_frame():
    r = recipe.parse({"segments": [_seg(blur_regions=[[0.9, 0.9, 0.5, 0.5], [0, 0, 0, 0]])]}, check_files=False)
    (x, y, w, h), = r.segments[0].blur_regions
    assert x + w <= 1.0 and y + h <= 1.0


def test_missing_intro_file_is_refused(tmp_path):
    with pytest.raises(RecipeError, match="intro"):
        recipe.parse({"segments": [_seg()], "intro": {"path": str(tmp_path / "nope.mp4")}})


def test_template_is_the_look_without_the_segments():
    full = {"canvas": "9:16", "segments": [_seg()], "transition": {"type": "fade"}, "title": "x"}
    tpl = recipe.template_of(full)
    assert tpl == {"canvas": "9:16", "transition": {"type": "fade"}}
    merged = recipe.apply_template({"canvas": "1:1"}, {"canvas": "16:9", "segments": [_seg()]})
    assert merged["canvas"] == "1:1" and merged["segments"] == [_seg()]


# ---- credits ---------------------------------------------------------------------


def test_credit_text_fills_the_template_and_cannot_inject_ass():
    style = CreditStyle(template="via {channel}")
    assert credits.credit_text(style, channel="Big {\\b1}Creator") == "via Big (b1)Creator"


def test_credit_falls_back_to_title_then_to_nothing():
    style = CreditStyle()
    assert credits.credit_text(style, channel="", title="Cool video") == "Clip: Cool video"
    assert credits.credit_text(style, channel="", title="") == ""


def test_a_template_typo_degrades_to_the_name():
    assert credits.credit_text(CreditStyle(template="{chanel}!"), channel="Ann") == "Ann"


def test_credit_ass_scales_to_the_canvas_and_ends_inside_the_segment():
    ass = credits.build_ass("Clip: Ann", CreditStyle(seconds=10, font_size=44), (1080, 1920), 3.0)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "0:00:02.90" in ass  # clamped to the 3s segment


def test_credit_can_stay_up_for_the_whole_segment():
    ass = credits.build_ass("Clip: Ann", CreditStyle(seconds=2, whole_clip=True), (1920, 1080), 12.0)
    assert "0:00:11.90" in ass  # the segment's end, not the 2s
    r = recipe.parse({"segments": [_seg()], "credits": {"whole_clip": True}}, check_files=False)
    assert r.credits.whole_clip and not recipe.parse({"segments": [_seg()]}, check_files=False).credits.whole_clip


def test_credit_look_is_validated():
    r = recipe.parse({"segments": [_seg()], "credits": {
        "font": "Impact", "bold": False, "italic": True, "color": "#ff8800",
        "backing": "outline", "bg_image": "abc.png", "bg_scale": 99,
    }}, check_files=False)
    c = r.credits
    assert (c.font, c.bold, c.italic, c.color, c.backing, c.bg_image) == (
        "Impact", False, True, "#FF8800", "outline", "abc.png")
    assert c.bg_scale == 6.0  # clamped
    for bad in ({"font": "Wingdings"}, {"color": "red"}, {"backing": "glow"}, {"bg_image": "../x.png"}):
        with pytest.raises(RecipeError):
            recipe.parse({"segments": [_seg()], "credits": bad}, check_files=False)


def test_credit_style_reaches_the_ass():
    ass = credits.build_ass("Ann", CreditStyle(font="Georgia", italic=True, bold=False, color="#FF8800",
                                               backing="none"), (1920, 1080), 5.0)
    assert "Style: Credit,Georgia,44,&H000088FF," in ass
    assert ",0,-1,0,0,100,100,0,0,1,0,0,1," in ass  # not bold, italic, no border


def test_plate_sits_in_the_corner_and_the_text_is_centred_and_fitted_on_it():
    style = CreditStyle(position="bottom_right", font_size=44, bg_scale=2.5)
    plate = credits.plate_for(style, (1920, 1080), Path("p.png"), (400, 100))
    assert (plate.h, plate.w) == (110, 440)  # 44px * 2.5 tall, 4:1 kept
    assert plate.x + plate.w == 1920 - 49 and plate.y + plate.h == 1080 - 49
    ass = credits.build_ass("Clip: Ann", style, (1920, 1080), 5.0, plate)
    assert f"\\pos({plate.x + 220},{plate.y + 55})" in ass and ",5," in ass
    long = "Clip: " + "An extremely long creator name indeed" * 2
    assert credits.fit_px(long, style, (1920, 1080), plate) < 44


def test_wide_plate_is_capped_to_the_canvas():
    plate = credits.plate_for(CreditStyle(bg_scale=6), (1080, 1920), Path("p.png"), (2000, 100))
    assert plate.w <= 1080 - 2 * 49 and plate.w % 2 == 0 and plate.h % 2 == 0


# ---- graph building --------------------------------------------------------------------


def test_xfade_offsets_account_for_each_overlap():
    g = render.xfade_graph([5.0, 4.0, 6.0], "fade", 0.5)
    assert "offset=4.500" in g           # 5 - 0.5
    assert "offset=8.000" in g           # (5 + 4 - 0.5) - 0.5
    assert g.count("acrossfade") == 2 and "[vout]" in g and "[aout]" in g


def test_fit_modes_never_distort():
    assert "force_original_aspect_ratio=increase" in render.fit_chain("crop", (1080, 1920), "i", "o")
    assert "pad=1080:1920" in render.fit_chain("pad", (1080, 1920), "i", "o")
    blur = render.fit_chain("blur", (1080, 1920), "i", "o")
    assert "gblur" in blur and "overlay=(W-w)/2:(H-h)/2" in blur


def test_no_blur_regions_means_no_blur_stage():
    assert render.blur_chain([], "0:v", "o") == ""
    assert render.blur_chain([(0, 0, 0.5, 0.5)], "0:v", "o").endswith("[o]")


# ---- storage ---------------------------------------------------------------------------


def test_video_credit_columns_and_editing(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="T", channel_name="Ann", source_url="https://y/1", channel_url="https://y/@ann")
    d.upsert_video("v1", title="T")  # a later upsert without them keeps them
    row = d.conn.execute("SELECT * FROM videos WHERE video_id='v1'").fetchone()
    assert row["source_url"] == "https://y/1" and row["channel_url"] == "https://y/@ann"
    assert row["rights"] == "unknown"
    assert d.set_video_credit("v1", rights="permission", channel_name=" Annie ")
    row = d.conn.execute("SELECT * FROM videos WHERE video_id='v1'").fetchone()
    assert row["rights"] == "permission" and row["channel_name"] == "Annie"
    with pytest.raises(ValueError):
        d.set_video_credit("v1", rights="stolen")
    assert not d.set_video_credit("nope", rights="own")


def test_editing_a_rendered_recipe_marks_it_draft_again(tmp_path):
    d = StateDB(tmp_path / "s.db")
    cid = store.create(d, "Best of", {"segments": []})
    store.set_status(d, cid, "done", output_path="x.mp4")
    store.update(d, cid, title="Renamed")
    assert store.get(d, cid)["status"] == "done"
    store.update(d, cid, recipe={"segments": [_seg()]})
    assert store.get(d, cid)["status"] == "draft"


def test_templates_store_only_the_look(tmp_path):
    d = StateDB(tmp_path / "s.db")
    tid = store.save_template(d, "Weekly", {"canvas": "9:16", "segments": [_seg()]})
    assert store.get_template(d, tid)["config"] == {"canvas": "9:16"}


# ---- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import compilations_api

    db_path = tmp_path / "state.db"
    data_dir = tmp_path / "data"
    (data_dir / "downloads").mkdir(parents=True)

    class _Worker:
        running: dict = {}  # noqa: RUF012 (test fake)

        def notify(self):
            pass

        def progress_snapshot(self, job_id):
            return self.running.get(job_id)

    class _Broadcaster:
        def publish(self, _):
            pass

    app = FastAPI()
    compilations_api.install(
        app, config={"paths": {"data_dir": str(data_dir)}}, db=lambda: StateDB(db_path),
        data_dir=data_dir, worker=_Worker(), broadcaster=_Broadcaster(),
    )
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path, c.data_dir, c.worker = db_path, data_dir, _Worker
    return c


def test_template_names_are_unique_and_replacing_is_a_put(client):
    a = client.post("/compilation-templates", json={"name": "Look", "config": {"canvas": "9:16"}}).json()
    dup = client.post("/compilation-templates", json={"name": " look ", "config": {"canvas": "1:1"}})
    assert dup.status_code == 409
    b = client.post("/compilation-templates", json={"name": "Look (2)", "config": {"canvas": "1:1"}}).json()
    assert client.put(f"/compilation-templates/{b['id']}", json={"name": "LOOK", "config": {}}).status_code == 409
    replaced = client.put(f"/compilation-templates/{a['id']}", json={"name": "Look", "config": {"canvas": "4:5"}})
    assert replaced.status_code == 200 and replaced.json()["config"]["canvas"] == "4:5"
    assert [t["name"] for t in client.get("/compilation-templates").json()] == ["Look", "Look (2)"]


def test_renaming_a_template_keeps_its_look_and_stays_unique(client):
    a = client.post("/compilation-templates", json={"name": "Look", "config": {"canvas": "9:16"}}).json()
    client.post("/compilation-templates", json={"name": "Other", "config": {"canvas": "1:1"}})
    got = client.patch(f"/compilation-templates/{a['id']}", json={"name": "  Vertical look "})
    assert got.status_code == 200
    assert got.json()["name"] == "Vertical look" and got.json()["config"] == {"canvas": "9:16"}
    # Its own name in another case is fine; someone else's is not.
    assert client.patch(f"/compilation-templates/{a['id']}", json={"name": "VERTICAL LOOK"}).status_code == 200
    assert client.patch(f"/compilation-templates/{a['id']}", json={"name": "other"}).status_code == 409
    assert client.patch(f"/compilation-templates/{a['id']}", json={"name": "   "}).status_code == 400
    assert client.patch("/compilation-templates/999", json={"name": "x"}).status_code == 404


def test_a_template_saves_even_if_a_segment_video_left_the_library(client):
    # The look is all a template keeps, so a stale segment must not block it.
    recipe = {"canvas": "9:16", "segments": [_seg("gone")]}
    r = client.post("/compilation-templates", json={"name": "Look", "config": recipe})
    assert r.status_code == 201
    assert r.json()["config"] == {"canvas": "9:16"}
    tid = r.json()["id"]
    assert client.put(f"/compilation-templates/{tid}", json={"name": "Look", "config": recipe}).status_code == 200


def test_render_progress_is_reported_per_compilation(client):
    d = StateDB(client.db_path)
    d.add_job("process", json.dumps({"url": "x"}), video_id="v0")
    d.add_job("compile", json.dumps({"compilation_id": 6}))
    # Renders go ahead of waiting videos, but not of each other.
    d.add_job("compile", json.dumps({"compilation_id": 7}))
    running = d.add_job("compile", json.dumps({"compilation_id": 8}))
    d.conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (running,))
    d.conn.commit()
    d.close()
    client.worker.running = {running: {"percent": 42, "label": "Segment 2/5", "eta_seconds": 90}}
    got = client.get("/compilations/progress").json()
    assert got["7"]["state"] == "queued" and got["7"]["ahead"] == 1
    assert got["8"] == {"job_id": running, "state": "running", "percent": 42,
                        "label": "Segment 2/5", "eta_seconds": 90}


def test_cancelling_a_waiting_render_takes_it_off_the_queue(client):
    comp = client.post("/compilations", json={"title": "C"}).json()
    d = StateDB(client.db_path)
    job = d.add_job("compile", json.dumps({"compilation_id": comp["id"]}))
    store.set_status(d, comp["id"], "queued")
    d.close()
    assert client.post(f"/compilations/{comp['id']}/cancel").json() == {"state": "removed"}
    assert client.get(f"/compilations/{comp['id']}").json()["status"] == "draft"
    d = StateDB(client.db_path)
    assert d.get_job(job) is None
    d.close()


def test_api_create_edit_and_queue_a_render(client):
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="Source", channel_name="Ann", duration=30)
    d.close()
    (client.data_dir / "downloads" / "v1.mp4").write_bytes(b"not really a video")

    tpl = client.post("/compilation-templates", json={"name": "Vertical", "config": {"canvas": "9:16"}}).json()
    comp = client.post("/compilations", json={"title": "Best of", "template_id": tpl["id"]}).json()
    assert comp["recipe"]["canvas"] == "9:16"

    bad = client.patch(f"/compilations/{comp['id']}", json={"recipe": {"canvas": "9:16", "segments": [_seg("v9")]}})
    assert "not in the library" in bad.json()["problem"]
    assert client.post(f"/compilations/{comp['id']}/render").status_code == 400

    client.patch(f"/compilations/{comp['id']}", json={"recipe": {"canvas": "9:16", "segments": [_seg("v1", 1, 9)]}})
    queued = client.post(f"/compilations/{comp['id']}/render")
    assert queued.status_code == 200 and queued.json()["started"] is True
    assert client.get(f"/compilations/{comp['id']}").json()["status"] == "queued"
    assert client.delete(f"/compilations/{comp['id']}").status_code == 409

    lib = client.get("/compilations/library").json()
    assert lib[0]["video_id"] == "v1" and lib[0]["has_source"] is True


def test_api_edits_a_video_credit(client):
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="Source")
    d.close()
    r = client.patch("/videos/v1/credit", json={"channel_name": "Ann", "rights": "own"})
    assert r.json()["channel_name"] == "Ann" and r.json()["rights"] == "own"
    assert client.patch("/videos/v1/credit", json={"rights": "nah"}).status_code == 400
    assert client.patch("/videos/zz/credit", json={"rights": "own"}).status_code == 404


# ---- a real render ------------------------------------------------------------------------


def _make_source(path: Path, size: str, seconds: int, audio: bool) -> Path:
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _probe(path: Path) -> tuple[int, int, float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    w, h = (int(v) for v in out[0].split(","))
    return w, h, float(out[1])


@needs_ffmpeg
@pytest.mark.parametrize("transition", ["none", "fade"])
def test_renders_a_multi_source_compilation(tmp_path, transition):
    wide = _make_source(tmp_path / "wide.mp4", "640x360", 6, audio=True)
    tall = _make_source(tmp_path / "tall.mp4", "360x640", 6, audio=False)  # silent source
    r = recipe.parse({
        "canvas": "9:16",
        "segments": [
            _seg("w", 1, 4, blur_regions=[[0, 0.8, 1, 0.2]]),
            _seg("t", 0, 3),
        ],
        "transition": {"type": transition, "duration": 0.5},
        "credits": {"template": "Clip: {channel}"},
    })
    sources = {
        "w": render.SourceInfo(path=wide, channel="Wide Creator", title="Wide"),
        "t": render.SourceInfo(path=tall, channel="Tall Creator", title="Tall"),
    }
    steps = []
    out = render.render(
        r, sources, tmp_path / "out" / "comp.mp4",
        banner={"type": "text", "text": "@VideoFactory", "position": "top_right"},
        on_progress=lambda i, t, label: steps.append(label),
    )
    w, h, duration = _probe(out)
    assert (w, h) == (1080, 1920)
    expected = 6.0 - (0.5 if transition == "fade" else 0.0)
    assert abs(duration - expected) < 0.25
    assert steps[-1] == "Done" and any("Segment 2/2" in s for s in steps)
    assert not (tmp_path / "out" / "comp.parts").exists()  # scratch cleaned up


@needs_ffmpeg
def test_render_progress_climbs_to_the_end_across_formats(tmp_path):
    src = _make_source(tmp_path / "src.mp4", "640x360", 8, audio=True)
    r = recipe.parse({
        "canvas": "9:16", "outputs": ["9:16", "16:9"],
        "segments": [_seg("s", 0, 3), _seg("s", 3, 8)],
        "transition": {"type": "fade", "duration": 0.5},
    })
    seen: list[float] = []
    lock = __import__("threading").Lock()

    def heard(fraction, label):
        with lock:
            seen.append(fraction)

    render.render_all(r, {"s": render.SourceInfo(path=src)}, tmp_path / "out", "comp",
                      on_fraction=heard)
    assert seen[-1] == 1.0
    assert all(0.0 <= f <= 1.0 for f in seen)
    # Both formats together: in between readings, not just a start and an end.
    assert len({round(f, 2) for f in seen}) > 4
    assert seen == sorted(seen)


@needs_ffmpeg
def test_cancelling_stops_ffmpeg_mid_pass(monkeypatch):
    import time

    from core import cancel

    heard = []

    def cancelled_after_first_report(key):
        if heard:
            raise cancel.CancelledError(key)

    monkeypatch.setattr(cancel, "check", cancelled_after_first_report)
    # Ten minutes of video: only a cancel that stops FFmpeg ends this quickly.
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=600",
           "-f", "null", "-"]
    started = time.monotonic()
    with pytest.raises(cancel.CancelledError):
        render.run_ffmpeg(cmd, duration=600, on_fraction=heard.append, cancel_key="c")
    assert heard and heard[0] < 0.5
    assert time.monotonic() - started < 20


@needs_ffmpeg
@pytest.mark.parametrize("audio", [True, False])
def test_renders_a_credit_on_a_background_image(tmp_path, audio):
    src = _make_source(tmp_path / "src.mp4", "640x360", 4, audio=audio)
    assets = tmp_path / "assets"
    assets.mkdir()
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red@0.8:s=300x80,format=rgba",
                    "-frames:v", "1", str(assets / "plate.png")], capture_output=True, check=True)
    r = recipe.parse({
        "canvas": "16:9",
        "segments": [_seg("s", 0, 3)],
        "credits": {"bg_image": "plate.png", "whole_clip": True, "font": "Impact"},
    })
    out = render.render(r, {"s": render.SourceInfo(path=src, channel="Ann")},
                        tmp_path / "out" / "c.mp4", banner_assets=assets)
    w, h, duration = _probe(out)
    assert (w, h) == (1920, 1080) and abs(duration - 3.0) < 0.25
    # Mid-segment, the plate's red shows at the bottom-left corner.
    px = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "1.5", "-i", str(out), "-frames:v", "1",
         "-vf", "crop=4:4:60:1000,scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    assert px[0] > 150 and px[1] < 100 and px[2] < 100


def test_missing_credit_image_fails_clearly(tmp_path):
    r = recipe.parse({"segments": [_seg("s", 0, 3)], "credits": {"bg_image": "gone.png"}}, check_files=False)
    with pytest.raises(RuntimeError, match=r"gone\.png"):
        render.render(r, {"s": render.SourceInfo(path=tmp_path / "x.mp4")}, tmp_path / "o" / "c.mp4",
                      banner_assets=tmp_path)


def test_text_can_sit_off_centre_on_the_plate_and_fits_the_narrower_room():
    centred = CreditStyle(font_size=44, bg_scale=2.5)
    moved = CreditStyle(font_size=44, bg_scale=2.5, bg_text_x=0.7, bg_text_y=0.4)
    plate = credits.plate_for(moved, (1920, 1080), Path("p.png"), (400, 100))
    assert f"\\pos({plate.x + 308},{plate.y + 44})" in credits.build_ass("Ann", moved, (1920, 1080), 5.0, plate)
    name = "Clip: A fairly long channel name"
    assert credits.fit_px(name, moved, (1920, 1080), plate) < credits.fit_px(name, centred, (1920, 1080), plate)
    assert recipe.parse({"segments": [_seg()], "credits": {"bg_text_x": 5}}, check_files=False).credits.bg_text_x == 0.9


# ---- versions --------------------------------------------------------------------------


def _fake_render(monkeypatch):
    """store.run with render_all writing tiny files instead of video."""
    def fake(recipe_, sources, out_dir, base_name, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        canvases = recipe_.outputs or [recipe_.canvas]
        outs = {}
        for c in canvases:
            p = out_dir / (f"{base_name}.mp4" if len(canvases) == 1 else f"{base_name} {c.replace(':', 'x')}.mp4")
            p.write_bytes(b"x" * 10)
            outs[c] = p
        return outs
    monkeypatch.setattr(store, "render_all", fake)


def _versioned_comp(client, monkeypatch, renders=1, title="Best of"):
    _fake_render(monkeypatch)
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="Source", channel_name="Ann", duration=30)
    (client.data_dir / "downloads" / "v1.mp4").write_bytes(b"x")
    cid = store.create(d, title, {"segments": [_seg("v1", 0, 5)]})
    config = {"paths": {"data_dir": str(client.data_dir)}}
    for i in range(renders):
        store.update(d, cid, recipe={"segments": [_seg("v1", 0, 5 + i)]})
        store.run(d, cid, config)
    return d, cid, config


def test_each_render_is_a_version_with_its_recipe_and_old_ones_are_pruned(client, monkeypatch):
    d, cid, _ = _versioned_comp(client, monkeypatch, renders=7)
    rows = store.renders(d, cid)
    assert [r["version"] for r in rows] == [7, 6, 5, 4, 3]  # keep_versions defaults to 5
    assert rows[0]["recipe"]["segments"][0]["end"] == 11 and rows[-1]["recipe"]["segments"][0]["end"] == 7
    files = sorted(p.name for p in (client.data_dir / "compilations").iterdir())
    assert files == [f"Best of [{cid}] v{n}.mp4" for n in (3, 4, 5, 6, 7)]
    assert store.get(d, cid)["output_path"].endswith("v7.mp4")


def test_lowering_keep_versions_prunes_now_and_zero_keeps_all(client, monkeypatch):
    d, cid, _ = _versioned_comp(client, monkeypatch, renders=4)
    assert client.put("/compilation-settings", json={"keep_versions": 0}).json()["removed"] == 0
    assert client.put("/compilation-settings", json={"keep_versions": 2}).json()["removed"] == 2
    assert [r["version"] for r in client.get(f"/compilations/{cid}/renders").json()] == [4, 3]
    assert client.get("/compilation-settings").json() == {"keep_versions": 2}


def test_restore_puts_a_versions_recipe_back_and_delete_falls_back_to_the_previous(client, monkeypatch):
    d, cid, _ = _versioned_comp(client, monkeypatch, renders=2)
    v1, v2 = sorted(client.get(f"/compilations/{cid}/renders").json(), key=lambda r: r["version"])
    restored = client.post(f"/compilations/{cid}/renders/{v1['id']}/restore").json()
    assert restored["recipe"]["segments"][0]["end"] == 5
    assert client.get(f"/compilations/{cid}/renders/{v1['id']}/media").status_code == 200
    out = client.delete(f"/compilations/{cid}/renders/{v2['id']}").json()
    assert out["deleted"] and out["compilation"]["output_path"].endswith("v1.mp4")
    client.delete(f"/compilations/{cid}/renders/{v1['id']}")
    assert store.get(d, cid)["output_path"] == "" and store.get(d, cid)["status"] == "draft"


def test_renaming_moves_every_versions_files(client, monkeypatch):
    d, cid, _ = _versioned_comp(client, monkeypatch, renders=2)
    client.patch(f"/compilations/{cid}", json={"title": "Top 10"})
    files = sorted(p.name for p in (client.data_dir / "compilations").iterdir())
    assert files == [f"Top 10 [{cid}] v1.mp4", f"Top 10 [{cid}] v2.mp4"]
    assert store.get(d, cid)["output_path"].endswith(f"Top 10 [{cid}] v2.mp4")
    assert client.get(f"/compilations/{cid}/media").status_code == 200


def test_a_render_from_before_versions_becomes_version_1(client, monkeypatch):
    _fake_render(monkeypatch)
    d = StateDB(client.db_path)
    out = client.data_dir / "compilations"
    out.mkdir(parents=True)
    old = out / "Old [1].mp4"
    old.write_bytes(b"x")
    cid = store.create(d, "Old", {"segments": []})
    store.set_status(d, cid, "done", output_path=str(old), outputs={"16:9": str(old)})
    assert store.next_version(d, cid) == 2
    (v1,) = store.renders(d, cid)
    assert v1["version"] == 1 and v1["recipe"] is None
    assert client.post(f"/compilations/{cid}/renders/{v1['id']}/restore").status_code == 400
    assert client.get("/compilation-files/unused").json() == []  # adopted, not a leftover


def test_unused_files_are_listed_and_cleaned_but_tracked_ones_never(client, monkeypatch):
    d, cid, _ = _versioned_comp(client, monkeypatch, renders=1)
    out = client.data_dir / "compilations"
    (out / "Untitled compilation [9].mp4").write_bytes(b"x" * 100)
    (out / "notes.txt").write_text("not a video")
    (out / "Best of [1] v2.parts").mkdir()  # a render in progress
    assert client.get("/compilation-files/unused").json() == [{"name": "Untitled compilation [9].mp4", "bytes": 100}]
    assert client.delete("/compilation-files/unused").json()["deleted"] == ["Untitled compilation [9].mp4"]
    assert (out / f"Best of [{cid}] v1.mp4").exists() and (out / "notes.txt").exists()


# ---- credit placement ---------------------------------------------------------------


def _margins_of(ass: str) -> tuple[int, int, int]:
    """MarginL, MarginV and the alignment from the Credit style line."""
    fields = next(l for l in ass.splitlines() if l.startswith("Style: Credit")).split(",")
    return int(fields[-4]), int(fields[-2]), int(fields[-5])


def test_unset_insets_keep_the_original_margin():
    left, vertical, _ = _margins_of(credits.build_ass("Ann", CreditStyle(), (1080, 1920), 5.0))
    assert left == vertical == round(0.045 * 1080)


def test_insets_are_fractions_of_their_own_axis():
    style = CreditStyle(position="top_left", inset_x=0.06, inset_y=0.14)
    left, vertical, align = _margins_of(credits.build_ass("Ann", style, (1080, 1920), 5.0))
    assert (left, vertical, align) == (65, 269, 7)


def test_middle_positions_use_the_middle_row():
    _, _, align = _margins_of(credits.build_ass("Ann", CreditStyle(position="middle_right"), (1080, 1920), 5.0))
    assert align == 6
    plate = credits.plate_for(CreditStyle(position="middle_left"), (1080, 1920), Path("p.png"), (400, 100))
    assert plate.y == (1920 - plate.h) // 2


def test_plate_respects_the_insets():
    style = CreditStyle(position="top_right", inset_x=0.1, inset_y=0.2)
    plate = credits.plate_for(style, (1080, 1920), Path("p.png"), (400, 100))
    assert (plate.x + plate.w, plate.y) == (1080 - 108, 384)


def test_custom_position_is_centred_on_the_point_and_kept_in_frame():
    style = CreditStyle(position="custom", x=0.5, y=0.3)
    assert "\\pos(540,576)" in credits.build_ass("Ann", style, (1080, 1920), 5.0)
    corner = CreditStyle(position="custom", x=0.0, y=0.0)
    ass = credits.build_ass("Ann", corner, (1080, 1920), 5.0)
    cx, cy = (int(n) for n in ass.split("\\pos(")[1].split(")")[0].split(","))
    assert cx > 0 and cy > 0  # nudged in, not half off the frame
    plate = credits.plate_for(CreditStyle(position="custom", x=1.0, y=1.0), (1080, 1920), Path("p.png"), (400, 100))
    assert plate.x + plate.w <= 1080 and plate.y + plate.h <= 1920


def test_recipe_validates_the_new_credit_fields():
    def parse(**kw):
        return recipe.parse({"segments": [_seg()], "credits": kw}, check_files=False).credits

    assert parse().inset_x is None and parse().inset_y is None
    assert parse(inset_x=5, inset_y=-1).inset_x == recipe.MAX_INSET
    assert parse(inset_x=5, inset_y=-1).inset_y == 0.0
    assert parse(position="custom", x=9, y=-9).x == 1.0
    assert parse(position="middle_center").position == "middle_center"
    with pytest.raises(RecipeError):
        parse(position="sideways")
