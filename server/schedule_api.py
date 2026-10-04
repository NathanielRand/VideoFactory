"""The posting schedule, across every provider: see it, cancel what can be
cancelled, and clear what is finished.

Two different things, on purpose:

* **Cancel** actually stops a post. It is only offered where the provider (or
  this app's own queue) really can: a direct-YouTube upload still waiting in
  the queue, or a WoopSocial post before any of its deliveries has started.
  WoopSocial refuses (409) once one has, and that refusal is passed on.
  Upload-Post has a cancel, but it takes a scheduler job id that this app does
  not record from the upload, so it is not offered; those posts show a link to
  the provider instead of a button that cannot know which job to cancel.
* **Clear** only tidies this list. It never deletes a record: the publish
  badges, "Fully posted" and analytics all read the same rows, so a finished
  row just stops being *scheduled* (its scheduled_for is emptied). A post
  that is still waiting at a provider cannot be cleared, because hiding it
  would not stop it going out.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel

WAITING = ("queued", "processing", "sending")
FINISHED = ("published", "failed", "skipped")


class RowIn(BaseModel):
    clip_id: int
    platform: str


class NextIn(BaseModel):
    count: int = 1
    # Where the posts are going, so a platform's daily cap is respected. The
    # provider decides whose caps apply ("woopsocial", "uploadpost", "youtube").
    platforms: list[str] = []
    provider: str = ""
    horizon_days: int = 60


class DupIn(BaseModel):
    clip_ids: list[int]


class ClearIn(BaseModel):
    # Specific rows, or every finished row when omitted.
    items: list[RowIn] | None = None


def all_items(d) -> list[dict]:
    """Everything committed or recently handled, from every provider: one
    source for the schedule list, the calendar, the quota forecast and the slot
    planner, so none of them can disagree about what is taken."""
    return _rows(d) + _uploads(d) + _jobs(d)


def _rows(d) -> list[dict]:
    rows = d.conn.execute(
        "SELECT p.clip_id, p.platform, p.provider, p.state, p.scheduled_for, p.post_url, "
        "       p.error, p.request_id, c.title, c.hook, m.title AS comp_title "
        "FROM clip_publishes p LEFT JOIN clips c ON c.id = p.clip_id "
        "LEFT JOIN compilations m ON p.clip_id < 0 AND m.id = -p.clip_id "
        # A waiting post with no time is one the provider is queueing for
        # its own slot (Upload-Post's queue) or from before times were kept.
        "WHERE p.scheduled_for != '' OR p.state IN ('queued', 'processing', 'sending') "
        "ORDER BY p.scheduled_for ASC LIMIT 500"
    ).fetchall()
    out = []
    for r in rows:
        provider = r["provider"] or ""
        waiting = r["state"] in WAITING
        # WoopSocial's post id is the request id shared by the whole
        # fan-out: cancelling one platform cancels the post for all.
        group = 0
        if provider == "woopsocial" and r["request_id"]:
            group = d.conn.execute(
                "SELECT COUNT(*) FROM clip_publishes WHERE request_id = ? AND state IN ('queued','processing')",
                (r["request_id"],),
            ).fetchone()[0]
        out.append({
            "kind": "post",
            "publish_id": r["clip_id"],
            "clip_id": r["clip_id"],
            "platform": r["platform"],
            "provider": provider,
            "state": r["state"],
            "scheduled_for": r["scheduled_for"],
            "post_url": r["post_url"] or "",
            "error": r["error"] or "",
            "title": r["title"] or r["hook"] or r["comp_title"] or f"Clip {r['clip_id']}",
            "waiting": waiting,
            "can_cancel": provider == "woopsocial" and bool(r["request_id"]) and r["state"] == "queued",
            "cancel_group": group,
        })
    return out

def _uploads(d) -> list[dict]:
    """Direct-YouTube clips already uploaded and waiting for their go-live
    time on YouTube. They never wrote a clip_publishes row (only
    compilations do), so the schedule could not see them."""
    now = datetime.now(timezone.utc)
    out = []
    for u in d.conn.execute(
        "SELECT u.clip_id, u.youtube_id, u.publish_at, c.title, c.hook FROM uploads u "
        "LEFT JOIN clips c ON c.id = u.clip_id WHERE u.publish_at != ''"
    ).fetchall():
        try:
            when = datetime.fromisoformat(str(u["publish_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when <= now:
            continue  # it has gone live: that is history, not schedule
        out.append({
            "kind": "upload",
            "publish_id": u["clip_id"],
            "youtube_id": u["youtube_id"],
            "clip_id": u["clip_id"],
            "platform": "youtube",
            "provider": "youtube",
            "state": "scheduled",
            "scheduled_for": u["publish_at"],
            "post_url": f"https://studio.youtube.com/video/{u['youtube_id']}/edit",
            "error": "",
            "title": u["title"] or u["hook"] or f"Clip {u['clip_id']}",
            "waiting": True,
            # Takes it off YouTube's schedule (needs the full permission).
            "can_cancel": True,
            "cancel_group": 0,
        })
    return out

def _jobs(d) -> list[dict]:
    """Direct-YouTube uploads still waiting their turn in the app's queue:
    nothing has been sent to YouTube yet, so cancelling costs nothing."""
    out = []
    for j in d.conn.execute("SELECT * FROM publish_jobs WHERE status = 'queued' ORDER BY id").fetchall():
        try:
            req = json.loads(j["request"] or "{}")
        except ValueError:
            req = {}
        clip = d.get_publishable(j["clip_id"])
        out.append({
            "kind": "job",
            "publish_id": j["clip_id"],
            "job_id": j["id"],
            "clip_id": j["clip_id"],
            "platform": "youtube",
            "provider": "youtube",
            "state": "queued",
            "scheduled_for": req.get("publish_at") or "",
            "post_url": "",
            "error": "",
            "title": (clip["title"] or clip["hook"]) if clip is not None else f"Clip {j['clip_id']}",
            "waiting": True,
            "can_cancel": True,
            "cancel_group": 0,
        })
    return out


def _instant(text: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _norm(provider: str) -> str:
    """"upload_post" (what is stored) and "uploadpost" (what the UI says)."""
    return (provider or "").replace("_", "").lower()


def committed(items: list[dict]) -> dict:
    """What is already spoken for, from `all_items`.

    `instants`: every distinct moment something is scheduled, however many
    platforms it goes to (a fan-out to three is ONE post). `by_platform`:
    per (provider, platform), for the platform caps. Failed and skipped posts
    hold nothing; a post with no time (a provider's own queue) has no moment."""
    seen: set[tuple[int, str]] = set()
    instants: list[datetime] = []
    by_platform: dict[tuple[str, str], set[tuple[int, datetime]]] = {}
    for i in items:
        if i["state"] in ("failed", "skipped") or not i["scheduled_for"]:
            continue
        when = _instant(i["scheduled_for"])
        if when is None:
            continue
        key = (int(i.get("publish_id") or 0), i["scheduled_for"])
        if key not in seen:
            seen.add(key)
            instants.append(when)
        by_platform.setdefault((_norm(i["provider"]), i["platform"]), set()).add((key[0], when))
    return {"instants": sorted(instants), "by_platform": by_platform}


def _machine_offset_minutes() -> int:
    return int((datetime.now().astimezone().utcoffset() or timedelta()).total_seconds() // 60)


def _grid(d, platform: str = "youtube"):
    """The blended best-hours grid for the machine's own timezone."""
    from publish import timing
    from server import publishing_api

    grid, _ = timing.blended_grid(platform, publishing_api.stats_rows(d, platform), _machine_offset_minutes())
    return grid


def _z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_slots(d, *, count: int, platforms: list[str], provider: str, horizon_days: int = 60,
               now: datetime | None = None, tz=None) -> list[datetime]:
    """The next `count` free slots for a post going to `platforms` through
    `provider`, around everything committed, within the policy and the caps."""
    from publish.quota import PROVIDER_LIMITS
    from publish.slots import Policy, free_slots
    from server import publishing_api

    policy = Policy.from_settings(publishing_api.load_settings(d))
    have = committed(all_items(d))
    prov = _norm(provider)
    caps, taken_by = {}, {}
    for platform in platforms:
        cap = PROVIDER_LIMITS.get((prov, platform))
        if cap:
            caps[platform] = cap[0]
            taken_by[platform] = [w for _, w in have["by_platform"].get((prov, platform), set())]
    return free_slots(
        policy, have["instants"], max(1, min(int(count), 200)), now=now,
        grid=_grid(d, platforms[0] if platforms else "youtube") if policy.mode == "best" else None,
        caps=caps, platform_taken=taken_by, horizon_days=max(1, min(int(horizon_days), 365)), tz=tz,
    )


def duplicates(d, publish_ids: list[int]) -> dict[int, list[dict]]:
    """Where each clip (or, negative, compilation) is already live or waiting to
    go out, so publishing it again can be a decision instead of an accident.

    Matched on the clip's lasting identity as well as its id, because a
    re-render gives the same clip a new id. Failed and skipped posts hold
    nothing and are not duplicates: they are what a retry is for."""
    found: dict[int, list[dict]] = {}
    now = datetime.now(timezone.utc)
    for pid in dict.fromkeys(publish_ids[:500]):
        clip = d.get_publishable(pid)
        if clip is None:
            continue
        vid, start, end = clip["video_id"] or "", clip["start_s"], clip["end_s"]
        seen: dict[tuple[str, str], dict] = {}

        def add(platform, provider, state, at, url, seen=seen):  # bound per iteration
            seen.setdefault((_norm(provider), platform), {
                "platform": platform, "provider": provider, "state": state, "at": at or "", "url": url or ""})

        for r in d.conn.execute(
            "SELECT platform, provider, state, scheduled_for, post_url FROM clip_publishes "
            "WHERE state IN ('queued', 'processing', 'sending', 'published') AND "
            "(clip_id = ? OR (video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?))",
            (pid, vid, start, end),
        ).fetchall():
            add(r["platform"], r["provider"] or "", "live" if r["state"] == "published" else "scheduled",
                r["scheduled_for"], r["post_url"])
        for u in d.conn.execute(
            "SELECT youtube_id, publish_at FROM uploads WHERE clip_id = ? OR "
            "(video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)",
            (pid, vid, start, end),
        ).fetchall():
            when = _instant(u["publish_at"]) if u["publish_at"] else None
            add("youtube", "youtube", "scheduled" if when and when > now else "live", u["publish_at"],
                f"https://www.youtube.com/watch?v={u['youtube_id']}")
        for j in d.conn.execute(
            "SELECT request FROM publish_jobs WHERE status IN ('queued', 'running') AND "
            "(clip_id = ? OR (video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?))",
            (pid, vid, start, end),
        ).fetchall():
            try:
                at = json.loads(j["request"] or "{}").get("publish_at") or ""
            except ValueError:
                at = ""
            add("youtube", "youtube", "scheduled", at, "")
        if seen:
            found[pid] = list(seen.values())
    return found


def install(app, *, config, db, data_dir) -> None:
    @app.get("/publishing/schedule")
    def schedule():
        """Everything waiting to go out or recently handled, from every
        provider, soonest first."""
        d = db()
        try:
            items = all_items(d)
        finally:
            d.close()
        return {"items": sorted(items, key=lambda i: i["scheduled_for"] or "9999")}

    @app.get("/publishing/calendar")
    def calendar(start: str = "", days: int = 14):
        """The schedule as a calendar: the policy's slots over a range, and
        everything committed or recently handled, so the UI can show which
        slots are open. Days are the creator's local days."""
        from dataclasses import asdict

        from publish.slots import Policy, is_taken, policy_slots
        from server import publishing_api

        days = max(1, min(int(days), 60))
        try:
            first = date.fromisoformat(start) if start else datetime.now().astimezone().date()
        except ValueError:
            raise HTTPException(400, "start must be a date like 2026-09-28") from None
        d = db()
        try:
            policy = Policy.from_settings(publishing_api.load_settings(d))
            items = all_items(d)
            grid = _grid(d) if policy.mode == "best" else None
        finally:
            d.close()
        have = committed(items)
        lo = datetime(first.year, first.month, first.day).astimezone(timezone.utc)
        hi = lo + timedelta(days=days)
        inside, waiting_untimed = [], []
        for i in items:
            when = _instant(i["scheduled_for"]) if i["scheduled_for"] else None
            if when is None:
                if i["waiting"]:
                    waiting_untimed.append(i)
            elif lo - timedelta(hours=1) <= when < hi + timedelta(hours=1):
                inside.append(i)
        slots = [
            {"at": _z(s), "taken": is_taken(s, have["instants"])}
            for s in policy_slots(policy, first, days, grid=grid)
        ]
        return {
            "policy": asdict(policy),
            "start": first.isoformat(),
            "days": days,
            "items": inside,
            "unscheduled": waiting_untimed,
            "slots": slots,
        }

    @app.post("/publishing/duplicates")
    def find_duplicates(body: DupIn):
        """Which of these clips are already live or scheduled, and where."""
        d = db()
        try:
            return {"duplicates": {str(k): v for k, v in duplicates(d, body.clip_ids).items()}}
        finally:
            d.close()

    @app.post("/publishing/slots/next")
    def slots_next(body: NextIn):
        """The next free slots for a new post, around everything already
        scheduled. The plan a person agrees to in a dialog is asked for again
        at the moment of publishing, so two batches never land on one slot."""
        d = db()
        try:
            got = next_slots(d, count=body.count, platforms=body.platforms, provider=body.provider,
                             horizon_days=body.horizon_days)
        finally:
            d.close()
        return {"slots": [_z(m) for m in got], "short": len(got) < max(1, body.count)}

    @app.post("/publishing/schedule/job/{job_id}/cancel")
    def cancel_job(job_id: int):
        d = db()
        try:
            job = d.get_publish_job(job_id)
            if job is None:
                raise HTTPException(404, "no such queued upload")
            if job["status"] != "queued":
                raise HTTPException(409, "That upload has already started. Cancel it from its progress bar.")
            d.finish_publish_job(job_id, "cancelled", error="Cancelled.")
        finally:
            d.close()
        return {"cancelled": True}

    @app.post("/publishing/schedule/cancel")
    def cancel_post(body: RowIn):
        """Stop a scheduled post at the provider. Cancels every platform that
        shared the post, because the provider holds them as one."""
        from publish.errors import PublishError

        d = db()
        try:
            row = d.conn.execute(
                "SELECT * FROM clip_publishes WHERE clip_id = ? AND platform = ?",
                (body.clip_id, body.platform),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "no such scheduled post")
            if row["provider"] != "woopsocial" or not row["request_id"]:
                raise HTTPException(
                    400,
                    "This post cannot be cancelled from here. Cancel it in the provider's own dashboard.",
                )
            if row["state"] != "queued":
                raise HTTPException(409, "That post has already started sending, so it can no longer be cancelled.")
            from server import woopsocial_service as service

            try:
                service.make_client(data_dir).delete_post(row["request_id"])
            except PublishError as e:
                raise HTTPException(400, e.message) from e
            group = d.conn.execute(
                "SELECT clip_id, platform FROM clip_publishes WHERE request_id = ?", (row["request_id"],)
            ).fetchall()
            for g in group:
                d.record_clip_publish(g["clip_id"], g["platform"], {
                    "state": "skipped", "error": "Cancelled before it went out.", "scheduled_for": "",
                })
            return {"cancelled": len(group)}
        finally:
            d.close()

    @app.post("/publishing/schedule/clear")
    def clear(body: ClearIn):
        """Take finished rows off the schedule list. Keeps every record."""
        d = db()
        try:
            if body.items is None:
                pairs = [
                    (r["clip_id"], r["platform"])
                    for r in d.conn.execute(
                        "SELECT clip_id, platform FROM clip_publishes WHERE scheduled_for != '' "
                        "AND state IN ('published','failed','skipped')"
                    ).fetchall()
                ]
            else:
                pairs = [(i.clip_id, i.platform) for i in body.items]
            cleared, refused = 0, []
            for clip_id, platform in pairs:
                row = d.conn.execute(
                    "SELECT state FROM clip_publishes WHERE clip_id = ? AND platform = ?", (clip_id, platform)
                ).fetchone()
                if row is None:
                    continue
                if row["state"] in WAITING:
                    refused.append({"clip_id": clip_id, "platform": platform,
                                    "reason": "Still waiting to go out: cancel it instead."})
                    continue
                d.conn.execute(
                    "UPDATE clip_publishes SET scheduled_for = '' WHERE clip_id = ? AND platform = ?",
                    (clip_id, platform),
                )
                cleared += 1
            d.conn.commit()
            return {"cleared": cleared, "refused": refused}
        finally:
            d.close()
