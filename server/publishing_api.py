"""Reach and search across every publisher: SEO defaults, per-platform
captions, the pre-publish check, best times to post, the performance stats
those times learn from, and compilation publish metadata.

The three publishers (youtube_api, uploadpost_api, woopsocial_api) own the
sending. This module owns what gets sent: they call the helpers below so a
clip's keywords, captions, first comment and playlist are decided in one place
whichever provider carries them.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

from publish import seo, timing

SETTINGS_KEY = "publishing_seo"

DEFAULTS = {
    # Search phrases added to every YouTube upload's tags, after the clip's own.
    "channel_keywords": [],
    # Hashtags every post carries, ahead of the generated ones.
    "hashtags": [],
    # Posted under every video unless that video has its own (which replaces
    # it, rather than stacking on top).
    "first_comment": "",
    # Fit each caption-only platform's text to that platform (see publish/seo.py).
    "platform_captions": True,
    # "creator:<id>" -> YouTube playlist id. "__compilations__" holds the
    # playlist compilations go to.
    "playlist_rules": {},
    # Make a playlist for a creator the first time one of their clips goes out.
    "auto_playlists": False,
    # Credit the source in every description: "Source: <channel> - <link>",
    # at the moment the clip starts. And, for a YouTube upload that went into a
    # playlist, a link to that playlist.
    "link_source": True,
    "link_playlist": True,
    # Every title ends "| <source channel> #Tag" (publish/metadata.decorate_title).
    # The tag is `title_hashtag`, or the first always-on hashtag when that is empty.
    "title_channel": True,
    "title_hashtag_on": True,
    "title_hashtag": "",
    # How many posts a day "best times" plans for, and the gap between them.
    "per_day": 3,
    "min_gap_hours": 2,
    # The posting schedule (publish/slots.py): "best" hours of the day, or the
    # clock times below, on the days ticked (Monday = 0).
    "slot_mode": "best",
    "fixed_times": ["09:00", "13:00", "18:00"],
    "slot_days": [0, 1, 2, 3, 4, 5, 6],
}

COMPILATIONS_KEY = "__compilations__"
STATS_EVERY_SECONDS = 6 * 3600


def load_settings(db) -> dict:
    try:
        stored = json.loads(db.get_flag(SETTINGS_KEY, "") or "{}")
    except (TypeError, ValueError):
        stored = {}
    return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}


def save_settings(db, patch: dict) -> dict:
    merged = {**load_settings(db), **{k: v for k, v in patch.items() if k in DEFAULTS}}
    db.set_flag(SETTINGS_KEY, json.dumps(merged))
    return merged


# ---- helpers the publishers call ----------------------------------------------


def _json_list(row, key: str) -> list[str]:
    try:
        raw = row[key] if key in row.keys() else ""
    except (IndexError, KeyError):
        raw = ""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


def creator_of(db, clip) -> str:
    """The channel a clip came from, '' for a compilation or when unknown."""
    video_id = clip["video_id"] or ""
    if not video_id or video_id.startswith("compilation:"):
        return ""
    row = db.conn.execute("SELECT channel_name FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    return (row["channel_name"] or "") if row else ""


def youtube_tags(db, clip, given: list[str] | None = None) -> list[str]:
    """YouTube's hidden tags: what was typed if anything was, else the clip's
    search keywords, its creator, the channel's standing keywords, its hashtags."""
    if given:
        return seo.keywords_for(keywords=given)
    s = load_settings(db)
    return seo.keywords_for(
        keywords=_json_list(clip, "keywords"),
        hashtags=_json_list(clip, "hashtags"),
        creator=creator_of(db, clip),
        channel_keywords=s.get("channel_keywords") or [],
    )


def hashtags_for(db, clip, video: list[str] | None = None) -> list[str]:
    """The always-on hashtags, then this video's own, duplicates folded.

    Always-on first, because platforms cap the count and those must survive.
    `video` replaces the clip's stored hashtags when given: it is what the
    person has in the publish box right now, and a tag they deleted there
    must not come back from the stored list.
    """
    s = load_settings(db)
    own = _json_list(clip, "hashtags") if video is None else list(video)
    return seo.unique_hashtags(s.get("hashtags") or [], own)


def title_for(db, clip, title: str) -> str:
    """The title as it goes out: the source channel and one always-on hashtag
    added to it. Applied where the title is sent, not where it is written, so
    clips that already exist get it too and an edit in the box is respected.

    A compilation has no single source, so it gets the hashtag only. Whatever
    goes wrong reading the settings, the title goes out as it was given."""
    from publish.metadata import clamp_title, decorate_title

    try:
        s = load_settings(db)
        channel = creator_of(db, clip) if s.get("title_channel", True) else ""
        tag = ""
        if s.get("title_hashtag_on", True):
            own = str(s.get("title_hashtag") or "").strip()
            always = [h for h in (s.get("hashtags") or []) if str(h).strip()]
            tag = own or (str(always[0]) if always else "")
        return decorate_title(title, channel, tag)
    except Exception:
        return clamp_title(title)


def enrich_clip_metadata(
    db, video_id: str, start: float, *, title: str, description: str,
    hashtags: list[str], keywords: list[str],
) -> dict:
    """The clip's stored title, description, hashtags and keywords with the
    source channel in them, so the editor shows what will be published.

    Only adds, and never adds twice: the same clip can pass through here on
    every re-render, and a person's edits to any field are kept as written.
    Nothing here may fail a render, so a problem returns the input unchanged."""
    from publish.metadata import creator_tag, with_links

    given = {"title": title, "description": description,
             "hashtags": list(hashtags), "keywords": list(keywords)}
    try:
        clip = {"id": 1, "video_id": video_id, "start_s": start}
        channel = creator_of(db, clip)
        out = dict(given)
        out["title"] = title_for(db, clip, title) if title else title
        out["description"] = with_links(description, [source_line(db, clip)]) if description else description
        if channel:
            tag = creator_tag(channel)
            if tag and tag.lower() not in {h.lower() for h in hashtags}:
                out["hashtags"] = [tag, *hashtags]
            if channel.lower() not in {k.lower() for k in keywords}:
                out["keywords"] = [channel, *keywords]
        return out
    except Exception:
        return given


def standing_comment(db, provider_standing: str = "") -> str:
    """The comment every video gets unless it has its own: the Publish page's,
    else the one saved in the provider's own settings from before it existed."""
    return (load_settings(db).get("first_comment") or provider_standing or "").strip()


def first_comment_for(db, clip, provider_standing: str = "") -> str:
    """This video's own comment if it has one, else the standing comment.

    The video's own REPLACES the standing one: a comment is one message, and
    two stacked read as a bot."""
    own = ""
    try:
        own = (clip["first_comment"] or "") if "first_comment" in clip.keys() else ""
    except (IndexError, KeyError):
        own = ""
    return (own.strip() or standing_comment(db, provider_standing))[:1000]


# Platforms where the caption is the whole post: Upload-Post sends them the
# generic title unless told otherwise, which left TikTok with a bare title.
CAPTION_PLATFORMS = ("tiktok", "instagram", "x", "threads", "bluesky")


def caption_overrides(
    db, *, platforms: list[str], title: str, description: str, hashtags: list[str],
    footer: str = "", given: dict | None = None,
) -> dict[str, dict]:
    """Per-platform caption overrides for Upload-Post, keyed like its
    `overrides` body. A platform the caller already set a title for keeps it."""
    out = {k: dict(v) for k, v in (given or {}).items() if isinstance(v, dict)}
    if not load_settings(db).get("platform_captions", True):
        return out
    for platform in platforms:
        if platform not in CAPTION_PLATFORMS:
            continue
        mine = out.setdefault(platform, {})
        if not mine.get("title"):
            mine["title"] = seo.caption_for(
                platform, title=title, description=description, hashtags=hashtags, footer=footer
            )
    return out


def source_line(db, clip) -> str:
    """The credit line for a clip's description: where the footage came from,
    linked to the moment it starts. Empty for a compilation (its description
    already credits every part), for a source with no web link or channel,
    and when the setting is off."""
    from publish.metadata import source_credit_line

    # A credit is a nicety: whatever goes wrong reading it, the post goes out.
    try:
        if not load_settings(db).get("link_source", True) or int(clip["id"]) < 0:
            return ""
        row = db.conn.execute(
            "SELECT source_url, channel_name FROM videos WHERE video_id = ?", (clip["video_id"] or "",)
        ).fetchone()
        if row is None:
            return ""
        return source_credit_line(row["channel_name"] or "", row["source_url"] or "", clip["start_s"] or 0)
    except Exception:
        return ""


def playlist_line(db, playlist_id: str) -> str:
    from publish.metadata import playlist_url

    if not playlist_id or not load_settings(db).get("link_playlist", True):
        return ""
    return f"Playlist: {playlist_url(playlist_id)}"


def with_source(db, clip, text: str) -> str:
    """`text` with the clip's source credit under it (providers that have no
    playlist to link)."""
    from publish.metadata import with_links

    return with_links(text, [source_line(db, clip)])


def playlist_key(db, clip) -> str:
    """Which playlist rule applies: the creator's, keyed by creator id (a
    display name can be renamed; the id cannot), or the compilations one."""
    if int(clip["id"]) < 0:
        return COMPILATIONS_KEY
    row = db.conn.execute(
        "SELECT creator_id FROM videos WHERE video_id = ?", (clip["video_id"] or "",)
    ).fetchone()
    return f"creator:{row['creator_id']}" if row and row["creator_id"] else ""


def _playlist_title(db, key: str) -> str:
    if key == COMPILATIONS_KEY:
        return "Compilations"
    name = ""
    if key.startswith("creator:"):
        try:
            row = db.conn.execute(
                "SELECT display_name FROM creators WHERE creator_id = ?", (int(key.split(":", 1)[1]),)
            ).fetchone()
            name = row["display_name"] if row else ""
        except (ValueError, TypeError):
            name = ""
    return f"{name or 'Creator'} clips"


def can_use_playlists(publisher) -> bool:
    """Whether this channel's sign-in includes the playlist permission."""
    try:
        from publish.youtube_shorts import PLAYLIST_SCOPES

        publisher.credentials(PLAYLIST_SCOPES)
        return True
    except Exception:
        return False


def resolve_playlist(db, publisher, key: str) -> str | None:
    """The playlist a rule sends this to, making one if asked to and allowed.

    Called by the publish worker, which holds the channel's publisher. Never
    raises: a playlist is a nice-to-have and must not cost the upload.
    """
    if not key:
        return None
    s = load_settings(db)
    rules = dict(s.get("playlist_rules") or {})
    if rules.get(key):
        return rules[key]
    if not s.get("auto_playlists") or not can_use_playlists(publisher):
        return None
    title = _playlist_title(db, key)
    try:
        created = publisher.create_playlist(
            title, description=f"Every {title.lower()} from this channel, newest first."
        )
    except Exception as e:
        print(f"  Could not create the playlist {title!r}: {e}")
        return None
    if created:
        from server import youtube_service as _yt

        _yt.spend(db, "playlists.insert")
        rules[key] = created
        save_settings(db, {"playlist_rules": rules})
    return created or None


# ---- performance stats -------------------------------------------------------


def record_stats(db, videos: list[dict]) -> int:
    """Store the numbers of live videos (publish/youtube_status.summarize
    shapes) for best times to learn from. Returns how many were stored."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stored = 0
    for v in videos:
        # Only what an audience could see: a private or scheduled video has
        # no views to learn from yet.
        if v.get("state") not in ("live", "unlisted") or not v.get("published_at"):
            continue
        db.conn.execute(
            "INSERT INTO publish_stats (platform, post_id, published_at, views, likes, "
            "comments, checked_at) VALUES ('youtube', ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(platform, post_id) DO UPDATE SET views = excluded.views, "
            "likes = excluded.likes, comments = excluded.comments, "
            "checked_at = excluded.checked_at, published_at = excluded.published_at",
            (v["video_id"], v["published_at"], v.get("views") or 0, v.get("likes") or 0,
             v.get("comments") or 0, now),
        )
        stored += 1
    db.conn.commit()
    return stored


def refresh_youtube_stats(db, config: dict, data_dir: Path) -> int:
    """Read every connected channel's recent videos into publish_stats.

    The channel's own uploads list, not just what this app uploaded direct:
    posts that went out through WoopSocial or Upload-Post land on the same
    channel, and their audience is the same audience. About 2 quota units per
    50 videos per channel.
    """
    from publish.youtube_status import summarize
    from server import youtube_service as yt

    if not yt.load_settings(db).get("enabled"):
        return 0
    channels = [a.get("id") for a in yt.load_accounts(db)] or [None]
    stored = 0
    for channel in channels:
        try:
            publisher = yt.make_publisher(config, data_dir, channel_id=channel)
            items = publisher.channel_videos(100)
        except Exception as e:
            print(f"  Could not read YouTube stats: {e}")
            continue
        yt.spend_read(db, len(items))
        stored += record_stats(db, [summarize(i) for i in items])
    return stored


def taken_slots(db, offset_minutes: int, *, now: datetime | None = None) -> set[tuple[int, int]]:
    """Posts already scheduled, on any provider, as (days from today, local
    hour), so best times plans around them."""
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(timezone(timedelta(minutes=offset_minutes)))
    out: set[tuple[int, int]] = set()
    rows = db.conn.execute(
        "SELECT DISTINCT clip_id, scheduled_for FROM clip_publishes WHERE scheduled_for != '' "
        "AND state IN ('queued', 'processing', 'sending')"
    ).fetchall()
    stamps = [r["scheduled_for"] for r in rows]
    try:
        # Direct-YouTube uploads waiting to go live, and uploads queued here.
        from server import schedule_api

        stamps += [i["scheduled_for"] for i in schedule_api._uploads(db) + schedule_api._jobs(db) if i["scheduled_for"]]
    except Exception:
        pass
    for stamp in stamps:
        try:
            when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        local = when.astimezone(local_now.tzinfo)
        days = (local.date() - local_now.date()).days
        if days >= 0:
            out.add((days, local.hour))
    return out


def stats_rows(db, platform: str) -> list[dict]:
    """Every platform's learned posts count toward a platform's times, the
    platform's own twice: an audience's waking hours are mostly the same
    wherever they watch, and only YouTube reports numbers today."""
    rows = [dict(r) for r in db.conn.execute(
        "SELECT platform, published_at, views FROM publish_stats"
    ).fetchall()]
    return [r for r in rows for _ in range(2 if r["platform"] == platform else 1)]


def _source_channel(db, item) -> str:
    """Whose video a clip came from (a compilation: the channels it draws on),
    so copy written for it is about them. "" when unknown."""
    try:
        if int(item["id"]) < 0:
            from compilation import metadata as comp_meta
            from compilation import store

            f = comp_meta.facts(db, store.get(db, -int(item["id"])))
            return ", ".join(dict.fromkeys(f["channels"][:3]))
        row = db.conn.execute(
            "SELECT channel_name FROM videos WHERE video_id = ?", (item["video_id"],)
        ).fetchone()
        return (row["channel_name"] or "") if row else ""
    except Exception:
        return ""


def content_of(db, item, data_dir: Path) -> str:
    """What happens in a clip or compilation, as text for the model: the
    clip's stretch of transcript, or a compilation's part names."""
    if int(item["id"]) < 0:
        from compilation import metadata as comp_meta
        from compilation import store

        f = comp_meta.facts(db, store.get(db, -int(item["id"])))
        return "Parts:\n" + "\n".join(f"- {label}" for label in f["labels"])
    path = Path(data_dir) / "transcripts" / f"{item['video_id']}.json"
    try:
        segments = json.loads(path.read_text(encoding="utf-8")).get("segments") or []
    except (OSError, ValueError):
        return item["description"] or ""
    start, end = float(item["start_s"]), float(item["end_s"])
    return " ".join(
        str(s.get("text", "")).strip() for s in segments
        if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end
    )


# ---- routes ------------------------------------------------------------------


class SettingsPatch(BaseModel):
    channel_keywords: list[str] | None = None
    hashtags: list[str] | None = None
    first_comment: str | None = Field(default=None, max_length=1000)
    platform_captions: bool | None = None
    playlist_rules: dict[str, str] | None = None
    auto_playlists: bool | None = None
    link_source: bool | None = None
    link_playlist: bool | None = None
    title_channel: bool | None = None
    title_hashtag_on: bool | None = None
    title_hashtag: str | None = Field(default=None, max_length=40)
    slot_mode: str | None = Field(default=None, pattern="^(best|fixed)$")
    fixed_times: list[str] | None = None
    slot_days: list[int] | None = None
    per_day: int | None = Field(default=None, ge=1, le=24)
    min_gap_hours: int | None = Field(default=None, ge=1, le=12)


class CheckIn(BaseModel):
    title: str = ""
    description: str = ""
    keywords: list[str] = []
    hashtags: list[str] = []
    long_form: bool = False
    has_thumbnail: bool = False
    has_playlist: bool = False


class CaptionsIn(BaseModel):
    title: str = ""
    description: str = ""
    hashtags: list[str] = []
    platforms: list[str] = []
    footer: str = ""


class PublishMetaIn(BaseModel):
    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    hashtags: list[str] = []
    keywords: list[str] = []
    first_comment: str = Field(default="", max_length=1000)
    suggested_comment: str = Field(default="", max_length=1000)
    alt_titles: list[str] = []
    canvas: str = ""


def install(app, *, config, db, data_dir: Path) -> StatsPoller:
    data_path = Path(data_dir)

    @app.get("/publish/states")
    def publish_states(ids: str = ""):
        """Posting state for clips (positive ids) and compilations (negative),
        one summary each: see publish/rollup.py."""
        from publish.rollup import item_states

        try:
            wanted = [int(x) for x in ids.split(",") if x.strip()][:2000]
        except ValueError:
            raise HTTPException(400, "ids must be a comma-separated list of integers") from None
        d = db()
        try:
            return {str(k): v for k, v in item_states(d, wanted).items()}
        finally:
            d.close()

    @app.get("/publish/video-states")
    def publish_video_states():
        """How far each whole video has got: its clips gathered into one stage."""
        from publish.rollup import video_rollups

        d = db()
        try:
            return video_rollups(d)
        finally:
            d.close()

    @app.get("/publishing/settings")
    def get_settings():
        d = db()
        try:
            return load_settings(d)
        finally:
            d.close()

    @app.patch("/publishing/settings")
    def patch_settings(body: SettingsPatch):
        from analysis.metadata import clean_keywords

        patch = body.model_dump(exclude_none=True)
        if "channel_keywords" in patch:
            patch["channel_keywords"] = clean_keywords(patch["channel_keywords"])
        if "hashtags" in patch:
            patch["hashtags"] = seo.unique_hashtags(patch["hashtags"])[:10]
        if "fixed_times" in patch:
            from publish.slots import _clock

            clocks = filter(None, map(_clock, patch["fixed_times"]))
            patch["fixed_times"] = sorted({f"{h:02d}:{m:02d}" for h, m in clocks})[:12]
        if "slot_days" in patch:
            patch["slot_days"] = sorted({int(x) for x in patch["slot_days"] if 0 <= int(x) <= 6})
        if "playlist_rules" in patch:
            patch["playlist_rules"] = {
                str(k)[:200]: str(v)[:64] for k, v in patch["playlist_rules"].items() if v
            }
        d = db()
        try:
            return save_settings(d, patch)
        finally:
            d.close()

    @app.post("/publishing/check")
    def check(body: CheckIn):
        return seo.check(**body.model_dump()).as_dict()

    @app.post("/publishing/captions")
    def captions(body: CaptionsIn):
        return {
            "captions": {
                p: seo.caption_for(p, title=body.title, description=body.description,
                                   hashtags=body.hashtags, footer=body.footer)
                for p in body.platforms
            },
            "limits": {p: seo.rules(p).__dict__ for p in body.platforms},
        }

    @app.get("/publishing/best-times")
    def best_times(
        platform: str = "youtube",
        offset: int = 0,          # the browser's minutes EAST of UTC
        weekday: int = 0,         # local weekday now, Monday = 0
        hour: int = 0,            # local hour now
        count: int = 10,
        per_day: int | None = None,
        gap: int | None = None,
    ):
        if not (0 <= weekday <= 6 and 0 <= hour <= 23 and 1 <= count <= 500):
            raise HTTPException(400, "weekday, hour or count is out of range")
        d = db()
        try:
            s = load_settings(d)
            posts = stats_rows(d, platform)
            taken = taken_slots(d, offset)
        finally:
            d.close()
        grid, confidence = timing.blended_grid(platform, posts, offset)
        slots = timing.best_slots(
            grid, now_weekday=weekday, now_hour=hour, count=count,
            per_day=per_day or int(s.get("per_day") or 3),
            min_gap_hours=gap or int(s.get("min_gap_hours") or 2),
            taken=taken,
        )
        return {
            "slots": [{"day_offset": day, "hour": h} for day, h in slots],
            "top": [{"weekday": w, "hour": h, "weight": round(v, 3)} for w, h, v in timing.top_hours(grid, 6)],
            "confidence": round(confidence, 2),
            "learned_from": len({p["published_at"] for p in posts}),
        }

    @app.post("/publishing/apply-source-metadata")
    def apply_source_metadata():
        """Put the source channel into the stored title, description, hashtags
        and keywords of every clip that lacks it. Additive and repeatable."""
        d = db()
        try:
            rows = d.conn.execute(
                "SELECT id, video_id, start_s, title, description, hashtags, keywords FROM clips"
            ).fetchall()
            changed = 0
            for row in rows:
                tags, words = _json_list(row, "hashtags"), _json_list(row, "keywords")
                got = enrich_clip_metadata(
                    d, row["video_id"] or "", row["start_s"] or 0,
                    title=row["title"] or "", description=row["description"] or "",
                    hashtags=tags, keywords=words,
                )
                diff = {}
                if got["title"] != (row["title"] or ""):
                    diff["title"] = got["title"]
                if got["description"] != (row["description"] or ""):
                    diff["description"] = got["description"]
                if got["hashtags"] != tags:
                    diff["hashtags"] = json.dumps(got["hashtags"])
                if got["keywords"] != words:
                    diff["keywords"] = json.dumps(got["keywords"])
                if diff:
                    d.set_clip(row["id"], **diff)
                    changed += 1
            return {"updated": changed, "total": len(rows)}
        finally:
            d.close()

    @app.post("/publishing/stats/refresh")
    def refresh_stats():
        from server import youtube_service as yt

        d = db()
        try:
            if not yt.load_settings(d).get("enabled"):
                return {"updated": 0, "reason": "YouTube direct is not connected, and it is the only "
                        "publisher that reports views. Best times use each platform's usual peaks."}
            updated = refresh_youtube_stats(d, config, data_path)
            reason = "" if updated else "No uploads old enough to have views yet."
            return {"updated": updated, "reason": reason}
        finally:
            d.close()

    @app.post("/publishing/suggest-comment/{publish_id}")
    def suggest_comment(publish_id: int):
        """An AI first comment for one clip (id > 0) or compilation (id < 0),
        from what actually happens in it. Stored as the video's suggestion;
        applying it is the caller's choice."""
        from analysis.metadata import suggest_first_comment

        d = db()
        try:
            item = d.get_publishable(publish_id)
            if item is None:
                raise HTTPException(404, "no such clip or compilation")
            content = content_of(d, item, data_path)
            try:
                from analysis import audience
                from llm.stages import metadata_backend

                comment = suggest_first_comment(
                    title=item["title"] or item["hook"] or "", description=item["description"] or "",
                    content=content, llm=metadata_backend(config["llm"]),
                    channel=_source_channel(d, item), audience=audience.for_run(d, config, data_path),
                )
            except Exception as e:
                raise HTTPException(503, f"The AI model could not write a comment: {e}") from e
            if not comment:
                raise HTTPException(503, "The AI model did not come up with a usable comment. Try again.")
            if publish_id >= 0:
                d.set_clip(publish_id, suggested_comment=comment)
            else:
                from compilation import store

                comp = store.get(d, -publish_id)
                store.set_publish_meta(d, -publish_id, {**(comp.get("publish_meta") or {}),
                                                        "suggested_comment": comment})
            return {"suggested_comment": comment}
        finally:
            d.close()

    @app.get("/publishing/standing-comment")
    def get_standing_comment():
        """The comment a video without its own gets, whichever setting holds it."""
        from server import uploadpost_service

        d = db()
        try:
            legacy = uploadpost_service.load_settings(d).get("first_comment") or ""
            return {"first_comment": standing_comment(d, legacy)}
        finally:
            d.close()

    # ---- compilations ----------------------------------------------------

    def _comp(d, comp_id: int) -> dict:
        from compilation import store

        comp = store.get(d, comp_id)
        if comp is None:
            raise HTTPException(404, "no such compilation")
        return comp

    def _standing_footer(d) -> str:
        from server import youtube_service as yt

        return (yt.load_settings(d).get("common_description") or "").strip()

    @app.get("/compilations/{comp_id}/publish-meta")
    def get_publish_meta(comp_id: int):
        d = db()
        try:
            comp = _comp(d, comp_id)
            meta = comp.get("publish_meta") or {}
            return {
                "meta": meta,
                "publish_id": -comp_id,
                "outputs": sorted((comp.get("outputs") or {}).keys()),
                "status": comp.get("status"),
            }
        finally:
            d.close()

    @app.put("/compilations/{comp_id}/publish-meta")
    def put_publish_meta(comp_id: int, body: PublishMetaIn):
        from analysis.metadata import clean_keywords
        from compilation import store

        d = db()
        try:
            comp = _comp(d, comp_id)
            meta = {**(comp.get("publish_meta") or {}), **body.model_dump()}
            meta["keywords"] = clean_keywords(meta["keywords"])
            meta["hashtags"] = seo.unique_hashtags(meta["hashtags"])[:15]
            if meta["canvas"] and meta["canvas"] not in (comp.get("outputs") or {}):
                raise HTTPException(400, "That format has not been rendered for this compilation.")
            store.set_publish_meta(d, comp_id, meta)
            return {"meta": meta}
        finally:
            d.close()

    @app.post("/compilations/{comp_id}/publish-meta/generate")
    def generate_publish_meta(comp_id: int, ai: bool = True, save: bool = True):
        """ai=false returns at once: chapters, credits and plain text from the
        facts, for the dialog to show while the model writes the rest.

        save=false only returns the draft. The dialog uses it so the one
        thing that stores metadata is the person pressing Save: a model call
        that finished after they saved used to overwrite their edits."""
        from compilation import metadata as comp_meta
        from compilation import store

        d = db()
        try:
            comp = _comp(d, comp_id)
            llm = None
            if ai:
                try:
                    from llm.stages import metadata_backend

                    llm = metadata_backend(config["llm"])
                except Exception:
                    llm = None
            from analysis import audience

            meta = comp_meta.generate(
                d, comp, llm, footer=_standing_footer(d),
                audience=audience.for_run(d, config, data_path) if ai else "",
            )
            # Keep choices the person already made that generation knows nothing of.
            old = comp.get("publish_meta") or {}
            meta["canvas"] = old.get("canvas") or (comp.get("recipe") or {}).get("canvas") or ""
            meta["first_comment"] = old.get("first_comment") or ""
            if save:
                store.set_publish_meta(d, comp_id, meta)
            return {"meta": meta}
        finally:
            d.close()

    poller = StatsPoller(config, db, data_path)
    return poller


class StatsPoller(threading.Thread):
    """Reads YouTube view counts every few hours so best times can learn.

    Its own thread rather than the publish worker's loop: a slow stats read
    must never hold up an upload that is waiting to start.
    """

    def __init__(self, config: dict, db, data_dir: Path):
        super().__init__(daemon=True, name="publish-stats")
        self.config, self.db, self.data_dir = config, db, data_dir
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # Let startup finish first; nothing here is urgent.
        if self._stop.wait(120):
            return
        while not self._stop.is_set():
            d = self.db()
            try:
                refresh_youtube_stats(d, self.config, self.data_dir)
            except Exception as e:
                print(f"  Publish stats: {e}")
            finally:
                d.close()
            if self._stop.wait(STATS_EVERY_SECONDS):
                return

