"""Uploading is separate from deciding what the upload is for.

A video can come in to make clips (as always), or only into the library, to go
into a compilation now or later. Whatever it is used for, the Library says
where: clips made, clips posted, compilations that use it.
"""

import json

import pytest

from compilation import store as compilations
from core import queue
from core.state import StateDB

api = pytest.importorskip("server.api")


def test_import_only_and_its_compilation_ride_on_the_payload():
    payload = api._process_options(api.JobIn(url="u", import_only=True, add_to_compilation=4))
    assert payload == {"import_only": True, "add_to_compilation": 4}


def test_a_patch_can_turn_an_import_into_a_clip_job():
    patch = api.JobPatch(clear=["import_only", "add_to_compilation"])
    assert api._process_options(patch, {"import_only": True, "add_to_compilation": 4}) == {}


def test_append_whole_video_adds_one_credited_segment(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="Clip", duration=12.5)
    comp = compilations.create(d, "Best of")
    assert compilations.append_whole_video(d, comp, "v1")
    assert compilations.get(d, comp)["recipe"]["segments"] == [
        {"credit": True, "video_id": "v1", "start": 0.0, "end": 12.5}
    ]
    # No length yet, or no such compilation: nothing is appended.
    d.upsert_video("v2", title="Unknown length")
    assert not compilations.append_whole_video(d, comp, "v2")
    assert not compilations.append_whole_video(d, 999, "v1")


def test_a_rendering_compilation_is_not_changed_underneath_it(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="Clip", duration=5)
    comp = compilations.create(d, "Best of")
    compilations.set_status(d, comp, "rendering")
    assert not compilations.append_whole_video(d, comp, "v1")


def test_usage_counts_segments_per_compilation(tmp_path):
    d = StateDB(tmp_path / "s.db")
    a = compilations.create(d, "A", {"segments": [{"video_id": "v1"}, {"video_id": "v1"}, {"video_id": "v2"}]})
    b = compilations.create(d, "B", {"segments": [{"video_id": "v1"}]})
    used = compilations.usage_by_video(d)
    assert sorted((u["id"], u["segments"]) for u in used["v1"]) == [(a, 2), (b, 1)]
    assert [u["id"] for u in used["v2"]] == [a]


def test_imports_do_not_count_toward_the_queue_estimate(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.add_job("process", json.dumps({"url": "x", "import_only": True}), video_id="v1")
    assert queue.estimate(d)["queued_seconds"] == 0
    d.add_job("process", json.dumps({"url": "y"}), video_id="v2")
    assert queue.estimate(d)["queued_seconds"] > 0


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = api.create_app(config, tmp_path / "settings.yaml")
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path, c.data_dir = data_dir / "state.db", data_dir
    return c


def test_importing_a_video_already_in_the_library_just_attaches_it(client):
    d = StateDB(client.db_path)
    d.upsert_video("dQw4w9WgXcQ", title="Song", duration=30)
    d.set_video_status("dQw4w9WgXcQ", "done")
    comp = compilations.create(d, "Mix")
    d.close()
    (client.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (client.data_dir / "downloads" / "dQw4w9WgXcQ.mp4").write_bytes(b"x")

    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    res = client.post("/jobs/batch", json={"items": [{"url": url, "import_only": True, "add_to_compilation": comp}]})
    assert res.json()["created"][0]["already_in_library"] is True
    assert client.get("/queue").json()["queued"] == []

    lib = {v["video_id"]: v for v in client.get("/library").json()}
    row = lib["dQw4w9WgXcQ"]
    assert row["has_source"] is True
    assert [c["title"] for c in row["compilations"]] == ["Mix"]


def test_import_only_link_is_queued_even_when_clipped_before_if_the_file_is_gone(client):
    d = StateDB(client.db_path)
    d.upsert_video("dQw4w9WgXcQ", title="Song", duration=30)
    d.set_video_status("dQw4w9WgXcQ", "done")
    d.close()
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    res = client.post("/jobs/batch", json={"items": [{"url": url, "import_only": True}]}).json()
    assert res["skipped"] == [] and res["created"][0]["job_id"]


def test_segments_can_be_appended_from_outside_the_editor(client):
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="Source", duration=40)
    comp = compilations.create(d, "Mix")
    d.close()
    ranged = client.post(f"/compilations/{comp}/segments", json={"video_id": "v1", "start": 5, "end": 12})
    assert ranged.status_code == 200
    whole = client.post(f"/compilations/{comp}/segments", json={"video_id": "v1", "start": 20, "end": 30})
    segs = whole.json()["recipe"]["segments"]
    assert [(s["start"], s["end"]) for s in segs] == [(5.0, 12.0), (20.0, 30.0)]
    # Footage already in it is refused, naming the segment it repeats.
    again = client.post(f"/compilations/{comp}/segments", json={"video_id": "v1"})
    assert again.status_code == 409 and "segment 1" in again.json()["detail"]
    assert client.post(f"/compilations/{comp}/segments", json={"video_id": "v1", "start": 9, "end": 3}).status_code == 400
    assert client.post(f"/compilations/{comp}/segments", json={"video_id": "nope"}).status_code == 400


def test_the_same_footage_is_never_appended_twice(tmp_path):
    d = StateDB(tmp_path / "s.db")
    d.upsert_video("v1", title="Clip", duration=60)
    comp = compilations.create(d, "Best of")
    assert compilations.append_segment(d, comp, {"video_id": "v1", "start": 10, "end": 20})
    # The same clip, a clip mostly inside it, and the whole video: all repeats.
    assert compilations.append_segment(d, comp, {"video_id": "v1", "start": 10, "end": 20})
    assert compilations.append_segment(d, comp, {"video_id": "v1", "start": 12, "end": 21})
    assert compilations.append_whole_video(d, comp, "v1")
    assert len(compilations.get(d, comp)["recipe"]["segments"]) == 1
    # Another moment of the same video is new footage.
    assert compilations.append_segment(d, comp, {"video_id": "v1", "start": 19, "end": 40})
    assert len(compilations.get(d, comp)["recipe"]["segments"]) == 2


def test_duplicates_points_each_repeat_at_the_first():
    segs = [
        {"video_id": "a", "start": 0, "end": 30},
        {"video_id": "b", "start": 0, "end": 10},
        {"video_id": "a", "start": 5, "end": 15},
        {"video_id": "b", "start": 0, "end": 10},
        {"video_id": "b", "start": 9, "end": 20},
    ]
    assert compilations.duplicates(segs) == {2: 0, 3: 1}
