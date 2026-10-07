"""Audit the videos already on a channel against the monetization rules, and
propose the fix for each.

Stdlib only, like the rest of `publish/`: it works on the `videos.list` items
that `YouTubeShortsPublisher.channel_videos` returns, so it runs without a
Google library and the tests need no network. Nothing here talks to YouTube.

Two kinds of fix, kept apart because they carry different risk:

* **Deterministic**: strip hashtags from a title, cap and dedupe hashtags,
  drop tags that echo the title. `publish.compliance.sanitize` does these; the
  same function gates every new upload, so the audit and the generators agree.
* **Rewrite**: machine-sounding or stuffed wording. Only a model can repair it,
  so it is opt-in (`rewrite_entry`), touches only prose paragraphs (never the
  links, timestamps, credits or standing blocks in a description), and is kept
  only if the result passes the same linter.

Every entry keeps the video's ORIGINAL title, description and tags, so a
change can be checked against the live video before it is applied and undone
afterwards.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from publish import compliance

# What videos.update lets a client set in the snippet. The API replaces the
# whole snippet, so these are sent back whole with only our fields changed.
WRITABLE = ("title", "description", "tags", "categoryId", "defaultLanguage")


def snapshot(item: dict) -> dict:
    """The fields of a videos.list item that the audit reads and may change."""
    sn = item.get("snippet") or {}
    return {
        "video_id": item.get("id", ""),
        "title": sn.get("title", ""),
        "description": sn.get("description", ""),
        "tags": list(sn.get("tags") or []),
        "category_id": str(sn.get("categoryId", "") or ""),
        "language": sn.get("defaultLanguage", ""),
        "privacy": (item.get("status") or {}).get("privacyStatus", ""),
        "published_at": sn.get("publishedAt", ""),
    }


def audit_item(item: dict, rules: compliance.Rules | None = None) -> dict:
    """One video: what is wrong, what would be changed, what needs a rewrite."""
    snap = snapshot(item)
    genre = compliance.CATEGORY_GENRES.get(snap["category_id"], "")
    r = rules or compliance.rules()
    findings = compliance.check(snap["title"], snap["description"], snap["tags"], genre, r)
    cleaned = compliance.sanitize(snap["title"], snap["description"], snap["tags"], genre, r)
    proposed = {"title": cleaned.title, "description": cleaned.description, "tags": cleaned.tags}
    changes = [k for k in ("title", "description", "tags") if proposed[k] != snap[k]]
    return {
        **snap,
        "genre": genre,
        "findings": [f.as_dict() for f in findings],
        "score": compliance.score(findings),
        "changes": changes,
        "proposed": proposed if changes else None,
        "manual": [f.as_dict() for f in cleaned.remaining],
        "rewritten": False,
    }


def audit_all(items: list[dict], rules: compliance.Rules | None = None) -> dict:
    """The report for a channel: every video, worst first, with a summary."""
    videos = sorted((audit_item(i, rules) for i in items), key=lambda v: (v["score"], v["published_at"]))
    flagged = [v for v in videos if v["findings"]]
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "checked": len(videos),
            "clean": len(videos) - len(flagged),
            "fixable": sum(1 for v in videos if v["proposed"]),
            "needs_rewrite": sum(1 for v in videos if v["manual"]),
        },
        "videos": videos,
    }


# ---- rewrites ---------------------------------------------------------------

_TIMESTAMP = re.compile(r"^\s*\(?\d{1,2}:\d{2}", re.M)
_LABEL = re.compile(r"^\s*(source|sources|featuring|music|credits?|follow|socials?|links?|discord|business)\b", re.I)


def _protected(paragraph: str) -> bool:
    """A paragraph a rewrite must not touch: links, timestamps and credits are
    facts a model would damage, and a hashtag line is not prose."""
    p = paragraph.strip()
    if not p:
        return True
    if re.search(r"https?://|www\.|@\w{3,}|\b[\w-]+\.(?:com|tv|gg|net|org)\b", p, re.I):
        return True
    if _TIMESTAMP.search(p) or _LABEL.match(p):
        return True
    return not re.sub(r"#\w+", "", p).strip()


def split_description(description: str) -> list[tuple[str, bool]]:
    """(paragraph, protected) pairs, in order. Paragraphs are blank-line separated."""
    return [(p, _protected(p)) for p in re.split(r"\n\s*\n", description or "")]


_PROSE_CODES = frozenset({"machine-tone", "keyword-repeat", "keyword-share", "keyword-list", "emoji"})


def _bad_prose(text: str, genre: str) -> bool:
    return any(f.code in _PROSE_CODES for f in compliance.check("", text, [], genre))


def rewrite_entry(entry: dict, llm, channel: str = "") -> dict:
    """Ask the model to repair the wording the sanitizer could not, in place.

    Only prose paragraphs with a problem of their own go to the model, and the
    title when it is the problem. A reply is kept only when it is shorter than
    about the original and passes the linter, so a bad rewrite changes nothing.
    Returns the entry with `proposed`, `changes`, `manual` and `rewritten`
    updated; on any failure it is returned as it was."""
    from llm.base import generate_json

    base = entry["proposed"] or {"title": entry["title"], "description": entry["description"], "tags": entry["tags"]}
    genre = entry.get("genre", "")
    parts = split_description(base["description"])
    todo = {i: p for i, (p, locked) in enumerate(parts) if not locked and _bad_prose(p, genre)}
    title_bad = any(f.code == "machine-tone" for f in compliance.check(base["title"], "", [], genre)
                    if f.field == "title")
    if not todo and not title_bad:
        return entry

    who = channel or "the channel"
    asks = []
    if title_bad:
        asks.append(f"TITLE: {base['title']}")
    asks += [f"PARAGRAPH {i}: {p}" for i, p in todo.items()]
    prompt = (
        "Rewrite the text below so it reads like a person wrote it: plain, specific words in the "
        "same language, no stock phrases (dive into, game-changer, must-watch, you won't believe), "
        "no em dashes, no exclamation marks, no hashtags, no lists of keywords, and no word repeated "
        "more than twice. Keep every fact. Add nothing. Do not make it longer. "
        f"It is a description on {who}'s YouTube video.\n\n" + "\n\n".join(asks)
        + '\n\nRespond with ONLY valid JSON: {"title": "...", "paragraphs": [{"index": 0, "text": "..."}]}'
        + "\nOmit the title if there is no TITLE above, and list only the paragraphs given."
    )
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "paragraphs": {"type": "array", "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["index", "text"], "additionalProperties": False}},
        },
        "required": ["paragraphs"], "additionalProperties": False,
    }
    try:
        from analysis.metadata import _parse

        got = _parse(generate_json(llm, prompt, schema)) or {}
    except Exception:
        return entry

    new = dict(base)
    texts = [p for p, _ in parts]
    for row in got.get("paragraphs") or []:
        try:
            i, text = int(row["index"]), str(row["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if i in todo and text and len(text) <= len(todo[i]) * 1.15 + 20 and not _bad_prose(text, genre) \
                and not compliance.hashtags_in(text):
            texts[i] = text
    new["description"] = "\n\n".join(texts)
    title = str(got.get("title") or "").strip()
    if title_bad and title and len(title) <= 100 and not compliance.machine_signals(title) \
            and not compliance.hashtags_in(title):
        new["title"] = title

    cleaned = compliance.sanitize(new["title"], new["description"], new["tags"], genre)
    final = {"title": cleaned.title, "description": cleaned.description, "tags": cleaned.tags}
    orig = {"title": entry["title"], "description": entry["description"], "tags": entry["tags"]}
    changes = [k for k in ("title", "description", "tags") if final[k] != orig[k]]
    return {**entry, "proposed": final if changes else None, "changes": changes,
            "manual": [f.as_dict() for f in cleaned.remaining], "rewritten": True}


def is_unchanged(live: dict, entry: dict) -> bool:
    """True when the live video still matches what the audit read, so applying
    the proposal cannot overwrite an edit made since."""
    return all(live.get(k) == entry.get(k) for k in ("title", "description", "tags"))
