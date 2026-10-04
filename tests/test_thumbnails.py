"""The thumbnail designer's routes (server/thumbnails_api.py)."""

import base64
import subprocess
from pathlib import Path

import pytest

from core.state import StateDB
from server.thumbnails_api import clean_copy, clean_ideas, core_keywords, mentions_keyword

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_text_ideas_are_short_and_distinct():
    ideas = clean_ideas(["COMEBACK", "comeback", "He actually did it again today folks", '"#CLUTCH"', "", 7])
    assert ideas == ["COMEBACK", "CLUTCH", "7"]
    assert clean_ideas("nope") == []


def test_filler_ideas_and_headlines_are_dropped():
    assert clean_ideas(["NO WAY", "Insane!", "ACE ON MIRAGE"]) == ["ACE ON MIRAGE"]
    sets = clean_copy([{"headline": "NO WAY", "kicker": "", "badge": ""},
                       {"headline": "ACE ON MIRAGE", "kicker": "", "badge": ""}])
    assert [s["headline"] for s in sets] == ["ACE ON MIRAGE"]


def test_core_keywords_skip_the_always_on_tags():
    got = core_keywords(["poker night"], ["#PokerNight", "#shorts", "#fyp", "#AllIn"],
                        creator="Rounders TV", ignore=["#AllIn"])
    assert got == ["poker night", "Rounders TV"]     # PokerNight folds into "poker night"
    assert core_keywords(["#12345", "ab"], []) == []


def test_every_set_carries_a_core_keyword():
    raw = [{"headline": "ACE ON MIRAGE", "kicker": "One tap", "badge": "CS2"},
           {"headline": "SILENT CLUTCH", "kicker": "", "badge": ""}]
    sets = clean_copy(raw, keywords=["Mirage", "Rounders TV"])
    assert all(mentions_keyword(s, ["Mirage", "Rounders TV"]) for s in sets)
    assert sets[0]["headline"] == "ACE ON MIRAGE"      # already had it: first, untouched
    assert sets[1]["badge"] == "Mirage"                # did not: gets it as its label
    # a keyword too long for a badge is skipped for one that fits
    assert clean_copy(raw[1:], keywords=["a very long keyword phrase", "Mirage"])[0]["badge"] == "Mirage"
    # no keywords, no change
    assert [s["badge"] for s in clean_copy(raw)] == ["CS2", ""]


@pytest.fixture
def client(tmp_path: Path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.binaries import ffmpeg
    from server import thumbnails_api

    video = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:d=3",
                    "-pix_fmt", "yuv420p", str(video)], check=True, capture_output=True)
    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T")
    clip_id = d.add_clip("v1", 0, 3, 70, "hook", path=str(video), title="A clip")
    d.close()
    app = FastAPI()
    thumbnails_api.install(app, config={"llm": {}}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.clip_id, c.tmp = clip_id, tmp_path
    return c


def test_source_and_frames(client):
    src = client.get(f"/thumbnails/{client.clip_id}/source").json()
    assert 2.5 < src["duration"] < 3.5 and not src["has_design"]
    frame = client.get(f"/thumbnails/{client.clip_id}/frame", params={"t": 1.2})
    assert frame.status_code == 200 and frame.content.startswith(b"\xff\xd8")
    best = client.get(f"/thumbnails/{client.clip_id}/best-frames", params={"count": 3}).json()["frames"]
    assert 1 <= len(best) <= 3 and all(0 <= f["t"] <= 3 for f in best)
    assert client.get("/thumbnails/999/frame").status_code == 404


def test_save_makes_it_the_thumbnail_every_publisher_sends(client):
    image = base64.b64encode(JPEG).decode()
    design = {"version": 1, "layers": [{"kind": "text", "text": "NO WAY"}]}
    got = client.post(f"/thumbnails/{client.clip_id}", json={"image": f"data:image/jpeg;base64,{image}",
                                                              "design": design})
    assert got.status_code == 200
    # The same file the YouTube and Upload-Post paths already read.
    assert (client.tmp / "thumbnails" / f"clip_{client.clip_id}_chosen.jpg").read_bytes() == JPEG
    assert client.get(f"/thumbnails/{client.clip_id}/design").json()["design"] == design
    assert client.get(f"/thumbnails/{client.clip_id}/image").content == JPEG
    assert client.get(f"/thumbnails/{client.clip_id}/source").json()["has_design"]
    client.delete(f"/thumbnails/{client.clip_id}")
    assert client.get(f"/thumbnails/{client.clip_id}/image").status_code == 404


def test_save_refuses_what_youtube_would(client):
    bad = base64.b64encode(b"GIF89a" + b"\x00" * 10).decode()
    assert client.post(f"/thumbnails/{client.clip_id}", json={"image": bad}).status_code == 400


def test_cutout_with_nobody_in_frame_is_a_clear_answer(client, monkeypatch):
    import video.cutout

    monkeypatch.setattr(video.cutout, "cutout", lambda frame, target: False)
    got = client.post(f"/thumbnails/{client.clip_id}/cutout", params={"t": 1})
    assert got.status_code == 422 and "Nobody" in got.json()["detail"]


def test_cutout_mask_keeps_the_subject_and_drops_extras():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from video import cutout

    class Result:
        class masks:
            # One person filling a third of the frame, one tiny figure far away.
            xy = [np.array([[10, 10], [100, 10], [100, 190], [10, 190]], dtype=np.float32),  # noqa: RUF012 (test fake)
                  np.array([[150, 150], [153, 150], [153, 153]], dtype=np.float32)]

    cutout._model = lambda *a, **k: [Result()]
    try:
        mask = cutout.person_mask(np.zeros((200, 200, 3), dtype=np.uint8))
    finally:
        cutout._model = None
    assert mask[100, 50] == 255 and mask[151, 151] == 0
    assert cv2.countNonZero(mask) > 0


def test_status_only_checks_for_the_file(client):
    (client.tmp / "thumbnails").mkdir(exist_ok=True)
    (client.tmp / "thumbnails" / "clip_5_chosen.jpg").write_bytes(JPEG)
    got = client.get("/thumbnails/status", params={"ids": f"5,{client.clip_id},-3,abc,../x"}).json()["thumbnails"]
    assert got == {"5": True, str(client.clip_id): False, "-3": False}


def test_copy_returns_ranked_sets_from_the_model(client, monkeypatch):
    import llm.base
    import llm.registry

    monkeypatch.setattr(llm.registry, "create_backend", lambda cfg: object())
    monkeypatch.setattr(
        llm.base, "generate_json",
        lambda *a, **k: '{"sets": [{"headline": "He Snapped", "kicker": "then it got worse", '
                        '"badge": "REAL", "emoji": "😱", "mood": "shock"}]}',
    )
    got = client.post(f"/thumbnails/{client.clip_id}/copy")
    assert got.status_code == 200
    assert got.json()["sets"][0]["headline"] == "He Snapped"


def test_frames_come_from_the_source_unless_the_render_is_asked_for(tmp_path):
    pytest.importorskip("httpx")
    import io

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from PIL import Image

    from core.binaries import ffmpeg
    from server import thumbnails_api

    def make(path, size):
        subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:d=3", "-pix_fmt", "yuv420p",
                        str(path)], check=True, capture_output=True)

    render = tmp_path / "clip.mp4"
    make(render, "320x568")                      # what a vertical render looks like
    (tmp_path / "downloads").mkdir()
    make(tmp_path / "downloads" / "v1.mp4", "640x360")   # the original
    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T")
    clip_id = d.add_clip("v1", 0, 3, 70, "hook", path=str(render), title="A clip")
    d.close()
    app = FastAPI()
    thumbnails_api.install(app, config={"llm": {}}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")

    def size(url):
        r = c.get(url)
        assert r.status_code == 200
        return Image.open(io.BytesIO(r.content)).size

    assert size(f"/thumbnails/{clip_id}/frame?t=1") == (640, 360)                   # the source: wide, no captions
    assert size(f"/thumbnails/{clip_id}/frame?t=1&src=render") == (320, 568)        # an old design's frame
    assert c.get(f"/thumbnails/{clip_id}/source").json()["keywords"] == []


# ---- a thumbnail is made once, for a clip's first render -----------------------------------


def _first_render_env(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.binaries import ffmpeg
    from server import thumbnails_api

    video = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:d=3", "-pix_fmt", "yuv420p", str(video)],
                   check=True, capture_output=True)
    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T")
    a = d.add_clip("v1", 0, 3, 70, "hook", path=str(video), title="A")
    b = d.add_clip("v1", 10, 13, 70, "hook", path=str(video), title="B")
    unrendered = d.add_clip("v1", 20, 23, 70, "hook", path=str(tmp_path / "nope.mp4"), title="C")
    d.close()
    app = FastAPI()
    thumbnails_api.install(app, config={"llm": {}}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1"), a, b, unrendered


def test_only_new_rendered_clips_without_a_thumbnail_are_pending(tmp_path):
    client, a, b, unrendered = _first_render_env(tmp_path)
    ids = f"{a},{b},{unrendered},-4,junk,9999"
    assert client.get(f"/thumbnails/pending?ids={ids}").json() == {"pending": [a, b]}   # not unrendered, not compilations
    client.post(f"/thumbnails/{a}", json={"image": base64.b64encode(JPEG).decode(), "auto": True})
    assert client.get(f"/thumbnails/pending?ids={ids}").json() == {"pending": [b]}      # made once: not again


def test_deleting_a_thumbnail_on_purpose_does_not_bring_the_app_back_to_make_another(tmp_path):
    client, a, b, _ = _first_render_env(tmp_path)
    client.post(f"/thumbnails/{a}", json={"image": base64.b64encode(JPEG).decode()})
    assert client.delete(f"/thumbnails/{a}").status_code == 200
    assert client.get(f"/thumbnails/pending?ids={a}").json() == {"pending": []}


def test_a_rerender_keeps_the_thumbnail_and_never_asks_for_another(tmp_path):
    from server.thumbnails_api import carry_over

    client, a, b, _ = _first_render_env(tmp_path)
    client.post(f"/thumbnails/{a}", json={"image": base64.b64encode(JPEG).decode(), "design": {"version": 1}})
    folder = tmp_path / "thumbnails"
    carry_over(tmp_path, a, 501, keep_design=True)                       # the clip came back as clip 501
    assert (folder / "clip_501_chosen.jpg").exists() and (folder / "clip_501_design.json").exists()
    assert not (folder / f"clip_{a}_chosen.jpg").exists()               # moved, not copied
    assert (folder / "clip_501_auto.done").exists()

    # The start moved: the picture stays, the design (laid out on the old frames) does not.
    carry_over(tmp_path, 501, 502, keep_design=False)
    assert (folder / "clip_502_chosen.jpg").exists() and not (folder / "clip_502_design.json").exists()

    # A clip that never had one is not handed one by being re-rendered.
    carry_over(tmp_path, b, 503)
    assert not (folder / "clip_503_chosen.jpg").exists() and (folder / "clip_503_auto.done").exists()
