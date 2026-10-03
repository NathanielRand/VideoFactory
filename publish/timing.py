"""When to post: default audience peaks per platform, learned from results.

Everything here is in the creator's LOCAL wall-clock time, as a 7x24 grid of
weights (Monday = 0, hour 0-23). The browser owns timezones (see
publish/schedule.py for why), so it sends its UTC offset and turns the slots
this returns back into instants itself.

Two sources, blended:

* Defaults: the widely reported peak windows for each platform. They are a
  starting point, and a reasonable one for a channel with no history.
* Learned: how this channel's own posts did by the hour they went live. Views
  are compared on a log scale against the channel's own typical post, so one
  viral outlier cannot claim a whole weekday. The learned grid takes over
  gradually, in proportion to how many posts back it.

Stdlib only.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

Grid = list[list[float]]  # [weekday][hour]

# Posts younger than this have not had their views yet, and would read as
# failures. Two days covers the bulk of a Short's or a post's reach.
MIN_AGE_HOURS = 48
# How many posts before the learned grid counts fully.
FULL_CONFIDENCE_POSTS = 40

WEEKDAYS = range(0, 5)
WEEKEND = range(5, 7)

# (days, first hour, last hour inclusive, weight). Later rows win on overlap.
_WINDOWS: dict[str, list[tuple[range, int, int, float]]] = {
    "youtube": [
        (range(7), 12, 15, 0.7), (range(7), 17, 21, 1.0),
        (WEEKEND, 9, 12, 0.8),
    ],
    "tiktok": [
        (range(7), 7, 9, 0.6), (range(7), 12, 14, 0.7), (range(7), 18, 22, 1.0),
        (range(1, 4), 9, 11, 0.8),
    ],
    "instagram": [
        (WEEKDAYS, 11, 13, 0.9), (range(7), 18, 21, 1.0), (WEEKEND, 9, 11, 0.7),
    ],
    "facebook": [(WEEKDAYS, 9, 13, 1.0), (range(7), 18, 20, 0.7)],
    "x": [(WEEKDAYS, 8, 10, 1.0), (WEEKDAYS, 12, 13, 0.8), (range(7), 17, 19, 0.7)],
    "threads": [(WEEKDAYS, 8, 10, 0.9), (range(7), 18, 21, 1.0)],
    "bluesky": [(WEEKDAYS, 8, 11, 1.0), (range(7), 18, 21, 0.8)],
    "linkedin": [(range(1, 4), 8, 10, 1.0), (WEEKDAYS, 12, 13, 0.8)],
    "pinterest": [(range(7), 20, 23, 1.0), (WEEKEND, 13, 16, 0.8)],
}
_FALLBACK = [(range(7), 12, 14, 0.7), (range(7), 18, 21, 1.0)]


def _empty(value: float = 0.0) -> Grid:
    return [[value] * 24 for _ in range(7)]


def default_grid(platform: str) -> Grid:
    """The platform's usual peaks. Daytime off-peak is 0.3; the small hours
    are 0.05, so nothing is ever scheduled for 4 a.m. without evidence."""
    grid = _empty()
    for day in range(7):
        for hour in range(24):
            grid[day][hour] = 0.05 if hour < 7 else 0.3
    for days, first, last, weight in _WINDOWS.get(platform, _FALLBACK):
        for day in days:
            for hour in range(first, last + 1):
                grid[day][hour] = weight
    return grid


def local_slot(published_at: str, offset_minutes: int) -> tuple[int, int] | None:
    """(weekday, hour) of a UTC timestamp, shifted to the creator's offset."""
    try:
        moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(minutes=offset_minutes)))
    return local.weekday(), local.hour


def learned_grid(posts: list[dict], offset_minutes: int, *, now: datetime | None = None) -> tuple[Grid, int]:
    """How posts did by the hour they went live, and how many posts count.

    Each post's log-views is compared with the channel's median, so the grid
    holds "better or worse than usual" (0 is typical), not raw views. A post
    also lends half its weight to the hours either side: one post at 18:00 is
    weak evidence about 18:00 and about as good for 17:00 and 19:00.
    """
    now = now or datetime.now(timezone.utc)
    samples: list[tuple[int, int, float]] = []
    for p in posts:
        slot = local_slot(str(p.get("published_at") or ""), offset_minutes)
        if slot is None:
            continue
        try:
            went = datetime.fromisoformat(str(p["published_at"]).replace("Z", "+00:00"))
            if went.tzinfo is None:
                went = went.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        if (now - went).total_seconds() < MIN_AGE_HOURS * 3600:
            continue
        samples.append((*slot, math.log1p(max(0, int(p.get("views") or 0)))))

    grid = _empty()
    if not samples:
        return grid, 0
    values = sorted(v for _, _, v in samples)
    median = (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2
    total, weight = _empty(), _empty()
    for day, hour, v in samples:
        for shift, w in ((0, 1.0), (-1, 0.5), (1, 0.5)):
            h = hour + shift
            d = (day + (h // 24)) % 7
            h %= 24
            total[d][h] += (v - median) * w
            weight[d][h] += w
    for d in range(7):
        for h in range(24):
            if weight[d][h]:
                grid[d][h] = total[d][h] / weight[d][h]
    return grid, len(samples)


def blended_grid(platform: str, posts: list[dict], offset_minutes: int,
                 *, now: datetime | None = None) -> tuple[Grid, float]:
    """Defaults nudged by what this channel's own posts showed.

    Returns the grid and the confidence given to the learned part (0..1), so
    the UI can say "based on your last 23 posts" rather than pretend.
    """
    base = default_grid(platform)
    learned, n = learned_grid(posts, offset_minutes, now=now)
    confidence = min(1.0, n / FULL_CONFIDENCE_POSTS)
    if not n:
        return base, 0.0
    out = _empty()
    for d in range(7):
        for h in range(24):
            # A log-views difference of ~1 (about 2.7x the usual views) is a
            # strong signal; squash it into -1..1 before mixing.
            signal = math.tanh(learned[d][h])
            out[d][h] = max(0.0, base[d][h] + confidence * signal * 0.8)
    return out, confidence


def best_slots(
    grid: Grid,
    *,
    now_weekday: int,
    now_hour: int,
    count: int,
    per_day: int = 3,
    min_gap_hours: int = 2,
    horizon_days: int = 60,
    taken: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """`count` posting slots as (days from today, local hour), soonest first.

    Each day takes its best `per_day` hours at least `min_gap_hours` apart,
    so a run still spreads across days instead of stacking onto the one best
    evening. Posts already `taken` (day offset, hour) count against their
    day's budget and its spacing, so a second batch queues around the first
    rather than doubling a day up. The first hour offered today is two hours
    out, room for the upload and the scheduler's lead time.
    """
    if count < 1:
        return []
    per_day = max(1, per_day)
    taken = taken or set()
    out: list[tuple[int, int]] = []
    for day_offset in range(horizon_days):
        weekday = (now_weekday + day_offset) % 7
        start = now_hour + 2 if day_offset == 0 else 0
        hours = sorted(
            (h for h in range(start, 24) if (day_offset, h) not in taken),
            key=lambda h: -grid[weekday][h],
        )
        booked = [h for d, h in taken if d == day_offset]
        room = per_day - len(booked)
        chosen: list[int] = []
        for h in hours:
            if len(chosen) >= room or grid[weekday][h] <= 0.06:
                break
            if all(abs(h - c) >= min_gap_hours for c in chosen + booked):
                chosen.append(h)
        for h in sorted(chosen):
            out.append((day_offset, h))
            if len(out) >= count:
                return out
    return out


def top_hours(grid: Grid, n: int = 5) -> list[tuple[int, int, float]]:
    """The n best (weekday, hour, weight) cells, for "best times" display."""
    cells = [(d, h, grid[d][h]) for d in range(7) for h in range(24)]
    return sorted(cells, key=lambda c: -c[2])[:n]
