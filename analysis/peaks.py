"""The most charged thing said in a clip, quoted exactly.

A thumbnail's biggest words, and a title when the model cannot be trusted with
one, are best taken from what was actually SAID at the peak of the clip: a real
line is specific ("HE'S IN THE HUMVEE") where a summary is vague ("CRAZY
MOMENT"), and it cannot be inaccurate because it is a quotation.

Everything here is deterministic and stdlib-only. That is deliberate: it is the
floor under the model. The model is asked to CHOOSE among these lines (by number,
so it can only ever pick something that was said); with no model, or a model
that answers nonsense, the same lines are ranked by the scorer below and the
result is still a real quotation.

There is no speaker information in a transcript, so a "two-sided conversation"
is two consecutive lines close together in time, at least one of them a
question or an exclamation, which is what an exchange looks like on the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What a person says at a peak. Whole words or phrases, matched case-blind.
INTENSE = (
    "oh my god", "oh my gosh", "no way", "what the", "holy", "let's go", "lets go", "get out",
    "look out", "watch out", "shut up", "i can't believe", "are you kidding", "are you serious",
    "you're kidding", "did you see", "did you just", "wait what", "hold on", "come on",
    "insane", "crazy", "unreal", "incredible", "unbelievable", "impossible", "clutch",
    "behind you", "he's behind", "they're behind", "run", "help", "please", "no no", "yes yes",
    "haha", "lol", "bro", "dude", "yo", "what", "why", "how", "wow", "omg", "oh no", "oh god",
    "finally", "got him", "got em", "i did it", "we did it", "you did it", "nice", "gg",
)
# Words that keep a line off a thumbnail or a title (platform advertiser rules,
# and plain taste). Substring-matched on word boundaries.
_BLOCKED = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|cunt|nigg\w*|fag\w*|retard\w*|rape\w*|kill yourself|kys|dick\w*|"
    r"pussy|whore|slut|asshole|cock)\b", re.IGNORECASE)
_FILLER_START = re.compile(r"^(?:(?:so|and|but|like|um+|uh+|well|yeah|okay|ok|right|then|because)\b[\s,]*)+", re.IGNORECASE)

MAX_WORDS = 6


@dataclass
class Line:
    start: float
    end: float
    text: str
    score: float = 0.0


def is_clean(text: str) -> bool:
    return _BLOCKED.search(text or "") is None


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", (text or "").strip()) if w]


def emotion(text: str) -> float:
    """How charged a line is, from the words on the page. Higher is more."""
    t = (text or "").strip()
    if not t:
        return 0.0
    words = _words(t)
    n = len(words)
    low = t.lower()
    score = 0.0
    score += 2.0 * min(t.count("!"), 3)
    score += 1.0 * min(t.count("?"), 2)
    score += 1.5 * sum(1 for w in words if len(w) > 2 and w.isupper() and w.isalpha())
    # A word said again and again ("shoot it, shoot it, shoot it") is a peak.
    stripped = [re.sub(r"\W", "", w.lower()) for w in words]
    repeats = sum(1 for a, b in zip(stripped, stripped[1:]) if a and a == b)
    repeats += sum(1 for a, b in zip(stripped, stripped[2:]) if a and a == b) // 2
    score += 1.5 * min(repeats, 3)
    for phrase in INTENSE:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", low):
            score += 1.5
    # Short and punchy reads at thumbnail size; a paragraph does not.
    if 2 <= n <= MAX_WORDS + 1:
        score += 2.0
    elif n > 14:
        score -= 0.25 * (n - 14)
    elif n == 1:
        score -= 1.0
    return max(0.0, score)


def split_lines(segments, start: float, end: float) -> list[Line]:
    """The clip's speech as short lines: each transcript segment cut into its
    sentences, times shared out by length, only what falls inside the clip."""
    out: list[Line] = []
    for seg in segments:
        s, e = float(seg.start), float(seg.end)
        if e <= start or s >= end:
            continue
        text = " ".join(str(seg.text).split())
        if not text:
            continue
        parts = [p.strip() for p in re.findall(r"[^.!?…]+[.!?…]*", text) if re.search(r"\w", p)]
        total = sum(len(p) for p in parts) or 1
        at = s
        for p in parts:
            span = (e - s) * len(p) / total
            out.append(Line(at, at + span, p))
            at += span
    return [ln for ln in out if len(_words(ln.text)) >= 1]


def _norm(word: str) -> str:
    return re.sub(r"\W", "", word.lower())


def _collapse_repeats(words: list[str]) -> list[str]:
    """A phrase said over and over ("shoot it, shoot it, shoot it, shoot it!")
    is a peak, but it only needs saying twice on a thumbnail. The last copy
    keeps the line's ending punctuation."""
    for n in (1, 2, 3):
        i = 0
        while i + 3 * n <= len(words):
            unit = [_norm(w) for w in words[i: i + n]]
            if not all(unit):
                i += 1
                continue
            reps = 1
            while i + (reps + 1) * n <= len(words) and [_norm(w) for w in words[i + reps * n: i + (reps + 1) * n]] == unit:
                reps += 1
            if reps >= 3:
                keep = words[i: i + 2 * n]
                keep[-1] = words[i + reps * n - 1]          # the final copy's punctuation
                words = words[:i] + keep + words[i + reps * n:]
                i += 2 * n
            else:
                i += 1
    return words


def trim_snippet(text: str, max_words: int = MAX_WORDS) -> str:
    """The strongest run of about `max_words` words in this line, as said:
    leading filler dropped (unless that would leave nothing: "Yeah, yeah,
    yeah." is the line), a repeated phrase said twice not five times, and a cut,
    when one is needed, made at a clause so it never starts mid-phrase."""
    t = " ".join((text or "").split())
    stripped = _FILLER_START.sub("", t).strip()
    if re.search(r"\w", stripped):
        t = stripped
    words = _words(t)
    limit = max_words + 1                       # a line one word over reads fine; shrink-to-fit handles it
    if len(words) > limit:
        words = _collapse_repeats(words)
    if len(words) > limit:
        starts = [0] + [i + 1 for i, w in enumerate(words[:-1]) if re.search(r"[,;:.!?…—-]$", w)]
        if len(starts) < 2:                     # no clauses to cut at: any window will do, the strongest wins
            starts = list(range(0, len(words) - min(3, max_words) + 1))
        best, best_score = None, -1.0
        for i in starts:
            window = words[i: i + max_words]
            if len(window) < min(3, max_words):
                continue
            s = emotion(" ".join(window))
            if i + max_words >= len(words):
                s += 1.5                        # runs to the end of the line: a finished thought
            if i == 0:
                s += 0.5
            if s > best_score:
                best, best_score = i, s
        if best is None:                        # no clause boundary gives a usable window
            best = max(0, len(words) - max_words)
        words = words[best: best + max_words]
    return " ".join(words).strip(" ,;:-")


def rank(lines: list[Line], clean_only: bool = True) -> list[Line]:
    """Lines best first, each with its score set."""
    pool = [ln for ln in lines if (is_clean(ln.text) or not clean_only)]
    for ln in pool:
        ln.score = emotion(ln.text)
    return sorted(pool, key=lambda ln: (ln.score, -len(ln.text)), reverse=True)


def exchanges(lines: list[Line], max_gap: float = 2.5) -> list[tuple[Line, Line]]:
    """Every pair of consecutive lines that reads as one person answering
    another, best first. Needs a question or an exclamation in it, both short,
    close in time, and different, so a person repeating themselves is not
    called a conversation."""
    found: list[tuple[float, Line, Line]] = []
    for a, b in zip(lines, lines[1:]):
        if not (is_clean(a.text) and is_clean(b.text)):
            continue
        wa, wb = len(_words(a.text)), len(_words(b.text))
        if not (1 <= wa <= 10 and 1 <= wb <= 10) or b.start - a.end > max_gap:
            continue
        if re.sub(r"\W", "", a.text.lower()) == re.sub(r"\W", "", b.text.lower()):
            continue
        if not any(ch in a.text + b.text for ch in "?!"):
            continue
        found.append((emotion(a.text) + emotion(b.text) + (2.0 if "?" in a.text else 0.0), a, b))
    return [(a, b) for _, a, b in sorted(found, key=lambda t: t[0], reverse=True)]


def best_exchange(lines: list[Line], max_gap: float = 2.5) -> tuple[Line, Line] | None:
    """The strongest exchange, or None."""
    found = exchanges(lines, max_gap)
    return found[0] if found else None


def peak(segments, start: float, end: float, count: int = 3) -> list[dict]:
    """The clip's strongest quotable moments, best first:
    [{'text': trimmed quote, 'line': full line, 'score': n, 't': seconds}].
    Distinct: a second pick never repeats the first's words."""
    picks: list[dict] = []
    seen: set[str] = set()
    for ln in rank(split_lines(segments, start, end)):
        quote = trim_snippet(ln.text)
        key = re.sub(r"\W", "", quote.lower())
        if not key or key in seen or any(key in k or k in key for k in seen):
            continue
        seen.add(key)
        picks.append({"text": quote, "line": ln.text, "score": round(ln.score, 1), "t": round(ln.start, 2)})
        if len(picks) >= count:
            break
    return picks
