"""LLM-generated upload metadata: title, description, hashtags, and the
search side: keywords, a first comment and alternative titles.

Never fails the pipeline: any LLM misbehavior falls back to metadata
derived from the clip's hook and the source video title.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core.models import ClipCandidate, Segment
from llm.base import LLMBackend, generate_json
from publish import compliance

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata.txt"

MAX_TITLE_LEN = 95  # leave headroom under YouTube's 100-char limit

# The shapes metadata.txt and metadata_batch.txt ask for. A cloud model on the
# user's key is held to them (llm.base.generate_json); _parse still checks.
_METADATA_FIELDS = {
    "title": {"type": "string"},
    "description": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "keywords": {"type": "array", "items": {"type": "string"}},
    "first_comment": {"type": "string"},
    "alt_titles": {"type": "array", "items": {"type": "string"}},
}
_REQUIRED = ["title", "description", "hashtags", "keywords", "first_comment", "alt_titles"]
METADATA_SCHEMA = {
    "type": "object",
    "properties": _METADATA_FIELDS,
    "required": _REQUIRED,
    "additionalProperties": False,
}
BATCH_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"index": {"type": "integer"}, **_METADATA_FIELDS},
        "required": ["index", *_REQUIRED],
        "additionalProperties": False,
    }}},
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass
class ClipMetadata:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    # Search phrases for YouTube's tags field; never shown to viewers.
    keywords: list[str] = field(default_factory=list)
    # Posted as the first comment where the platform allows it: a question
    # that gets replies, which is what the feed ranks on.
    first_comment: str = ""
    # Other titles worth trying, for the editor to offer.
    alt_titles: list[str] = field(default_factory=list)


def generate_metadata(
    candidate: ClipCandidate,
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
    channel: str = "",
    always_on: list[str] | tuple[str, ...] = (),
    audience: str = "",
    creator_context: str = "",
    avoid: list[str] | tuple[str, ...] = (),
    must_generate: bool = False,
) -> ClipMetadata:
    """Metadata for one clip.

    avoid: wording already on offer (the current title, description, keywords),
    which the model is told not to repeat: how a "regenerate" gets something
    new. must_generate: raise instead of falling back to hook-based metadata,
    so a caller that asked for new copy is not handed the fallback as if the
    model had written it."""
    clip_text = " ".join(
        s.text for s in segments if s.end > candidate.start and s.start < candidate.end
    )
    fallback = _fallback(candidate, video_title, channel)
    if not clip_text:
        if must_generate:
            raise RuntimeError("There is no speech in this clip to write from.")
        return _anchored(fallback, channel, always_on, clip_text)

    template = PROMPT_PATH.read_text(encoding="utf-8")
    if creator_context:
        template = template.replace("CLIP TRANSCRIPT:", _CONTEXT_HEAD + creator_context + "\n\nCLIP TRANSCRIPT:")
    used = [str(a).strip() for a in avoid if str(a or "").strip()]
    if used:
        template = template.replace(
            "CLIP TRANSCRIPT:",
            "ALREADY USED (write something clearly different: another angle, other words):\n"
            + "\n".join(f"- {u[:300]}" for u in used) + "\n\nCLIP TRANSCRIPT:",
        )
    prompt = (
        template
        .replace("{voice}", voice_rules(channel, audience))
        .replace("{video_title}", video_title)
        .replace("{channel}", channel or "(unknown)")
        .replace("{always_on}", _tags_line(always_on))
        .replace("{clip_text}", budget_text(clip_text))
    )
    try:
        raw = generate_json(llm, prompt, METADATA_SCHEMA)
        parsed = _parse(raw)
    except Exception:
        parsed = None
    if parsed is None:
        if must_generate:
            raise RuntimeError("The AI model did not give usable metadata. Try again.")
        return _anchored(fallback, channel, always_on, clip_text)

    meta = _from_parsed(parsed, fallback)
    meta = _checked(meta, candidate, segments, clip_text, llm, channel, always_on, [video_title], audience=audience)
    _unvoiced(meta, llm, channel, audience)
    _naturalized(meta, llm, channel, audience, fallback.title, clip_text)
    return _anchored(meta, channel, always_on, clip_text)


BATCH_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "metadata_batch.txt"


def generate_metadata_batch(
    candidates: list[ClipCandidate],
    segments: list[Segment],
    video_title: str,
    llm: LLMBackend,
    batch_size: int = 8,
    creator_context: str = "",
    channel: str = "",
    always_on: list[str] | tuple[str, ...] = (),
    audience: str = "",
) -> list[ClipMetadata]:
    """Metadata for ALL clips in a few LLM calls instead of one per clip —
    on a long stream this cuts dozens of model calls from the analysis time.
    Any clip the model skips or mangles falls back to hook-based metadata.
    creator_context (optional): learned facts about the creator — series
    names, running jokes, collaborators — for more accurate titles/hashtags.
    audience (optional): who the connected channel's viewers are
    (analysis.audience), so the wording can be theirs."""
    results: list[ClipMetadata] = [_fallback(c, video_title, channel) for c in candidates]
    template = BATCH_PROMPT_PATH.read_text(encoding="utf-8")
    if creator_context:
        template = template.replace("{clips}", _CONTEXT_HEAD + creator_context + "\n\n{clips}")
    template = template.replace("{voice}", voice_rules(channel, audience))

    texts = [
        " ".join(s.text for s in segments if s.end > c.start and s.start < c.end)
        for c in candidates
    ]
    for base in range(0, len(candidates), batch_size):
        batch = candidates[base : base + batch_size]
        blocks = []
        for i, c in enumerate(batch):
            # The whole clip, not its first 900 characters: a clip is 30-60
            # seconds of speech, and cutting it there hid the payoff, which is
            # what the title has to be about. A long one shows its start and end.
            text = budget_text(texts[base + i])
            blocks.append(f"CLIP {i}:\n{text or '(no speech)'}")
        prompt = (
            template.replace("{video_title}", video_title)
            .replace("{channel}", channel or "(unknown)")
            .replace("{always_on}", _tags_line(always_on))
            .replace("{count}", str(len(batch)))
            .replace("{clips}", "\n\n".join(blocks))
        )
        try:
            data = _parse(generate_json(llm, prompt, BATCH_SCHEMA))
        except Exception:
            data = None
        if not data or not isinstance(data.get("items"), list):
            continue  # whole batch falls back
        for item in data["items"]:
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            results[base + idx] = _from_parsed(item, results[base + idx])

    # What the model wrote is checked against what was said, and the anchors
    # (the source channel, one of the always-on hashtags) are put on in code,
    # so they are there whatever the model did or did not do with the prompt.
    repairs = 0
    for i, c in enumerate(candidates):
        meta = results[i]
        bad = _ungrounded(meta.title, texts[i], channel, [video_title], meta) or first_person(meta.title)
        # Rewriting costs a model call, so one video only gets so many; past
        # that a bad title is replaced by the clip's own strongest quote.
        ask = bool(bad) and repairs < MAX_REPAIRS
        repairs += 1 if ask else 0
        meta = _checked(meta, c, segments, texts[i], llm, channel, always_on, [video_title],
                        ask_model=ask, audience=audience)
        ask = repairs < MAX_REPAIRS
        repairs += 1 if _unvoiced(meta, llm, channel, audience, ask_model=ask) else 0
        ask = repairs < MAX_REPAIRS
        repairs += 1 if _naturalized(meta, llm, channel, audience, _fallback(c, video_title, channel).title,
                                     texts[i], ask_model=ask) else 0
        results[i] = _anchored(meta, channel, always_on, texts[i], i)
    return results


def _from_parsed(data: dict, fallback: ClipMetadata) -> ClipMetadata:
    """The model's answer, field by field, with the fallback wherever it is
    missing or unusable. The search fields are optional: a small local model
    that writes only the first three still yields a usable clip."""
    title = _clean_title(data.get("title", "")) or fallback.title
    alts = [
        t for t in (_clean_title(a) for a in (data.get("alt_titles") or []) if isinstance(a, str))
        if t and t.casefold() != title.casefold()
    ]
    return ClipMetadata(
        title=title,
        description=str(data.get("description", "")).strip() or fallback.description,
        hashtags=_clean_hashtags(data.get("hashtags", [])) or fallback.hashtags,
        keywords=clean_keywords(data.get("keywords")),
        first_comment=_clean_comment(data.get("first_comment")),
        alt_titles=list(dict.fromkeys(alts))[:3],
    )


COMMENT_SCHEMA = {
    "type": "object",
    "properties": {"first_comment": {"type": "string"}},
    "required": ["first_comment"],
    "additionalProperties": False,
}


def suggest_first_comment(*, title: str, description: str, content: str, llm,
                          channel: str = "", audience: str = "", creator_context: str = "") -> str:
    """A first comment written for one video: a question about what happens
    in it, the kind viewers answer. "" when the model gives nothing usable.

    Written to the viewers, about the source creator (`channel`), in the
    audience's own register; a comment that still speaks as "I" is asked for
    again once, then dropped, so the standing comment is used instead."""
    context = (_CONTEXT_HEAD + creator_context + "\n\n") if creator_context else ""
    prompt = (
        "Write the first comment the channel will post under this video, following the "
        "FIRST COMMENT rules below exactly. Short and witty, about one specific thing that "
        "happens in the video.\n\n"
        f"{voice_rules(channel, audience)}\n\n{context}"
        f"TITLE: {title}\nDESCRIPTION: {description}\n\nWHAT HAPPENS:\n{content[:2500]}\n\n"
        'Respond with ONLY valid JSON: {"first_comment": "..."}'
    )
    from llm.base import generate_json

    comment = ""
    for _ in range(2):
        parsed = _parse(generate_json(llm, prompt, COMMENT_SCHEMA))
        comment = _clean_comment((parsed or {}).get("first_comment"))
        if not first_person(comment):
            return comment
        prompt += f'\n\nYour last answer, "{comment}", spoke as "I". Write about {channel or "the creator"} in the third person.'
    return ""


def clean_keywords(raw) -> list[str]:
    """Search phrases: plain words, no #, no duplicates, within the hidden-tag
    limit (compliance `max_tags`)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        phrase = re.sub(r"\s+", " ", re.sub(r"[#<>\"]", "", str(entry))).strip(" ,.").lower()
        if phrase and len(phrase) <= 60 and phrase not in out:
            out.append(phrase)
    return out[: compliance.rules().max_tags]


def _clean_comment(raw) -> str:
    text = re.sub(r"[<>]", "", str(raw or "")).strip().strip('"')
    return text[:300]


# ---- voice: whose words these are, and who they are for ---------------------------------

_CONTEXT_HEAD = ("CREATOR CONTEXT (background knowledge — use for accuracy when relevant,"
                 " never invent beyond it):\n")

# A clip's transcript is the creator speaking, so everything lifted from it is
# first person. The post is NOT the creator speaking: it is a channel writing
# about them. "I", "me", "my" and "mine" are what give that away. "we" and "us"
# are left alone: a comment to the community ("can we talk about...") is natural.
_FIRST_PERSON = re.compile(r"\b(?:I|I['’](?:m|ve|ll|d)|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself)\b")
_QUOTED = re.compile(r'"[^"]*"|“[^”]*”')


def first_person(text: str) -> bool:
    """True when `text` speaks as "I". What is inside quotation marks does not
    count: attributing a quote to the creator is exactly the right way to
    keep their words."""
    return bool(_FIRST_PERSON.search(_QUOTED.sub(" ", str(text or ""))))


def voice_rules(channel: str = "", audience: str = "") -> str:
    """The voice section every copy prompt shares: third person about the
    creator, spoken to the viewers, in the way this audience writes.

    One definition so a title, a description, a first comment and a repair
    cannot drift into different voices."""
    who = (channel or "").strip() or "the creator"
    lines = [
        "VOICE AND AUDIENCE:",
        f'- This is a post by a channel ABOUT {who}, not by {who}. The transcript is {who} '
        f'speaking, so it is full of "I", "me" and "my": never carry those into the title, '
        f'description or first_comment. "I got him" becomes "{who} gets him". Talk about '
        f'{who} by name (or "they"), and to the viewer as "you".',
        f"- Never guess {who}'s gender: use the name or \"they\", never he or she.",
        (f"- {audience.strip()} Write for them."
         if (audience or "").strip()
         else "- No audience data yet: take the register from how the creator talks in the clip."),
        "- Write like a person describing a clip to a friend, not like a press release or an "
        "assistant: plain, specific words, normal spelling and punctuation, short sentences. Take "
        "the clip's own vocabulary where it fits, but never imitate typos or dropped apostrophes "
        "and never force slang. Match the energy of the clip. Spell the way its biggest country "
        "does (color or colour).",
        "- Keep it natural: at most one slang term per field, and only if the clip itself sounds "
        "like that. Slang changes how a thing is said, never what happened: every fact still has "
        "to come from the clip.",
        "- Avoid stock phrases that read as machine-written (dive into, game-changer, must-watch, "
        "you won't believe, buckle up, whether you're...), em dashes, exclamation marks, and "
        "hashtags in the title or description text.",
        comment_rules(who),
    ]
    return "\n".join(lines)


def comment_rules(who: str = "the creator") -> str:
    """How the first comment is written: short, dry, human. Piling on slang
    and hype is what made earlier ones read as a bot, so this asks for less."""
    return "\n".join([
        "FIRST COMMENT (first_comment) - what a funny viewer would type, not a brand:",
        "- 3 to 10 words. One line. Lowercase is fine, no ending period unless it helps the joke.",
        "- Witty and dry, one specific detail from the clip: an understated reaction, a deadpan "
        "observation, or a tiny question. Like \"the confidence before that was unreal\", "
        f"\"{who} really said that with a straight face\", \"bro had a plan\".",
        "- Plain words. Skip slang unless it is exactly how the clip sounds; never force it. "
        "No emoji, or one at most. No exclamation marks.",
        "- Never: asking viewers to comment, \"what do you think\", \"who else\", \"thoughts\", "
        "\"favorite part\", hype words (amazing, epic, insane), or explaining the joke.",
        "- Only what the clip shows. Do not invent an outcome, score or name.",
    ])


def _unvoiced(meta: ClipMetadata, llm, channel: str, audience: str, ask_model: bool = True) -> bool:
    """Make the description and first comment third person, in place.

    A field that speaks as "I" is rewritten once by the model; if that misses
    (or no model call is left) the field is dropped rather than shipped: an
    empty description is a normal state, and an empty first comment means the
    standing one is used. Titles are handled by `_checked`. True when a model
    call was made."""
    meta.alt_titles = [t for t in meta.alt_titles if not first_person(t)]
    bad = {k: getattr(meta, k) for k in ("description", "first_comment") if first_person(getattr(meta, k))}
    if not bad:
        return False
    fixed: dict = {}
    asked = False
    if ask_model:
        asked = True
        try:
            prompt = (
                f"Rewrite these so they are about {channel or 'the creator'} in the third person. "
                "Same facts, same length, nothing added.\n\n"
                f"{voice_rules(channel, audience)}\n\n"
                + "\n".join(f"{k.upper()}: {v}" for k, v in bad.items())
                + '\n\nRespond with ONLY valid JSON: {"description": "...", "first_comment": "..."}'
            )
            fixed = _parse(generate_json(llm, prompt, {
                "type": "object",
                "properties": {"description": {"type": "string"}, "first_comment": {"type": "string"}},
                "required": ["description", "first_comment"], "additionalProperties": False,
            })) or {}
        except Exception:
            fixed = {}
    for key in bad:
        text = str(fixed.get(key) or "").strip()
        text = _clean_comment(text) if key == "first_comment" else text
        setattr(meta, key, text if text and not first_person(text) else "")
    return asked


def _naturalized(meta: ClipMetadata, llm, channel: str, audience: str, fallback_title: str = "",
                 text: str = "", ask_model: bool = True) -> bool:
    """Take the machine-sounding wording out of the title, description and
    first comment, in place (publish/compliance.py has the signals).

    A title or description that trips the check is rewritten once with the
    problem named. If that still trips it (or no model call is left) a title
    becomes the clip's own hook and a description or first comment is dropped:
    an empty description is a normal state, a stock-phrase one is not. The
    rewrite is held to the same accuracy and voice checks as any title. True
    when a model call was made."""
    meta.alt_titles = [t for t in meta.alt_titles if not compliance.machine_signals(t)]
    if compliance.machine_signals(meta.first_comment):
        meta.first_comment = ""
    bad = {f.field: f.message for f in compliance.tone_problems(meta.title, meta.description)}
    if not bad:
        return False
    fixed: dict = {}
    asked = False
    if ask_model:
        asked = True
        try:
            fields = {"title": meta.title, "description": meta.description}
            prompt = (
                "Rewrite these so they read like a person wrote them: plain, specific words, "
                "no stock phrases, no em dashes, no hashtags, no exclamation marks. Same facts, "
                "same length or shorter, nothing added.\n"
                + "".join(f"- The {k} {v}\n" for k, v in bad.items())
                + f"\n{voice_rules(channel, audience)}\n\n"
                + "\n".join(f"{k.upper()}: {fields[k]}" for k in bad)
                + '\n\nRespond with ONLY valid JSON: {"title": "...", "description": "..."}'
                + "\n(Copy a field unchanged if it was not listed above.)"
            )
            fixed = _parse(generate_json(llm, prompt, {
                "type": "object",
                "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
                "required": ["title", "description"], "additionalProperties": False,
            })) or {}
        except Exception:
            fixed = {}
    if "title" in bad:
        new = _clean_title(fixed.get("title") or "")
        ok = (new and not compliance.machine_signals(new) and not first_person(new)
              and not _ungrounded(new, text, channel, [], meta))
        meta.title = new if ok else (fallback_title or meta.title)
    if "description" in bad:
        new = str(fixed.get("description") or "").strip()
        meta.description = new if new and not compliance.machine_signals(new) and not first_person(new) else ""
    return asked


def _fallback(candidate: ClipCandidate, video_title: str, channel: str = "") -> ClipMetadata:
    """What a clip carries when the model could not write its metadata.

    No description and no hashtags. This used to be "Clip from: <video title>"
    with #clips, and every clip of a video whose metadata failed got that same
    caption: seven TikTok posts went out reading "Clip from: my brother exposes
    me… #clips" and were flagged as unoriginal content, which is exactly what a
    caption announcing a repost invites. The title still comes from the hook,
    and the creator's and required hashtags are added where they always were.
    """
    title = _clean_title(candidate.hook) or _clean_title(video_title) or "Clip"
    return ClipMetadata(title=_attributed(title, channel), description="", hashtags=[])


def _attributed(quote: str, channel: str) -> str:
    """A line the creator said, as a title: 'Channel: "quote"' when it speaks as
    "I" (the hook and the strongest-quote fallback both do), so the post does not
    sound like the creator talking. Anything else, or no channel to name, is
    returned as it was."""
    if not channel or not first_person(quote):
        return quote
    room = MAX_TITLE_LEN - len(channel) - 4
    if room < 12:
        return quote
    return f'{channel}: "{quote[:room].rstrip(" ,;:-")}"'


def _parse(raw: str) -> dict | None:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_title(title: str) -> str:
    title = re.sub(r"[<>]", "", str(title)).strip().strip('"')
    return title[:MAX_TITLE_LEN].strip()


def _clean_hashtags(tags) -> list[str]:
    """One hashtag per entry, at most the compliance limit (three by default).

    The model sometimes answers with several run together in one string,
    "#creatorname#drama#apology", and keeping that as one tag put it into
    posts as a single unreadable hashtag. Split on every # and every space.
    """
    if not isinstance(tags, list):
        return []
    cleaned: list[str] = []
    for entry in tags:
        for part in re.split(r"[#\s]+", str(entry).lower()):
            word = re.sub(r"[^\w]", "", part)
            tag = f"#{word}"
            if word and tag != "#shorts" and tag not in cleaned:
                cleaned.append(tag)
    return cleaned[: compliance.rules().max_hashtags]


# ---- accuracy: what the model wrote against what was said ------------------------------

TEXT_BUDGET = 1800     # characters of one clip shown to the model
MAX_REPAIRS = 12       # extra model calls one video may spend rewriting bad titles

# Words that say nothing about a particular clip, and words that only make
# noise: neither counts as "supported by the transcript".
_STOP = frozenset(
    "about after again against also always another because been before being between both "
    "cant could didnt does doing dont down during each even every from gets getting give goes "
    "going gone gonna gotta have having here into just keep know like look made make many more "
    "most much must never only other over really said same says should since some still such "
    "take than that their them then there these they thing things think this those through "
    "together under until very wanna want wants were what when where which while will with "
    "without would your youre yours".split()
)
_VAGUE = frozenset(
    "insane crazy epic amazing unbelievable shocking wild incredible ultimate best greatest "
    "unreal legendary mindblowing".split()
)


# Register, not facts. The copy is asked to sound like the audience, and slang
# is by nature absent from the transcript: "cooked" and "lowkey" are not claims
# about what happened, so they are never "unsupported". (Hype words such as
# "insane" stay in _VAGUE: those are banned for being empty, not for being slang.)
_TONE = frozenset(
    "cooked cook lowkey highkey fumbled fumble fumbles crashout crashed clutch clutched "
    "cracked goated bussin cappin nahh deadass frfr fricking mogged sigma rizz cringe based "
    "sheesh bruh dawg yall lmao nope yikes ngl lol chill vibes vibe wasted brutal diff "
    "folded cheeks washed sweaty tilted ratio ratioed".split()
)


def budget_text(text: str, limit: int = TEXT_BUDGET) -> str:
    """A clip's speech within the budget: all of it when it fits, otherwise its
    opening and its ending, which is where the setup and the payoff are."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = int(limit * 0.55)
    return text[:head].rstrip() + " ... " + text[-(limit - head):].lstrip()


def _stem(word: str) -> str:
    return word[:4]


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'’-]{3,}", (text or "").lower())
            if re.sub(r"['’-]", "", w) not in _STOP]


def unsupported_words(title: str, transcript: str, known: list[str] | None = None) -> list[str]:
    """Words in `title` that are neither in the transcript nor in `known` (the
    channel, the video's own title, the clip's tags), plus any vague hype
    word. Compared by their first four letters, so 'blowing' matches 'blow'."""
    have = {_stem(w) for w in _content_words(transcript)}
    for k in known or []:
        have |= {_stem(w) for w in _content_words(str(k))}
    out = []
    for w in _content_words(title):
        bare = re.sub(r"['’-]", "", w)
        said = _stem(w) in have
        # A hype word is only empty when the clip never says it: "that was
        # insane" said out loud is a quote, not padding.
        if (bare in _VAGUE and not said) or (not said and bare not in _TONE):
            out.append(w)
    return out


def _ungrounded(title: str, transcript: str, channel: str, extra: list[str], meta=None) -> list[str]:
    """The unsupported words when the title is not trustworthy, else []. Bad
    means most of its content words have no support, or two or more do and they
    are at least 40% of it: one creative word in a good title is fine."""
    known = [channel, *extra]
    if meta is not None:
        known += list(meta.keywords) + list(meta.hashtags)
    words = _content_words(title)
    if not words or not transcript.strip():
        return []
    bad = unsupported_words(title, transcript, known)
    if len(bad) == len(words) or (len(bad) >= 2 and len(bad) / len(words) >= 0.4):
        return bad
    return []


TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}


def _checked(meta: ClipMetadata, candidate: ClipCandidate, segments, text: str, llm, channel: str,
             always_on, extra: list[str], ask_model: bool = True, audience: str = "") -> ClipMetadata:
    """Replace a title the transcript does not support, or one that speaks as
    "I": first by asking the model again with the problem named, then, if that
    still misses, by the strongest thing actually said in the clip, credited to
    the channel."""
    bad = _ungrounded(meta.title, text, channel, extra, meta)
    speaks_as_i = first_person(meta.title)
    if not bad and not speaks_as_i:
        return meta
    title = ""
    try:
        if not ask_model:
            raise RuntimeError("no model call left for this video")
        faults = []
        if bad:
            faults.append(f'it uses words that are not said in the clip: {", ".join(bad)}')
        if speaks_as_i:
            faults.append(f'it speaks as "I", but the post is by a channel about {channel or "the creator"}')
        prompt = (
            "Rewrite this YouTube Shorts title so that it is TRUE to the clip.\n"
            f'The title "{meta.title}" is wrong: {"; and ".join(faults)}.\n'
            "Use only what is said. Build it around one specific thing that happens or is said, "
            "no vague hype words, under 90 characters, no quotation marks.\n\n"
            f"{voice_rules(channel, audience)}\n\n"
            f"CLIP TRANSCRIPT:\n{budget_text(text)}\n\n"
            'Respond with ONLY valid JSON: {"title": "..."}'
        )
        title = _clean_title((_parse(generate_json(llm, prompt, TITLE_SCHEMA)) or {}).get("title", ""))
    except Exception:
        title = ""
    if not title or _ungrounded(title, text, channel, extra, meta) or first_person(title):
        from analysis import peaks

        top = peaks.peak(segments, candidate.start, candidate.end, 1)
        title = _clean_title(top[0]["text"].rstrip(".,;:").strip()) if top else ""
        if title:
            title = _attributed(title[0].upper() + title[1:], channel)
    if title:
        meta.title = title
    return meta


# ---- the set pieces: source channel and an always-on hashtag ----------------------------

# Tags every channel puts on everything say nothing about THIS clip, so they are
# never picked as "the" tag for a clip when a real one is on offer.
_GENERIC_TAGS = frozenset("shorts short fyp foryou foryoupage viral trending reels explore".split())


def _tags_line(always_on) -> str:
    tags = [t for t in (_bare_tag(x) for x in always_on or []) if t]
    return ", ".join(f"#{t}" for t in tags) if tags else "(none)"


def _bare_tag(tag) -> str:
    return re.sub(r"[^\w]", "", str(tag or "").lstrip("#")).lower()


def pick_always_on(always_on, text: str = "", index: int = 0) -> str:
    """One of the channel's always-on hashtags for this clip, bare (no #), or "".
    One the clip is about wins; otherwise they take turns clip by clip, so a
    channel with several does not put the same one on everything."""
    tags = [t for t in dict.fromkeys(_bare_tag(x) for x in always_on or []) if t and t not in _GENERIC_TAGS]
    if not tags:
        return ""
    low = (text or "").lower()
    for t in tags:
        if t in re.sub(r"[^\w]", "", low) or t in low:
            return t
    return tags[index % len(tags)]


def _anchored(meta: ClipMetadata, channel: str, always_on, text: str = "", index: int = 0) -> ClipMetadata:
    """Put the source channel and one always-on hashtag on this clip's metadata,
    whether or not the model did.

    * the hashtag leads the clip's own hashtags;
    * the title names the channel ("| Channel") unless it, or a standing hashtag,
      is already in it and the room is there;
    * the description names the channel, so search results show whose clip it is."""
    channel = (channel or "").strip()
    tag = pick_always_on(always_on, f"{meta.title} {text}", index)
    if tag:
        tags = [f"#{tag}"] + [h for h in meta.hashtags if h.lower() != f"#{tag}"]
        meta.hashtags = tags[: compliance.rules().max_hashtags]
    title_low = meta.title.casefold()
    standing = [_bare_tag(t) for t in always_on or []]
    if channel and channel.casefold() not in title_low and not any(t and t in title_low.replace(" ", "") for t in standing):
        suffix = f" | {channel}"
        if len(meta.title) + len(suffix) <= MAX_TITLE_LEN + 5:      # a title may run to 100
            meta.title = meta.title + suffix
    if channel and meta.description and channel.casefold() not in meta.description.casefold():
        meta.description = f"{channel}: {meta.description}"
    return _compliant(meta)


def _compliant(meta: ClipMetadata) -> ClipMetadata:
    """The fixes that need no judgement, last, so nothing added above (the
    always-on tag, the channel) can push the copy back over a limit: no hashtag
    in the title, hashtags within the limit, hidden tags that are not echoes of
    the title or of a hashtag. Wording is `_naturalized`'s job, earlier."""
    meta.title = compliance.sanitize(meta.title, "").title
    meta.hashtags = meta.hashtags[: compliance.rules().max_hashtags]
    meta.keywords = compliance.fix_tags(meta.keywords, meta.title, " ".join(meta.hashtags))
    meta.alt_titles = [t for t in (compliance.sanitize(a, "").title for a in meta.alt_titles) if t]
    return meta
