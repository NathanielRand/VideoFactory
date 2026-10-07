"""Turning a PublishRequest into a videos.insert body.

One module owns the field list, so there is exactly one place to check against
the API reference when YouTube changes something — and so the tests can verify
the body without a network call or a Google library.

The limits here are YouTube's, and they are enforced rather than trusted: the
API rejects the whole upload for a title of 101 characters, and losing a
finished render to that would be an unkind way to find out.
"""

from publish import compliance
from publish.base import DESCRIPTION_MAX, TAGS_BUDGET, TITLE_MAX, PublishRequest

# How many hashtags a description gets is `compliance.rules().max_hashtags`
# (config/settings.yaml, `compliance:`), three by default. YouTube only shows
# the first three above the title anyway, fifteen is the cliff past which it
# ignores EVERY hashtag on the video, and under the 2027 Partner Program rules a
# pile of them reads as spam. This module used to hard-code five.

# Angle brackets are rejected outright in titles and descriptions.
_FORBIDDEN = str.maketrans({"<": "", ">": ""})


def clamp_title(title: str) -> str:
    """A title must be non-empty and at most 100 characters."""
    cleaned = title.translate(_FORBIDDEN).strip()
    return cleaned[:TITLE_MAX]


def _cut_at_word(text: str, room: int) -> str:
    """`text` shortened to `room` characters, at a word boundary when one is
    near, with trailing punctuation dropped rather than left dangling."""
    if len(text) <= room:
        return text
    cut = text[: max(0, room)]
    space = cut.rfind(" ")
    if space >= room * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-|–—")


def decorate_title(title: str, channel: str = "", hashtag: str = "", limit: int = TITLE_MAX) -> str:
    """"<title> | <channel> #Tag": the source channel and one always-on hashtag.

    The suffix is what the creator asked for, so it is the title that gives way
    when the two do not fit in `limit`, never the suffix (clamp_title cuts from
    the end, which would take exactly these off). A part already in the title is
    not added again, because the publish box is prefilled with whatever went up
    last time and a second publish would otherwise stack a second copy.
    A channel that would leave almost no room for the title itself is dropped.
    """
    base = title.translate(_FORBIDDEN).strip()
    lowered = base.casefold()
    word = "".join(c for c in (hashtag or "") if c.isalnum() or c == "_")[:30]
    tag = f"#{word}" if word else ""
    name = " ".join((channel or "").translate(_FORBIDDEN).split())
    if name and (name.casefold() in lowered or len(name) > limit // 2):
        name = ""
    if tag and tag.casefold() in lowered:
        tag = ""
    if not (name or tag):
        return base[:limit]
    # "Title | Channel #Tag", or "Title #Tag" when there is no channel to name.
    suffix = f"{name} {tag}".strip()
    if not base:
        return suffix[:limit]
    joined = f" | {suffix}" if name else f" {suffix}"
    return f"{_cut_at_word(base, limit - len(joined))}{joined}"


def clamp_description(description: str) -> str:
    return description.translate(_FORBIDDEN)[:DESCRIPTION_MAX]


def clamp_tags(tags: list[str]) -> list[str]:
    """Fit tags into YouTube's 500-character total budget.

    Drops whole tags rather than truncating one, because half a tag is not a
    tag. A tag containing a space counts as quoted, so it costs two extra
    characters — accounted for here, since ignoring it is how you end up just
    over the limit with no idea why.
    """
    kept: list[str] = []
    used = 0
    for raw in tags:
        tag = raw.lstrip("#").strip()
        if not tag:
            continue
        cost = len(tag) + (2 if " " in tag else 0)
        separator = 1 if kept else 0
        if used + separator + cost > TAGS_BUDGET:
            continue
        kept.append(tag)
        used += separator + cost
    return kept


def creator_tag(name: str) -> str:
    """A channel name as a hashtag, or "" when nothing usable is left.

    YouTube hashtags carry no spaces or punctuation, so "Some Streamer" has to
    become "#SomeStreamer": left as it is, YouTube reads the tag "#Some" and
    then some loose words.
    """
    kept = "".join(c for c in (name or "") if c.isalnum())
    return f"#{kept}" if kept else ""


def _normalise(hashtags: list[str]) -> list[str]:
    """Bare words gain their hash; blanks and lone hashes are dropped."""
    out = []
    for raw in hashtags:
        tag = (raw or "").strip()
        if not tag:
            continue
        tag = tag if tag.startswith("#") else f"#{tag}"
        if len(tag) > 1:
            out.append(tag)
    return out


def _strip_our_last_tag_line(description: str, ours: set[str]) -> str:
    """The description minus the tag line THIS module put there last time.

    Publishing a clip twice would otherwise stack a second line: the editor's
    box is prefilled with whatever went up before, which already ends in one.

    Recognised by content, not by position. A creator's standing block can end
    in a hashtag of its own, and removing any trailing hashtag line would eat
    that on the first publish, leave the block no longer matching, and re-add
    the whole thing on the next one, growing the description forever. A line
    only qualifies if every tag on it is one we are about to write anyway.
    """
    lines = description.rstrip().split("\n")
    if lines:
        words = lines[-1].split()
        if (
            words
            and all(w.startswith("#") for w in words)
            and {w.lower() for w in words} <= ours
        ):
            lines.pop()
    return "\n".join(lines).rstrip()


def with_common_block(description: str, common: str) -> str:
    """The description with the creator's standing block under it.

    The block is the same on every video: where to watch live, the Discord,
    the socials. It goes between the clip's own description and the hashtag
    line, which is where a viewer expects it and where it does not push the
    first sentence out of the preview.

    Skipped when the text is already there, so re-publishing a clip does not
    repeat the links.
    """
    block = (common or "").strip()
    if not block:
        return description
    body = description.rstrip()
    if block in body:
        return body
    return f"{body}\n\n{block}" if body else block


def description_with_hashtags(
    description: str, hashtags: list[str], creator: str = ""
) -> str:
    """Put the clip's hashtags in the description, the creator's tag first.

    First because the list is cut at the hashtag limit, so whatever must survive
    has to lead. Duplicates fold together case-insensitively: a generated
    "#creatorname" and a channel called "CreatorName" are one tag, not two.
    """
    tags = _normalise(([creator_tag(creator)] if creator else []) + list(hashtags))

    seen: set[str] = set()
    unique: list[str] = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)

    body = _strip_our_last_tag_line(description, {tag.lower() for tag in unique})
    if not unique:
        return clamp_description(_strip_our_last_tag_line(description, set()))
    line = " ".join(unique[: compliance.rules().max_hashtags])
    return clamp_description(f"{body}\n\n{line}" if body else line)


def build_insert_body(request: PublishRequest) -> dict:
    """The request body for videos.insert.

    Only fields the public API actually accepts. The publishAt invariant is
    enforced here rather than at the call site: YouTube rejects publishAt on
    anything but a private video, and a caller that forgets would get a
    confusing 400 instead of a scheduled video.
    """
    # The last gate before text leaves the app: whatever was generated, edited
    # in the publish box or prefilled from an earlier publish is held to the
    # monetization rules here (publish/compliance.py). Only the fixes that need
    # no judgement are made; wording that still fails is the editor's check to
    # report, because refusing an upload over it would lose a finished render.
    genre = compliance.CATEGORY_GENRES.get(str(request.category_id), "")
    clean = compliance.sanitize(
        clamp_title(request.title), request.description, clamp_tags(request.tags), genre=genre
    )
    snippet: dict = {
        "title": clamp_title(clean.title),
        "description": clamp_description(clean.description),
        "categoryId": str(request.category_id),
    }
    tags = clamp_tags(clean.tags)
    if tags:
        snippet["tags"] = tags
    if request.default_language:
        snippet["defaultLanguage"] = request.default_language

    privacy = request.privacy
    status: dict = {
        "selfDeclaredMadeForKids": bool(request.made_for_kids),
        "embeddable": bool(request.embeddable),
        "publicStatsViewable": bool(request.public_stats_viewable),
        "license": request.license,
    }
    # An answer is sent as given, "no" included: leaving a "no" out left
    # YouTube's "AI use" question open, and Studio holds processing for it.
    if request.contains_synthetic_media is not None:
        status["containsSyntheticMedia"] = bool(request.contains_synthetic_media)
    if request.publish_at:
        # Scheduling IS a private upload with a publish time attached.
        privacy = "private"
        status["publishAt"] = request.publish_at
    status["privacyStatus"] = privacy

    body: dict = {"snippet": snippet, "status": status}
    if request.has_paid_product_placement is not None:
        # YouTube's "Paid promotion" question, answered at upload.
        body["paidProductPlacementDetails"] = {
            "hasPaidProductPlacement": bool(request.has_paid_product_placement)
        }
    if request.recording_date:
        body["recordingDetails"] = {"recordingDate": request.recording_date}
    if request.localizations:
        body["localizations"] = _clean_localizations(request.localizations, genre)
    return body


def _clean_localizations(localizations: dict, genre: str = "") -> dict:
    """Each translated title and description under the same rules as the
    original: a translation is a second chance to put hashtags in a title."""
    out: dict = {}
    for lang, entry in localizations.items():
        if not isinstance(entry, dict):
            out[lang] = entry
            continue
        clean = compliance.sanitize(entry.get("title", ""), entry.get("description", ""), [], genre=genre)
        out[lang] = {**entry, "title": clamp_title(clean.title), "description": clamp_description(clean.description)}
    return out


def parts_for(request: PublishRequest) -> str:
    """Which `part` values the insert call needs for this body."""
    parts = ["snippet", "status"]
    if request.recording_date:
        parts.append("recordingDetails")
    if request.localizations:
        parts.append("localizations")
    if request.has_paid_product_placement is not None:
        parts.append("paidProductPlacementDetails")
    return ",".join(parts)


def timestamped_url(url: str, start: float) -> str:
    """A YouTube link to the moment a clip starts, so the source link lands
    where the clip came from. Any other link is returned unchanged."""
    import re

    link = (url or "").strip()
    seconds = max(0, int(start or 0))
    if not seconds or re.search(r"[?&]t=", link):
        return link
    if re.match(r"https?://(www\.|m\.)?youtube\.com/watch", link):
        return f"{link}&t={seconds}s"
    if re.match(r"https?://youtu\.be/", link):
        return f"{link}{'&' if '?' in link else '?'}t={seconds}s"
    return link


def source_credit_line(name: str, url: str, start: float = 0.0) -> str:
    """"Source: <channel> - <link>", or "" when there is neither. Only a real
    web link is used; a local file's path is not something to publish."""
    link = timestamped_url(url, start) if (url or "").strip().lower().startswith(("http://", "https://")) else ""
    name = (name or "").strip()
    if name and link:
        return f"Source: {name} - {link}"
    return f"Source: {link}" if link else (f"Source: {name}" if name else "")


def playlist_url(playlist_id: str) -> str:
    pid = (playlist_id or "").strip()
    return f"https://www.youtube.com/playlist?list={pid}" if pid else ""


def with_links(description: str, lines: list[str]) -> str:
    """The description with credit lines under it. A line already there (same
    link, or the same text) is not repeated, so re-publishing does not stack
    them."""
    body = description.rstrip()
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        link = line.rsplit(" ", 1)[-1] if "http" in line else line
        if line in body or (link.startswith("http") and link in body):
            continue
        body = f"{body}\n\n{line}" if body else line
    return body
