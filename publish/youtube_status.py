"""What a video on YouTube is doing right now, in words a creator uses.

YouTube reports a video's condition across four separate fields (upload
status, processing status, privacy and a scheduled publish time), and none of
them alone answers "is it live yet?". This folds them into one state:

  processing  uploaded, YouTube still working on it
  scheduled   private with a publish time still ahead
  live        public
  unlisted    viewable by link
  private     private with nothing scheduled
  rejected    refused (copyright, duplicate, terms...), with the reason
  failed      processing failed, with the reason
  deleted     gone from YouTube

Stdlib only, so it is tested without any Google library installed.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

STATES = ("processing", "scheduled", "live", "unlisted", "private", "rejected", "failed", "deleted")

# YouTube's reason codes, as a person would say them.
_REJECTIONS = {
    "claim": "a copyright claim",
    "copyright": "copyright",
    "duplicate": "it duplicates another video",
    "inappropriate": "inappropriate content",
    "legal": "a legal complaint",
    "length": "it is too long",
    "termsOfUse": "the Terms of Service",
    "trademark": "a trademark",
    "uploaderAccountClosed": "the uploader's account is closed",
    "uploaderAccountSuspended": "the uploader's account is suspended",
}
_FAILURES = {
    "codec": "YouTube could not read the video codec",
    "conversion": "YouTube could not convert the video",
    "emptyFile": "the file was empty",
    "invalidFile": "the file was not a video YouTube could read",
    "tooSmall": "the file was too small",
    "uploadAborted": "the upload was cut off",
}


def _parse(ts: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def state_of(item: dict, now: datetime | None = None) -> tuple[str, str]:
    """(state, detail) for one videos.list item. Detail is "" or a sentence."""
    now = now or datetime.now(timezone.utc)
    status = item.get("status") or {}
    processing = (item.get("processingDetails") or {}).get("processingStatus", "")
    upload = status.get("uploadStatus", "")
    privacy = status.get("privacyStatus", "")

    if upload == "deleted":
        return "deleted", ""
    if upload == "rejected":
        reason = status.get("rejectionReason", "")
        return "rejected", f"Rejected for {_REJECTIONS.get(reason, reason or 'a reason YouTube did not give')}."
    if upload == "failed" or processing in ("failed", "terminated"):
        reason = status.get("failureReason", "")
        return "failed", (_FAILURES.get(reason, reason) or "YouTube could not process it.").rstrip(".") + "."
    if upload == "uploaded" or processing == "processing":
        return "processing", ""
    publish_at = _parse(status.get("publishAt", ""))
    if privacy == "private" and publish_at and publish_at > now:
        return "scheduled", ""
    if privacy == "public":
        return "live", ""
    if privacy == "unlisted":
        return "unlisted", ""
    return "private", ""


def seconds(duration: str) -> int:
    """ISO 8601 duration ("PT1M5S") to seconds; 0 when unreadable."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _count(stats: dict, key: str) -> int | None:
    """A statistic, or None when YouTube hides it (comments off, likes hidden)."""
    raw = stats.get(key)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def summarize(item: dict, now: datetime | None = None) -> dict:
    """One video as the Publish page shows it."""
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    stats = item.get("statistics") or {}
    state, detail = state_of(item, now)
    thumbs = snippet.get("thumbnails") or {}
    thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    length = seconds((item.get("contentDetails") or {}).get("duration", ""))
    vid = item.get("id", "")
    return {
        "video_id": vid,
        "title": snippet.get("title", ""),
        "thumbnail": thumb,
        "published_at": snippet.get("publishedAt", ""),
        "publish_at": status.get("publishAt", ""),
        "state": state,
        "detail": detail,
        "privacy": status.get("privacyStatus", ""),
        "duration": length,
        # YouTube's own rule is up to three minutes and vertical or square;
        # the API does not say which, so length is the honest hint.
        "short": 0 < length <= 180,
        "views": _count(stats, "viewCount"),
        "likes": _count(stats, "likeCount"),
        "comments": _count(stats, "commentCount"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
    }


def video_id_from(url_or_id: str) -> str:
    """A YouTube video id from a watch, shorts or youtu.be link, or an id."""
    text = (url_or_id or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    m = re.search(r"(?:v=|/shorts/|youtu\.be/|/live/)([A-Za-z0-9_-]{11})", text)
    return m.group(1) if m else ""
