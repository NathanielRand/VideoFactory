"""Compilations: recipe validation, credits, graph building, storage, the API,
and one real render of a multi-source compilation."""

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
        def notify(self):
            pass

    class _Broadcaster:
        def publish(self, _):
            pass

    app = FastAPI()
    compilations_api.install(
        app, config={"paths": {"data_dir": str(data_dir)}}, db=lambda: StateDB(db_path),
        data_dir=data_dir, worker=_Worker(), broadcaster=_Broadcaster(),
    )
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path, c.data_dir = db_path, data_dir
    return c


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
