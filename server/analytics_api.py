"""Analytics: everything the connected sources report, in one place.

Only YouTube reports numbers back today (views, likes, comments, when each
video went live). WoopSocial and Upload-Post only say whether a post went out,
so for them this reports counts of posts by state and never invents a metric.
Each figure is broken down by what the content is: a clip, a compilation, or
something on the channel this app did not make.

Read-only. YouTube is asked for at most a couple of quota units per 50 videos,
and the answer is kept for ten minutes because the Home and Publish pages
both ask on every visit.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CACHE_SECONDS = 600
TIMELINE_DAYS = 30
TOP = 8

_cache: dict[str, tuple[float, list[dict]]] = {}


def _zero() -> dict:
    return {"videos": 0, "views": 0, "likes": 0, "comments": 0}


def _add(bucket: dict, v: dict) -> None:
    bucket["videos"] += 1
    bucket["views"] += v.get("views") or 0
    bucket["likes"] += v.get("likes") or 0
    bucket["comments"] += v.get("comments") or 0


def _day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def kind_of(publish_id: int | None) -> str:
    if publish_id is None:
        return "other"
    return "compilation" if publish_id < 0 else "clip"


def summarize_videos(videos: list[dict], today: datetime | None = None) -> dict:
    """Totals, splits and the best performers of channel videos (each shaped
    by publish.youtube_status.summarize plus `publish_id` and `via`)."""
    today = today or datetime.now(timezone.utc)
    totals = _zero()
    states: dict[str, int] = {}
    by_type = {"clip": _zero(), "compilation": _zero(), "other": _zero()}
    by_format = {"short": _zero(), "long": _zero()}
    by_via: dict[str, dict] = {}
    by_channel: dict[str, dict] = {}
    days: dict[str, dict] = {}
    counted: list[dict] = []
    for v in videos:
        states[v["state"]] = states.get(v["state"], 0) + 1
        # Private and scheduled videos have no audience yet: counting their
        # zeros would drag every average down.
        if v["state"] not in ("live", "unlisted"):
            continue
        counted.append(v)
        _add(totals, v)
        _add(by_type[kind_of(v.get("publish_id"))], v)
        _add(by_format["short" if v.get("short") else "long"], v)
        _add(by_via.setdefault(v.get("via") or "manual", _zero()), v)
        _add(by_channel.setdefault(v.get("channel") or "", _zero()), v)
        d = _day(v.get("published_at") or "")
        if d:
            _add(days.setdefault(d, _zero()), v)
    start = (today - timedelta(days=TIMELINE_DAYS - 1)).date()
    timeline = []
    for i in range(TIMELINE_DAYS):
        d = (start + timedelta(days=i)).isoformat()
        timeline.append({"date": d, **days.get(d, _zero())})
    best = sorted(counted, key=lambda v: v.get("views") or 0, reverse=True)[:TOP]
    totals["average_views"] = round(totals["views"] / totals["videos"]) if totals["videos"] else 0
    return {
        "totals": totals,
        "states": states,
        "by_type": by_type,
        "by_format": by_format,
        "by_via": by_via,
        "by_channel": by_channel,
        "timeline": timeline,
        "top": [
            {
                "video_id": v["video_id"],
                "title": v["title"],
                "thumbnail": v["thumbnail"],
                "url": v["url"],
                "views": v.get("views"),
                "likes": v.get("likes"),
                "comments": v.get("comments"),
                "kind": kind_of(v.get("publish_id")),
                "short": v.get("short", False),
                "publish_id": v.get("publish_id"),
            }
            for v in best
        ],
    }


def install(app, *, config, db, data_dir: Path) -> None:
    def _channel_videos(d, fresh: bool) -> tuple[list[dict] | None, str, list[dict]]:
        """(videos, error, accounts). videos is None when YouTube is off or
        not connected: not an error, just nothing to report."""
        from publish.youtube_status import summarize
        from server import publishing_api
        from server import youtube_service as yt
        from server.youtube_api import ours_by_video

        if not yt.is_enabled(d):
            return None, "", []
        # No roster: a single-token install, which still works and is read
        # as the unqualified default channel (as refresh_youtube_stats does).
        accounts = yt.load_accounts(d) or [{"id": None, "title": ""}]
        ours = ours_by_video(d)
        via: dict[str, str] = {}
        for r in d.conn.execute("SELECT youtube_id FROM uploads").fetchall():
            via[r["youtube_id"]] = "direct"
        from publish.youtube_status import video_id_from

        for r in d.conn.execute(
            "SELECT provider, post_id, post_url FROM clip_publishes WHERE platform = 'youtube'"
        ).fetchall():
            vid = video_id_from(r["post_url"] or "") or video_id_from(r["post_id"] or "")
            if vid:
                via.setdefault(vid, r["provider"] or "app")
        out: list[dict] = []
        errors: list[str] = []
        for a in accounts:
            key = a.get("id") or ""
            hit = _cache.get(key)
            if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
                videos = hit[1]
            else:
                try:
                    items = yt.make_publisher(config, data_dir, "private", a["id"]).channel_videos(200)
                except Exception as e:
                    errors.append(f"{a.get('title') or 'YouTube'}: {getattr(e, 'message', str(e))}")
                    continue
                videos = [summarize(i) for i in items]
                _cache[key] = (time.monotonic(), videos)
                yt.spend_read(d, len(items))
                try:
                    publishing_api.record_stats(d, videos)
                except Exception:
                    pass
            for v in videos:
                out.append({
                    **v,
                    "channel": a.get("title") or "",
                    "publish_id": ours.get(v["video_id"]),
                    "via": via.get(v["video_id"], "manual"),
                })
        if not out and errors:
            return None, "; ".join(errors)[:400], accounts
        return out, "; ".join(errors)[:400], accounts

    def _posts(d) -> dict:
        """What went out through every provider, from this app's own record:
        the one number all of them can give."""
        rows = d.conn.execute(
            "SELECT platform, provider, state, COUNT(*) AS n FROM clip_publishes "
            "GROUP BY platform, provider, state"
        ).fetchall()
        by_platform: dict[str, dict] = {}
        for r in rows:
            p = by_platform.setdefault(r["platform"], {"published": 0, "scheduled": 0, "failed": 0, "providers": []})
            if r["state"] == "published":
                p["published"] += r["n"]
            elif r["state"] in ("queued", "processing", "sending"):
                p["scheduled"] += r["n"]
            elif r["state"] == "failed":
                p["failed"] += r["n"]
            if r["provider"] and r["provider"] not in p["providers"]:
                p["providers"].append(r["provider"])
        direct = d.conn.execute("SELECT COUNT(*) AS n FROM uploads").fetchone()["n"]
        if direct:
            y = by_platform.setdefault("youtube", {"published": 0, "scheduled": 0, "failed": 0, "providers": []})
            y["published"] += direct
            if "direct" not in y["providers"]:
                y["providers"].append("direct")
        return by_platform

    def _thumbnail_coverage(d) -> dict:
        ids = [r["id"] for r in d.conn.execute("SELECT id FROM clips WHERE path IS NOT NULL AND path != ''").fetchall()]
        ids += [-r["id"] for r in d.conn.execute("SELECT id FROM compilations WHERE status = 'done'").fetchall()]
        folder = Path(data_dir) / "thumbnails"
        have = sum(1 for i in ids if (folder / f"clip_{int(i)}_chosen.jpg").exists())
        return {"with": have, "without": len(ids) - have, "total": len(ids)}

    def _daily(d, accounts: list[dict]) -> dict:
        """Real per-day numbers from YouTube Analytics, summed over channels.
        {"days": [...], "error": ""}; an error explains itself (usually: the
        connection predates the analytics permission)."""
        from server import youtube_service as yt

        days: dict[str, dict] = {}
        errors: list[str] = []
        for a in accounts:
            try:
                rows = yt.make_publisher(config, data_dir, "private", a.get("id")).daily_report(TIMELINE_DAYS)
            except Exception as e:
                errors.append(getattr(e, "message", str(e))[:200])
                continue
            for r in rows:
                day = days.setdefault(str(r.get("day")), {
                    "date": str(r.get("day")), "views": 0, "minutes": 0, "likes": 0,
                    "comments": 0, "subscribers": 0, "subscribers_lost": 0, "_dur": 0.0,
                })
                views = int(r.get("views") or 0)
                day["views"] += views
                day["minutes"] += int(r.get("estimatedMinutesWatched") or 0)
                day["likes"] += int(r.get("likes") or 0)
                day["comments"] += int(r.get("comments") or 0)
                day["subscribers"] += int(r.get("subscribersGained") or 0)
                day["subscribers_lost"] += int(r.get("subscribersLost") or 0)
                day["_dur"] += float(r.get("averageViewDuration") or 0) * views
        out = []
        for k in sorted(days):
            day = days[k]
            dur = day.pop("_dur")
            day["average_view_seconds"] = round(dur / day["views"]) if day["views"] else 0
            out.append(day)
        return {"days": out, "error": "; ".join(dict.fromkeys(errors))[:300]}

    def _subscribers(d, accounts: list[dict], videos: list[dict]) -> dict:
        """Subscriber totals, and the videos credited with new subscribers.
        Per channel, then merged. `videos` (the channel's own list) names them
        and says whether each is a clip, a compilation or something else."""
        from server import youtube_service as yt

        by_id = {v["video_id"]: v for v in videos}
        total, hidden, any_total = 0, False, False
        credited: dict[str, dict] = {}
        errors: list[str] = []
        for a in accounts:
            try:
                pub = yt.make_publisher(config, data_dir, "private", a.get("id"))
                got = pub.subscriber_count()
                yt.spend(d, "channels.list")
                if got["count"] is not None:
                    total += got["count"]
                    any_total = True
                hidden = hidden or got["hidden"]
                rows = pub.video_subscribers(90, 25)
            except Exception as e:
                errors.append(getattr(e, "message", str(e))[:200])
                continue
            for r in rows:
                vid = str(r.get("video") or "")
                mine = credited.setdefault(vid, {"video_id": vid, "gained": 0, "lost": 0, "views": 0})
                mine["gained"] += int(r.get("subscribersGained") or 0)
                mine["lost"] += int(r.get("subscribersLost") or 0)
                mine["views"] += int(r.get("views") or 0)
        top = []
        for vid, r in sorted(credited.items(), key=lambda kv: -kv[1]["gained"])[:10]:
            if r["gained"] <= 0:
                continue
            v = by_id.get(vid) or {}
            top.append({
                **r,
                "title": v.get("title") or vid,
                "thumbnail": v.get("thumbnail") or "",
                "url": v.get("url") or f"https://www.youtube.com/watch?v={vid}",
                "kind": kind_of(v.get("publish_id")) if v else "other",
                "publish_id": v.get("publish_id"),
            })
        return {
            "total": total if any_total else None,
            "hidden": hidden,
            "days": 90,
            "top": top,
            "error": "; ".join(dict.fromkeys(errors))[:300],
        }

    @app.get("/analytics/summary")
    def summary(fresh: bool = False):
        d = db()
        try:
            videos, error, accounts = _channel_videos(d, fresh)
            body = {
                "youtube": {
                    "connected": videos is not None,
                    "channels": [{"id": a["id"], "title": a.get("title") or ""} for a in accounts],
                    "error": error,
                },
                "posts": _posts(d),
                "thumbnails": _thumbnail_coverage(d),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            if videos is not None:
                body.update(summarize_videos(videos))
                body["daily"] = _daily(d, accounts)
                body["subscribers"] = _subscribers(d, accounts, videos)
            return body
        finally:
            d.close()
