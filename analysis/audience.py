"""Who the connected channel's viewers are, as one line a model can write for.

The numbers come from YouTube Analytics for the connected channel (the only
audience data anyone can read: another channel's demographics are private).
They are turned into text by plain code, not by a model: "71% male, mostly
18-24, mostly US" is already enough for the metadata model to pick a register,
and an extra model call would only add a place for it to invent something.

Everything here is optional and failure-safe. A channel too small for YouTube
to report on, a connection made before the analytics permission was asked for,
or no connection at all gives "", and the copy is then written from the
creator's own speech alone. It never raises, because metadata never fails a run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

FLAG = "audience_profile"
REFRESH = timedelta(days=7)     # an audience changes slowly; Analytics is not hit per run
RETRY_EMPTY = timedelta(days=1)  # nothing to report yet: look again sooner than that

_AGE = {
    "age13-17": "13-17", "age18-24": "18-24", "age25-34": "25-34", "age35-44": "35-44",
    "age45-54": "45-54", "age55-64": "55-64", "age65-": "65+",
}
_COUNTRY = {  # the ones whose spelling and slang differ; others are shown by code
    "US": "US", "GB": "UK", "CA": "Canada", "AU": "Australia", "NZ": "New Zealand",
    "IE": "Ireland", "IN": "India", "PH": "Philippines", "DE": "Germany", "FR": "France",
    "BR": "Brazil", "MX": "Mexico", "ES": "Spain", "NL": "Netherlands", "SE": "Sweden",
}


def describe(age_gender: list[dict], countries: list[dict]) -> str:
    """The audience in a sentence or two, from the two Analytics reports.

    `age_gender` rows are {"ageGroup", "gender", "viewerPercentage"} (a share of
    all viewers per combination); `countries` rows are {"country", "views"}.
    "" when there is nothing to say."""
    parts: list[str] = []

    by_gender: dict[str, float] = {}
    by_age: dict[str, float] = {}
    for r in age_gender or []:
        pct = _num(r.get("viewerPercentage"))
        if pct <= 0:
            continue
        gender = str(r.get("gender") or "")
        by_gender[gender] = by_gender.get(gender, 0.0) + pct
        age = _AGE.get(str(r.get("ageGroup") or ""))
        if age:
            by_age[age] = by_age.get(age, 0.0) + pct
    total = sum(by_gender.values())
    if total > 0:
        shares = sorted(((g, v / total * 100) for g, v in by_gender.items() if g in ("male", "female")),
                        key=lambda kv: -kv[1])
        if shares:
            parts.append(", ".join(f"{round(v)}% {g}" for g, v in shares))
        top = sorted(by_age.items(), key=lambda kv: -kv[1])[:2]
        age_total = sum(by_age.values())
        if top and age_total > 0:
            parts.append("mostly ages " + " and ".join(
                f"{a} ({round(v / age_total * 100)}%)" for a, v in top))

    views = [(str(r.get("country") or ""), _num(r.get("views"))) for r in countries or []]
    views = [(c, v) for c, v in views if c and v > 0]
    all_views = sum(v for _, v in views)
    if all_views > 0:
        top3 = sorted(views, key=lambda kv: -kv[1])[:3]
        parts.append("top countries: " + ", ".join(
            f"{_COUNTRY.get(c, c)} {round(v / all_views * 100)}%" for c, v in top3))

    return ("Viewers of this channel: " + "; ".join(parts) + ".") if parts else ""


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fetch(publisher) -> str:
    """The audience line from a connected publisher, "" on any failure."""
    try:
        report = publisher.audience_report()
        return describe(report.get("age_gender") or [], report.get("countries") or [])
    except Exception as e:  # AuthRequired (old connection), quota, no rows, no network
        print(f"      (audience profile unavailable: {getattr(e, 'message', e)})")
        return ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cached(db, make_publisher, now: datetime | None = None) -> str:
    """The stored audience line, refreshed through `make_publisher()` when it is
    older than REFRESH. A failed refresh keeps what was stored: a stale
    audience still beats none. Never raises."""
    now = now or _now()
    try:
        stored = json.loads(db.get_flag(FLAG, "") or "{}")
    except (ValueError, TypeError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    text = str(stored.get("text") or "")
    try:
        fetched = datetime.fromisoformat(str(stored.get("fetched_at") or ""))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except ValueError:
        fetched = None
    if fetched is not None and now - fetched < (REFRESH if text else RETRY_EMPTY):
        return text
    try:
        fresh = fetch(make_publisher())
    except Exception as e:
        print(f"      (audience profile unavailable: {getattr(e, 'message', e)})")
        fresh = ""
    text = fresh or text
    try:
        db.set_flag(FLAG, json.dumps({"fetched_at": now.isoformat(), "text": text}))
    except Exception:
        pass
    return text


def for_run(db, config: dict, data_dir) -> str:
    """The audience line for a processing run or a copy request: "" unless
    YouTube is connected, and never an error."""
    try:
        from server import youtube_service as yt

        if not yt.load_settings(db).get("enabled"):
            return ""
        return cached(db, lambda: yt.make_publisher(config, data_dir, "private"))
    except Exception:
        return ""
