"""The posting schedule as slots: when the next posts may go out.

One planner for the calendar, the publish dialogs and anything that adds to
the schedule, so what the calendar shows is what gets used. It answers one
question: given the policy (which days, which clock times or which best
hours, how many a day) and everything already committed, what are the next N
free instants?

The policy is a *template* of slots. A day's slots are its capacity; content
already scheduled fills the slot it sits on, and a new post takes the next slot
nothing sits on. Hard limits are never bent to fit more in:

* nothing sooner than the scheduler's lead time (publish.schedule.MIN_LEAD_SECONDS);
* a platform's own daily cap (`caps`), counted with what is already scheduled
  to it on that day;
* nothing beyond `horizon_days`.

Days are the creator's local days, and every conversion goes through a real
timezone (`tz`, or the machine's own), never a fixed offset: "18:00" is 18:00
on both sides of a daylight-saving change, so its UTC instant moves by an hour.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo

from publish.schedule import MIN_LEAD_SECONDS

DEFAULT_TIMES = ["09:00", "13:00", "18:00"]
# A slot is taken when something is scheduled this close to it: posts are
# entered a few minutes off a slot often enough (a hand-picked "17:55").
CLASH_MINUTES = 30
GRID_FLOOR = 0.06  # an hour weaker than this is not worth a slot (timing.best_slots)


@dataclass
class Policy:
    mode: str = "best"  # "best": the strongest hours of the day; "fixed": the times below
    per_day: int = 3
    min_gap_hours: float = 2.0
    fixed_times: list[str] = field(default_factory=lambda: list(DEFAULT_TIMES))
    days: list[int] = field(default_factory=lambda: list(range(7)))  # Monday = 0

    @classmethod
    def from_settings(cls, s: dict) -> Policy:
        times = [t for t in (s.get("fixed_times") or []) if _clock(t) is not None] or list(DEFAULT_TIMES)
        days = [d for d in (s.get("slot_days") or []) if isinstance(d, int) and 0 <= d <= 6] or list(range(7))
        return cls(
            mode="fixed" if s.get("slot_mode") == "fixed" else "best",
            per_day=max(1, min(24, int(s.get("per_day") or 3))),
            min_gap_hours=max(0.5, float(s.get("min_gap_hours") or 2)),
            fixed_times=sorted(set(times)),
            days=sorted(set(days)),
        )


def _clock(text: str) -> tuple[int, int] | None:
    try:
        h, m = str(text).split(":", 1)
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None


def _at(day: date, hour: int, minute: int, tz: tzinfo | None) -> datetime:
    """The instant of local wall-clock `hour:minute` on `day`, as UTC."""
    if tz is not None:
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz).astimezone(timezone.utc)
    return datetime(day.year, day.month, day.day, hour, minute).astimezone(timezone.utc)


def _local(moment: datetime, tz: tzinfo | None) -> datetime:
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def _best_hours(policy: Policy, weekday: int, grid: list[list[float]] | None) -> list[int]:
    """This weekday's strongest hours, `per_day` of them, spaced by the gap."""
    if not grid:
        return [9, 13, 18][: policy.per_day]
    gap = max(1, int(round(policy.min_gap_hours)))
    chosen: list[int] = []
    for h in sorted(range(24), key=lambda h: -grid[weekday][h]):
        if len(chosen) >= policy.per_day or grid[weekday][h] <= GRID_FLOOR:
            break
        if all(abs(h - c) >= gap for c in chosen):
            chosen.append(h)
    return sorted(chosen)


def policy_slots(
    policy: Policy,
    first_day: date,
    days: int,
    *,
    grid: list[list[float]] | None = None,
    tz: tzinfo | None = None,
) -> list[datetime]:
    """Every slot the policy offers over `days` local days from `first_day`,
    as UTC instants, soonest first. The template, before anything is subtracted."""
    out: list[datetime] = []
    for i in range(max(0, days)):
        day = first_day + timedelta(days=i)
        if day.weekday() not in policy.days:
            continue
        if policy.mode == "fixed":
            clocks = [c for c in map(_clock, policy.fixed_times) if c is not None]
        else:
            clocks = [(h, 0) for h in _best_hours(policy, day.weekday(), grid)]
        out += [_at(day, h, m, tz) for h, m in clocks]
    return sorted(set(out))


def is_taken(slot: datetime, taken: list[datetime]) -> bool:
    window = timedelta(minutes=CLASH_MINUTES)
    return any(abs(slot - t) < window for t in taken)


def free_slots(
    policy: Policy,
    taken: list[datetime],
    count: int,
    *,
    now: datetime | None = None,
    grid: list[list[float]] | None = None,
    caps: dict[str, int] | None = None,
    platform_taken: dict[str, list[datetime]] | None = None,
    horizon_days: int = 60,
    tz: tzinfo | None = None,
) -> list[datetime]:
    """The next `count` free slots, soonest first (fewer if the horizon runs
    out first, which is an honest answer rather than a slot that breaks a limit).

    `taken` is every instant already committed, whatever it is (clip or
    compilation, any provider); a fan-out to three platforms is ONE instant.
    `caps` maps a platform to its documented posts per day and `platform_taken`
    lists what is already scheduled to each, so a day never goes over a cap.
    """
    now = now or datetime.now(timezone.utc)
    earliest = now + timedelta(seconds=MIN_LEAD_SECONDS + 60)
    today = _local(now, tz).date()
    caps = caps or {}
    platform_taken = platform_taken or {}
    used: dict[tuple[str, date], int] = {}
    for platform, moments in platform_taken.items():
        for m in moments:
            key = (platform, _local(m, tz).date())
            used[key] = used.get(key, 0) + 1

    chosen: list[datetime] = []
    booked = list(taken)
    for slot in policy_slots(policy, today, horizon_days, grid=grid, tz=tz):
        if len(chosen) >= count:
            break
        if slot < earliest or is_taken(slot, booked):
            continue
        day = _local(slot, tz).date()
        if any(used.get((p, day), 0) >= cap for p, cap in caps.items()):
            continue
        chosen.append(slot)
        booked.append(slot)
        for p in caps:
            used[(p, day)] = used.get((p, day), 0) + 1
    return chosen
