"""Monetization-safe text: one linter and one sanitizer for every title,
description and tag list that leaves this app.

YouTube's Partner Program rules (updated for 2027) disqualify spammy metadata:
piles of hashtags, hashtags in titles, keyword stuffing, and copy that reads as
machine-written. The exact thresholds are not published as numbers, so every
one here is a setting (config/settings.yaml, `compliance:`) with a conservative
default, not a claim about what YouTube enforces.

Stdlib only, no model call: the same functions run when copy is generated, when
it is assembled for upload, and when an existing video is audited, so what the
audit flags is exactly what the generators now avoid.

* `check` returns Findings for a draft, without changing it.
* `sanitize` applies the fixes that need no judgement (strip, dedupe, cap) and
  returns the findings it could NOT fix: wording only a rewrite can repair.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path

# ---- settings ---------------------------------------------------------------


@dataclass(frozen=True)
class Rules:
    # Hashtags across title + description together, each counted once.
    max_hashtags: int = 3
    hashtags_in_title: bool = False
    # Hidden tags: how many, and the characters they may use in total.
    max_tags: int = 8
    max_tag_chars: int = 200
    # A word used this many times in a description of this many words or more.
    max_word_repeats: int = 4
    # Share of title+description+tags words taken by the single most repeated one.
    max_word_share: float = 0.12
    max_emoji: int = 2
    # "standard": two machine-sounding signals fail a text. "strict": one does.
    # "off" skips the tone check entirely.
    tone: str = "standard"
    # Per-genre overrides, e.g. {"gaming": {"tone": "strict"}}.
    genre_overrides: dict = field(default_factory=dict)

    def for_genre(self, genre: str = "") -> Rules:
        extra = (self.genre_overrides or {}).get(str(genre or "").lower())
        if not isinstance(extra, dict):
            return self
        known = {k: v for k, v in extra.items() if k in self.__dataclass_fields__ and k != "genre_overrides"}
        return replace(self, **known)


_rules: Rules | None = None

# YouTube category ids, as a genre name for `Rules.for_genre`.
CATEGORY_GENRES = {
    "1": "film", "2": "autos", "10": "music", "15": "pets", "17": "sports", "19": "travel",
    "20": "gaming", "22": "people", "23": "comedy", "24": "entertainment", "25": "news",
    "26": "howto", "27": "education", "28": "tech",
}


def configure(raw: dict | None) -> Rules:
    """Install rules from a settings dict (unknown keys ignored, bad values
    fall back to the default). Returns them."""
    global _rules
    base = Rules()
    raw = raw if isinstance(raw, dict) else {}
    values: dict = {}
    for name, fld in base.__dataclass_fields__.items():
        if name not in raw:
            continue
        default = getattr(base, name)
        try:
            if isinstance(default, bool):
                values[name] = bool(raw[name])
            elif isinstance(default, int):
                values[name] = max(0, int(raw[name]))
            elif isinstance(default, float):
                values[name] = max(0.0, float(raw[name]))
            elif isinstance(default, dict):
                values[name] = dict(raw[name])
            else:
                values[name] = str(raw[name])
        except (TypeError, ValueError):
            continue
    if values.get("tone") not in (None, "standard", "strict", "off"):
        values.pop("tone")
    _rules = replace(base, **values)
    return _rules


def rules(genre: str = "") -> Rules:
    """The installed rules; read from config/settings.yaml the first time."""
    global _rules
    if _rules is None:
        raw: dict = {}
        try:
            import yaml

            path = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
            raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("compliance") or {}
        except Exception:
            raw = {}
        configure(raw)
    return _rules.for_genre(genre) if genre else _rules  # type: ignore[union-attr]


# ---- findings ---------------------------------------------------------------


@dataclass
class Finding:
    code: str
    field: str       # title | description | tags
    message: str
    fixable: bool = False   # sanitize() repairs it without a rewrite

    def as_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "message": self.message, "fixable": self.fixable}


HASHTAG = re.compile(r"(?<![\w&/])#(?!\d+\b)(\w+)", re.UNICODE)   # "#1" is a rank, not a hashtag
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆✅❌]")
_WORD = re.compile(r"[a-z][a-z'’]{2,}")
_STOP = frozenset(
    "the and for with that this from you your are was were has have had but not all can will "
    "its their they them then than into out one two get got just like more when what who how "
    "why him her his she our about over very too also been being does did".split()
)

# Phrases that mark copy as machine-written. Each is a signal, not proof: one
# alone passes in "standard" tone, two fail it.
_MACHINE_PHRASES = tuple(re.compile(p, re.I) for p in (
    r"\bdelve[sd]?\b", r"\bdive (?:deep )?into\b", r"\bgame[- ]chang(?:er|ing)\b",
    r"\bin (?:today'?s|this) (?:fast[- ]paced|digital|ever[- ]evolving)\b",
    r"\bever[- ]evolving\b", r"\btapestry\b", r"\bunleash(?:es|ed)?\b", r"\belevate\b",
    r"\bembark(?:s|ed)? on\b", r"\bunlock(?:s|ed)? the (?:secrets?|power|potential)\b",
    r"\bit'?s (?:important|worth) (?:to note|noting)\b", r"\bin conclusion\b",
    r"\bbuckle up\b", r"\bget ready (?:to|for)\b", r"\bprepare (?:to be|yourself)\b",
    r"\bwill leave you\b", r"\byou won'?t believe\b", r"\bmust[- ]watch\b",
    r"\bjaw[- ]dropping\b", r"\bmind[- ]blowing\b", r"\bnot just [^.,;]{3,40}, (?:but|it'?s)\b",
    r"\bwhether you'?re\b", r"\bin the (?:world|realm) of\b", r"\btestament to\b",
    r"\bseamless(?:ly)?\b", r"\bcutting[- ]edge\b", r"\bsmash (?:that )?(?:like|subscribe)\b",
    r"\bdon'?t forget to (?:like|subscribe)\b", r"\blike,? comment,? (?:and )?subscribe\b",
    r"\bin this (?:short|clip|video)\b", r"\bwatch (?:as|how) [^.]{0,40} (?:unfold|navigate)s?\b",
    r"\bhere'?s (?:the|a) (?:breakdown|rundown)\b", r"\bleaves? (?:us|you|everyone) (?:speechless|stunned)\b",
))
_EM_DASH = re.compile(r"[—–]")


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP]


def hashtags_in(text: str) -> list[str]:
    return [f"#{m}" for m in HASHTAG.findall(text or "")]


def machine_signals(text: str) -> list[str]:
    """What in `text` reads as machine-written, as short human descriptions."""
    text = text or ""
    out = [m.group(0).strip().lower() for p in _MACHINE_PHRASES for m in [p.search(text)] if m]
    dashes = len(_EM_DASH.findall(text))
    if dashes >= 2 or (dashes and len(text) < 120):
        out.append("em dashes")
    if len(re.findall(r"^\s*[-•*✅✔➡▶🔥⭐]\s", text, re.M)) >= 3:
        out.append("bulleted list in a short post")
    if len(re.findall(r"\b\w+, \w+,? and \w+\b", text)) >= 2:
        out.append("repeated lists of three")
    if len(re.findall(r"!", text)) >= 3:
        out.append("exclamation marks")
    return out


def _tone_threshold(r: Rules) -> int:
    return 0 if r.tone == "off" else (1 if r.tone == "strict" else 2)


def check(title: str = "", description: str = "", tags: list[str] | None = None,
          genre: str = "", rules_: Rules | None = None) -> list[Finding]:
    """Everything wrong with this draft under the current rules."""
    r = (rules_ or rules()).for_genre(genre)
    tags = [t for t in (tags or []) if str(t).strip()]
    out: list[Finding] = []

    # -- hashtags
    in_title = hashtags_in(title)
    if in_title and not r.hashtags_in_title:
        out.append(Finding("hashtag-in-title", "title",
                           f"Hashtags in the title ({' '.join(in_title)}) can cost monetization.", True))
    body_tags = hashtags_in(description)
    distinct = list(dict.fromkeys(t.lower() for t in [*in_title, *body_tags]))
    if len(distinct) > r.max_hashtags:
        out.append(Finding("too-many-hashtags", "description",
                           f"{len(distinct)} hashtags; keep it to {r.max_hashtags} at most.", True))
    if len(body_tags) != len({t.lower() for t in body_tags}):
        out.append(Finding("repeated-hashtag", "description", "The same hashtag appears more than once.", True))

    # -- hidden tags
    if len(tags) > r.max_tags:
        out.append(Finding("too-many-tags", "tags", f"{len(tags)} tags; keep it to {r.max_tags} at most.", True))
    if sum(len(t) for t in tags) > r.max_tag_chars:
        out.append(Finding("tag-budget", "tags", f"Tags use more than {r.max_tag_chars} characters.", True))
    lowered = [re.sub(r"\s+", " ", str(t).lstrip("#")).strip().lower() for t in tags]
    if len(lowered) != len(set(lowered)):
        out.append(Finding("duplicate-tags", "tags", "Some tags are repeated.", True))
    title_words = set(_tokens(title))
    echoes = [t for t in lowered if t and set(_tokens(t)) and set(_tokens(t)) <= title_words]
    if echoes:
        out.append(Finding("tags-echo-title", "tags",
                           f"Tags that only repeat the title: {', '.join(echoes[:4])}.", True))
    in_desc = {t.lstrip("#").lower() for t in body_tags}
    echoed_hash = [t for t in lowered if t.replace(" ", "") in in_desc]
    if echoed_hash:
        out.append(Finding("tags-echo-hashtags", "tags",
                           f"Tags that repeat a hashtag: {', '.join(echoed_hash[:4])}.", True))

    # -- keyword stuffing
    desc_plain = HASHTAG.sub(" ", description or "")
    words = _tokens(desc_plain)
    if len(words) >= 12:
        top, n = max(((w, words.count(w)) for w in set(words)), key=lambda x: x[1])
        if n > r.max_word_repeats:
            out.append(Finding("keyword-repeat", "description",
                               f"'{top}' appears {n} times in the description.", False))
    everything = _tokens(f"{title} {desc_plain} {' '.join(lowered)}")
    if len(everything) >= 20:
        top, n = max(((w, everything.count(w)) for w in set(everything)), key=lambda x: x[1])
        if n / len(everything) > r.max_word_share:
            out.append(Finding("keyword-share", "description",
                               f"'{top}' makes up {n * 100 // len(everything)}% of the text.", False))
    if re.search(r"(?:[\w' ]{2,30},\s*){7,}[\w' ]{2,30}", desc_plain):
        out.append(Finding("keyword-list", "description", "A long comma-separated list of keywords.", False))
    if _tokens(title) and len(set(_tokens(title))) < len(_tokens(title)) - 1:
        out.append(Finding("title-repeat", "title", "The title repeats its own words.", False))

    # -- noise
    letters = [c for c in title if c.isalpha()]
    if len(letters) > 8 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        out.append(Finding("title-caps", "title", "The title is mostly capital letters.", False))
    if len(_EMOJI.findall(f"{title}{description}")) > r.max_emoji:
        out.append(Finding("emoji", "description", f"More than {r.max_emoji} emoji.", False))
    if re.search(r"([!?])\1{1,}|[!?]{3,}", f"{title} {description}"):
        out.append(Finding("punctuation", "title", "Stacked !! or ?? punctuation.", False))

    # -- machine tone
    threshold = _tone_threshold(r)
    if threshold:
        for name, text in (("title", title), ("description", description)):
            signals = machine_signals(text)
            if len(signals) >= threshold:
                out.append(Finding("machine-tone", name,
                                   f"Reads as machine-written: {', '.join(signals[:4])}.", False))
    return out


# ---- fixes ------------------------------------------------------------------


@dataclass
class Cleaned:
    title: str
    description: str
    tags: list[str]
    remaining: list[Finding]   # what still needs a rewrite

    @property
    def clean(self) -> bool:
        return not self.remaining


def _strip_title_hashtags(title: str) -> str:
    t = HASHTAG.sub("", title or "")
    t = re.sub(r"\s*\|\s*(?=\||$)", "", t)      # "Title | Channel | " -> "Title | Channel"
    t = re.sub(r"\s{2,}", " ", t).strip(" |-")
    return t


def _fix_hashtag_lines(description: str, limit: int, taken: int = 0) -> str:
    """Keep the first `limit - taken` distinct hashtags, dropping the rest from
    the text. A line left holding nothing is removed."""
    keep = max(0, limit - taken)
    seen: list[str] = []

    def sub(m: re.Match) -> str:
        tag = m.group(1).lower()
        if tag in seen:
            return ""
        if len(seen) < keep:
            seen.append(tag)
            return m.group(0)
        return ""

    text = HASHTAG.sub(sub, description or "")
    lines = [re.sub(r"[ \t]{2,}", " ", ln).rstrip() for ln in text.split("\n")]
    cleaned = [ln for ln, orig in zip(lines, (description or "").split("\n")) if ln.strip() or not orig.strip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()


def fix_tags(tags: list[str], title: str = "", description: str = "", rules_: Rules | None = None) -> list[str]:
    """Hidden tags without repeats, echoes of the title or of a hashtag, within
    the count and character budget. Order is kept: the first ones matter most."""
    r = rules_ or rules()
    title_words = set(_tokens(title))
    hashed = {t.lstrip("#").lower() for t in hashtags_in(description)}
    out: list[str] = []
    used = 0
    for raw in tags or []:
        tag = re.sub(r"\s+", " ", str(raw).lstrip("#")).strip(" ,")
        key = tag.lower()
        if not tag or key in {t.lower() for t in out} or key.replace(" ", "") in hashed:
            continue
        toks = set(_tokens(tag))
        if toks and toks <= title_words:
            continue
        if len(out) >= r.max_tags or used + len(tag) > r.max_tag_chars:
            continue
        out.append(tag)
        used += len(tag)
    return out


def sanitize(title: str = "", description: str = "", tags: list[str] | None = None,
             genre: str = "", rules_: Rules | None = None) -> Cleaned:
    """Apply every fix that needs no judgement, then report what is left.

    Never invents text. A title with nothing left after the hashtags come off
    is returned as it was rather than emptied."""
    r = (rules_ or rules()).for_genre(genre)
    new_title = title or ""
    if not r.hashtags_in_title:
        stripped = _strip_title_hashtags(new_title)
        new_title = stripped or new_title
    taken = len({t.lower() for t in hashtags_in(new_title)})
    new_desc = _fix_hashtag_lines(description or "", r.max_hashtags, taken)
    new_tags = fix_tags(list(tags or []), new_title, new_desc, r)
    remaining = [f for f in check(new_title, new_desc, new_tags, rules_=r) if not f.fixable]
    return Cleaned(new_title, new_desc, new_tags, remaining)


def tone_problems(title: str = "", description: str = "", genre: str = "") -> list[Finding]:
    """Only the machine-tone findings: what a rewrite pass is asked to fix."""
    return [f for f in check(title, description, [], genre) if f.code == "machine-tone"]


_WEIGHT = {"machine-tone": 25, "keyword-repeat": 15, "keyword-share": 15, "keyword-list": 15,
           "hashtag-in-title": 20, "too-many-hashtags": 20}


def cost(finding: Finding) -> int:
    """Points one finding takes off a 0-100 score."""
    return _WEIGHT.get(finding.code, 8)


def score(findings: list[Finding]) -> int:
    """0-100, for sorting an audit: every finding costs points."""
    return max(0, 100 - sum(cost(f) for f in findings))
