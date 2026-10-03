"""Serving video and posters (server/media.py)."""

import os
import subprocess
import sys
import time

import pytest

from core.binaries import ffmpeg
from server import media


def _clip(path, size="320x568", seconds=3):
    subprocess.run([ffmpeg(), "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:d={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True, capture_output=True)


@pytest.fixture
def clean_switching():
    """The interval and the lease are process-wide; leave them as found."""
    with media._boost_lock:                       # a lease left by an earlier test
        media._boost["until"], media._boost["previous"] = 0.0, None
    before = sys.getswitchinterval()
    yield
    with media._boost_lock:
        media._boost["until"], media._boost["previous"] = 0.0, None
    sys.setswitchinterval(before)


def test_the_short_interval_is_lent_and_handed_back(clean_switching):
    sys.setswitchinterval(0.005)
    media.boost_thread_switching(hold=0.3)
    assert sys.getswitchinterval() <= media.SWITCH_INTERVAL + 1e-9
    time.sleep(0.15)
    media.boost_thread_switching(hold=0.3)                 # more media sent: the lease is extended
    time.sleep(0.25)
    assert sys.getswitchinterval() <= media.SWITCH_INTERVAL + 1e-9
    deadline = time.time() + 3
    while time.time() < deadline and sys.getswitchinterval() < 0.004:
        time.sleep(0.05)
    assert sys.getswitchinterval() == pytest.approx(0.005)  # given back: long-running work is not left contending


def test_a_lower_interval_someone_else_set_is_never_raised(clean_switching):
    sys.setswitchinterval(0.00005)
    media.boost_thread_switching(hold=0.1)
    time.sleep(0.4)
    assert sys.getswitchinterval() == pytest.approx(0.00005, abs=3e-6)


def test_sending_a_video_takes_the_lease(clean_switching, tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    video = tmp_path / "v.mp4"
    _clip(video)
    app = FastAPI()

    @app.get("/v")
    def v():
        return media.video_response(video)

    sys.setswitchinterval(0.005)
    assert TestClient(app).get("/v", headers={"Range": "bytes=0-99"}).status_code == 206
    assert sys.getswitchinterval() <= media.SWITCH_INTERVAL + 1e-9    # taken while it was sent


def test_video_responses_read_in_large_pieces_and_serve_ranges(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    video = tmp_path / "v.mp4"
    _clip(video)
    app = FastAPI()

    @app.get("/v")
    def v():
        return media.video_response(video)

    assert media.MediaResponse.chunk_size == 1024 * 1024        # not Starlette's 64 KB
    r = TestClient(app).get("/v", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["accept-ranges"] == "bytes"
    assert "max-age" in r.headers["cache-control"]
    assert r.headers["content-range"].startswith("bytes 0-99/")


def test_a_poster_is_made_once_kept_and_replaced_when_the_clip_is(tmp_path):
    video, cache = tmp_path / "v.mp4", tmp_path / "posters"
    _clip(video)
    first = media.poster(ffmpeg(), cache, video)
    assert first is not None and first.exists() and first.stat().st_size > 500
    stamp = first.stat().st_mtime_ns
    assert media.poster(ffmpeg(), cache, video) == first            # cached: not made again
    assert first.stat().st_mtime_ns == stamp

    _clip(video, seconds=4)                                          # re-rendered: a different file
    os.utime(video, ns=(video.stat().st_atime_ns, video.stat().st_mtime_ns + 5_000_000_000))
    second = media.poster(ffmpeg(), cache, video)
    assert second != first and second.exists()
    assert not first.exists()                                        # the old one does not pile up
    assert len(list(cache.glob("*.jpg"))) == 1


def test_a_poster_has_the_asked_width_and_a_missing_clip_has_none(tmp_path):
    from PIL import Image

    video = tmp_path / "v.mp4"
    _clip(video, size="640x360")
    got = media.poster(ffmpeg(), tmp_path / "p", video, width=200)
    assert Image.open(got).size == (200, 112)
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video")
    assert media.poster(ffmpeg(), tmp_path / "p", bad) is None


def test_the_poster_route_and_the_media_route_through_the_real_app(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")

    clip_file = data_dir / "clips" / "c" / "clip.mp4"
    clip_file.parent.mkdir(parents=True)
    _clip(clip_file)
    db = StateDB(data_dir / "state.db")
    db.upsert_video("v1", title="T", channel_name="c", duration=100)
    cid = db.add_clip("v1", 1.0, 5.0, 70, "hook", path=str(clip_file), title="A clip")
    db.conn.close()
    client = TestClient(app, base_url="http://127.0.0.1")

    r = client.get(f"/media/{cid}/poster")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) > 500
    assert client.get(f"/media/{cid}/poster?w=100000").status_code == 200       # width is clamped, not trusted
    assert client.get("/media/999/poster").status_code == 404
    v = client.get(f"/media/{cid}", headers={"Range": "bytes=0-9"})
    assert v.status_code == 206 and "max-age" in v.headers["cache-control"]


# ---- draft previews reuse subject tracking ----------------------------------------------


def test_tracking_cache_is_keyed_on_what_changes_the_footage_and_is_bounded(tmp_path):
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x" * 10)
    k = media.TrackingCache.key
    base = k(1, src, 1.0, 5.0, {"keep": [[0, 4]]}, "track", False, "yolo", 8)
    assert base == k(1, src, 1.0, 5.0, {"keep": [[0, 4]]}, "track", False, "yolo", 8)
    for other in (k(2, src, 1.0, 5.0, {"keep": [[0, 4]]}, "track", False, "yolo", 8),
                  k(1, src, 1.5, 5.0, {"keep": [[0, 4]]}, "track", False, "yolo", 8),      # trimmed
                  k(1, src, 1.0, 5.0, {"keep": [[0, 3]]}, "track", False, "yolo", 8),      # edited
                  k(1, src, 1.0, 5.0, None, "track", False, "yolo", 8),
                  k(1, src, 1.0, 5.0, {"keep": [[0, 4]]}, "letterbox", False, "yolo", 8),  # framing
                  k(1, src, 1.0, 5.0, {"keep": [[0, 4]]}, "track", True, "yolo", 8)):
        assert other != base

    cache = media.TrackingCache(size=2)
    a = cache.for_key(("a",))
    a["tracking"] = 1
    cache.for_key(("b",))
    assert cache.for_key(("a",)) is a                # reuse hands back the same dict
    cache.for_key(("c",))                            # evicts the least recently used: "b"
    assert cache.for_key(("a",))["tracking"] == 1
    assert cache.for_key(("b",)) == {}


def test_a_second_preview_gets_the_first_ones_tracking(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import core.pipeline as pipeline
    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    (data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (data_dir / "downloads" / "v1.mp4").write_bytes(b"x" * 100)
    db = StateDB(data_dir / "state.db")
    db.upsert_video("v1", title="T", channel_name="c", duration=100)
    cid = db.add_clip("v1", 1.0, 5.0, 70, "hook", path=str(tmp_path / "c.mp4"), title="A clip")
    db.conn.close()

    seen = []

    def fake_render(source, candidate, segments, folder, config, opts, lang, tracking_cache=None):
        seen.append(tracking_cache)
        first = "tracking" not in tracking_cache
        tracking_cache["tracking"] = {"mode": "track"}     # what a real render stores on a miss
        out = folder / "made.mp4"
        out.write_bytes(b"v")
        return out, "{}"

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    client = TestClient(app, base_url="http://127.0.0.1")
    r1 = client.post(f"/clips/{cid}/preview", json={"edit": None, "caption_lines": [{"start": 0, "end": 1, "text": "a"}]})
    r2 = client.post(f"/clips/{cid}/preview", json={"edit": None, "caption_lines": [{"start": 0, "end": 1, "text": "b"}]})
    assert r1.status_code == r2.status_code == 200
    assert seen[0] is seen[1] and "tracking" in seen[1]               # caption text changed; tracking reused
    client.post(f"/clips/{cid}/preview", json={"edit": {"keep": [[0.0, 2.0]]}})
    assert seen[2] is not seen[0]                                     # a trim changes the footage: fresh


# ---- posters are made as clips are finished ----------------------------------------------


def test_a_finished_clip_gets_its_poster_in_the_background(tmp_path):
    from core.pipeline import _queue_poster

    video = tmp_path / "data" / "clips" / "c.mp4"
    video.parent.mkdir(parents=True)
    _clip(video)
    _queue_poster(video, {"paths": {"data_dir": str(tmp_path / "data")}})
    deadline = time.time() + 20
    while time.time() < deadline and not list((tmp_path / "data" / "posters").glob("*.jpg")):
        time.sleep(0.1)
    assert len(list((tmp_path / "data" / "posters").glob("*.jpg"))) == 1
    _queue_poster(video, None)                       # no config: quietly nothing, never an error
    _queue_poster(tmp_path / "gone.mp4", {"paths": {"data_dir": str(tmp_path / "data")}})


def test_a_burst_of_missing_posters_does_not_hold_up_video(tmp_path):
    """Forty cards asking at once for stills that do not exist yet, while a
    clip is being fetched: the clip must not wait behind them. Against a real
    server, since that is where threads and connections are actually shared."""
    requests = pytest.importorskip("requests")
    uvicorn = pytest.importorskip("uvicorn")
    import concurrent.futures as cf
    import socket
    import threading

    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.upsert_video("v1", title="T", channel_name="c", duration=100)
    ids = []
    for i in range(12):
        f = data_dir / "clips" / "c" / f"clip{i}.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        _clip(f, seconds=2 + i % 2)
        ids.append(db.add_clip("v1", float(i * 10), float(i * 10 + 5), 70, "h", path=str(f), title=f"c{i}"))
    db.conn.close()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not server.started:
            time.sleep(0.05)
        assert server.started
        with cf.ThreadPoolExecutor(max_workers=24) as pool:
            posters = [pool.submit(requests.get, f"{base}/media/{cid}/poster", timeout=60) for cid in ids * 3]
            time.sleep(0.2)                               # the burst is in flight
            t0 = time.time()
            r = requests.get(f"{base}/media/{ids[0]}", headers={"Range": "bytes=0-99999"}, timeout=60)
            waited = time.time() - t0
            assert all(f.result().status_code == 200 for f in posters)
        assert r.status_code == 206
        assert waited < 2.0, f"a video range waited {waited:.1f}s behind the poster burst"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_playback_requests_take_the_lease_before_they_are_handled(clean_switching):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(media.BoostPlayback)
    seen = {}

    @app.get("/media/{n}")
    def a(n: int):
        seen["media"] = sys.getswitchinterval()
        return {}

    @app.get("/clips/{n}/words")
    def b(n: int):
        seen["words"] = sys.getswitchinterval()
        return {}

    @app.get("/videos")
    def c():
        seen["videos"] = sys.getswitchinterval()
        return {}

    client = TestClient(app)
    sys.setswitchinterval(0.005)
    client.get("/videos")
    assert seen["videos"] == pytest.approx(0.005, abs=3e-6)          # ordinary calls are left alone
    client.get("/media/3")
    assert seen["media"] <= media.SWITCH_INTERVAL + 1e-9              # already short when the handler runs
    with media._boost_lock:
        media._boost["until"] = 0.0
    media._return_switching()
    sys.setswitchinterval(0.005)
    client.get("/clips/3/words")
    assert seen["words"] <= media.SWITCH_INTERVAL + 1e-9


def test_which_paths_count_as_playback():
    ok = ["/media/12", "/media/12/poster", "/media/preview/4", "/compilations/source/abc",
          "/compilations/3/media", "/clips/7/words", "/clips/7/captions", "/clips/7/preview",
          "/clips/7/variants/4x5/media", "/thumbnails/7/frame", "/thumbnails/7/best-frames"]
    no = ["/videos", "/clips/7", "/jobs", "/health", "/publish/states", "/clip-work", "/settings"]
    assert all(media._PLAYBACK.match(p) for p in ok)
    assert not any(media._PLAYBACK.match(p) for p in no)
