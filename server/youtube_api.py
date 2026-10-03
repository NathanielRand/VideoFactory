"""HTTP routes for publishing to YouTube.

Kept out of server/api.py deliberately. That file is 2,300 lines with every
route as a closure in one factory, and this feature is optional, self-contained
and removable — which stays true only if it is not threaded through there. It
is installed with one call from create_app().

Two rules hold everywhere below:

* When the feature is switched off, every route here 404s and /youtube/status
  returns `{"enabled": false}` and nothing else. A disabled feature should look
  absent, not disabled.
* No response, log line or error message may carry a token, a refresh token or
  a client secret. Errors go through server.feedback.redact() on the way out.
"""

import json
import subprocess
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.paths import within
from publish.errors import PublishError
from server import youtube_service as service
from server.feedback import redact

# One consent attempt at a time, held here because it outlives the request
# that started it.
_flow = {"thread": None}


# Room for a few links and a line of thanks, not an essay: the clip's own
# description has to survive YouTube's 5,000-character limit alongside it.
COMMON_DESCRIPTION_MAX = 1500


class PlanIn(BaseModel):
    """Ask for a publishing plan. Nothing is created by asking."""

    clip_ids: list[int] = []
    # Omit both and the plan is "upload these now". Give a start and an
    # interval and YouTube holds each one until its turn.
    start_at: str | None = None          # RFC 3339, WITH an offset
    every_hours: float | None = None
    privacy: str = "public"


class PlanItemIn(BaseModel):
    clip_id: int
    title: str | None = None
    publish_at: str | None = None
    privacy: str | None = None


class PlanExecuteIn(BaseModel):
    """A plan handed back to be carried out, one ordinary publish job per clip."""

    items: list[PlanItemIn] = []


class YouTubeSettingsPatch(BaseModel):
    enabled: bool | None = None
    privacy: str | None = None
    category_id: str | None = None
    made_for_kids: bool | None = None
    playlists_enabled: bool | None = None
    notify_subscribers: bool | None = None
    region: str | None = None
    common_description: str | None = None


class CredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class ConnectIn(BaseModel):
    playlists: bool = False
    # Ask for every permission the app can use (uploads, reading the channel,
    # playlists and managing videos, Analytics): "Update permissions".
    all_permissions: bool = False
    # Connecting normally REPLACES nothing — each consent adds a channel. This
    # exists so the UI can be explicit about which it meant.
    add: bool = False


class DisconnectIn(BaseModel):
    channel_id: str | None = None


class DefaultAccountIn(BaseModel):
    channel_id: str


class RenderFirst(BaseModel):
    start: float | None = None
    end: float | None = None
    render_opts: dict | None = None


class PublishIn(BaseModel):
    title: str
    description: str = ""
    tags: list[str] = []
    category_id: str = "22"
    privacy: str = "public"
    publish_at: str | None = None
    made_for_kids: bool = False
    # YouTube's "AI use" and "Paid promotion" questions. None leaves them for
    # Studio to ask; the app's forms always send an answer.
    contains_synthetic_media: bool | None = None
    paid_promotion: bool | None = None
    embeddable: bool = True
    public_stats_viewable: bool = True
    license: str = "youtube"
    default_language: str | None = None
    notify_subscribers: bool = True
    playlist_id: str | None = None
    # A flag, not a path. The thumbnail is chosen beforehand through
    # POST /clips/{id}/thumbnail and stored under an id-derived name, so there
    # is no reason for this body to be able to name a file — and letting it
    # meant any caller could have an arbitrary image uploaded to the channel.
    thumbnail: bool = False
    channel_id: str | None = None
    render_first: RenderFirst | None = None
    # A video already on the channel that this upload replaces: deleted once
    # the new one is up.
    replace_video_id: str | None = None


class ReplaceIn(BaseModel):
    channel_id: str | None = None


class DeleteVideoIn(BaseModel):
    channel_id: str | None = None
    # Must be true: a delete cannot be undone, so a body that forgot to say so
    # is refused rather than read as a yes.
    confirm: bool = False


class PushThumbnailIn(BaseModel):
    """Which of this app's saved thumbnails to put on a video already on the
    channel. Never image data or a path: what goes to the channel is always a
    thumbnail this app made (thumbnails_api), for a clip or compilation."""

    # Omitted: the clip or compilation this app posted as that video.
    publish_id: int | None = None
    channel_id: str | None = None


def ours_by_video(d) -> dict[str, int]:
    """YouTube video id -> the clip (or, negative, compilation) this app made
    it from: direct uploads, and posts that went out through WoopSocial or
    Upload-Post with a YouTube link."""
    from publish.youtube_status import video_id_from

    ours: dict[str, int] = {}
    for r in d.conn.execute("SELECT clip_id, youtube_id FROM uploads").fetchall():
        ours[r["youtube_id"]] = r["clip_id"]
    for r in d.conn.execute(
        "SELECT clip_id, post_id, post_url FROM clip_publishes WHERE platform = 'youtube'"
    ).fetchall():
        vid = video_id_from(r["post_url"] or "") or video_id_from(r["post_id"] or "")
        if vid:
            ours.setdefault(vid, r["clip_id"])
    return ours


class ThumbnailIn(BaseModel):
    # The image itself, base64, not a path to it. The desktop app's file
    # dialog runs in Electron's main process, which already has the bytes, so
    # there is no reason to send a filename over the API and have the backend
    # re-open it -- which would mean trusting an arbitrary path from an
    # unauthenticated local endpoint. Sending the data also lets the format be
    # checked from the actual bytes rather than from the name on the end.
    image: str | None = None
    t: float | None = None
    # Which generated candidate to keep (0 is the best one). Generated
    # thumbnails already sit in the thumbnails folder under a name derived
    # from the clip id, so this is an index rather than image data.
    generated: int | None = None


# Where a just-granted token waits while we ask YouTube which channel it is
# for. Never the unqualified slot — that may already hold another channel.
PENDING = "pending"

PRIVACIES = ("public", "unlisted", "private")
LICENSES = ("youtube", "creativeCommon")


def install(app, *, config, db, data_dir, worker, publish_worker) -> None:
    """Attach the publishing routes. `db` is the per-request StateDB factory."""

    def _guard(d):
        """404 when the feature is off, so it looks absent rather than refused."""
        if not service.is_enabled(d):
            raise HTTPException(404, "YouTube publishing is not enabled")

    def _publisher(privacy: str = "private", channel_id: str | None = None):
        return service.make_publisher(config, data_dir, privacy, channel_id)

    def _default(d) -> str | None:
        return service.default_channel_id(d)

    def _fail(e: Exception) -> HTTPException:
        message = e.message if isinstance(e, PublishError) else str(e)
        return HTTPException(400, redact(message)[:500])

    # ---- status and settings --------------------------------------------

    @app.get("/youtube/status")
    def youtube_status():
        d = db()
        try:
            return service.status_payload(d, config, data_dir)
        finally:
            d.close()

    @app.patch("/youtube/settings")
    def patch_youtube_settings(body: YouTubeSettingsPatch):
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        if "privacy" in patch and patch["privacy"] not in PRIVACIES:
            raise HTTPException(400, f"privacy must be one of {', '.join(PRIVACIES)}")
        if len(patch.get("common_description") or "") > COMMON_DESCRIPTION_MAX:
            # YouTube's own limit is 5,000 for the whole description. Capping the
            # standing part well below it leaves room for the clip's own words,
            # which would otherwise be the half that gets truncated away.
            raise HTTPException(
                400,
                f"Keep the common description under {COMMON_DESCRIPTION_MAX} characters.",
            )
        d = db()
        try:
            saved = service.save_settings(d, patch)
            return {"settings": saved, "status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    # ---- credentials (bring your own Google Cloud project) ---------------

    @app.put("/youtube/credentials")
    def put_credentials(body: CredentialsIn):
        from core import secrets
        from publish.oauth import client_config, looks_like_desktop_client
        from publish.youtube_shorts import CLIENT_SECRET

        d = db()
        try:
            _guard(d)
        finally:
            d.close()

        built = client_config(body.client_id, body.client_secret)
        if not looks_like_desktop_client(built) or not body.client_secret.strip():
            raise HTTPException(400, "Both a client ID and a client secret are needed.")
        secrets.save(Path(data_dir), CLIENT_SECRET, built)
        # Never echo the secret back, not even masked beyond recognition.
        return {"ok": True, "client_id_tail": body.client_id.strip()[-12:]}

    @app.delete("/youtube/credentials")
    def delete_credentials():
        from core import secrets
        from publish.youtube_shorts import CLIENT_SECRET, TOKEN_SECRET, token_name_for

        secrets.wipe(Path(data_dir), CLIENT_SECRET)
        secrets.wipe(Path(data_dir), TOKEN_SECRET)
        d = db()
        try:
            # Every channel's token, not just the default one: the client they
            # were all issued against is gone, so none of them can refresh.
            for account in service.load_accounts(d):
                secrets.wipe(Path(data_dir), token_name_for(account["id"]))
                service.remove_account(d, account["id"])
            service.remember_channel(d, None)
        finally:
            d.close()
        return {"cleared": True}

    # ---- connect / disconnect -------------------------------------------

    @app.post("/youtube/connect")
    def start_connect(body: ConnectIn):
        from core import secrets
        from publish.oauth import ConnectFlow, require_client, scopes_for
        from publish.youtube_shorts import CLIENT_SECRET

        d = db()
        try:
            _guard(d)
        finally:
            d.close()

        existing = _flow["thread"]
        if existing is not None and existing.is_alive():
            return {"state": "waiting"}

        stored = secrets.load(Path(data_dir), CLIENT_SECRET)
        try:
            client = require_client(stored)
        except Exception as e:
            raise _fail(e) from e

        thread = ConnectFlow(
            client, scopes_for(body.playlists or body.all_permissions, analytics=body.all_permissions)
        )
        _flow["thread"] = thread
        thread.start()
        return {"state": "waiting"}

    @app.get("/youtube/connect")
    def poll_connect():
        thread = _flow["thread"]
        if thread is None:
            return {"state": "idle"}
        if thread.state == "waiting":
            return {"state": "waiting"}
        if thread.state == "error":
            return {"state": "error", "error": redact(thread.error)[:300]}

        # Consent finished, but we do not yet know WHICH channel was picked,
        # and the token has to be filed under that channel's id. So it lands in
        # a scratch slot first — never the unqualified one, which may already
        # hold a different channel's token that this would clobber.
        from core import secrets
        from publish.youtube_shorts import token_name_for

        creds = thread.credentials
        token = json.loads(creds.to_json())
        secrets.save(Path(data_dir), token_name_for(PENDING), token)
        _flow["thread"] = None

        channel = None
        error = ""
        try:
            channel = _publisher(channel_id=PENDING).channel_info()
        except Exception as e:
            error = redact(str(getattr(e, "message", e)))[:300]

        d = db()
        try:
            if channel:
                secrets.save(Path(data_dir), token_name_for(channel["id"]), token)
                service.add_account(d, channel, list(getattr(creds, "scopes", []) or []))
            secrets.wipe(Path(data_dir), token_name_for(PENDING))
            # Checked, not assumed: Google lets a person untick a permission on
            # its consent screen, and the sign-in still "succeeds" without it.
            # Say which of the asked-for permissions did not come back.
            asked = set(getattr(thread, "_scopes", []) or [])
            got = set(getattr(creds, "scopes", []) or [])
            return {
                "state": "done",
                "channel": channel,
                "error": error,
                "not_granted": sorted(asked - got) if got else [],
                "status": service.status_payload(d, config, data_dir),
            }
        finally:
            d.close()

    @app.post("/youtube/disconnect")
    def disconnect(body: DisconnectIn | None = None):
        """Disconnect one channel, or every channel when none is named."""
        from core import secrets
        from publish.oauth import revoke
        from publish.youtube_shorts import TOKEN_SECRET, token_name_for

        channel_id = body.channel_id if body else None
        d = db()
        try:
            accounts = service.load_accounts(d)
            targets = [a for a in accounts if a["id"] == channel_id] if channel_id else accounts

            for account in targets:
                name = (
                    TOKEN_SECRET if account.get("legacy_token") else token_name_for(account["id"])
                )
                stored = secrets.load(Path(data_dir), name) or {}
                revoke(stored.get("refresh_token") or stored.get("token") or "")
                secrets.wipe(Path(data_dir), name)
                service.remove_account(d, account["id"])

            if not channel_id:
                # Belt and braces: an install that predates the roster has a
                # token in the unqualified slot and nothing to enumerate.
                stored = secrets.load(Path(data_dir), TOKEN_SECRET) or {}
                revoke(stored.get("refresh_token") or stored.get("token") or "")
                secrets.wipe(Path(data_dir), TOKEN_SECRET)
                service.remember_channel(d, None)

            return {"disconnected": True, "status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    @app.patch("/youtube/default-account")
    def set_default_account(body: DefaultAccountIn):
        d = db()
        try:
            _guard(d)
            service.set_default_account(d, body.channel_id)
            return {"status": service.status_payload(d, config, data_dir)}
        finally:
            d.close()

    # ---- reference data --------------------------------------------------

    @app.get("/youtube/categories")
    def categories(region: str = "US"):
        d = db()
        try:
            _guard(d)
            channel_id = _default(d)
        finally:
            d.close()
        try:
            got = {"categories": _publisher(channel_id=channel_id).categories(region)}
        except Exception as e:
            raise _fail(e) from e
        d = db()
        try:
            service.spend(d, "videoCategories.list")
        finally:
            d.close()
        return got

    @app.get("/youtube/playlists")
    def playlists():
        d = db()
        try:
            _guard(d)
            channel_id = _default(d)
        finally:
            d.close()
        try:
            got = {"playlists": _publisher(channel_id=channel_id).playlists()}
        except Exception as e:
            raise _fail(e) from e
        d = db()
        try:
            service.spend(d, "playlists.list")
        finally:
            d.close()
        return got

    # ---- publishing ------------------------------------------------------

    @app.get("/clips/{clip_id}/publish")
    def clip_publish_status(clip_id: int):
        d = db()
        try:
            _guard(d)
            row = d.get_upload(clip_id)
            active = d.active_publish_job_for_clip(clip_id)
            return {
                "upload": dict(row) if row else None,
                "job": dict(active) if active else None,
            }
        finally:
            d.close()

    @app.post("/clips/{clip_id}/publish")
    def publish_clip(clip_id: int, body: PublishIn):
        from publish.schedule import validate_publish_at

        d = db()
        try:
            _guard(d)
            clip = d.get_publishable(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            if d.active_publish_job_for_clip(clip_id):
                raise HTTPException(409, "This clip is already being published.")

            if not body.title.strip():
                raise HTTPException(400, "A title is required.")
            if body.privacy not in PRIVACIES:
                raise HTTPException(400, f"privacy must be one of {', '.join(PRIVACIES)}")
            if body.license not in LICENSES:
                raise HTTPException(400, f"license must be one of {', '.join(LICENSES)}")

            publish_at = None
            if body.publish_at:
                try:
                    publish_at = validate_publish_at(body.publish_at)
                except Exception as e:
                    raise HTTPException(400, str(getattr(e, "message", e))) from e

            thumbnail = None
            if body.thumbnail:
                thumbnail = _thumb_path(clip_id, "chosen")
                if not thumbnail.exists():
                    raise HTTPException(
                        400, "No thumbnail has been chosen for this clip yet."
                    )

            # Which channel this goes to. Named explicitly, or the default.
            channel_id = body.channel_id or service.default_channel_id(d)
            if body.channel_id:
                known = {a["id"] for a in service.load_accounts(d)}
                if body.channel_id not in known:
                    raise HTTPException(
                        400, "That YouTube channel is not connected. Reconnect it in Settings."
                    )

            # Chain onto the EXISTING render path rather than re-implementing
            # one: same job type, same worker, same code that Apply edits runs.
            after_job_id = 0
            if body.render_first is not None:
                payload = {"clip_id": clip_id}
                if body.render_first.start is not None:
                    payload["start"] = body.render_first.start
                if body.render_first.end is not None:
                    payload["end"] = body.render_first.end
                if body.render_first.render_opts:
                    payload["render_opts"] = body.render_first.render_opts
                after_job_id = d.add_job("render", json.dumps(payload))
            elif not (clip["path"] and Path(clip["path"]).exists()):
                raise HTTPException(
                    400, "This clip has no rendered file yet. Apply your edits first."
                )

            from server import publishing_api

            request = {
                "title": publishing_api.title_for(d, clip, body.title),
                "description": body.description,
                # Search keywords, the creator and the channel's standing
                # keywords when nothing was typed (publish/seo.py).
                "tags": publishing_api.youtube_tags(d, clip, body.tags),
                "category_id": body.category_id,
                "privacy": body.privacy,
                "publish_at": publish_at,
                "made_for_kids": body.made_for_kids,
                "contains_synthetic_media": body.contains_synthetic_media,
                "has_paid_product_placement": body.paid_promotion,
                "embeddable": body.embeddable,
                "public_stats_viewable": body.public_stats_viewable,
                "license": body.license,
                "default_language": body.default_language,
                "notify_subscribers": body.notify_subscribers,
                "playlist_id": body.playlist_id,
                "thumbnail": str(thumbnail) if thumbnail else None,
            }
            # Not part of PublishRequest — it selects which token to publish
            # with, rather than describing the video.
            request["channel_id"] = channel_id
            if body.replace_video_id:
                import re

                from publish.youtube_shorts import PLAYLIST_SCOPES

                if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", body.replace_video_id):
                    raise HTTPException(400, "That is not a YouTube video id.")
                try:
                    # Deleting needs the full permission: find out now, not
                    # after the render and the upload.
                    _publisher(channel_id=channel_id).credentials(PLAYLIST_SCOPES)
                except Exception as e:
                    raise _fail(e) from e
                request["replace_video_id"] = body.replace_video_id
            # No playlist picked: the worker applies the playlist rule for
            # this creator (or for compilations), creating one if allowed.
            if not body.playlist_id and service.load_settings(d).get("playlists_enabled"):
                request["playlist_auto"] = publishing_api.playlist_key(d, clip)
            job_id = d.add_publish_job(
                clip_id,
                json.dumps(request),
                video_id=clip["video_id"],
                start_s=clip["start_s"],
                end_s=clip["end_s"],
                after_job_id=after_job_id,
            )
        finally:
            d.close()

        if after_job_id:
            worker.notify()
        publish_worker.notify()
        return {"publish_job_id": job_id, "render_job_id": after_job_id or None}

    @app.post("/publish/{job_id}/cancel")
    def cancel_publish(job_id: int):
        d = db()
        try:
            _guard(d)
            job = d.get_publish_job(job_id)
            if job is None:
                raise HTTPException(404, "no such publish job")
            if job["status"] == "queued":
                d.finish_publish_job(job_id, "cancelled", error="Cancelled.")
                return {"cancelled": True}
        finally:
            d.close()
        publish_worker.cancel(job_id)
        return {"cancelling": True}

    # A few minutes of memory per channel: the page opens often, and the
    # numbers do not move by the second.
    _channel_cache: dict[str, tuple[float, list[dict]]] = {}

    @app.get("/youtube/channel-videos")
    def channel_videos(channel_id: str | None = None, limit: int = 50, fresh: bool = False):
        """The channel's newest videos and where each one stands on YouTube:
        processing, scheduled, live, rejected... with views, likes and
        comments. Everything on the channel, however it was posted; the ones
        this app made are marked with their clip or compilation."""
        import time as _time

        from publish.youtube_status import summarize
        from server import publishing_api

        d = db()
        try:
            _guard(d)
            channel = channel_id or service.default_channel_id(d)
            if channel_id and channel_id not in {a["id"] for a in service.load_accounts(d)}:
                raise HTTPException(400, "That YouTube channel is not connected.")
            key = f"{channel or ''}:{limit}"
            cached = _channel_cache.get(key)
            if cached and not fresh and _time.monotonic() - cached[0] < 120:
                videos = cached[1]
            else:
                try:
                    items = _publisher(channel_id=channel).channel_videos(max(1, min(limit, 200)))
                except Exception as e:
                    raise _fail(e) from e
                videos = [summarize(i) for i in items]
                _channel_cache[key] = (_time.monotonic(), videos)
                service.spend_read(d, len(items))
                try:
                    publishing_api.record_stats(d, videos)  # best times learn from these
                except Exception:
                    pass

            ours = ours_by_video(d)
            return {
                "channel_id": channel,
                "videos": [{**v, "publish_id": ours.get(v["video_id"])} for v in videos],
            }
        finally:
            d.close()

    @app.get("/youtube/uploads")
    def uploads(limit: int = 50):
        d = db()
        try:
            _guard(d)
            return {"uploads": [dict(r) for r in d.recent_uploads(max(1, min(limit, 200)))]}
        finally:
            d.close()

    @app.post("/youtube/videos/{video_id}/unschedule")
    def unschedule_video(video_id: str, body: PushThumbnailIn):
        """Take a scheduled video off YouTube's schedule (it stays, private).
        Works for any scheduled video on the channel, however it was posted,
        and marks this app's own record of it as cancelled so the schedule
        list stops showing it. About 51 quota units."""
        import re

        from publish.youtube_status import video_id_from

        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
            raise HTTPException(400, "That is not a YouTube video id.")
        d = db()
        try:
            _guard(d)
            channel = body.channel_id or _default(d)
            if body.channel_id and body.channel_id not in {a["id"] for a in service.load_accounts(d)}:
                raise HTTPException(400, "That YouTube channel is not connected.")
        finally:
            d.close()
        # YouTube already agrees with what was asked: not scheduled any more (an
        # earlier cancel got through, or it was done in Studio) or gone. That is
        # a success for this button; only our own records still need closing.
        settled = None
        try:
            _publisher(channel_id=channel).unschedule(video_id)
        except PublishError as e:
            if e.message == "That video is not scheduled.":
                settled = False
            elif e.message == "YouTube has no such video on this channel.":
                settled = True
            else:
                raise _fail(e) from e
        except Exception as e:
            raise _fail(e) from e
        d = db()
        try:
            service.spend(d, "videos.list")
            if settled is None:
                service.spend(d, "videos.update")
            service.retire_video(d, video_id, "Taken off YouTube's schedule.", forget_upload=bool(settled))
        finally:
            d.close()
        for key in [k for k in _channel_cache if k.startswith(f"{channel or ''}:")]:
            _channel_cache.pop(key, None)
        return {"unscheduled": True, "video_id": video_id}

    @app.post("/youtube/videos/{video_id}/replace")
    def replace_video(video_id: str, body: ReplaceIn):
        """Re-render the clip a video came from and put the result up in its
        place, in one step: re-render, upload a new copy with the same title,
        description, tags and settings (and the same go-live time when it was
        scheduled), and only once that upload has succeeded delete the old
        video. Nothing is lost if the render or upload fails. Subscribers are
        not notified a second time. Clips only: a compilation is re-rendered
        in the Editor. Needs the full YouTube permission (for the delete)."""
        import re

        from publish.schedule import validate_publish_at
        from publish.youtube_shorts import PLAYLIST_SCOPES

        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
            raise HTTPException(400, "That is not a YouTube video id.")
        d = db()
        try:
            _guard(d)
            channel = body.channel_id or _default(d)
            if body.channel_id and body.channel_id not in {a["id"] for a in service.load_accounts(d)}:
                raise HTTPException(400, "That YouTube channel is not connected.")
            pid = ours_by_video(d).get(video_id)
            if pid is None:
                raise HTTPException(400, "This video was not made in this app, so there is no clip to re-render.")
            if pid < 0:
                raise HTTPException(
                    400,
                    "This is a compilation. Re-render it in the Editor's Compilations tab, then "
                    "publish it and delete this one.",
                )
            clip = d.get_publishable(pid)
            if clip is None:
                # A re-render gave the clip a new id: find it by what it is.
                up = d.conn.execute("SELECT * FROM uploads WHERE youtube_id = ?", (video_id,)).fetchone()
                if up is not None and up["video_id"]:
                    clip = d.conn.execute(
                        "SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                        (up["video_id"], up["start_s"], up["end_s"]),
                    ).fetchone()
            if clip is None:
                raise HTTPException(404, "The clip this came from no longer exists.")
        finally:
            d.close()
        publisher = _publisher(channel_id=channel)
        try:
            publisher.credentials(PLAYLIST_SCOPES)
            item = publisher.video_details(video_id)
        except Exception as e:
            raise _fail(e) from e
        snippet, status = item.get("snippet") or {}, item.get("status") or {}
        d = db()
        try:
            service.spend(d, "videos.list")
        finally:
            d.close()
        publish_at = None
        if status.get("publishAt"):
            try:
                publish_at = validate_publish_at(status["publishAt"])
            except Exception as e:
                raise HTTPException(
                    400,
                    "The old video goes live too soon to re-render and replace on the same schedule. "
                    "Remove it from the schedule first, then publish the clip again. "
                    + str(getattr(e, "message", e)),
                ) from e
        has_thumb = (Path(data_dir) / "thumbnails" / f"clip_{int(clip['id'])}_chosen.jpg").exists()
        payload = PublishIn(
            title=snippet.get("title") or clip["title"] or "Untitled",
            description=snippet.get("description") or "",
            tags=list(snippet.get("tags") or []),
            category_id=snippet.get("categoryId") or "22",
            privacy="private" if publish_at else (status.get("privacyStatus") or "public"),
            publish_at=publish_at,
            made_for_kids=bool(status.get("selfDeclaredMadeForKids")),
            contains_synthetic_media=status.get("containsSyntheticMedia"),
            embeddable=status.get("embeddable", True),
            public_stats_viewable=status.get("publicStatsViewable", True),
            license=status.get("license") or "youtube",
            default_language=snippet.get("defaultLanguage"),
            notify_subscribers=False,
            thumbnail=has_thumb,
            channel_id=body.channel_id,
            render_first=RenderFirst(),
            replace_video_id=video_id,
        )
        return publish_clip(int(clip["id"]), payload)

    @app.post("/youtube/videos/{video_id}/delete")
    def delete_video(video_id: str, body: DeleteVideoIn):
        """Delete a video from the channel, for good. The way to replace one
        with a re-rendered clip: YouTube cannot swap the file of an existing
        upload. This app's record of it is closed (state 'skipped'), which is
        what lets the clip be published again. About 50 quota units."""
        import re

        from publish.youtube_status import video_id_from

        if not body.confirm:
            raise HTTPException(400, "Deleting a video cannot be undone: confirm it.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
            raise HTTPException(400, "That is not a YouTube video id.")
        d = db()
        try:
            _guard(d)
            channel = body.channel_id or _default(d)
            if body.channel_id and body.channel_id not in {a["id"] for a in service.load_accounts(d)}:
                raise HTTPException(400, "That YouTube channel is not connected.")
        finally:
            d.close()
        try:
            _publisher(channel_id=channel).delete_video(video_id)
        except Exception as e:
            raise _fail(e) from e
        d = db()
        try:
            service.spend(d, "videos.delete")
            service.retire_video(d, video_id, "Deleted from YouTube.", forget_upload=True)
        finally:
            d.close()
        for key in [k for k in _channel_cache if k.startswith(f"{channel or ''}:")]:
            _channel_cache.pop(key, None)
        return {"deleted": True, "video_id": video_id}

    @app.post("/youtube/videos/{video_id}/thumbnail")
    def push_thumbnail(video_id: str, body: PushThumbnailIn):
        """Put this app's saved thumbnail on a video that is already on the
        channel, however it was posted (direct, WoopSocial, Upload-Post: it is
        the same channel and the same YouTube API). About 50 quota units.
        YouTube's own refusal comes back as the message; custom thumbnails need
        a verified channel."""
        import re

        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
            raise HTTPException(400, "That is not a YouTube video id.")
        d = db()
        try:
            _guard(d)
            channel = body.channel_id or _default(d)
            if body.channel_id and body.channel_id not in {a["id"] for a in service.load_accounts(d)}:
                raise HTTPException(400, "That YouTube channel is not connected.")
            pid = body.publish_id if body.publish_id is not None else ours_by_video(d).get(video_id)
        finally:
            d.close()
        if pid is None:
            raise HTTPException(
                400, "This video was not made in this app, so there is no thumbnail here to send."
            )
        image = (Path(data_dir) / "thumbnails" / f"clip_{int(pid)}_chosen.jpg").resolve()
        if not within((Path(data_dir) / "thumbnails").resolve(), image) or not image.exists():
            raise HTTPException(404, "No thumbnail is saved for that clip yet. Design one first.")
        try:
            _publisher(channel_id=channel).set_thumbnail(video_id, str(image))
        except Exception as e:
            raise _fail(e) from e
        d = db()
        try:
            service.spend(d, "thumbnails.set")
        finally:
            d.close()
        # The list still holds the old picture until YouTube has re-cut it.
        for key in [k for k in _channel_cache if k.startswith(f"{channel or ''}:")]:
            _channel_cache.pop(key, None)
        return {"pushed": True, "video_id": video_id, "publish_id": pid}

    # ---- thumbnails ------------------------------------------------------

    def _thumb_path(clip_id: int, tag: str) -> Path:
        """Where a generated thumbnail for this clip lives.

        Both parts of the name are integers by the time they get here — FastAPI
        has already coerced the route's `clip_id: int` and `t: float` — so no
        separator can survive into the filename. The int() calls are written
        out anyway so that is visible in this function rather than inferred
        from a type annotation two screens away, and within() states the
        confinement instead of leaving it to be worked out.
        """
        folder = (Path(data_dir) / "thumbnails").resolve()
        target = folder / f"clip_{int(clip_id)}_{tag}.jpg"
        if not within(folder, target):
            raise HTTPException(400, "bad thumbnail name")  # unreachable with ints
        folder.mkdir(parents=True, exist_ok=True)
        return target

    @app.get("/clips/{clip_id}/frame")
    def clip_frame(clip_id: int, t: float = 0.0):
        """One JPEG frame from a clip, for the thumbnail picker."""
        d = db()
        try:
            _guard(d)
            clip = d.get_publishable(clip_id)
        finally:
            d.close()
        if clip is None or not clip["path"]:
            raise HTTPException(404, "no such clip")
        # From the database, written by the render pipeline — not from the request.
        source = Path(clip["path"])
        if not source.exists():
            raise HTTPException(404, "this clip has no rendered file")

        at = max(0.0, t)
        target = _thumb_path(clip_id, str(int(at * 1000)))
        if not target.exists():
            _extract_frame(source, at, target)
        return FileResponse(str(target), media_type="image/jpeg")

    @app.post("/publish/plan")
    def publish_plan(body: PlanIn):
        """What publishing these clips would do. Creates nothing.

        The point of a plan is that a batch is consequential: thirty uploads
        with the wrong description cannot be taken back, and each one spends
        quota. So the caller sees the resolved titles, times and descriptions
        first, and executes a plan it has actually looked at.
        """
        from publish.errors import PublishError as _PublishError
        from publish.schedule import spread, validate_publish_at
        from server.publisher import _describe

        if not body.clip_ids:
            raise HTTPException(400, "No clips were given.")
        if body.privacy not in PRIVACIES:
            raise HTTPException(400, f"privacy must be one of {', '.join(PRIVACIES)}")

        times: list[str | None] = [None] * len(body.clip_ids)
        if body.start_at:
            try:
                if body.every_hours:
                    times = list(spread(body.start_at, len(body.clip_ids), body.every_hours))
                else:
                    times = [validate_publish_at(body.start_at)] + [None] * (
                        len(body.clip_ids) - 1
                    )
            except _PublishError as e:
                raise HTTPException(400, getattr(e, "message", str(e))) from e
        elif body.every_hours:
            raise HTTPException(
                400, "An interval needs a starting time: say when the first one goes out."
            )

        d = db()
        try:
            _guard(d)
            items, warnings = [], []
            for clip_id, when in zip(body.clip_ids, times):
                clip = d.get_publishable(clip_id)
                if clip is None:
                    warnings.append(f"Clip {clip_id} no longer exists.")
                    continue
                if not (clip["path"] and Path(clip["path"]).exists()):
                    warnings.append(f"Clip {clip_id} has no rendered file yet.")
                    continue
                items.append({
                    "clip_id": clip_id,
                    "title": clip["title"] or clip["hook"] or f"Clip {clip_id}",
                    # Resolved exactly as the worker will build it, standing
                    # block and hashtags included, so the preview is the truth.
                    "description": _describe(
                        d, clip, clip["description"] or "",
                        (clip["playlist_id"] if "playlist_id" in clip.keys() else "") or "",
                    ),
                    # YouTube rejects publishAt on anything but a private video,
                    # so a scheduled item is private until its moment arrives.
                    "privacy": "private" if when else body.privacy,
                    "publish_at": when,
                })

            ledger = service.load_ledger(d)
            room = ledger.remaining()
            if len(items) > room:
                warnings.append(
                    f"{len(items)} clips, but only {room} uploads left on today's quota. "
                    "The rest will fail until it resets at midnight Pacific."
                )
            # Each upload can also spend up to ~100 units from the shared pool
            # (a thumbnail and a playlist, 50 each), which runs out about as
            # soon as the upload bucket does once other calls are counted.
            worst = len(items) * 100
            if worst > ledger.units_remaining():
                warnings.append(
                    f"These could spend up to {worst} of the {ledger.units_remaining()} quota units "
                    "left today (thumbnails and playlists cost 50 each). Some steps may be refused."
                )
            return {"items": items, "warnings": warnings}
        finally:
            d.close()

    @app.post("/publish/plan/execute")
    def publish_plan_execute(body: PlanExecuteIn):
        """Carry out a plan: one ordinary publish job per clip.

        Each clip goes through the same path a single publish from the editor
        takes, so one failure leaves the others alone.
        """
        if not body.items:
            raise HTTPException(400, "The plan is empty.")

        # Titles are optional in a plan: an agent that only reordered the
        # schedule should not have to repeat metadata it never touched.
        #
        # The clip's own description is read here for the same reason. The
        # preview from /publish/plan resolves it, so executing without it
        # published a different video than the one the user agreed to — the
        # standing block and hashtags survived, but whatever the clip itself
        # said was silently dropped.
        titles: dict[int, str] = {}
        descriptions: dict[int, str] = {}
        d = db()
        try:
            _guard(d)
            for item in body.items:
                clip = d.get_publishable(item.clip_id)
                if clip is None:
                    continue
                if not item.title:
                    titles[item.clip_id] = (
                        clip["title"] or clip["hook"] or f"Clip {item.clip_id}"
                    )
                descriptions[item.clip_id] = clip["description"] or ""
        finally:
            d.close()

        started, skipped = [], []
        for item in body.items:
            try:
                created = publish_clip(
                    item.clip_id,
                    PublishIn(
                        title=item.title or titles.get(item.clip_id, ""),
                        description=descriptions.get(item.clip_id, ""),
                        privacy=item.privacy or ("private" if item.publish_at else "public"),
                        publish_at=item.publish_at,
                    ),
                )
                started.append({"clip_id": item.clip_id, **created})
            except HTTPException as e:
                # One clip's problem must not cost the rest of the batch.
                skipped.append({"clip_id": item.clip_id, "reason": str(e.detail)})
        return {"started": started, "skipped": skipped}

    @app.post("/clips/{clip_id}/thumbnail/generate")
    def generate_thumbnails(clip_id: int, count: int = 3):
        """Thumbnail candidates made from the clip itself, on this machine.

        The three suggestions beside this are fixed positions: a quarter, half
        and three quarters in. These look for a frame with a face in it,
        crop 16:9 around them and burn the clip's hook across the bottom.

        Returns how many were made. An empty list is a normal answer for a
        clip with no readable frames, not an error: the fixed suggestions are
        still there.
        """
        d = db()
        try:
            _guard(d)
            clip = d.get_publishable(clip_id)
        finally:
            d.close()
        if clip is None or not clip["path"]:
            raise HTTPException(404, "no such clip")
        source = Path(clip["path"])
        if not source.exists():
            raise HTTPException(404, "this clip has no rendered file")

        from video.thumbnail import generate

        wanted = max(1, min(int(count), 4))
        targets = [_thumb_path(clip_id, f"gen{i}") for i in range(wanted)]
        for stale in targets:
            stale.unlink(missing_ok=True)
        made = generate(source, clip["hook"] or clip["title"] or "", targets)
        return {"generated": len(made)}

    @app.get("/clips/{clip_id}/thumbnail/generated/{index}")
    def generated_thumbnail(clip_id: int, index: int):
        """One generated candidate, for the picker to show."""
        target = _thumb_path(clip_id, f"gen{max(0, int(index))}")
        if not target.exists():
            raise HTTPException(404, "no such generated thumbnail")
        return FileResponse(str(target), media_type="image/jpeg")

    @app.post("/clips/{clip_id}/thumbnail")
    def choose_thumbnail(clip_id: int, body: ThumbnailIn):
        """Pick a thumbnail: a local image, a frame from the clip, or one of
        the generated candidates."""
        d = db()
        try:
            _guard(d)
            clip = d.get_publishable(clip_id)
        finally:
            d.close()
        if clip is None:
            raise HTTPException(404, "no such clip")

        target = _thumb_path(clip_id, "chosen")

        if body.image:
            from publish.images import decode_thumbnail

            try:
                target.write_bytes(decode_thumbnail(body.image))
            except PublishError as e:
                raise HTTPException(400, e.message) from e
        elif body.generated is not None:
            candidate = _thumb_path(clip_id, f"gen{max(0, int(body.generated))}")
            if not candidate.exists():
                raise HTTPException(404, "that generated thumbnail is gone")
            target.write_bytes(candidate.read_bytes())
        elif body.t is not None:
            source = Path(clip["path"] or "")
            if not source.exists():
                raise HTTPException(404, "this clip has no rendered file")
            _extract_frame(source, max(0.0, body.t), target)
        else:
            raise HTTPException(
                400, "Give an image, a time in the clip, or a generated candidate."
            )
        return {"thumbnail": str(target)}


def _extract_frame(source: Path, at: float, target: Path) -> None:
    from core.binaries import ffmpeg

    result = subprocess.run(
        [
            ffmpeg(), "-y", "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", str(source),
            "-frames:v", "1",
            # YouTube wants 16:9 and at least 1280 wide. A vertical Short
            # letterboxed into that is what Studio shows too.
            "-vf", "scale=1280:-2",
            "-q:v", "3",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.exists():
        raise HTTPException(500, "Could not read a frame from this clip.")
