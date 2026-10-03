import pytest

from core.state import StateDB


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import schedule_api
    from server import woopsocial_service as service

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    d.upsert_video("v1", title="T")
    a = d.add_clip("v1", 0, 3, 70, "hook", path="x.mp4", title="Clip A")
    d.record_clip_publish(a, "youtube", {"provider": "woopsocial", "state": "queued", "request_id": "P1",
                                         "scheduled_for": "2099-01-01T10:00:00Z"})
    d.record_clip_publish(a, "tiktok", {"provider": "woopsocial", "state": "queued", "request_id": "P1",
                                        "scheduled_for": "2099-01-01T10:00:00Z"})
    d.record_clip_publish(a, "instagram", {"provider": "uploadpost", "state": "queued", "request_id": "U1",
                                           "scheduled_for": "2099-01-02T10:00:00Z"})
    d.record_clip_publish(a, "x", {"provider": "woopsocial", "state": "published", "request_id": "P0",
                                   "scheduled_for": "2020-01-01T10:00:00Z", "post_url": "https://x"})
    d.close()
    deleted = []

    class Client:
        def delete_post(self, post_id):
            deleted.append(post_id)

    monkeypatch.setattr(service, "make_client", lambda data_dir: Client())
    app = FastAPI()
    schedule_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.clip, c.deleted, c.db_path = a, deleted, db_path
    return c


def test_lists_every_provider_and_says_what_can_be_cancelled(client):
    items = {(i["platform"]): i for i in client.get("/publishing/schedule").json()["items"]}
    assert items["youtube"]["can_cancel"] and items["youtube"]["cancel_group"] == 2
    assert not items["instagram"]["can_cancel"]  # Upload-Post: no job id to cancel with
    assert not items["x"]["waiting"]


def test_cancel_removes_the_whole_post_and_keeps_the_records(client):
    got = client.post("/publishing/schedule/cancel", json={"clip_id": client.clip, "platform": "youtube"})
    assert got.status_code == 200 and got.json()["cancelled"] == 2 and client.deleted == ["P1"]
    d = StateDB(client.db_path)
    rows = {r["platform"]: r for r in d.clip_publishes(client.clip)}
    d.close()
    assert rows["youtube"]["state"] == "skipped" and rows["tiktok"]["state"] == "skipped"
    assert rows["youtube"]["scheduled_for"] == ""
    # Not cancellable here: refused, and nothing was sent to a provider.
    assert client.post("/publishing/schedule/cancel", json={"clip_id": client.clip, "platform": "instagram"}).status_code == 400


def test_clear_only_takes_finished_rows_and_keeps_them_mapped(client):
    got = client.post("/publishing/schedule/clear", json={}).json()
    assert got["cleared"] == 1
    got = client.post("/publishing/schedule/clear", json={"items": [{"clip_id": client.clip, "platform": "instagram"}]}).json()
    assert got["cleared"] == 0 and got["refused"][0]["platform"] == "instagram"
    d = StateDB(client.db_path)
    row = {r["platform"]: r for r in d.clip_publishes(client.clip)}["x"]
    d.close()
    assert row["state"] == "published" and row["post_url"] == "https://x" and row["scheduled_for"] == ""


def test_direct_youtube_uploads_and_unscheduled_upload_post_posts_are_listed(client):
    d = StateDB(client.db_path)
    a = client.clip
    d.record_upload(a, "aaaaaaaaaaa")
    d.conn.execute("UPDATE uploads SET publish_at = '2099-03-01T10:00:00Z' WHERE clip_id = ?", (a,))
    d.record_clip_publish(a, "tiktok", {"provider": "upload_post", "state": "queued", "request_id": "Q1"})
    d.conn.commit()
    d.close()
    items = client.get("/publishing/schedule").json()["items"]
    kinds = {(i["kind"], i["platform"]): i for i in items}
    assert kinds[("upload", "youtube")]["youtube_id"] == "aaaaaaaaaaa" and kinds[("upload", "youtube")]["can_cancel"]
    assert kinds[("post", "tiktok")]["waiting"] and not kinds[("post", "tiktok")]["can_cancel"]
    # An upload that has already gone live is history, not schedule.
    d = StateDB(client.db_path)
    d.conn.execute("UPDATE uploads SET publish_at = '2020-01-01T10:00:00Z'")
    d.conn.commit()
    d.close()
    assert not [i for i in client.get("/publishing/schedule").json()["items"] if i["kind"] == "upload"]


def test_duplicates_flag_what_is_already_live_or_scheduled_and_follow_a_rerender(client):
    d = StateDB(client.db_path)
    a = client.clip
    d.record_upload(a, "aaaaaaaaaaa")  # live on YouTube (no go-live time)
    d.record_clip_publish(a, "tiktok", {"provider": "upload_post", "state": "failed"})  # holds nothing
    d.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, path, created_at) VALUES ('v1', 9, 12, 70, 'z.mp4', 'x')"
    )
    d.conn.commit()
    other = d.conn.execute("SELECT id FROM clips WHERE start_s = 9").fetchone()["id"]
    d.close()
    got = client.post("/publishing/duplicates", json={"clip_ids": [a, other]}).json()["duplicates"]
    assert list(got) == [str(a)]
    kinds = {(x["provider"], x["platform"], x["state"]) for x in got[str(a)]}
    assert ("youtube", "youtube", "live") in kinds and not any(k[1] == "tiktok" for k in kinds)
    # The WoopSocial post of the same clip is scheduled, and shows once.
    scheduled = [x for x in got[str(a)] if x["provider"] == "woopsocial"]
    assert {x["state"] for x in scheduled} <= {"scheduled", "live"}
