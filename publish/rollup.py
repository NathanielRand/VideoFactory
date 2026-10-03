"""Where each clip, compilation and video stands on the way out.

Posting state is spread over four tables: `uploads` (a direct YouTube upload),
`clip_publishes` (one row per platform from WoopSocial or Upload-Post),
`publish_jobs` (a YouTube upload still waiting its turn) and the clip's own
`exported_at`. Answering "what has gone out?" meant knowing all four, so each
screen answered it a different way, and the Publish page's "ready to post"
list only ever looked at one. This reads all of them into ONE summary per
item, in one place, so every screen can show the same badge.

A compilation is published under a NEGATIVE id (-compilation_id), in the same
tables a clip uses, so `item_states` takes both.
"""

from __future__ import annotations

from datetime import datetime, timezone

# How a provider's own word maps onto ours.
_PUBLISHED = {"published", "live", "unlisted", "uploaded", "private"}
_WAITING = {"queued", "processing", "scheduled"}
_FAILED = {"failed", "rejected", "locked_private", "deleted"}


def _future(iso: str) -> bool:
    """Is this instant still ahead of us. An unreadable or empty one is not."""
    if not iso:
        return False
    try:
        at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at > datetime.now(timezone.utc)


def _blank() -> dict:
    return {
        "state": "ready",
        "published": 0,
        "scheduled": 0,
        "publishing": 0,
        "failed": 0,
        "platforms": [],
        "next_at": "",
        "exported": False,
        "errors": [],
    }


def _headline(s: dict) -> str:
    """One word for the badge. Trouble outranks everything: a failed post the
    user never sees is the one that costs them a day."""
    if s["failed"]:
        return "failed"
    if s["publishing"]:
        return "publishing"
    if s["scheduled"] and s["published"]:
        return "partial"
    if s["scheduled"]:
        return "scheduled"
    if s["published"]:
        return "published"
    return "exported" if s["exported"] else "ready"


def _last_render(db) -> dict[int, str]:
    """When each clip was last successfully re-rendered, by clip id."""
    import json

    out: dict[int, str] = {}
    for r in db.conn.execute(
        "SELECT payload, updated_at FROM jobs WHERE type = 'render' AND status = 'done' "
        "ORDER BY id DESC LIMIT 600"
    ):
        try:
            cid = int(json.loads(r["payload"] or "{}").get("clip_id"))
        except (TypeError, ValueError):
            continue
        out.setdefault(cid, r["updated_at"] or "")
    return out


def item_states(db, ids: list[int]) -> dict[int, dict]:
    """A summary for every id: how many destinations are published, waiting on
    a future time, being sent now, or failed, and the next time one goes out."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    out = {i: _blank() for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))

    def add(i: int, where: str, kind: str, when: str = "", error: str = "") -> None:
        s = out[i]
        s[kind] += 1
        if where and where not in s["platforms"]:
            s["platforms"].append(where)
        if kind == "scheduled" and when and (not s["next_at"] or when < s["next_at"]):
            s["next_at"] = when
        if kind == "failed" and error and len(s["errors"]) < 3:
            s["errors"].append(f"{where}: {error[:160]}" if where else error[:160])

    # Multi-platform posts, one row per platform.
    for r in db.conn.execute(
        f"SELECT clip_id, platform, state, scheduled_for, error FROM clip_publishes "
        f"WHERE clip_id IN ({marks})", ids
    ):
        st = r["state"]
        if st == "published":
            add(r["clip_id"], r["platform"], "published")
        elif st == "failed":
            add(r["clip_id"], r["platform"], "failed", error=r["error"])
        elif st in ("queued", "processing"):
            later = r["scheduled_for"] if _future(r["scheduled_for"]) else ""
            add(r["clip_id"], r["platform"], "scheduled" if later else "publishing", later)
        # 'skipped' is a platform that was ticked but not connected: not a post.

    # Direct YouTube uploads.
    for r in db.conn.execute(
        f"SELECT clip_id, state, publish_at, error FROM uploads WHERE clip_id IN ({marks})", ids
    ):
        st = r["state"] or "uploaded"
        if st == "unscheduled":
            continue          # taken off YouTube's schedule: back in the pool, not posted
        if _future(r["publish_at"]) and st not in _FAILED:
            add(r["clip_id"], "youtube", "scheduled", r["publish_at"])
        elif st in _FAILED:
            add(r["clip_id"], "youtube", "failed", error=r["error"] or st)
        elif st == "processing":
            add(r["clip_id"], "youtube", "publishing")
        elif st in _PUBLISHED or st == "scheduled":
            add(r["clip_id"], "youtube", "published")

    # A YouTube upload still waiting for its turn in the queue.
    for r in db.conn.execute(
        f"SELECT clip_id, status, error FROM publish_jobs WHERE clip_id IN ({marks}) "
        "AND status IN ('queued', 'running')", ids
    ):
        add(r["clip_id"], "youtube", "publishing")

    # A failed upload is only news while it is the last thing that happened to
    # the clip. The job row never goes away, so without this a clip kept its
    # "failed" badge after a retry, after it was posted some other way, and
    # after it was re-rendered (the failed upload was of the old render).
    rerendered = _last_render(db)
    for r in db.conn.execute(
        f"SELECT clip_id, error, updated_at FROM publish_jobs WHERE clip_id IN ({marks}) "
        "AND status = 'failed' AND id = (SELECT MAX(id) FROM publish_jobs j WHERE j.clip_id = publish_jobs.clip_id)",
        ids,
    ):
        s = out[r["clip_id"]]
        if s["published"] or s["scheduled"] or s["publishing"]:
            continue                                   # it went out some other way
        if (rerendered.get(r["clip_id"]) or "") > (r["updated_at"] or ""):
            continue                                   # re-rendered since: a fresh start
        add(r["clip_id"], "youtube", "failed", error=r["error"] or "upload failed")

    real = [i for i in ids if i > 0]
    if real:
        rm = ",".join("?" * len(real))
        for r in db.conn.execute(
            f"SELECT id FROM clips WHERE id IN ({rm}) AND exported_at IS NOT NULL AND exported_at != ''",
            real,
        ):
            out[r["id"]]["exported"] = True

    for s in out.values():
        s["state"] = _headline(s)
    return out


def video_rollups(db) -> dict[str, dict]:
    """Every video's clips gathered into one status: how many are published,
    scheduled, going out, failed or still waiting, and a stage that says how far
    the whole video has got. 'complete' means every clip has been posted."""
    rows = db.conn.execute("SELECT id, video_id FROM clips").fetchall()
    by_video: dict[str, list[int]] = {}
    for r in rows:
        by_video.setdefault(r["video_id"], []).append(r["id"])
    states = item_states(db, [r["id"] for r in rows])

    running = {
        r["video_id"]: r["status"]
        for r in db.conn.execute(
            "SELECT video_id, status FROM jobs WHERE type = 'process' "
            "AND status IN ('queued', 'running') AND video_id != ''"
        )
    }
    out: dict[str, dict] = {}
    for video_id, clip_ids in by_video.items():
        counts = {"published": 0, "scheduled": 0, "publishing": 0, "failed": 0, "ready": 0, "exported": 0}
        next_at = ""
        for cid in clip_ids:
            s = states[cid]
            key = {"partial": "scheduled", "exported": "ready"}.get(s["state"], s["state"])
            counts[key] += 1
            if s["exported"]:
                counts["exported"] += 1
            if s["next_at"] and (not next_at or s["next_at"] < next_at):
                next_at = s["next_at"]
        total = len(clip_ids)
        if video_id in running:
            stage = "processing"
        elif counts["failed"]:
            stage = "attention"
        elif counts["published"] == total:
            stage = "complete"
        elif counts["published"] + counts["scheduled"] + counts["publishing"] == total:
            stage = "queued"
        elif counts["published"] + counts["scheduled"] + counts["publishing"]:
            stage = "partial"
        else:
            stage = "ready"
        out[video_id] = {"stage": stage, "clips": total, "next_at": next_at, **counts}
    # A video being processed with no clips yet.
    for video_id in running:
        out.setdefault(
            video_id,
            {"stage": "processing", "clips": 0, "next_at": "", "published": 0, "scheduled": 0,
             "publishing": 0, "failed": 0, "ready": 0, "exported": 0},
        )
    return out
