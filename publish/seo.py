"""Search and reach: the rules that decide how metadata is shaped per platform.

Stdlib only, like the rest of `publish/`, so the tests run without any
provider library installed.

Four jobs, each a pure function:

* `caption_for` fits one set of metadata to one platform. TikTok, Instagram
  and X read the caption and nothing else; each has its own length, its own
  hashtag habits, and its own point at which the text is cut off in the feed.
  Sending the YouTube description everywhere wasted the first line on
  platforms that show only the first line.
* `keywords_for` builds YouTube's hidden tags: search phrases, not hashtags.
  They used to be the clip's hashtags with the # removed, which spends the
  500-character budget on words already in the description.
* `chapters` and `credits` write the two blocks a compilation's description
  needs: timestamps (YouTube turns them into chapters, which search indexes)
  and a line crediting every source channel.
* `check` grades a draft against what each platform rewards, so the editor
  can say "put the keyword in the first line" instead of leaving it to luck.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from publish.metadata import TAGS_BUDGET, clamp_tags


# Per platform: the whole caption's limit, how much shows before "more", how
# many hashtags it rewards, and whether it has a title separate from the text.
#
# `visible` is the one that matters for reach: it is what a scrolling viewer
# actually reads. The hook has to fit inside it.
@dataclass(frozen=True)
class PlatformRules:
    caption_max: int
    visible: int
    hashtags: int
    has_title: bool = False
    title_max: int = 100


RULES: dict[str, PlatformRules] = {
    "youtube": PlatformRules(caption_max=5000, visible=100, hashtags=3, has_title=True, title_max=100),
    "tiktok": PlatformRules(caption_max=2200, visible=90, hashtags=5),
    "instagram": PlatformRules(caption_max=2200, visible=125, hashtags=5),
    "facebook": PlatformRules(caption_max=5000, visible=120, hashtags=3, has_title=True, title_max=255),
    "x": PlatformRules(caption_max=280, visible=280, hashtags=2),
    "threads": PlatformRules(caption_max=500, visible=500, hashtags=1),
    "bluesky": PlatformRules(caption_max=300, visible=300, hashtags=2),
    "linkedin": PlatformRules(caption_max=3000, visible=140, hashtags=3, has_title=True, title_max=200),
    "pinterest": PlatformRules(caption_max=500, visible=100, hashtags=5, has_title=True, title_max=100),
}
DEFAULT_RULES = PlatformRules(caption_max=2200, visible=100, hashtags=3)

# The title length YouTube shows in full on a phone, search results included.
# Longer titles are allowed, but the end is cut off.
TITLE_VISIBLE = 60
SHORTS_TITLE_VISIBLE = 40


def rules(platform: str) -> PlatformRules:
    return RULES.get(platform, DEFAULT_RULES)


def _hashtag(tag: str) -> str:
    word = re.sub(r"[^\w]", "", str(tag or "").lstrip("#"))
    return f"#{word}" if word else ""


def unique_hashtags(*groups: list[str]) -> list[str]:
    """Every group's tags, in order, as #words, duplicates folded case-blind."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for raw in group or []:
            tag = _hashtag(raw)
            if tag and tag.lower() not in seen:
                seen.add(tag.lower())
                out.append(tag)
    return out


def _cut(text: str, limit: int) -> str:
    """Shorten at a word boundary with an ellipsis, never mid-word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    head = text[: limit - 1]
    space = head.rfind(" ")
    if space > limit // 2:
        head = head[:space]
    return head.rstrip(" ,.;:-") + "…"


def caption_for(
    platform: str,
    *,
    title: str,
    description: str = "",
    hashtags: list[str] | None = None,
    footer: str = "",
) -> str:
    """One platform's caption: hook first, then the body, footer, hashtags.

    On platforms without a title field the title IS the opening line, since
    it is the hook and the only part most viewers see. Where the whole caption
    is short (X, Bluesky, Threads) the body goes first when anything has to
    give: the title and the tags survive, the description is cut to fit.
    """
    r = rules(platform)
    tags = unique_hashtags(hashtags or [])[: r.hashtags]
    tag_line = " ".join(tags)
    head = "" if r.has_title else title.strip()
    body = description.strip()
    if head and body.casefold().startswith(head.casefold()):
        head = ""  # the description already opens with it
    extra = footer.strip()

    def assemble(b: str, f: str) -> str:
        return "\n\n".join(p for p in (head, b, f, tag_line) if p)

    text = assemble(body, extra)
    if len(text) <= r.caption_max:
        return text
    # Drop the footer before cutting into the clip's own words.
    text = assemble(body, "")
    if len(text) <= r.caption_max:
        return text
    fixed = len(assemble("", ""))
    room = r.caption_max - fixed - (2 if fixed else 0)
    if room > 10:
        return assemble(_cut(body, room), "")
    # Not even the title fits with the tags: title alone, cut to fit.
    return _cut(head or body, r.caption_max)


def keywords_for(
    *,
    keywords: list[str] | None = None,
    hashtags: list[str] | None = None,
    creator: str = "",
    channel_keywords: list[str] | None = None,
) -> list[str]:
    """YouTube tags: search phrases first, then the creator, then the rest.

    Order is priority. `clamp_tags` drops whatever runs past the 500-character
    budget from the end, so the phrases people actually search lead and the
    hashtag words, the least useful here, come last.
    """
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        tag = re.sub(r"\s+", " ", str(raw or "").lstrip("#")).strip(" ,")
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)

    for k in keywords or []:
        add(k)
    if creator:
        add(creator)
    for k in channel_keywords or []:
        add(k)
    for h in hashtags or []:
        add(h)
    return clamp_tags(out)


# ---- compilations ------------------------------------------------------------

# YouTube's own rules for turning timestamps into chapters: the first at 0:00,
# at least three, each at least ten seconds long. Miss one and it shows the
# lines as plain text with no chapters at all.
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10
GENERIC = ("Intro", "Outro", "Part")


def timestamp(seconds: float) -> str:
    s = max(0, int(seconds))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chapters(parts: list[tuple[str, float]]) -> str:
    """Timestamp lines from (label, duration) pairs, or "" when YouTube would
    not accept them as chapters.

    Parts too short to be a chapter are folded into the one before, so a
    three-second intro does not cost the whole video its chapters.
    """
    merged: list[list] = []  # [start, label, length]
    t = 0.0
    for label, duration in parts:
        label = re.sub(r"\s+", " ", str(label or "")).strip()[:80] or "Part"
        if merged and (duration < MIN_CHAPTER_SECONDS or merged[-1][2] < MIN_CHAPTER_SECONDS):
            # A part too short to be a chapter joins the one before; so does
            # any part after a chapter still too short to stand alone, lending
            # its name if the one so far was only "Intro".
            merged[-1][2] += duration
            if merged[-1][1] in GENERIC and duration >= MIN_CHAPTER_SECONDS:
                merged[-1][1] = label
        else:
            merged.append([t, label, float(duration)])
        t += duration
    if merged and merged[-1][2] < MIN_CHAPTER_SECONDS and len(merged) > 1:
        tail = merged.pop()
        merged[-1][2] += tail[2]
    if len(merged) < MIN_CHAPTERS:
        return ""
    merged[0][0] = 0.0
    return "\n".join(f"{timestamp(start)} {label}" for start, label, _ in merged)


def credits(sources: list[dict]) -> str:
    """"Featuring" lines, one per source channel, with a link when known.

    A compilation of other people's clips without credit reads as a reupload,
    to viewers and to the platform's originality checks both.
    """
    seen: set[str] = set()
    lines: list[str] = []
    for s in sources:
        name = str(s.get("channel") or "").strip()
        url = str(s.get("channel_url") or s.get("url") or "").strip()
        key = (name or url).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        lines.append(f"• {name} {url}".strip() if name else f"• {url}")
    return ("Featuring:\n" + "\n".join(lines)) if lines else ""


# ---- grading -----------------------------------------------------------------


@dataclass
class Tip:
    level: str  # good | warn | bad
    message: str


@dataclass
class Report:
    score: int
    tips: list[Tip] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"score": self.score, "tips": [t.__dict__ for t in self.tips]}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2}


def check(
    *,
    title: str,
    description: str = "",
    keywords: list[str] | None = None,
    hashtags: list[str] | None = None,
    long_form: bool = False,
    has_thumbnail: bool = False,
    has_playlist: bool = False,
) -> Report:
    """Grade a YouTube draft out of 100 against what search and the feed reward.

    Each rule is a small, explainable thing a creator can fix, not a mystery
    score: every point lost comes with the sentence that says how to earn it.
    """
    tips: list[Tip] = []
    score = 100
    keywords = [k for k in (keywords or []) if k.strip()]
    tags = unique_hashtags(hashtags or [])

    def lose(points: int, level: str, message: str) -> None:
        nonlocal score
        score -= points
        tips.append(Tip(level, message))

    visible = TITLE_VISIBLE if long_form else SHORTS_TITLE_VISIBLE
    if not title.strip():
        lose(40, "bad", "Add a title.")
    elif len(title) > visible:
        lose(8, "warn", f"The title is cut off after about {visible} characters on phones. Put the hook first.")
    else:
        tips.append(Tip("good", "The whole title shows on phones."))

    main = keywords[0] if keywords else ""
    if not keywords:
        lose(15, "warn", "Add search keywords: the phrases someone would type to find this.")
    elif _words(main) and not (_words(main) & _words(title)):
        lose(10, "warn", f"Use your main keyword (\"{main}\") in the title.")
    else:
        tips.append(Tip("good", "The title uses your main keyword."))

    first_line = description.strip().split("\n", 1)[0][:150]
    if not description.strip():
        lose(15, "warn", "Add a description. Its first line shows in search results.")
    elif main and _words(main) and not (_words(main) & _words(first_line)):
        lose(8, "warn", "Put your main keyword in the description's first sentence.")
    if long_form:
        if len(description) < 200:
            lose(8, "warn", "Long videos rank better with a fuller description: 200+ characters.")
        if not re.search(r"^0:00\b", description, re.M):
            lose(10, "warn", "Add chapters (timestamps starting at 0:00). YouTube indexes them in search.")
        else:
            tips.append(Tip("good", "Chapters are set."))
        if not has_thumbnail:
            lose(10, "warn", "Choose a thumbnail. Long videos are picked by their thumbnail.")
        if not has_playlist:
            lose(4, "warn", "Add it to a playlist, so one video leads viewers into the next.")

    if not tags:
        lose(6, "warn", "Add 3 hashtags. The first three show above the title.")
    elif len(tags) > 15:
        lose(20, "bad", "More than 15 hashtags and YouTube ignores all of them.")
    else:
        tips.append(Tip("good", f"{min(len(tags), 3)} hashtag(s) will show above the title."))

    budget = sum(len(k) for k in keywords)
    if keywords and budget < TAGS_BUDGET // 5:
        lose(4, "warn", "Add a few more keyword variations, including the creator's name.")

    return Report(score=max(0, score), tips=tips)
