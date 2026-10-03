"""Watched channels and playlists: a creator posts, Video Factory clips it,
adds it to a compilation, or both, and nobody pastes a link.

This is orchestration only. Detection is sources/channel_feed.py, processing is
the ordinary queue and worker, and publishing is woopsocial_service. What lives
here is the part that joins them without a person in between: noticing a new
video once, waiting until it can be downloaded, queueing it once, and deciding
what happens to its clips.

Built the way server/integrations.py is. Only the detection facts and the
publish decision are stored; once a video has a job, the job row and
clip_publishes are the truth about it, read live, so there is no second copy of
state to drift.

Off unless switched on. Nothing in this app starts processing on its own
(core/queue.is_paused), so turning automation on, and each watch, is the
standing go-ahead for exactly those videos and no others.

Supported API, documented in docs/API.md.
"""

import json
import threading
import time
import traceback
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from core import queue
from core.state import StateDB

ENABLED_KEY = "automation_enabled"
DELETE_SOURCES_KEY = "automation_delete_sources"

PLATFORM_NAMES = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}

WATCH_TICK_SECONDS = 30
DEFAULT_INTERVAL_MINUTES = 15
# A look every few minutes is plenty for videos that take an hour to process,
# and each look is a request to someone else's platform.
MIN_INTERVAL_MINUTES = 5
# How soon to look again at a video that is live, premiering or processing.
RECHECK_SECONDS = 15 * 60
# A premiere can be scheduled days ahead; past a week it is not coming.
GIVE_UP_SECONDS = 7 * 24 * 60 * 60
# The "last 24 hours" catch-up policy.
DAY_SECONDS = 24 * 60 * 60

BACKLOG = ("all", "newest", "day", "none")

# How many recent steps the page's live panel can show.
ACTIVITY_KEEP = 30

# A hands-off channel (publishing set to Automatic) runs on a PC nobody is
# watching, so a failure that might pass is tried again instead of waiting for
# a person. Each list is the wait before each successive try; when it runs out,
# the video stays as it is with the reason showing.
#   - A download or processing run that failed: a dropped connection, a 403
#     from YouTube. Twice, half an hour and then three hours later.
PROCESS_RETRY_DELAYS = (30 * 60, 3 * 60 * 60)
#   - A publish that could not start because WoopSocial was unreachable, busy
#     or rate limiting. Every 15 minutes, six times, then ask.
PUBLISH_RETRY_SECONDS = 15 * 60
PUBLISH_START_ATTEMPTS = 6
#   - Posts the platform rejected, typically a daily cap that other posts
#     already used. Only the rejected ones are sent again, 6 and 24 hours on.
DELIVERY_RETRY_DELAYS = (6 * 60 * 60, 24 * 60 * 60)

_MISSED = "Posted while Video Factory wasn't watching."
_BASELINE = "Posted before you started watching this channel."

_JOB_STATES = {
    "queued": "queued",
    "running": "processing",
    "done": "complete",
    "failed": "failed",
    "cancelled": "cancelled",
}


# ---- request bodies ---------------------------------------------------------


class PublishSettings(BaseModel):
    """What to do with a watched video's clips. Configured once per channel."""

    mode: Literal["off", "ask", "auto"] = "ask"
    platforms: list[str] = []
    # How many of each video's clips to post, best first. 0 posts every clip
    # the run makes; the rest stay in the library either way.
    max_posts: int = Field(default=0, ge=0, le=100)
    # False: post each clip the moment it is made, as the Publish dialog does
    # with "Space them out" unticked. True spreads them on the budget below.
    spread: bool = True
    # A daily budget, not a flat gap: WoopSocial allows about five YouTube posts
    # a day, and a burst past that is rejected (see woopsocial_service).
    per_day: int = Field(default=5, ge=1, le=50)
    gap_hours: float = Field(default=1, ge=0.25, le=24)
    # Local "HH:MM" for each day's first post, or "" to start as soon as the
    # scheduler allows.
    day_start: str = Field(default="", pattern=r"^$|^([01]\d|2[0-3]):[0-5]\d$")
    # The creator's own, set once ("#creatorname #twitch"). They lead every
    # caption so nothing trims them off.
    hashtags: list[str] = []
    # False: only the hashtags above, none of the ones the model chose.
    ai_hashtags: bool = True
    footer: str = Field(default="", max_length=1000)
    # Per platform, the fields WoopSocial's build_post reads: youtube.privacy,
    # tiktok.privacyLevel / allowComment / allowDuet / allowStitch /
    # isYourBrand / isBrandedContent, instagram|facebook.postType,
    # pinterest.pinterestBoardId.
    overrides: dict = {}


class CompilationTarget(BaseModel):
    """Where a watch's videos go when it adds them to a compilation."""

    compilation_id: int | None = None
    # Instead of an id: make a compilation with this title when the watch is
    # saved. Never stored; the id it gets is.
    new_title: str = Field(default="", max_length=200)
    # "whole": each video, start to end. "clips": its best clips, which needs
    # the watch to make clips too.
    what: Literal["whole", "clips"] = "whole"
    # How many of each video's best clips; 0 is all of them.
    max_clips: int = Field(default=3, ge=0, le=20)


class WatchActions(BaseModel):
    """What a watch does with each new video. Both off: it only lands in the
    Library, to be picked up from there."""

    clips: bool = True
    compile: bool = False
    compilation: CompilationTarget = CompilationTarget()


class BackfillSettings(BaseModel):
    """Catching up on what a channel or playlist already has, when it is first
    watched. Nothing set: earlier videos are listed, not taken, as always."""

    # The latest this many. With `since`, the most to look through.
    count: int = Field(default=0, ge=0, le=1000)
    # "YYYY-MM-DD": only videos posted on or after this day.
    since: str = Field(default="", pattern=r"^$|^\d{4}-\d{2}-\d{2}$")
    # A playlist's listing has no dates, so which end is newest is asked:
    # YouTube adds to the bottom unless the owner changed it.
    newest_at: Literal["bottom", "top"] = "bottom"

    def wanted(self) -> bool:
        return bool(self.count or self.since)

    def cutoff(self) -> float:
        """The since day as unix seconds (local midnight), or 0."""
        if not self.since:
            return 0.0
        return datetime.strptime(self.since, "%Y-%m-%d").timestamp()


# The most a "back to a date" catch-up looks through when no count is given.
BACKFILL_SINCE_CAP = 200


class AutomationPatch(BaseModel):
    enabled: bool | None = None
    # Delete a watched video's download once its clips are published, so an
    # always-on PC does not fill its disk with multi-gigabyte sources.
    delete_sources: bool | None = None


class WatchIn(BaseModel):
    platform: Literal["youtube", "twitch", "kick"]
    channel: str = Field(min_length=1, max_length=300)
    # What happens to its clips, chosen when the channel is added, so a
    # hands-off channel is one step to set up rather than two.
    publish: PublishSettings | None = None
    # How its clips are made, as the Generate bar sends them (captions,
    # podcast, caption style...). Without these a watched video got the app's
    # defaults, captions and all, whatever the person had chosen.
    options: dict | None = None
    preset: str | None = None
    actions: WatchActions | None = None
    backfill: BackfillSettings | None = None


class WatchPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=100)
    preset: str | None = None
    options: dict | None = None
    publish: PublishSettings | None = None
    actions: WatchActions | None = None
    backlog: Literal["all", "newest", "day", "none"] | None = None
    min_minutes: float | None = Field(default=None, ge=0, le=600)


# ---- reading state ----------------------------------------------------------


def is_enabled(d: StateDB) -> bool:
    return d.get_flag(ENABLED_KEY, "0") == "1"


def publish_settings(watch) -> PublishSettings:
    try:
        return PublishSettings(**json.loads(watch["publish"] or "{}"))
    except Exception:
        return PublishSettings()


def watch_backfill(watch) -> BackfillSettings:
    try:
        return BackfillSettings(**json.loads(watch["backfill"] or "{}"))
    except Exception:
        return BackfillSettings()


def watch_actions(watch) -> WatchActions:
    try:
        return WatchActions(**json.loads(watch["actions"] or "{}"))
    except Exception:
        return WatchActions()


def clip_only(watch) -> bool:
    """Only making clips: then a Short, or anything under the minimum length,
    has nothing worth taking. A watch that feeds a compilation or the Library
    takes them, since a short video is often exactly what a compilation wants."""
    actions = watch_actions(watch)
    return actions.clips and not actions.compile


def watch_options(watch) -> dict:
    try:
        options = json.loads(watch["options"] or "{}")
    except ValueError:
        options = {}
    return options if isinstance(options, dict) else {}


def job_payload(watch, url: str) -> dict:
    """What the queue is given for one of this watch's videos: the preset, then
    the watch's own options on top, exactly as a pasted link would carry them."""
    from server.integrations import PRESETS

    options = watch_options(watch)
    preset = PRESETS.get(options.pop("preset", "standard")) or PRESETS["standard"]
    payload = {"url": url, **preset["options"], **options}
    # The watcher adds the video to the compilation itself, once, so the
    # worker's own "add when done" must not do it a second time.
    payload.pop("add_to_compilation", None)
    payload.pop("import_only", None)
    if not watch_actions(watch).clips:
        payload["import_only"] = True  # into the Library, no clips
    return payload


def render_footer(template: str, item, watch) -> str:
    """Fill the source placeholders. Plain replacement, not str.format, so any
    other braces a person typed stay exactly as they typed them."""
    values = {
        "{source_url}": item["url"] or "",
        "{source_title}": item["title"] or "",
        "{source_channel}": watch["name"] or watch["channel_key"],
        "{source_platform}": PLATFORM_NAMES.get(watch["platform"], watch["platform"]),
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template.strip()


def _job_status(d: StateDB, item) -> str | None:
    """The item's job status, or None when it has no job. A job row cleared
    from the queue's history still counts as done if its video finished."""
    if not item["job_id"]:
        # Already in the Library when the watch found it: nothing to run.
        return "done" if item["state"] == "library" else None
    job = d.get_job(item["job_id"])
    if job is not None:
        return job["status"]
    # 'imported': a watch set to bring videos into the Library, not clip them.
    return "done" if d.video_status(item["video_id"]) in ("done", "imported") else "cancelled"


def _deliveries(d: StateDB, video_id: str) -> list[dict]:
    rows = d.conn.execute(
        "SELECT p.clip_id, p.platform, p.state, p.post_url, p.scheduled_for, p.error "
        "FROM clip_publishes p WHERE p.video_id = ? ORDER BY p.clip_id, p.platform",
        (video_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def view_item(d: StateDB, item, worker) -> dict:
    out = {
        key: item[key]
        for key in (
            "id", "watch_id", "platform", "video_id", "url", "title", "published_at",
            "detected_at", "state", "reason", "job_id", "publish_state", "publish_error",
            "retries", "retry_at", "publish_attempts", "publish_retry_at", "delivery_retries",
            "source_freed", "compiled", "compile_note",
        )
    }
    status = {
        "baseline": "earlier",
        "new": "waiting_for_video",
        "not_ready": "waiting_for_video",
        "waiting": "waiting_for_queue",
        "skipped": "skipped",
        "error": "error",
        "library": "complete",
    }.get(item["state"], "queued")
    job_status = _job_status(d, item) if item["state"] == "queued" else None
    if job_status is not None:
        status = _JOB_STATES.get(job_status, "queued")
        if job_status == "queued":
            out["waiting_behind"] = queue.waiting_ahead(d, item["job_id"])
            out["queue_paused"] = queue.is_paused(d)
        elif job_status == "running":
            out["progress"] = worker.progress_snapshot(item["job_id"])
        elif job_status == "failed":
            job = d.get_job(item["job_id"])
            out["details"] = ((job["error"] if job is not None else "") or "")[:500]
    out["status"] = status
    out["imported"] = item["state"] == "library" or (_imported_only(d, item) if item["job_id"] else False)
    if status == "complete":
        out["clips"] = d.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE video_id = ?", (item["video_id"],)
        ).fetchone()[0]
        out["deliveries"] = _deliveries(d, item["video_id"])
    return out


def _imported_only(d: StateDB, item) -> bool:
    """Whether the item's job brought the video into the Library without
    making clips. Read from the job, not the watch, whose settings may have
    changed since."""
    job = d.get_job(item["job_id"]) if item["job_id"] else None
    if job is not None:
        try:
            return bool(json.loads(job["payload"] or "{}").get("import_only"))
        except ValueError:
            return False
    return d.video_status(item["video_id"]) == "imported"


def view_actions(d: StateDB, watch) -> dict:
    actions = watch_actions(watch).model_dump()
    actions["compilation"].pop("new_title", None)
    comp_id = actions["compilation"]["compilation_id"]
    comp = None
    if comp_id:
        from compilation import store as compilations

        comp = compilations.get(d, int(comp_id))
    # The title, so the card can name it; None when it has been deleted.
    actions["compilation"]["title"] = comp["title"] if comp else None
    return actions


def view_watch(d: StateDB, watch) -> dict:
    counts = {
        r["state"]: r["n"]
        for r in d.conn.execute(
            "SELECT state, COUNT(*) AS n FROM watch_items WHERE watch_id = ? GROUP BY state",
            (watch["id"],),
        )
    }
    options = watch_options(watch)
    return {
        "id": watch["id"],
        "platform": watch["platform"],
        "channel_key": watch["channel_key"],
        "kind": "playlist" if _is_playlist(watch) else "channel",
        "name": watch["name"],
        "enabled": bool(watch["enabled"]),
        "preset": options.pop("preset", "standard"),
        "options": options,
        "publish": publish_settings(watch).model_dump(),
        "actions": view_actions(d, watch),
        "backfill": watch_backfill(watch).model_dump(),
        "backlog": watch["backlog"],
        "min_minutes": watch["min_minutes"],
        "last_ok_poll_at": watch["last_ok_poll_at"],
        "next_poll_at": watch["next_poll_at"],
        "last_error": watch["last_error"],
        "counts": counts,
        "creator": creator_summary(d, watch),
    }


# ---- the creator it learns about --------------------------------------------


def _is_playlist(watch) -> bool:
    from sources.channel_feed import is_playlist

    return is_playlist(watch["platform"], watch["channel_key"])


def creator_name(watch) -> str:
    """The channel name a watch's creator profile goes by, or "" when there
    is no real one. A YouTube channel whose name could not be read is known
    only by its UC id, and a profile called that would help nobody. A
    playlist is named for itself, not a creator, and may hold many, so each
    of its videos is tagged by its own channel as it is processed."""
    if _is_playlist(watch):
        return ""
    name = (watch["name"] or "").strip()
    if watch["platform"] == "youtube" and name == watch["channel_key"]:
        return ""
    return name


def watch_creator(d: StateDB, watch, *, create: bool = False) -> int | None:
    """The creator profile a watch's videos learn into.

    Found the way the pipeline finds one, by platform and channel name, so a
    video of the channel clipped by hand lands on the same profile. Not
    stored on the watch, so merging or splitting profiles in the Creators tab
    is followed. `create` makes it on first sight, which is how a channel is
    in Creators from the moment it is watched."""
    name = creator_name(watch)
    if not name:
        return None
    if create:
        from creator import identity

        return identity.resolve(d, "", name, platform=watch["platform"])
    row = d.conn.execute(
        "SELECT creator_id FROM platform_accounts"
        " WHERE platform = ? AND platform_account_id = ? COLLATE NOCASE"
        " ORDER BY platform_account_id = ? DESC LIMIT 1",
        (watch["platform"], name, name),
    ).fetchone()
    return row["creator_id"] if row else None


def creator_summary(d: StateDB, watch) -> dict | None:
    """What the watch card shows about its creator: who, and how much the
    app has learned about them so far."""
    creator_id = watch_creator(d, watch)
    if creator_id is None:
        return None
    row = d.conn.execute(
        "SELECT display_name, learning_enabled FROM creators WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()
    if row is None:
        return None
    def count(sql: str) -> int:
        return d.conn.execute(sql, (creator_id,)).fetchone()[0]

    return {
        "id": creator_id,
        "name": row["display_name"],
        "learning": bool(row["learning_enabled"]),
        "videos": count("SELECT COUNT(*) FROM videos WHERE creator_id = ? AND status = 'done'"),
        "facts": count("SELECT COUNT(*) FROM creator_knowledge WHERE creator_id = ?")
        + count("SELECT COUNT(*) FROM creator_events WHERE creator_id = ?"),
    }


def learned_from(d: StateDB, video_id: str) -> tuple[str, int] | None:
    """The creator a video was learned into, and how many new facts and
    storyline events it gave: (name, count), or None when it has no creator."""
    row = d.conn.execute(
        "SELECT c.creator_id, c.display_name FROM videos v"
        " JOIN creators c ON c.creator_id = v.creator_id WHERE v.video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    n = sum(
        d.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE creator_id = ? AND source_video = ?",
            (row["creator_id"], video_id),
        ).fetchone()[0]
        for table in ("creator_knowledge", "creator_events")
    )
    return row["display_name"], n


# ---- the watcher ------------------------------------------------------------


class ChannelWatcher(threading.Thread):
    """Looks at each watched channel every few minutes and moves what it finds
    along: detected, ready, queued, published or waiting to be asked.

    Every step reads its state from the database and writes it back before the
    next, so a restart at any point carries on from where it stopped. A video is
    one row keyed by its id, which is what keeps it to one job however many
    times, or by however many watches, it is seen."""

    def __init__(
        self,
        db: Callable[[], StateDB],
        *,
        feed,
        worker,
        broadcaster,
        publisher: Callable[..., dict] | None = None,
        interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
        downloads=None,
        clock=time.time,
    ):
        super().__init__(daemon=True, name="channel-watcher")
        self._downloads = downloads
        self._db = db
        self._feed = feed
        self._worker = worker
        self._broadcaster = broadcaster
        self._publisher = publisher
        self._interval = max(MIN_INTERVAL_MINUTES, float(interval_minutes)) * 60
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        # What it is doing right now, and the last few things it did, for the
        # page's live panel. In memory only: it is a window onto the work, and
        # the work itself is all in the database.
        self._activity: deque[dict] = deque(maxlen=ACTIVITY_KEEP)
        self._activity_lock = threading.Lock()
        self._doing = ""
        self._published_seen: set[tuple[int, str]] | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def activity(self) -> dict:
        with self._activity_lock:
            return {"doing": self._doing, "events": list(self._activity)}

    def _say(self, text: str, kind: str = "info", url: str = "") -> None:
        """One step worth seeing: in the page's live panel, and the log. `url`
        is the video found, or the post that went live, so the panel can link
        straight to it."""
        event = {"at": self._clock(), "text": text, "kind": kind}
        if url:
            event["url"] = url
        with self._activity_lock:
            self._activity.appendleft(event)
        print(f"Watch: {text}")
        self._broadcaster.publish({"type": "automation", "activity": event})

    @contextmanager
    def _busy(self, text: str):
        """What it is doing for as long as it takes, shown live on the page."""
        with self._activity_lock:
            self._doing = text
        self._broadcaster.publish({"type": "automation", "doing": text})
        try:
            yield
        finally:
            with self._activity_lock:
                self._doing = ""
            self._broadcaster.publish({"type": "automation", "doing": ""})

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()  # the watcher must outlive any one failure
            self._wake.wait(timeout=WATCH_TICK_SECONDS)
            self._wake.clear()

    def tick(self) -> None:
        now = self._clock()
        d = self._db()
        try:
            if not is_enabled(d):
                # Watching is off, but a publish someone asked for, or one a
                # restart interrupted, still goes out.
                self._decide_publishing(d, new=False)
                return
            for watch in d.watches_due(now):
                self._poll(d, watch, now)
            self._check_readiness(d, now)
            self._queue_waiting(d)
            self._retry_failed_runs(d, now)
            self._retry_rejected_posts(d, now)
            self._decide_publishing(d)
            self._add_to_compilations(d)
            self._free_disk(d)
            self._notice_posts(d)
        finally:
            d.close()

    # -- 1. look at the channel -----------------------------------------------

    def _poll(self, d: StateDB, watch, now: float) -> None:
        name = watch["name"] or watch["channel_key"]
        try:
            with self._busy(f"Checking {name} for new videos"):
                videos = self._feed.latest(watch["platform"], watch["channel_key"])
        except Exception as e:
            self._say(f"Couldn't check {name}: {_short(e)}", "error")
            d.set_watch(watch["id"], last_error=_short(e), next_poll_at=now + self._interval)
            return
        # A watch that only makes clips passes over Shorts: nothing to clip,
        # so not worth a row. One feeding a compilation keeps them.
        if clip_only(watch):
            videos = [v for v in videos if not getattr(v, "short", False)]
        known = d.watch_item_ids()
        fresh, seen = [], set()
        for video in videos:
            if video.video_id and video.video_id not in known and video.video_id not in seen:
                fresh.append(video)
                seen.add(video.video_id)

        if not watch["last_ok_poll_at"]:
            # The first look records what is already there, so adding a channel
            # never queues its back catalogue unless asked to catch up. Listed,
            # so any of it can still be taken with one click.
            backfill = watch_backfill(watch)
            if backfill.wanted():
                self._catch_up_first_look(d, watch, backfill, fresh, now)
            else:
                for video in fresh:
                    self._insert(d, watch, video, now, "baseline", _BASELINE)
                self._say(f"Now watching {name}. {len(fresh)} earlier videos noted, none taken.",
                          "info")
        else:
            take, hold = fresh, []
            missed = now - watch["last_ok_poll_at"] > 2 * self._interval
            if missed and len(fresh) > 1:
                take, hold = self._catch_up(watch["backlog"], fresh, now)
            for video in take:
                self._insert(d, watch, video, now, "new", "")
                self._say(f"New video on {name}: {_quoted(video.title)}", "found", video.url)
            for video in hold:
                self._insert(d, watch, video, now, "skipped", _MISSED)
            if hold:
                self._say(f"{len(hold)} older video(s) on {name} were posted while Video "
                          "Factory wasn't watching. Set aside, one click to clip.", "info")
            if not fresh:
                # Worth a line too: a panel that only speaks when something is
                # found looks, for hours at a time, exactly like one that died.
                self._say(f"Checked {name}: nothing new", "info")
        d.set_watch(watch["id"], last_ok_poll_at=now, next_poll_at=now + self._interval,
                    last_error="")
        if fresh:
            self._broadcaster.publish({"type": "automation"})

    def _insert(self, d: StateDB, watch, video, now: float, state: str, reason: str,
                not_before: float = 0.0) -> bool:
        return d.insert_watch_item(
            video.video_id, watch_id=watch["id"], platform=watch["platform"], url=video.url,
            title=video.title, published_at=video.published_at, detected_at=now, state=state,
            reason=reason, next_check_at=now, not_before=not_before,
        )

    def _catch_up_first_look(self, d: StateDB, watch, backfill: BackfillSettings,
                             fresh: list, now: float) -> None:
        """Take some of what is already there, as the watch was asked to.

        Looks deeper than the newest fifteen, puts the list newest first, and
        takes the latest `count`, or everything back to `since` (looking
        through at most `count`, or BACKFILL_SINCE_CAP). What is taken goes
        through the same steps a new video does, so each is checked against
        the Library and the queue before anything downloads. The rest is
        listed as earlier, as without a catch-up."""
        name = watch["name"] or watch["channel_key"]
        limit = backfill.count or BACKFILL_SINCE_CAP
        try:
            with self._busy(f"Looking through what {name} already has"):
                history = self._feed.history(watch["platform"], watch["channel_key"], limit)
        except Exception as e:
            # The newest fifteen are already in hand: catch up on those.
            self._say(f"Couldn't read further back on {name} ({_short(e)}); "
                      "catching up on the newest only.", "error")
            history = []
        from sources.channel_feed import is_playlist

        if is_playlist(watch["platform"], watch["channel_key"]):
            dated = history and all(v.published_at for v in history)
            if not dated and backfill.newest_at == "bottom":
                history = list(reversed(history))
        if clip_only(watch):
            history = [v for v in history if not getattr(v, "short", False)]
        # A channel's deep listing is undated, but its feed dates the newest
        # fifteen: carry those across so they are cut off by date right away.
        dates = {v.video_id: v.published_at for v in fresh if v.published_at}
        for video in history:
            if not video.published_at and video.video_id in dates:
                video.published_at = dates[video.video_id]
        known = d.watch_item_ids()
        ordered, seen = [], set()
        for video in [*history, *fresh]:
            vid = video.video_id
            if vid and vid not in seen:
                seen.add(vid)
                ordered.append(video)
        # Dated listings are put newest first by date; undated keep their order.
        if ordered and all(v.published_at for v in ordered):
            ordered.sort(key=lambda v: v.published_at, reverse=True)

        cutoff = backfill.cutoff()
        take, rest = [], []
        for video in ordered:
            if len(take) >= limit:
                rest.append(video)
            elif cutoff and video.published_at and video.published_at < cutoff:
                rest.append(video)
            else:
                take.append(video)
        already = 0
        for video in take:
            if video.video_id in known:
                already += 1  # another watch has it: one row, one job
                continue
            # Undated with a cutoff: its date is read with the readiness check.
            not_before = cutoff if cutoff and not video.published_at else 0.0
            self._insert(d, watch, video, now, "new", "Catching up on an earlier video.", not_before)
        for video in rest:
            self._insert(d, watch, video, now, "baseline", _BASELINE)
        took = len(take) - already
        extra = f", {already} already watched elsewhere" if already else ""
        self._say(f"Now watching {name}. Catching up on {took} earlier video"
                  f"{'' if took == 1 else 's'}{extra}; {len(rest)} older noted, not taken.", "found")

    def _catch_up(self, policy: str, fresh: list, now: float) -> tuple[list, list]:
        """Several videos appeared while nobody was watching. Which to clip.

        Newest first throughout, because every listing is. Nothing is dropped:
        what is not taken is recorded as skipped, visible, one click from being
        clipped after all."""
        if policy == "all":
            return fresh, []
        if policy == "none":
            return [], fresh
        if policy == "day":
            take = []
            for index, video in enumerate(fresh):
                if not video.published_at:
                    # Twitch listings carry no dates; the video's own page does.
                    video.published_at = self._feed.readiness(video.url, 0).published_at
                if video.published_at and now - video.published_at > DAY_SECONDS:
                    return take, fresh[index:]
                take.append(video)
            return take, []
        return fresh[:1], fresh[1:]  # "newest", the default

    # -- 2. wait until it can be downloaded -----------------------------------

    def _check_readiness(self, d: StateDB, now: float) -> None:
        for item in reversed(d.watch_items(states=("new", "not_ready"))):
            if item["next_check_at"] > now:
                continue
            watch = d.get_watch(item["watch_id"])
            if watch is None or not watch["enabled"]:
                continue
            if item["state"] == "not_ready" and now - item["detected_at"] > GIVE_UP_SECONDS:
                d.set_watch_item(item["id"], state="skipped",
                                 reason="It never became available to download.")
                continue
            with self._busy(f"Checking whether {_quoted(item['title'])} is ready"):
                shortest = float(watch["min_minutes"] or 0) * 60 if clip_only(watch) else 0
                found = self._feed.readiness(item["url"], shortest)
            facts = {}
            if found.title and not item["title"]:
                facts["title"] = found.title
            if found.published_at and not item["published_at"]:
                facts["published_at"] = found.published_at
            posted = found.published_at or item["published_at"]
            if item["not_before"] and posted and posted < item["not_before"] and found.state != "skip":
                # Caught up only back to a date, and this one is from before it.
                d.set_watch_item(item["id"], state="baseline", reason=_BASELINE,
                                 not_before=0, **facts)
                continue
            if found.state == "ready":
                d.set_watch_item(item["id"], state="waiting", reason="", **facts)
            elif found.state == "not_yet":
                if item["state"] == "new":
                    self._say(f"{_quoted(item['title'] or found.title)} isn't ready yet. "
                              "Looking again in 15 minutes.", "waiting")
                d.set_watch_item(item["id"], state="not_ready", reason=found.reason,
                                 next_check_at=now + RECHECK_SECONDS, **facts)
            else:
                self._say(f"Skipped {_quoted(item['title'] or found.title)}: {found.reason}",
                          "info")
                d.set_watch_item(item["id"], state="skipped", reason=found.reason, **facts)

    # -- 3. queue it, once ------------------------------------------------------

    def _queue_waiting(self, d: StateDB) -> None:
        queued_any = False
        for item in reversed(d.watch_items(states=("waiting",))):
            watch = d.get_watch(item["watch_id"])
            if watch is None or not watch["enabled"]:
                continue
            title = item["title"] or f"{watch['name'] or watch['channel_key']} video"
            payload = job_payload(watch, item["url"])
            if self._in_library(d, item["video_id"], payload):
                # Nothing to download or make. Taken from the Library as it is,
                # so a watch feeding a compilation still adds it.
                d.set_watch_item(item["id"], state="library", publish_state="off",
                                 reason="Already in your Library.")
                self._say(f"{_quoted(title)} is already in your Library", "info")
                continue
            outcome, job_id = queue.enqueue_once(d, item["video_id"], payload, title=title)
            if outcome == "done":
                if watch_actions(watch).compile:
                    # Clipped before: the clips are there for the compilation.
                    d.set_watch_item(item["id"], state="library", publish_state="off",
                                     reason="Already clipped in Video Factory.")
                else:
                    d.set_watch_item(item["id"], state="skipped",
                                     reason="Already clipped in Video Factory.")
                continue
            if outcome == "full":
                full = f"Waiting for room in the queue ({queue.MAX_ACTIVE} videos at most)."
                if item["reason"] != full:
                    self._say(f"{_quoted(title)} is waiting for room in the queue.", "waiting")
                d.set_watch_item(item["id"], reason=full)
                break  # nothing else fits either
            self._tag_creator(d, watch, item["video_id"], title)
            # The watch is the user's go-ahead for its own videos, not for
            # anything else they staged and have not started.
            queue.start_if_alone(d, job_id)
            d.set_watch_item(item["id"], state="queued", job_id=job_id, reason="")
            into = "your Library" if job_payload(watch, item["url"]).get("import_only") else "clipping"
            self._say(f"Queued {_quoted(title)} for {into}", "queued", item["url"])
            queued_any = True
        if queued_any:
            self._worker.notify()
            self._broadcaster.publish({"type": "queue"})
            self._broadcaster.publish({"type": "automation"})

    def _in_library(self, d: StateDB, video_id: str, payload: dict) -> bool:
        """For a watch that imports rather than clips: the video is in the
        Library already, with its file still on disk. A clip watch is not
        stopped by an import: the video has not been clipped yet."""
        if not payload.get("import_only"):
            return False
        if d.video_status(video_id) not in ("done", "imported"):
            return False
        if self._downloads is None:
            return True
        from core.paths import cached_source

        return cached_source(self._downloads, video_id) is not None

    def _tag_creator(self, d: StateDB, watch, video_id: str, title: str) -> None:
        """Put the video on the watch's creator before it runs, the way the
        upload form does. The pipeline keeps a creator already set, so what it
        learns lands on this profile whatever the download calls the channel.
        Never in the way of queueing: without it the pipeline tags as before."""
        name = creator_name(watch)
        if not name:
            return
        try:
            from creator import identity

            d.upsert_video(video_id, title=title, channel_name=name)
            identity.tag_video(d, video_id, name, platform=watch["platform"])
        except Exception as e:
            print(f"Watch: couldn't link {video_id} to its creator: {e}")

    # -- 4. decide what happens to the clips ----------------------------------

    def _decide_publishing(self, d: StateDB, *, new: bool = True) -> None:
        """Once a video's clips exist: publish, ask, or leave them be.

        "publishing" is also what an approval sets and what a restart finds
        half done, and both simply run again: publish_clips(once=True) skips
        every clip already sent or on its way, so a repeat sends only what is
        missing or failed."""
        now = self._clock()
        for item in d.watch_items(states=("queued",)):
            if item["publish_state"] not in ("", "publishing"):
                continue
            if item["publish_state"] == "" and not new:
                continue
            if item["publish_state"] == "publishing" and item["publish_retry_at"] > now:
                continue  # waiting out a failed start before trying again
            if _job_status(d, item) != "done":
                continue
            watch = d.get_watch(item["watch_id"])
            if watch is None:
                continue
            if item["publish_state"] == "" and _imported_only(d, item):
                # Brought into the Library, no clips: nothing to publish.
                d.set_watch_item(item["id"], publish_state="off")
                self._say(f"{_quoted(item['title'])} is in your Library", "done")
                self._broadcaster.publish({"type": "automation"})
                continue
            if item["publish_state"] == "":
                learned = learned_from(d, item["video_id"])
                if learned and learned[1]:
                    name, n = learned
                    self._say(f"Learned {n} new thing{'' if n == 1 else 's'} about {name} "
                              f"from {_quoted(item['title'])}", "learned")
            mode = publish_settings(watch).mode
            if item["publish_state"] == "publishing" or mode == "auto":
                self.publish(d, item, watch)
            else:
                d.set_watch_item(item["id"], publish_state="off" if mode == "off" else "ask")
                self._say(
                    f"Clips of {_quoted(item['title'])} are ready"
                    + (". Waiting for you to publish them." if mode == "ask" else "."),
                    "done",
                )
            self._broadcaster.publish({"type": "automation"})

    def publish(self, d: StateDB, item, watch) -> dict:
        """Send a finished video's clips out with its watch's settings."""
        settings = publish_settings(watch)
        # clips_for_video is ordered by score, so "the best N" is the first N.
        clip_ids = [int(c["id"]) for c in d.clips_for_video(item["video_id"])]
        if settings.max_posts:
            clip_ids = clip_ids[: settings.max_posts]
        if not clip_ids:
            d.set_watch_item(item["id"], publish_state="done",
                             publish_error="The run made no clips to publish.")
            return {}
        if not settings.platforms:
            d.set_watch_item(item["id"], publish_state="ask",
                             publish_error="Choose where this channel's clips go.")
            return {}
        if self._publisher is None:
            d.set_watch_item(item["id"], publish_state="ask",
                             publish_error="Publishing isn't available.")
            return {}
        d.set_watch_item(item["id"], publish_state="publishing", publish_error="")
        where = ", ".join(_label(p) for p in settings.platforms)
        try:
            with self._busy(f"Sending clips of {_quoted(item['title'])} to {where}"):
                out = self._publish_now(d, item, watch, settings, clip_ids)
        except Exception as e:
            return self._publish_failed(d, item, settings, e)
        problems = sorted({
            s.get("reason", "") for s in out.get("skipped", [])
            if s.get("reason") and s.get("reason") != "already sent"
        })
        d.set_watch_item(item["id"], publish_state="done", publish_attempts=0,
                         publish_retry_at=0, publish_error="; ".join(problems)[:500])
        started = out.get("started", [])
        if started:
            first = _when(
                next((s.get("scheduled_for") for s in started if s.get("scheduled_for")), "")
            )
            if first:
                self._say(f"Scheduled {len(started)} clip(s) of {_quoted(item['title'])} "
                          f"for {where}, the first at {first}.", "posted")
            else:
                self._say(f"Sent {len(started)} clip(s) of {_quoted(item['title'])} "
                          f"to {where} now.", "posted")
        elif problems:
            self._say(f"Couldn't send the clips of {_quoted(item['title'])}: {problems[0]}",
                      "error")
        return out

    def _publish_now(self, d: StateDB, item, watch, settings, clip_ids) -> dict:
        return self._publisher(
            d,
            clip_ids=clip_ids,  # best first, so the daily budget goes to them
            platforms=list(settings.platforms),
            lead_hashtags=list(settings.hashtags),
            ai_hashtags=settings.ai_hashtags,
            per_day=settings.per_day if settings.spread else 0,
            gap_hours=settings.gap_hours,
            day_start=settings.day_start if settings.spread else "",
            overrides=dict(settings.overrides),
            footer=render_footer(settings.footer, item, watch),
        )

    def _publish_failed(self, d: StateDB, item, settings, e: Exception) -> dict:
        """A publish that did not start: try again later, or ask a person."""
        attempts = int(item["publish_attempts"] or 0)
        if (
            settings.mode == "auto"
            and getattr(e, "retryable", False)
            and attempts < PUBLISH_START_ATTEMPTS
        ):
            # Unreachable, busy or rate limiting: likely to pass, and a
            # hands-off channel has nobody to press Publish later.
            d.set_watch_item(
                item["id"], publish_state="publishing", publish_attempts=attempts + 1,
                publish_retry_at=self._clock() + PUBLISH_RETRY_SECONDS,
                publish_error=_short(e),
            )
            self._say(f"WoopSocial couldn't take the clips of {_quoted(item['title'])} "
                      "just now. Trying again in 15 minutes.", "retry")
            return {}
        # Not set up, no account, a key refused, or out of tries: only a
        # person can fix these, so it waits in front of one.
        d.set_watch_item(item["id"], publish_state="ask", publish_error=_short(e),
                         publish_retry_at=0)
        self._say(f"Couldn't publish the clips of {_quoted(item['title'])}: {_short(e)}", "error")
        return {}

    # -- 5. add it to the compilation --------------------------------------------

    def _add_to_compilations(self, d: StateDB) -> None:
        """Once a video is done, put it in its watch's compilation: whole, or
        its best clips in the order they happen. Once per video, marked on the
        item, so a restart or a second look never adds it twice. A video that
        finished while the watch had no compilation is marked as not asked, so
        switching one on later only takes what comes after."""
        from compilation import store as compilations

        changed = False
        for item in d.watch_items(states=("queued", "library"), limit=500):
            if item["compiled"] or _job_status(d, item) != "done":
                continue
            watch = d.get_watch(item["watch_id"])
            actions = watch_actions(watch) if watch is not None else None
            if actions is None or not actions.compile or not actions.compilation.compilation_id:
                d.set_watch_item(item["id"], compiled=3)
                continue
            target = actions.compilation
            comp = compilations.get(d, int(target.compilation_id))
            if comp is None:
                d.set_watch_item(item["id"], compiled=2,
                                 compile_note="Its compilation has been deleted.")
                changed = True
                continue
            if comp["status"] in ("queued", "rendering"):
                continue  # frozen while it renders; next tick
            segments = self._segments_for(d, item, target)
            if not segments:
                note = ("The run made no clips to add." if target.what == "clips"
                        else "Its length isn't known yet, so it couldn't be added.")
                d.set_watch_item(item["id"], compiled=2, compile_note=note)
                changed = True
                continue
            # Footage already in the compilation (added by hand, by another
            # watch, or an earlier find of the same video) is not added again.
            have = list((comp["recipe"] or {}).get("segments") or [])
            fresh = []
            for seg in segments:
                if compilations.duplicate_of(have, seg) is None:
                    fresh.append(seg)
                    have.append(seg)
            if not fresh:
                d.set_watch_item(item["id"], compiled=1,
                                 compile_note="Already in the compilation, so not added again.")
                changed = True
                continue
            added = sum(1 for seg in fresh if compilations.append_segment(d, comp["id"], seg))
            d.set_watch_item(item["id"], compiled=1 if added else 2,
                             compile_note="" if added else "The compilation couldn't take it.")
            if added:
                what = "" if target.what == "whole" else f"{added} clip{'s' if added != 1 else ''} of "
                self._say(f"Added {what}{_quoted(item['title'])} to {_quoted(comp['title'])}",
                          "done")
                self._broadcaster.publish({"type": "compilation", "compilation_id": comp["id"]})
            changed = True
        if changed:
            self._broadcaster.publish({"type": "automation"})

    @staticmethod
    def _segments_for(d: StateDB, item, target: CompilationTarget) -> list[dict]:
        from compilation import store as compilations

        vid = item["video_id"]
        if target.what == "clips":
            best = list(d.clips_for_video(vid))  # best first
            if target.max_clips:
                best = best[: target.max_clips]
            best.sort(key=lambda c: float(c["start_s"]))
            return [{"video_id": vid, "start": round(float(c["start_s"]), 2),
                     "end": round(float(c["end_s"]), 2)} for c in best]
        duration = compilations.library_durations(d, [vid]).get(vid, 0.0)
        return [{"video_id": vid, "start": 0.0, "end": round(duration, 2)}] if duration > 0 else []

    # -- 6. keep a hands-off channel going -------------------------------------

    def _hands_off(self, d: StateDB, item):
        """The item's watch if it is enabled and publishes automatically, else
        None. Only those retry: anywhere else a person is in the loop."""
        watch = d.get_watch(item["watch_id"])
        if watch is None or not watch["enabled"] or publish_settings(watch).mode != "auto":
            return None
        return watch

    def _retry_failed_runs(self, d: StateDB, now: float) -> None:
        """A download or processing run that failed is queued again later."""
        started = False
        for item in d.watch_items(states=("queued",)):
            tries = int(item["retries"] or 0)
            if item["publish_state"] != "" or tries >= len(PROCESS_RETRY_DELAYS):
                continue
            if _job_status(d, item) != "failed" or self._hands_off(d, item) is None:
                continue
            if not item["retry_at"]:
                d.set_watch_item(item["id"], retry_at=now + PROCESS_RETRY_DELAYS[tries])
                continue
            if item["retry_at"] > now:
                continue
            if queue.retry(d, item["job_id"]) is None:
                continue
            queue.start_if_alone(d, item["job_id"])
            d.set_watch_item(item["id"], retries=tries + 1, retry_at=0)
            self._say(f"Trying {_quoted(item['title'])} again (retry {tries + 1} of "
                      f"{len(PROCESS_RETRY_DELAYS)})", "retry")
            started = True
        if started:
            self._worker.notify()
            self._broadcaster.publish({"type": "queue"})
            self._broadcaster.publish({"type": "automation"})

    def _retry_rejected_posts(self, d: StateDB, now: float) -> None:
        """Posts a platform rejected are sent again later, and only those.

        Rejections arrive after the publish, as the status refresh learns them,
        so this looks at finished items rather than at the publish itself. The
        re-send goes through publish(), where publish_clips(once=True) passes
        over every clip already published or on its way."""
        for item in d.watch_items(states=("queued",)):
            tries = int(item["delivery_retries"] or 0)
            if item["publish_state"] != "done" or tries >= len(DELIVERY_RETRY_DELAYS):
                continue
            if self._hands_off(d, item) is None:
                continue
            rejected = d.conn.execute(
                "SELECT COUNT(*) FROM clip_publishes WHERE video_id = ? AND state = 'failed'",
                (item["video_id"],),
            ).fetchone()[0]
            if not rejected:
                continue
            if not item["publish_retry_at"]:
                d.set_watch_item(item["id"],
                                 publish_retry_at=now + DELIVERY_RETRY_DELAYS[tries])
                continue
            if item["publish_retry_at"] > now:
                continue
            d.set_watch_item(item["id"], publish_state="publishing", publish_retry_at=0,
                             publish_attempts=0, delivery_retries=tries + 1)
            self._say(f"Sending {rejected} rejected post(s) of {_quoted(item['title'])} again",
                      "retry")

    def _notice_posts(self, d: StateDB) -> None:
        """Say so when one of a watched video's posts goes live.

        The publish worker is what learns it, from WoopSocial. This only
        compares what it saw last time, so the panel can show each post
        landing. The first look just remembers what was already there."""
        rows = d.conn.execute(
            "SELECT p.clip_id, p.platform, p.post_url, c.title, c.hook FROM clip_publishes p "
            "JOIN watch_items w ON w.video_id = p.video_id "
            "LEFT JOIN clips c ON c.id = p.clip_id WHERE p.state = 'published'"
        ).fetchall()
        seen = {(int(r["clip_id"]), r["platform"]) for r in rows}
        if self._published_seen is not None:
            for r in rows:
                if (int(r["clip_id"]), r["platform"]) not in self._published_seen:
                    self._say(f"Posted {_quoted(r['title'] or r['hook'] or '')} "
                              f"to {_label(r['platform'])}", "posted", r["post_url"] or "")
        self._published_seen = seen

    def _free_disk(self, d: StateDB) -> None:
        """Delete a watched video's download once its clips are published, when
        asked to. Clips, transcripts and the library entry stay; only the
        several-gigabyte source goes, which an always-on PC would otherwise
        pile up until the disk is full."""
        if self._downloads is None or d.get_flag(DELETE_SOURCES_KEY, "0") != "1":
            return
        from core.paths import cached_source, discard

        for item in d.watch_items(states=("queued",)):
            if item["source_freed"] or item["publish_state"] not in ("done", "off"):
                continue
            if _job_status(d, item) != "done":
                continue
            source = cached_source(self._downloads, item["video_id"])
            if source is None:
                # Nothing on disk to free. Marked so the folder is not scanned
                # for it again, and told apart so nobody is told it was deleted.
                d.set_watch_item(item["id"], source_freed=2)
            elif discard(source):
                d.set_watch_item(item["id"], source_freed=1)
                self._say(f"Deleted the download of {_quoted(item['title'])} to save space",
                          "info")


def woopsocial_publisher(data_dir) -> Callable[..., dict]:
    """Publishing for watched channels: WoopSocial, with the duplicate guard on
    and the publish dialog's remembered platforms left alone."""

    def publish(d: StateDB, **kwargs) -> dict:
        from server import woopsocial_service as woop

        if not woop.is_enabled(d) or not woop.has_key(data_dir):
            raise RuntimeError("WoopSocial isn't set up. Add your key in Settings to publish.")
        return woop.publish_clips(d, data_dir, once=True, remember=False, **kwargs)

    return publish


_OPEN, _CLOSE, _MORE = "\u201c", "\u201d", "\u2026"

PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "linkedin": "LinkedIn"}


def _label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform.title())


def _quoted(title: str) -> str:
    """A video or clip title in curly quotes, trimmed for a one-line panel."""
    title = (title or "").strip() or "a new video"
    if len(title) > 70:
        title = title[:70] + _MORE
    return _OPEN + title + _CLOSE


def _when(iso: str) -> str:
    """'Thu 18:00' in this PC's time, for saying when a post goes out."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %H:%M")
    except (ValueError, AttributeError):
        return ""


def _short(error) -> str:
    text = str(error).strip()
    return (text.splitlines()[0] if text else type(error).__name__).replace("ERROR: ", "")[:300]


# ---- routes -----------------------------------------------------------------


def install(
    app,
    *,
    db: Callable[[], StateDB],
    worker,
    broadcaster,
    options_from: Callable[[dict], dict],
    data_dir=None,
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
    feed=None,
    publisher=None,
    clock=time.time,
) -> ChannelWatcher:
    """Add the automation routes to `app`. Returns the watcher for the caller
    to start and stop with the rest of the server.

    `options_from` turns a watch's processing options into the job payload the
    worker reads, through the same validation a queued job's options go
    through, so there is one set of rules for both."""
    if feed is None:
        from sources import channel_feed as feed

    from server.integrations import PRESETS

    if publisher is None and data_dir is not None:
        publisher = woopsocial_publisher(data_dir)
    from pathlib import Path

    watcher = ChannelWatcher(
        db, feed=feed, worker=worker, broadcaster=broadcaster, publisher=publisher,
        interval_minutes=interval_minutes, clock=clock,
        downloads=Path(data_dir) / "downloads" if data_dir is not None else None,
    )

    def settle_actions(d: StateDB, actions: WatchActions, name: str) -> WatchActions:
        """Check a watch's actions before they are stored, and make the
        compilation it asked for. Raises 400 with a reason worth showing."""
        target = actions.compilation
        if actions.compile:
            from compilation import store as compilations

            if target.what == "clips" and not actions.clips:
                raise HTTPException(400, "Adding the best clips needs Generate clips on too.")
            if target.new_title.strip():
                target.compilation_id = compilations.create(d, target.new_title.strip())
                broadcaster.publish({"type": "compilation",
                                     "compilation_id": target.compilation_id})
            elif not target.compilation_id:
                target.compilation_id = compilations.create(d, f"{name} compilation")
            elif compilations.get(d, int(target.compilation_id)) is None:
                raise HTTPException(400, "That compilation no longer exists. Choose another.")
        target.new_title = ""
        return actions

    def load_watch(d: StateDB, watch_id: int):
        row = d.get_watch(watch_id)
        if row is None:
            raise HTTPException(404, "no such watch")
        return row

    def load_item(d: StateDB, item_id: int):
        row = d.get_watch_item(item_id)
        if row is None:
            raise HTTPException(404, "no such video")
        return row

    def status(d: StateDB) -> dict:
        watches = d.list_watches()
        return {
            "enabled": is_enabled(d),
            "delete_sources": d.get_flag(DELETE_SOURCES_KEY, "0") == "1",
            "interval_minutes": watcher.interval_seconds / 60,
            "watches": len(watches),
            "watching": sum(1 for w in watches if w["enabled"]),
            "presets": [{"id": key, **value} for key, value in PRESETS.items()],
        }

    @app.get("/automation")
    def get_automation():
        d = db()
        try:
            return status(d)
        finally:
            d.close()

    @app.patch("/automation")
    def patch_automation(body: AutomationPatch):
        d = db()
        try:
            if body.enabled is not None:
                d.set_flag(ENABLED_KEY, "1" if body.enabled else "0")
            if body.delete_sources is not None:
                d.set_flag(DELETE_SOURCES_KEY, "1" if body.delete_sources else "0")
            result = status(d)
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.get("/automation/activity")
    def activity():
        """What the watcher is doing right now, and what it did last.

        For a live panel: "now" is one line to show big, "events" the recent
        steps, newest first. Processing is the worker's, so it is read from
        the job the way the history rows read it."""
        d = db()
        try:
            live = watcher.activity()
            watches = [w for w in d.list_watches() if w["enabled"]]
            now: dict
            if not is_enabled(d):
                now = {"state": "off", "text": "Not watching. Switch it on to start."}
            elif live["doing"]:
                now = {"state": "busy", "text": live["doing"]}
            else:
                now = {}
                for item in d.watch_items(states=("queued",), limit=50):
                    status = _job_status(d, item)
                    if status == "running":
                        now = {"state": "busy",
                               "text": f"Making clips of {_quoted(item['title'])}",
                               "progress": worker.progress_snapshot(item["job_id"])}
                        break
                    if status == "queued" and not now:
                        now = {"state": "waiting",
                               "text": f"{_quoted(item['title'])} is waiting in the queue"}
                if not now:
                    count = len(watches)
                    now = {"state": "watching",
                           "text": f"Watching {count} channel{'s' if count != 1 else ''}"
                           if count else "No channels to watch yet"}
            next_checks = [w["next_poll_at"] for w in watches if w["next_poll_at"]]
            return {
                "now": now,
                "watching": len(watches) if is_enabled(d) else 0,
                "next_check_at": min(next_checks) if next_checks else 0,
                "events": live["events"],
            }
        finally:
            d.close()

    @app.get("/automation/slots")
    def slots(per_day: int = 5, gap_hours: float = 1, day_start: str = "", count: int = 5):
        """When the next posts would go out with these settings, after
        everything already scheduled. For the preview beside the controls;
        it reserves nothing."""
        from publish.errors import PublishError
        from server import woopsocial_service as woop

        try:
            PublishSettings(per_day=per_day, gap_hours=gap_hours, day_start=day_start)
        except Exception as e:
            raise HTTPException(400, "Those schedule settings are not valid.") from e
        d = db()
        try:
            times = woop.schedule_times(
                d, max(1, min(20, count)), per_day=per_day, gap_hours=gap_hours,
                day_start=day_start,
            )
            return {"times": times, "already_scheduled": len(woop.committed_times(d))}
        except PublishError as e:
            raise HTTPException(400, e.message) from e
        finally:
            d.close()

    @app.get("/automation/watches")
    def list_watches():
        d = db()
        try:
            return [view_watch(d, w) for w in d.list_watches()]
        finally:
            d.close()

    @app.post("/automation/watches")
    def add_watch(body: WatchIn):
        try:
            channel = feed.resolve(body.platform, body.channel)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        d = db()
        try:
            existing = d.find_watch(channel.platform, channel.channel_key)
            if existing is not None:
                return {"created": False, **view_watch(d, existing)}
            options = options_from(body.options) if body.options else {}
            preset = body.preset or "standard"
            if preset not in PRESETS:
                raise HTTPException(400, f"unknown preset '{preset}'")
            options["preset"] = preset
            actions = settle_actions(d, body.actions or WatchActions(), channel.name)
            watch_id = d.insert_watch(
                channel.platform, channel.channel_key, name=channel.name,
                publish=(body.publish or PublishSettings()).model_dump_json(),
                options=json.dumps(options), actions=actions.model_dump_json(),
                backfill=(body.backfill or BackfillSettings()).model_dump_json(),
            )
            watch = load_watch(d, watch_id)
            # In Creators straight away, before the first video, so what it
            # learns has somewhere visible to go.
            try:
                watch_creator(d, watch, create=True)
            except Exception as e:
                print(f"Watch: couldn't make a creator profile for {channel.name}: {e}")
            result = {"created": True, **view_watch(d, watch)}
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.patch("/automation/watches/{watch_id}")
    def patch_watch(watch_id: int, body: WatchPatch):
        d = db()
        try:
            watch = load_watch(d, watch_id)
            fields: dict = {}
            if body.enabled is not None:
                fields["enabled"] = 1 if body.enabled else 0
            if body.name is not None and body.name.strip():
                fields["name"] = body.name.strip()
            if body.options is not None or body.preset is not None:
                options = watch_options(watch)
                preset = body.preset if body.preset is not None else options.get("preset", "standard")
                if preset not in PRESETS:
                    raise HTTPException(400, f"unknown preset '{preset}'")
                if body.options is not None:
                    options = options_from(body.options)
                options["preset"] = preset
                fields["options"] = json.dumps(options)
            if body.publish is not None:
                fields["publish"] = body.publish.model_dump_json()
            if body.actions is not None:
                fields["actions"] = settle_actions(
                    d, body.actions, watch["name"] or watch["channel_key"]
                ).model_dump_json()
            if body.backlog is not None:
                fields["backlog"] = body.backlog
            if body.min_minutes is not None:
                fields["min_minutes"] = body.min_minutes
            d.set_watch(watch_id, **fields)
            result = view_watch(d, load_watch(d, watch_id))
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.delete("/automation/watches/{watch_id}")
    def delete_watch(watch_id: int):
        d = db()
        try:
            load_watch(d, watch_id)
            d.delete_watch(watch_id)
        finally:
            d.close()
        broadcaster.publish({"type": "automation"})
        return {"deleted": True}

    @app.post("/automation/watches/{watch_id}/check")
    def check_now(watch_id: int):
        d = db()
        try:
            load_watch(d, watch_id)
            d.set_watch(watch_id, next_poll_at=0)
        finally:
            d.close()
        watcher.wake()
        return {"checking": True}

    @app.get("/automation/items")
    def list_items(watch_id: int | None = None, limit: int = 100):
        d = db()
        try:
            rows = d.watch_items(watch_id=watch_id, limit=max(1, min(500, limit)))
            return [view_item(d, r, worker) for r in rows]
        finally:
            d.close()

    @app.post("/automation/items/{item_id}/queue")
    def clip_this(item_id: int):
        """Clip a video the watch set aside: back catalogue, missed while
        closed, too short, or anything else skipped. The person asking is the
        go-ahead, so the minimum length does not apply."""
        d = db()
        try:
            item = load_item(d, item_id)
            if item["state"] not in ("baseline", "skipped", "error"):
                return view_item(d, item, worker)
            found = feed.readiness(item["url"], 0)
            if found.state == "ready":
                d.set_watch_item(item_id, state="waiting", reason="", not_before=0)
            elif found.state == "not_yet":
                d.set_watch_item(item_id, state="not_ready", reason=found.reason,
                                 detected_at=clock(), next_check_at=clock() + RECHECK_SECONDS)
            else:
                raise HTTPException(409, found.reason or "That video can't be clipped.")
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        watcher.wake()
        return result

    @app.post("/automation/items/{item_id}/publish")
    def publish_item(item_id: int):
        """Publish a finished video's clips with its watch's settings: the
        answer to "ask first", and the retry for any that failed. Only what is
        not already sent or on its way goes out. Runs on the watcher's thread,
        so the request returns at once and uploads never hold it open."""
        d = db()
        try:
            item = load_item(d, item_id)
            if item["state"] != "queued" or _job_status(d, item) != "done":
                raise HTTPException(409, "This video's clips aren't ready yet.")
            if item["publish_state"] != "publishing" or item["publish_retry_at"]:
                # A person asking is the go-ahead now, not after a wait.
                d.set_watch_item(item_id, publish_state="publishing", publish_error="",
                                 publish_retry_at=0, publish_attempts=0)
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        watcher.wake()
        broadcaster.publish({"type": "automation"})
        return result

    @app.post("/automation/items/{item_id}/skip")
    def skip_item(item_id: int):
        d = db()
        try:
            item = load_item(d, item_id)
            if item["state"] in ("new", "not_ready", "waiting"):
                d.set_watch_item(item_id, state="skipped", reason="Skipped by you.")
            elif item["publish_state"] == "ask":
                d.set_watch_item(item_id, publish_state="off")
            result = view_item(d, load_item(d, item_id), worker)
        finally:
            d.close()
        broadcaster.publish({"type": "automation"})
        return result

    return watcher
