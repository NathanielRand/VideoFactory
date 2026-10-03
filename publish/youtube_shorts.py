"""YouTube upload via the YouTube Data API v3.

One-time setup per user (Video Factory walks you through this in Settings):

1. Google Cloud Console -> create project -> enable "YouTube Data API v3"
2. OAuth consent screen -> fill it in -> **Publish app**. Leaving it in
   *Testing* makes Google expire the sign-in every 7 days, so you would have to
   reconnect weekly. "In production" does not mean verified and costs nothing.
3. Credentials -> OAuth client ID -> **Desktop app**
4. Paste the client ID and secret into Settings, or run `python main.py auth`

Quota, as of the 1 June 2026 granular-buckets change: `videos.insert` has its
OWN daily bucket of 100 uploads per API project, separate from the 10,000-unit
pool everything else shares. (Older documentation, and this file until
recently, said an upload cost 1,600 of those 10,000 — that stopped being true
on 4 December 2025, when the cost fell to ~100 units, and stopped being the
right model entirely six months later.) Since every user brings their own Cloud
project, nobody shares a bucket with anyone else.

The one thing no code here can fix: **until an API project passes YouTube's
free compliance audit, YouTube locks every video uploaded through it to
private**, whatever privacy was requested. That lock cannot be appealed and
cannot be undone in Studio — the video has to be uploaded again from an audited
project. So `publish()` reads the privacy back and reports what actually
happened rather than what was asked for.
"""

import time
from collections.abc import Callable
from pathlib import Path

from publish.base import ProgressFn, Publisher, PublishRequest, PublishResult
from publish.errors import (
    AuthRequired,
    NotConnected,
    PublishCancelled,
    PublishError,
    from_http_error,
)
from publish.metadata import build_insert_body, parts_for

SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READONLY = "https://www.googleapis.com/auth/youtube.readonly"
SCOPE_FULL = "https://www.googleapis.com/auth/youtube"

# Kept for the daemon and for tests/test_youtube_auth.py, which pin the
# original behaviour of authenticate().
UPLOAD_SCOPES = [SCOPE_UPLOAD]
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
AUTH_SCOPES = [*UPLOAD_SCOPES, ANALYTICS_SCOPE]

# What the editor connects with. youtube.upload alone cannot read back which
# channel is connected, nor confirm what privacy YouTube applied, so the
# read-only scope earns its place. The full scope is NOT requested by default:
# it is only needed to add a video to a playlist, and asking every user for
# read/write access to their channel for a feature most never touch is not a
# trade worth making.
CONNECT_SCOPES = [SCOPE_UPLOAD, SCOPE_READONLY]
PLAYLIST_SCOPES = [SCOPE_UPLOAD, SCOPE_READONLY, SCOPE_FULL]
# commentThreads.insert asks for this one specifically. It is requested
# alongside the playlist permission on new connections; a channel connected
# before that simply skips the first comment with a warning, and is not
# broken by it (PLAYLIST_SCOPES itself is unchanged, so no one is re-prompted).
SCOPE_COMMENT = "https://www.googleapis.com/auth/youtube.force-ssl"
COMMENT_SCOPES = [SCOPE_UPLOAD, SCOPE_READONLY, SCOPE_COMMENT]

# Multiple of 256 KB, as the resumable protocol requires. The old code passed
# chunksize=-1, which uploads the whole file in one request and makes progress
# reporting impossible — there is nothing to report between "started" and
# "finished".
CHUNK_BYTES = 8 * 1024 * 1024

TOKEN_SECRET = "youtube_token"
CLIENT_SECRET = "youtube_client"


def token_name_for(channel_id: str | None) -> str:
    """Where one channel's token lives in the credential store.

    A creator with a main channel and a clips channel needs both connected at
    once, and each channel is a separate consent with its own token — YouTube
    binds a token to whichever channel was picked on the consent screen.

    The unqualified name is kept for whatever was connected first, so an
    install that predates multi-channel support keeps working without being
    asked to reconnect.
    """
    return f"{TOKEN_SECRET}__{channel_id}" if channel_id else TOKEN_SECRET


class YouTubeShortsPublisher(Publisher):
    def __init__(
        self,
        client_secret: Path | None = None,
        token_path: Path | None = None,
        privacy: str = "unlisted",
        *,
        data_dir: Path | None = None,
        channel_id: str | None = None,
    ):
        """`data_dir` selects the encrypted credential store (the desktop app).
        `client_secret` / `token_path` are the original file-based arguments,
        still used by `python main.py auth` and core/scheduler.py.

        `channel_id` picks which connected channel to publish as. None means
        whatever was connected first.
        """
        self.client_secret = client_secret
        self.token_path = token_path
        self.privacy = privacy
        self.data_dir = Path(data_dir) if data_dir else None
        self.channel_id = channel_id or None
        self.token_secret = token_name_for(channel_id)
        self._service = None

    @property
    def name(self) -> str:
        return "youtube_shorts"

    @property
    def default_privacy(self) -> str:
        return self.privacy

    # ---- credential storage ---------------------------------------------

    def _store(self):
        from core import secrets

        return secrets

    def _load_token(self) -> dict | None:
        if self.data_dir is None:
            return None
        secrets = self._store()
        if self.token_path:
            secrets.migrate_plaintext(self.data_dir, TOKEN_SECRET, self.token_path)
        token = secrets.load(self.data_dir, self.token_secret)
        if token is None and self.channel_id:
            # Asked for a specific channel and there is no token filed under it.
            # The first connection predates per-channel names, so fall back to
            # the unqualified one rather than telling someone with a working
            # connection that they are not connected.
            token = secrets.load(self.data_dir, TOKEN_SECRET)
        return token

    def _client_config(self) -> dict | None:
        """The OAuth client, from the store when there is one."""
        if self.data_dir is None:
            return None
        return self._store().load(self.data_dir, CLIENT_SECRET)

    def has_client(self) -> bool:
        if self._client_config():
            return True
        return bool(self.client_secret and self.client_secret.exists())

    def _save_token(self, creds) -> None:
        if self.data_dir is not None:
            import json

            self._store().save(self.data_dir, self.token_secret, json.loads(creds.to_json()))
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")

    def forget(self) -> None:
        """Disconnect this channel. Other channels and the client are untouched."""
        self._service = None
        if self.data_dir is not None:
            self._store().wipe(self.data_dir, self.token_secret)
        # The legacy plaintext file only ever held the first connection, so it
        # goes with the unqualified token and not with a per-channel one.
        if not self.channel_id and self.token_path and self.token_path.exists():
            self.token_path.unlink()

    # ---- auth ------------------------------------------------------------

    def credentials(self, scopes: list[str] | None = None):
        """Return valid credentials for `scopes`, without any user interaction.

        Raises NotConnected when there is nothing stored, and AuthRequired when
        what is stored no longer works — the two need different UI, so they are
        different exceptions.
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        scopes = scopes or CONNECT_SCOPES
        creds = None

        stored = self._load_token()
        if stored is not None:
            creds = Credentials.from_authorized_user_info(stored)
        elif self.token_path and self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path))

        if creds is None:
            raise NotConnected("No YouTube account is connected yet.")
        if not creds.has_scopes(scopes):
            raise AuthRequired(
                "This needs a permission you have not granted yet. "
                "Press Update permissions on the YouTube connection in Settings."
            )
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                raise AuthRequired(
                    "Your YouTube sign-in has expired. Reconnect it in Settings. "
                    "(If this keeps happening every week, your Google Cloud consent "
                    "screen is still set to Testing — set it to In production.)",
                    detail=str(e)[:200],
                ) from e
            self._save_token(creds)
        if not creds.valid:
            raise AuthRequired("Your YouTube sign-in is no longer valid. Reconnect it.")
        return creds

    def authenticate(self, interactive: bool = False):
        """Return valid credentials.

        The explicit `auth` command requests every YouTube permission Clips
        Kitty knows how to use: upload plus read-only Analytics. Background
        uploads still require only the original upload scope, so an existing
        upload-only token does not regress.
        """
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        required_scopes = AUTH_SCOPES if interactive else UPLOAD_SCOPES
        creds = None

        if self.token_path and self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path))
            if not creds.has_scopes(required_scopes):
                creds = None

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self._save_token(creds)

        if not creds or not creds.valid:
            if not interactive:
                raise RuntimeError(
                    "No YouTube authorization yet. Run once: python main.py auth"
                )

            if not self.client_secret or not self.client_secret.exists():
                raise RuntimeError(
                    f"Missing {self.client_secret}. Follow README 'Enable uploads' "
                    "to create OAuth credentials in Google Cloud Console."
                )

            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.client_secret),
                required_scopes,
            )
            creds = flow.run_local_server(port=0)
            self._save_token(creds)

        return creds

    # ---- the API service -------------------------------------------------

    def service(self, scopes: list[str] | None = None):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build(
                "youtube", "v3", credentials=self.credentials(scopes), cache_discovery=False
            )
        return self._service

    # ---- reads -----------------------------------------------------------

    def channel_info(self) -> dict:
        """Who we would be publishing as. 1 quota unit."""
        try:
            response = (
                self.service().channels().list(part="snippet", mine=True).execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        items = response.get("items") or []
        if not items:
            raise AuthRequired("That Google account has no YouTube channel.")
        snippet = items[0]["snippet"]
        return {
            "id": items[0]["id"],
            "title": snippet.get("title", ""),
            "handle": snippet.get("customUrl", ""),
        }

    def playlists(self) -> list[dict]:
        """The user's playlists. 1 unit per page."""
        found: list[dict] = []
        page = None
        try:
            while True:
                response = (
                    self.service(PLAYLIST_SCOPES)
                    .playlists()
                    .list(part="snippet,status,contentDetails", mine=True,
                          maxResults=50, pageToken=page)
                    .execute()
                )
                for item in response.get("items", []):
                    found.append({
                        "id": item["id"],
                        "title": item["snippet"].get("title", ""),
                        "privacy": item.get("status", {}).get("privacyStatus", ""),
                        "count": item.get("contentDetails", {}).get("itemCount", 0),
                    })
                page = response.get("nextPageToken")
                if not page:
                    return found
        except Exception as e:
            raise _wrap(e) from e

    def create_playlist(self, title: str, description: str = "", privacy: str = "public") -> str:
        """Make a playlist and return its id. 50 units; needs the playlist scope."""
        try:
            response = (
                self.service(PLAYLIST_SCOPES)
                .playlists()
                .insert(
                    part="snippet,status",
                    body={
                        "snippet": {"title": title[:150], "description": description[:5000]},
                        "status": {"privacyStatus": privacy},
                    },
                )
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        return str(response.get("id") or "")

    def video_stats(self, video_ids: list[str]) -> dict[str, dict]:
        """Views, likes, comments and go-live time for up to 50 videos. 1 unit.

        Feeds publish/timing.py: which hours this channel's audience turns up.
        """
        ids = [v for v in video_ids if v][:50]
        if not ids:
            return {}
        try:
            response = (
                self.service()
                .videos()
                .list(part="statistics,snippet,status", id=",".join(ids))
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        out: dict[str, dict] = {}
        for item in response.get("items") or []:
            stats = item.get("statistics", {})
            out[item["id"]] = {
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
                "published_at": item.get("snippet", {}).get("publishedAt", ""),
                "privacy": item.get("status", {}).get("privacyStatus", ""),
            }
        return out

    def daily_report(self, days: int = 30) -> list[dict]:
        """Per-day channel numbers from YouTube Analytics: views, minutes
        watched, average view duration, likes, comments and subscribers
        gained. Needs the analytics permission, which connections made before
        it was asked for lack: that raises AuthRequired ("Reconnect")."""
        from datetime import date, timedelta

        from googleapiclient.discovery import build

        creds = self.credentials([ANALYTICS_SCOPE])
        try:
            report = (
                build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                .reports()
                .query(
                    ids="channel==MINE",
                    startDate=(date.today() - timedelta(days=max(1, days) - 1)).isoformat(),
                    endDate=date.today().isoformat(),
                    metrics="views,estimatedMinutesWatched,averageViewDuration,likes,comments,subscribersGained,subscribersLost",
                    dimensions="day",
                    sort="day",
                )
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        names = [h["name"] for h in report.get("columnHeaders") or []]
        return [dict(zip(names, row)) for row in report.get("rows") or []]

    def audience_report(self, days: int = 90) -> dict:
        """Who watches the channel, from YouTube Analytics: viewer share by
        age group and gender, and views by country (top five). Small channels
        come back with no rows (YouTube withholds what could identify a
        viewer), which is an empty answer, not an error. Needs the analytics
        permission (raises AuthRequired)."""
        from datetime import date, timedelta

        from googleapiclient.discovery import build

        creds = self.credentials([ANALYTICS_SCOPE])
        window = {
            "ids": "channel==MINE",
            "startDate": (date.today() - timedelta(days=max(1, days) - 1)).isoformat(),
            "endDate": date.today().isoformat(),
        }
        try:
            reports = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False).reports()
            demo = reports.query(metrics="viewerPercentage", dimensions="ageGroup,gender", **window).execute()
            geo = reports.query(metrics="views", dimensions="country", sort="-views", maxResults=5, **window).execute()
        except Exception as e:
            raise _wrap(e) from e

        def rows(report: dict) -> list[dict]:
            names = [h["name"] for h in report.get("columnHeaders") or []]
            return [dict(zip(names, row)) for row in report.get("rows") or []]

        return {"age_gender": rows(demo), "countries": rows(geo)}

    def video_subscribers(self, days: int = 90, limit: int = 25) -> list[dict]:
        """The videos that brought the most subscribers, from YouTube
        Analytics: per video, subscribers gained and lost over the last `days`
        (with views, for context), best first. "Gained" is what YouTube credits
        to that video (people who subscribed while watching it or from its
        page), the closest thing there is to "the video that caused the
        subscription". Needs the analytics permission (raises AuthRequired)."""
        from datetime import date, timedelta

        from googleapiclient.discovery import build

        creds = self.credentials([ANALYTICS_SCOPE])
        try:
            report = (
                build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
                .reports()
                .query(
                    ids="channel==MINE",
                    startDate=(date.today() - timedelta(days=max(1, days) - 1)).isoformat(),
                    endDate=date.today().isoformat(),
                    metrics="views,subscribersGained,subscribersLost",
                    dimensions="video",
                    sort="-subscribersGained",
                    maxResults=max(1, min(int(limit), 200)),
                )
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        names = [h["name"] for h in report.get("columnHeaders") or []]
        return [dict(zip(names, row)) for row in report.get("rows") or []]

    def subscriber_count(self) -> dict:
        """The channel's subscriber total (1 unit). YouTube rounds it to three
        significant figures, and a channel can hide it: `count` is then None."""
        try:
            got = self.service().channels().list(part="statistics", mine=True).execute().get("items") or []
        except Exception as e:
            raise _wrap(e) from e
        stats = (got[0].get("statistics") if got else None) or {}
        hidden = bool(stats.get("hiddenSubscriberCount"))
        raw = stats.get("subscriberCount")
        return {"count": None if hidden or raw is None else int(raw), "hidden": hidden}

    def unschedule(self, video_id: str) -> None:
        """Take a scheduled video off the schedule: it stays on the channel,
        private, with no go-live time. Nothing is deleted.

        videos.update replaces the whole status part, so the current status
        is read first and sent back with only publishAt removed; anything
        omitted (license, embeddable, made-for-kids) would reset. Costs 51
        units. Needs the full YouTube permission, the one playlists use, which
        the default connection does not include: that raises AuthRequired with
        a "Reconnect" message."""
        svc = self.service(PLAYLIST_SCOPES)
        try:
            got = svc.videos().list(part="status", id=video_id).execute().get("items") or []
            if not got:
                raise PublishError("YouTube has no such video on this channel.")
            status = dict(got[0].get("status") or {})
            if not status.get("publishAt"):
                raise PublishError("That video is not scheduled.")
            status.pop("publishAt")
            status["privacyStatus"] = "private"
            svc.videos().update(part="status", body={"id": video_id, "status": status}).execute()
        except PublishError:
            raise
        except Exception as e:
            raise _wrap(e) from e

    def video_details(self, video_id: str) -> dict:
        """The snippet and status of one video on the channel (1 unit), so a
        replacement can carry the same title, description, tags and settings."""
        try:
            got = self.service().videos().list(part="snippet,status", id=video_id).execute().get("items") or []
        except Exception as e:
            raise _wrap(e) from e
        if not got:
            raise PublishError("YouTube has no such video on this channel.")
        return got[0]

    def delete_video(self, video_id: str) -> None:
        """Delete a video from the channel. Permanent: its views, likes and
        comments go with it. 50 units; needs the full YouTube permission
        (see unschedule)."""
        svc = self.service(PLAYLIST_SCOPES)
        try:
            svc.videos().delete(id=video_id).execute()
        except Exception as e:
            raise _wrap(e) from e

    def set_thumbnail(self, video_id: str, image: str) -> None:
        """Replace the custom thumbnail of a video already on the channel.
        50 quota units. YouTube's own refusal (an unverified channel, a file
        over 2 MB) comes back as the PublishError message."""
        try:
            self.service().thumbnails().set(videoId=video_id, media_body=str(image)).execute()
        except Exception as e:
            raise _wrap(e) from e

    def channel_videos(self, limit: int = 50) -> list[dict]:
        """The channel's newest uploads, however they were posted (this app,
        WoopSocial, Upload-Post or by hand), as full videos.list items.

        Read from the channel's own uploads playlist, which as the owner
        includes private and scheduled videos. About 2 quota units per 50.
        """
        limit = max(1, min(int(limit), 200))
        try:
            svc = self.service()
            channels = svc.channels().list(part="contentDetails", mine=True).execute()
            items = channels.get("items") or []
            if not items:
                return []
            uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            ids: list[str] = []
            page = None
            while len(ids) < limit:
                got = (
                    svc.playlistItems()
                    .list(part="contentDetails", playlistId=uploads,
                          maxResults=min(50, limit - len(ids)), pageToken=page)
                    .execute()
                )
                ids += [i["contentDetails"]["videoId"] for i in got.get("items") or []]
                page = got.get("nextPageToken")
                if not page:
                    break
            videos: list[dict] = []
            for i in range(0, len(ids), 50):
                got = (
                    svc.videos()
                    .list(part="snippet,status,statistics,processingDetails,contentDetails",
                          id=",".join(ids[i : i + 50]))
                    .execute()
                )
                videos += got.get("items") or []
        except Exception as e:
            raise _wrap(e) from e
        # videos.list does not promise the order asked for.
        order = {vid: n for n, vid in enumerate(ids)}
        return sorted(videos, key=lambda v: order.get(v.get("id"), len(order)))

    def categories(self, region: str = "US") -> list[dict]:
        """Assignable video categories for a region. 1 unit."""
        try:
            response = (
                self.service()
                .videoCategories()
                .list(part="snippet", regionCode=region)
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        return [
            {"id": item["id"], "title": item["snippet"]["title"]}
            for item in response.get("items", [])
            if item["snippet"].get("assignable")
        ]

    def video_status(self, video_id: str) -> dict:
        """Read a video's status back. 1 unit.

        Used a minute after upload to catch three things the insert response
        cannot tell us: processing failure, rejection (duplicate, copyright),
        and confirmation of the unaudited-project privacy lock.
        """
        try:
            response = (
                self.service()
                .videos()
                .list(part="status,processingDetails", id=video_id)
                .execute()
            )
        except Exception as e:
            raise _wrap(e) from e
        items = response.get("items") or []
        if not items:
            return {}
        status = items[0].get("status", {})
        return {
            "privacy": status.get("privacyStatus", ""),
            "upload_status": status.get("uploadStatus", ""),
            "rejection_reason": status.get("rejectionReason", ""),
            "failure_reason": status.get("failureReason", ""),
            "publish_at": status.get("publishAt", ""),
        }

    # ---- publish ---------------------------------------------------------

    def publish(
        self,
        request: PublishRequest,
        on_progress: ProgressFn | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PublishResult:
        from googleapiclient.http import MediaFileUpload

        path = Path(request.video_path)
        if not path.exists():
            raise PublishError(
                "This clip's file is missing. Re-render it and try again."
            )

        scopes = PLAYLIST_SCOPES if request.playlist_id else CONNECT_SCOPES
        service = self.service(scopes)

        def start(req: PublishRequest):
            media = MediaFileUpload(
                str(path), chunksize=CHUNK_BYTES, resumable=True, mimetype="video/*"
            )
            return service.videos().insert(
                part=parts_for(req),
                body=build_insert_body(req),
                media_body=media,
                notifySubscribers=bool(req.notify_subscribers),
            )

        warnings: list[str] = []
        try:
            response = self._run_upload(start(request), on_progress, should_cancel)
        except PublishError as e:
            # The paid-promotion answer is a documented upload part but not
            # on YouTube's list of settable fields. Should it be refused, it
            # is refused when the upload session opens, before any video
            # exists, so going again without it cannot make a duplicate.
            said = f"{e.message} {getattr(e, 'detail', '')}".lower()
            if request.has_paid_product_placement is None or "paidproductplacement" not in said:
                raise
            from dataclasses import replace

            response = self._run_upload(
                start(replace(request, has_paid_product_placement=None)), on_progress, should_cancel
            )
            warnings.append(
                "YouTube did not take the paid-promotion answer through the API. "
                "Answer it in YouTube Studio."
            )

        video_id = response["id"]
        status = response.get("status", {})
        snippet = response.get("snippet", {})
        result = PublishResult(
            video_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            requested_privacy=request.privacy,
            actual_privacy=status.get("privacyStatus", ""),
            publish_at=status.get("publishAt") or request.publish_at,
            channel_id=snippet.get("channelId", ""),
            channel_title=snippet.get("channelTitle", ""),
            warnings=warnings,
        )

        if result.locked_private:
            result.warnings.append(
                "YouTube set this video to private even though you asked for "
                f"{request.privacy}. That happens when the Google Cloud project has not "
                "passed YouTube's free API audit. It cannot be changed in Studio — the "
                "video has to be uploaded again from an audited project."
            )

        # Neither of these should fail the publish. The video is already up;
        # reporting the whole thing as failed would be a lie, and would invite
        # a retry that posts it twice.
        if request.thumbnail:
            _emit(on_progress, "Setting the thumbnail", 0, 0)
            try:
                service.thumbnails().set(
                    videoId=video_id, media_body=str(request.thumbnail)
                ).execute()
                result.thumbnail_set = True
            except Exception as e:
                result.warnings.append(
                    f"Uploaded, but the thumbnail was refused: {_wrap(e).message}"
                )

        if request.playlist_id:
            _emit(on_progress, "Adding to the playlist", 0, 0)
            try:
                service.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": request.playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                ).execute()
                result.playlist_added = True
            except Exception as e:
                result.warnings.append(
                    f"Uploaded, but adding it to the playlist failed: {_wrap(e).message}"
                )

        comment = (request.first_comment or "").strip()
        if comment:
            _emit(on_progress, "Posting the first comment", 0, 0)
            try:
                self.service(COMMENT_SCOPES).commentThreads().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "videoId": video_id,
                            "topLevelComment": {"snippet": {"textOriginal": comment[:10000]}},
                        }
                    },
                ).execute()
                result.comment_posted = True
            except Exception as e:
                why = (
                    "reconnect the channel in Settings to allow comments"
                    if isinstance(e, (AuthRequired, NotConnected))
                    else _wrap(e).message
                )
                result.warnings.append(f"Uploaded, but the first comment was not posted: {why}")

        return result

    def _run_upload(self, insert, on_progress, should_cancel):
        """Drive the resumable upload, retrying only what is safe to retry."""
        response = None
        attempts = 0
        while response is None:
            if should_cancel and should_cancel():
                raise PublishCancelled("Upload cancelled.")
            try:
                status, response = insert.next_chunk(num_retries=3)
            except Exception as e:
                error = _wrap(e)
                # 404 means the session died. Restarting it would create a
                # SECOND video, so it is never retried automatically.
                if not error.retryable or attempts >= 4:
                    raise error from e
                attempts += 1
                time.sleep(min(2**attempts, 16))
                continue
            attempts = 0
            if status is not None:
                _emit(
                    on_progress,
                    "Uploading to YouTube",
                    int(getattr(status, "resumable_progress", 0) or 0),
                    int(getattr(status, "total_size", 0) or 0),
                )
        return response


def _emit(on_progress: ProgressFn | None, label: str, done: int, total: int) -> None:
    if on_progress is None:
        return
    try:
        on_progress(label, done, total)
    except Exception:
        # Progress reporting must never be able to fail an upload.
        pass


def _wrap(exc: Exception) -> PublishError:
    if isinstance(exc, PublishError):
        return exc
    return from_http_error(exc)
