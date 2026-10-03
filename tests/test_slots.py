"""The slot planner (publish/slots.py) and the calendar routes that use it."""

from datetime import date, datetime, timedelta, timezone, tzinfo

import pytest

from publish.slots import Policy, free_slots, policy_slots

UTC = timezone.utc


class _US(tzinfo):
    """US Eastern with real DST rules (2nd Sunday of March to 1st of November),
    so the tests need no timezone database (Windows ships none)."""

    def _dst(self, dt):
        def nth_sunday(year, month, n):
            d = date(year, month, 1)
            d += timedelta(days=(6 - d.weekday()) % 7 + 7 * (n - 1))
            return d
        start = datetime.combine(nth_sunday(dt.year, 3, 2), datetime.min.time()) + timedelta(hours=2)
        end = datetime.combine(nth_sunday(dt.year, 11, 1), datetime.min.time()) + timedelta(hours=1)
        return start <= dt.replace(tzinfo=None) < end

    def utcoffset(self, dt):
        return timedelta(hours=-4) if self._dst(dt) else timedelta(hours=-5)

    def dst(self, dt):
        return timedelta(hours=1) if self._dst(dt) else timedelta(0)

    def tzname(self, dt):
        return "EDT" if self._dst(dt) else "EST"


ET = _US()
FIXED = Policy(mode="fixed", fixed_times=["09:00", "18:00"])


def local(moment):
    return moment.astimezone(ET).strftime("%a %H:%M")


def test_fixed_slots_are_the_same_clock_time_either_side_of_a_dst_change():
    # 2026-03-08 is the spring-forward Sunday in the US: 18:00 stays 18:00,
    # while its UTC instant moves an hour.
    slots = policy_slots(FIXED, date(2026, 3, 7), 3, tz=ET)
    evening = [s for s in slots if s.astimezone(ET).hour == 18]
    assert [s.astimezone(ET).strftime("%m-%d %H:%M") for s in evening] == ["03-07 18:00", "03-08 18:00", "03-09 18:00"]
    assert evening[0].hour == 23 and evening[1].hour == 22  # UTC moved by an hour


def test_the_next_slots_skip_what_is_taken_and_the_lead_time():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # 08:00 ET Monday
    taken = [datetime(2026, 9, 28, 13, 5, tzinfo=UTC)]  # 09:05 ET: sits on the 09:00 slot
    got = free_slots(FIXED, taken, 3, now=now, tz=ET)
    assert [local(s) for s in got] == ["Mon 18:00", "Tue 09:00", "Tue 18:00"]
    # Nothing inside the scheduler's lead time: 08:50 is too close for a 09:00 slot.
    got = free_slots(FIXED, [], 1, now=datetime(2026, 9, 28, 12, 50, tzinfo=UTC), tz=ET)
    assert local(got[0]) == "Mon 18:00"


def test_days_can_be_switched_off():
    weekdays = Policy(mode="fixed", fixed_times=["18:00"], days=[0, 1, 2, 3, 4])
    got = free_slots(weekdays, [], 3, now=datetime(2026, 10, 2, 12, 0, tzinfo=UTC), tz=ET)  # a Friday
    assert [local(s) for s in got] == ["Fri 18:00", "Mon 18:00", "Tue 18:00"]


def test_a_platform_cap_is_never_exceeded():
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    tues = datetime(2026, 9, 29, 13, 0, tzinfo=UTC)
    got = free_slots(
        FIXED, [], 4, now=now, tz=ET,
        caps={"youtube": 2}, platform_taken={"youtube": [tues, tues + timedelta(hours=1)]},
    )
    # Tuesday already holds two YouTube posts, so it gets no more.
    assert all(s.astimezone(ET).date() != date(2026, 9, 29) for s in got)
    assert len(got) == 4


def test_the_horizon_ends_the_search_honestly():
    got = free_slots(FIXED, [], 500, now=datetime(2026, 9, 28, 12, 0, tzinfo=UTC), horizon_days=3, tz=ET)
    assert 0 < len(got) < 500


def test_best_mode_uses_the_strongest_hours_spaced_by_the_gap():
    grid = [[0.0] * 24 for _ in range(7)]
    for day in range(7):
        grid[day][18] = 1.0
        grid[day][19] = 0.95  # too close to 18 for a 2 hour gap
        grid[day][12] = 0.7
    policy = Policy(mode="best", per_day=2, min_gap_hours=2)
    slots = policy_slots(policy, date(2026, 9, 28), 1, grid=grid, tz=ET)
    assert [s.astimezone(ET).hour for s in slots] == [12, 18]


def test_the_policy_reads_settings_defensively():
    p = Policy.from_settings({"slot_mode": "fixed", "fixed_times": ["9:0", "bad", "25:00", "18:30"],
                              "slot_days": [9, 2], "per_day": 99})
    assert p.mode == "fixed" and set(p.fixed_times) == {"9:0", "18:30"}  # bad ones dropped, never empty
    assert p.days == [2] and p.per_day == 24
    assert Policy.from_settings({}).mode == "best"


# ---- the routes: one occupancy for everything ---------------------------------

@pytest.fixture
def client(tmp_path):
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.state import StateDB
    from server import publishing_api, schedule_api

    db_path = tmp_path / "s.db"
    d = StateDB(db_path)
    publishing_api.save_settings(d, {"slot_mode": "fixed", "fixed_times": ["09:00", "18:00"]})
    d.upsert_video("v1", title="T")
    c1 = d.add_clip("v1", 0, 3, 70, "hook", path="x.mp4", title="A")
    c2 = d.add_clip("v1", 5, 8, 70, "hook", path="y.mp4", title="B")
    d.close()
    app = FastAPI()
    schedule_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path, c.c1, c.c2 = db_path, c1, c2
    return c


def test_a_second_batch_queues_behind_direct_uploads_jobs_and_fan_outs(client):
    from core.state import StateDB

    tomorrow = (datetime.now(UTC) + timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    d = StateDB(client.db_path)
    # A fan-out to three platforms is ONE post; a direct upload and a queued job hold slots too.
    for platform in ("youtube", "tiktok", "instagram"):
        d.record_clip_publish(client.c1, platform, {"provider": "woopsocial", "state": "queued", "request_id": "P",
                                                    "scheduled_for": tomorrow.isoformat().replace("+00:00", "Z")})
    d.close()
    first = client.post("/publishing/slots/next", json={"count": 3, "platforms": [], "provider": "woopsocial"}).json()
    assert len(first["slots"]) == 3 and len(set(first["slots"])) == 3
    # The same call again gives the same answer: asking does not book anything.
    assert client.post("/publishing/slots/next", json={"count": 3}).json()["slots"] == first["slots"]
    # Once a post sits on the first slot, the next batch starts after it.
    d = StateDB(client.db_path)
    d.record_clip_publish(client.c2, "youtube", {"provider": "upload_post", "state": "queued", "request_id": "U",
                                                 "scheduled_for": first["slots"][0]})
    d.close()
    second = client.post("/publishing/slots/next", json={"count": 3}).json()["slots"]
    assert first["slots"][0] not in second and second[0] == first["slots"][1]


def test_the_calendar_marks_which_slots_are_open(client):
    from core.state import StateDB

    slot = client.post("/publishing/slots/next", json={"count": 1}).json()["slots"][0]
    d = StateDB(client.db_path)
    d.record_clip_publish(client.c1, "youtube", {"provider": "woopsocial", "state": "queued", "request_id": "P",
                                                 "scheduled_for": slot})
    d.close()
    cal = client.get("/publishing/calendar", params={"days": 14}).json()
    by_at = {s["at"]: s["taken"] for s in cal["slots"]}
    assert by_at[slot] is True and any(not v for v in by_at.values())
    assert [i["publish_id"] for i in cal["items"]] == [client.c1]
    assert cal["policy"]["mode"] == "fixed"
