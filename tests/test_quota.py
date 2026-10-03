import json
from datetime import datetime, timezone

import pytest

from core.state import StateDB
from publish.quota import COSTS, DAILY_UNITS, Ledger, read_cost


def test_ledger_counts_units_by_call_and_rolls_at_the_pacific_day():
    day1 = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 29, 20, 0, tzinfo=timezone.utc)
    led = Ledger()
    assert led.record_call("thumbnails.set", now=day1) == 50
    assert led.record_call("videos.list", 3, now=day1) == 3
    assert led.record_call("videos.insert", now=day1) == 0  # its own bucket
    assert led.units == 53 and led.calls == {"thumbnails.set": 1, "videos.list": 3}
    assert Ledger(led.as_dict()).units == 53
    assert led.units_remaining(day1) == DAILY_UNITS - 53
    assert led.units_remaining(day2) == DAILY_UNITS and led.calls == {}


def test_reading_a_channel_costs_a_unit_per_call_and_scales_by_50s():
    assert sum(COSTS[m] * n for m, n in read_cost(50).items()) == 3
    assert sum(COSTS[m] * n for m, n in read_cost(200).items()) == 9


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import quota_api

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    for i, (provider, platform) in enumerate([("woopsocial", "youtube")] * 6 + [("uploadpost", "tiktok")] * 2
                                                + [("uploadpost", "snapchat")]):
        d.record_clip_publish(100 + i, platform, {"provider": provider, "state": "queued",
                                                  "scheduled_for": "2099-06-15T12:00:00Z"})
    d.conn.execute("INSERT INTO publish_jobs (clip_id, request, created_at, updated_at) VALUES (1, ?, 'x', 'x')",
                   (json.dumps({"thumbnail": "t.jpg", "playlist_auto": "k"}),))
    d.conn.commit()
    d.close()
    app = FastAPI()
    quota_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1")


def test_forecast_flags_days_over_a_documented_cap_and_leaves_unknown_ones_open(client):
    got = client.get("/quota").json()
    assert got["estimate"] is True and got["youtube"]["units_limit"] == DAILY_UNITS
    day = got["forecast"]["days"][0]["items"]
    by = {(i["provider"], i["platform"]): i for i in day}
    assert by[("woopsocial", "youtube")]["over"] is True and by[("woopsocial", "youtube")]["limit"] == 5
    assert by[("uploadpost", "tiktok")]["over"] is False
    assert by[("uploadpost", "snapchat")]["limit"] is None  # no published limit: no guess
    assert got["forecast"]["youtube_queued_uploads"] == 1 and got["forecast"]["youtube_queued_units"] == 100
