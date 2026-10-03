"""The words on a thumbnail, taken from what was said.

The biggest text on a thumbnail is a QUOTATION from the clip's peak: the most
charged line actually spoken ("SHOOT IT, SHOOT IT, SHOOT IT!"), or the two
lines of an exchange ("IS IT BLOWED UP?" / "I GOT HIM!"). A quote cannot be
inaccurate, and it is specific to this clip in a way that a summary written by a
model ("CRAZY MOMENT") is not.

The small label (the badge) is never the model's to invent. It is one of the
channel's always-on hashtags without its "#", so it is the channel's own word,
and when there are none, the clip's own hashtag, then a keyword, then the
channel name. Vague genre words ("GAMING", "EPIC") cannot get in.

A model may be asked to RANK the quotes (by number, so it can only choose what
was said) and to name a mood and an emoji, but everything works without one:
the ranking below is the fallback and is what a bad answer falls back to.
"""

from __future__ import annotations

import json
import re

from analysis import peaks
from analysis.metadata import _GENERIC_TAGS, _bare_tag, pick_always_on

MOODS = ("hype", "shock", "funny", "serious", "wholesome")
MOOD_EMOJI = {"hype": "🔥", "shock": "😱", "funny": "😂", "serious": "", "wholesome": ""}
EMOJIS = ("😱", "🔥", "😂", "🤯", "💀", "👀", "❗", "❓", "💯", "🏆", "😡", "🥶")

MAX_HEADLINE_WORDS = 6
MAX_KICKER_WORDS = 5
MAX_BADGE_CHARS = 16
KICKER_MIN_SCORE = 3.0        # a supporting quote must be worth reading on its own

CHOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "mood": {"type": "string"},
        "emoji": {"type": "string"},
    },
    "required": ["order", "mood", "emoji"],
    "additionalProperties": False,
}


def pick_badge(always_on, hashtags=(), keywords=(), channel: str = "", text: str = "", index: int = 0) -> str:
    """The badge word, no '#', or "". Always-on tag first (the one this clip is
    about, else their turn), then the clip's own hashtags, then a keyword, then
    the channel. Must be short: it sits in a small box."""
    def fits(w: str) -> bool:
        return 0 < len(w) <= MAX_BADGE_CHARS and len(w.split()) <= 2

    tag = pick_always_on(always_on, text, index)
    if tag and fits(tag):
        return tag
    for h in hashtags or []:
        bare = _bare_tag(h)
        if bare and bare not in _GENERIC_TAGS and fits(bare):
            return bare
    for k in keywords or []:
        word = re.sub(r"[#<>\"]", "", str(k)).strip().lower()
        if word and word not in _GENERIC_TAGS and fits(word):
            return word
    channel = (channel or "").strip()
    return channel if fits(channel) else ""


def guess_mood(texts: list[str]) -> str:
    """A tone for the design from the words, when no model has named one."""
    blob = " ".join(texts).lower()
    if re.search(r"\b(ha){2,}|\blol\b|\blmao\b|funny|hilarious|dying", blob):
        return "funny"
    bangs = blob.count("!")
    if bangs >= 2 or re.search(r"oh my god|no way|holy|let's go|lets go|insane", blob):
        return "hype"
    if "?" in blob:
        return "shock"
    return "hype"


def choice_prompt(candidates: list[dict]) -> str:
    lines = "\n".join(f'{i}: "{c["line"]}"' for i, c in enumerate(candidates))
    return (
        "Below are lines spoken in one short video clip. Rank them from the one that is the PEAK of "
        "the clip (the most emotional or exciting thing said: the strongest reaction, the biggest "
        "payoff, the line a viewer would remember) to the least.\n"
        f"Then name the clip's mood, one of {', '.join(MOODS)}, and one emoji from "
        f"{' '.join(EMOJIS)} that suits it (or an empty string).\n\n"
        f"LINES:\n{lines}\n\n"
        'Respond with ONLY valid JSON listing every index once: {"order": [2, 0, 1], "mood": "hype", "emoji": "🔥"}'
    )


def parse_choice(raw: str, n: int) -> dict:
    """{'order': [...] (complete, or []), 'mood': str|'', 'emoji': str|''}."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    lo, hi = text.find("{"), text.rfind("}")
    if lo == -1 or hi <= lo:
        return {"order": [], "mood": "", "emoji": ""}
    try:
        data = json.loads(text[lo: hi + 1])
    except json.JSONDecodeError:
        return {"order": [], "mood": "", "emoji": ""}
    seen: list[int] = []
    for i in data.get("order") or []:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in seen:
            seen.append(i)
    order = seen + [i for i in range(n) if i not in seen] if seen else []
    mood = str(data.get("mood") or "").strip().lower()
    emoji = str(data.get("emoji") or "").strip()
    return {"order": order, "mood": mood if mood in MOODS else "", "emoji": emoji if emoji in EMOJIS else ""}


def _clean(text: str) -> str:
    return re.sub(r"[<>\"#]", "", " ".join((text or "").split())).strip()


def quote_sets(segments, start: float, end: float, *, badge: str = "", order: list[int] | None = None,
               mood: str = "", emoji: str = "", candidates: list[dict] | None = None) -> list[dict]:
    """Up to three complete text sets, best first, all quoted from the clip:

      1. the peak line, with the next-best line as its supporting text;
      2. a two-sided exchange (two lines close together), when there is one;
      3. a different single line.

    Empty when the clip has no usable speech. `candidates` is the list the
    model ranked (same order as `order`); pass what `peak_candidates` returned.
    """
    picks = list(candidates) if candidates is not None else peaks.peak(segments, start, end, 8)
    if not picks:
        return []
    if order:
        picks = [picks[i] for i in order if 0 <= i < len(picks)] or picks
    mood = mood if mood in MOODS else guess_mood([p["line"] for p in picks[:3]])
    emoji = emoji if emoji in EMOJIS else MOOD_EMOJI.get(mood, "")

    def make(headline: str, kicker: str = "", exchange: bool = False) -> dict:
        return {"headline": _clean(headline), "kicker": _clean(kicker), "badge": badge,
                "emoji": emoji, "mood": mood, "exchange": exchange}

    sets: list[dict] = []
    top = picks[0]
    support = next((p for p in picks[1:] if p["score"] >= KICKER_MIN_SCORE
                    and not _overlaps(p["text"], top["text"])), None)
    sets.append(make(peaks.trim_snippet(top["text"], MAX_HEADLINE_WORDS),
                     peaks.trim_snippet(support["text"], MAX_KICKER_WORDS) if support else ""))

    lines = peaks.split_lines(segments, start, end)
    used = {_key(sets[0]["headline"]), _key(sets[0]["kicker"])}
    for pair in peaks.exchanges(lines):          # the best one that does not repeat the first set
        a = peaks.trim_snippet(pair[0].text, MAX_KICKER_WORDS)
        b = peaks.trim_snippet(pair[1].text, MAX_KICKER_WORDS)
        if a and b and not ({_key(a), _key(b)} <= used):     # sharing one line with set 1 is fine: it reads differently
            sets.append(make(a, b, exchange=True))
            used |= {_key(a), _key(b)}
            break
    for p in picks[1:]:
        text = peaks.trim_snippet(p["text"], MAX_HEADLINE_WORDS)
        if text and _key(text) not in used and not any(_overlaps(text, u) for u in (top["text"],)):
            sets.append(make(text))
            break
    return sets[:3]


def _key(text: str) -> str:
    return re.sub(r"\W", "", (text or "").lower())


def _overlaps(a: str, b: str) -> bool:
    ka, kb = _key(a), _key(b)
    return bool(ka and kb and (ka in kb or kb in ka))
