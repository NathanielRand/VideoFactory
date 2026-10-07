"""Publish metadata for a compilation: title, description with chapters and
credits, keywords, hashtags, a first comment.

Two halves, kept apart on purpose:

* The FACTS are computed, never generated: where each part starts (the
  chapters) and who each clip came from (the credits). A model asked to write
  timestamps gets them wrong, and a wrong credit is worse than none.
* The WORDS come from the model: a title and a summary drawn from the chapter
  names and the source titles, plus search keywords. Any failure there falls
  back to plain text built from the same facts, so this never stops a publish.
"""

from __future__ import annotations

import json
from pathlib import Path

from publish.seo import chapters, credits

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "compilation_metadata.txt"


def _clip_label(db, video_id: str, start: float, end: float) -> str:
    """The title of the AI clip that best covers this segment, if any: those
    titles were written to be read, so they make the best chapter names."""
    rows = db.conn.execute(
        "SELECT title, hook, start_s, end_s FROM clips WHERE video_id = ?", (video_id,)
    ).fetchall()
    best, overlap = "", 0.0
    for r in rows:
        shared = min(end, r["end_s"]) - max(start, r["start_s"])
        if shared > overlap:
            best, overlap = (r["title"] or r["hook"] or ""), shared
    return best if overlap >= min(5.0, (end - start) / 2) else ""


def _bumper_seconds(entry) -> float:
    path = (entry or {}).get("path") if isinstance(entry, dict) else None
    if not path or not Path(path).exists():
        return 0.0
    try:
        from compilation.render import probe

        return float(probe(Path(path))[0])
    except Exception:
        return 0.0


def facts(db, comp: dict) -> dict:
    """Chapters, credits and what each part is, from the recipe and the DB."""
    recipe = comp.get("recipe") or {}
    segments = recipe.get("segments") or []
    transition = recipe.get("transition") or {}
    overlap = 0.0
    if isinstance(transition, dict) and (transition.get("type") or "none") != "none":
        # A crossfade overlaps neighbours, so every part after the first
        # starts that much earlier than the plain sum of lengths says.
        overlap = float(transition.get("duration") or 0.5)

    videos: dict[str, dict] = {}
    parts: list[tuple[str, float]] = []
    intro = _bumper_seconds(recipe.get("intro"))
    if intro:
        parts.append(("Intro", intro - overlap))
    labels: list[str] = []
    for i, seg in enumerate(segments):
        vid = str(seg.get("video_id") or "")
        if vid not in videos:
            row = db.conn.execute(
                "SELECT title, channel_name, channel_url, source_url FROM videos WHERE video_id = ?",
                (vid,),
            ).fetchone()
            videos[vid] = dict(row) if row else {"title": vid}
        start, end = float(seg.get("start") or 0), float(seg.get("end") or 0)
        label = _clip_label(db, vid, start, end) or (videos[vid].get("title") or f"Part {i + 1}")
        labels.append(label)
        last = i == len(segments) - 1 and not recipe.get("outro")
        parts.append((label, (end - start) - (0 if last else overlap)))
    outro = _bumper_seconds(recipe.get("outro"))
    if outro:
        parts.append(("Outro", outro))

    sources = [
        {"channel": v.get("channel_name") or "", "channel_url": v.get("channel_url") or "",
         "url": v.get("source_url") or ""}
        for v in videos.values()
    ]
    return {
        "chapters": chapters(parts),
        "credits": credits(sources),
        "labels": labels,
        "source_titles": [v.get("title") or "" for v in videos.values()],
        "channels": [s["channel"] for s in sources if s["channel"]],
        "long_form": (recipe.get("canvas") or "16:9") in ("16:9",),
    }


def compose_description(summary: str, f: dict, footer: str = "") -> str:
    """Summary first (search shows it), then chapters, credits, standing text."""
    return "\n\n".join(p for p in (summary.strip(), f["chapters"], f["credits"], footer.strip()) if p)


def generate(db, comp: dict, llm=None, *, footer: str = "", audience: str = "") -> dict:
    """A complete publish_meta for the compilation. `audience` (see
    analysis.audience) is who the connected channel's viewers are, so the
    wording can be theirs."""
    from analysis.metadata import (
        METADATA_SCHEMA,
        ClipMetadata,
        _clean_title,
        _from_parsed,
        _naturalized,
        _parse,
        _unvoiced,
        first_person,
        voice_rules,
    )

    f = facts(db, comp)
    channels = list(dict.fromkeys(f["channels"]))
    fallback = ClipMetadata(
        title=_clean_title(comp.get("title") or "") or "Compilation",
        description=(
            f"The best moments from {', '.join(channels[:3])}." if channels else ""
        ),
        hashtags=[f"#{''.join(c for c in ch if c.isalnum()).lower()}" for ch in channels[:2]],
    )
    meta = fallback
    if llm is not None and f["labels"]:
        prompt = (
            PROMPT_PATH.read_text(encoding="utf-8")
            .replace("{voice}", voice_rules(", ".join(channels[:3]), audience))
            .replace("{title}", comp.get("title") or "")
            .replace("{channels}", ", ".join(channels) or "(unknown)")
            .replace("{sources}", "\n".join(f"- {t}" for t in f["source_titles"] if t))
            .replace("{parts}", "\n".join(f"{i + 1}. {label}" for i, label in enumerate(f["labels"])))
        )
        try:
            from llm.base import generate_json

            parsed = _parse(generate_json(llm, prompt, METADATA_SCHEMA))
            if parsed:
                meta = _from_parsed(parsed, fallback)
                # Part names are clip titles and source titles, which can be
                # the creator talking ("my brother exposes me"); the post is
                # about them, not by them.
                _unvoiced(meta, llm, ", ".join(channels[:3]), audience)
                _naturalized(meta, llm, ", ".join(channels[:3]), audience, fallback.title)
                if first_person(meta.title):
                    meta.title = fallback.title
            else:
                print("  Compilation metadata: the model's answer was not JSON; using plain text.")
        except Exception as e:
            print(f"  Compilation metadata: the model failed ({type(e).__name__}: {e}); using plain text.")
            meta = fallback

    from publish import compliance

    keywords = list(meta.keywords)
    for ch in channels:
        if ch.lower() not in {k.lower() for k in keywords}:
            keywords.append(ch.lower())
    meta.title = compliance.sanitize(meta.title, "").title
    meta.hashtags = meta.hashtags[: compliance.rules().max_hashtags]
    return {
        "title": meta.title,
        "summary": meta.description,
        "description": compose_description(meta.description, f, footer),
        "hashtags": meta.hashtags,
        "keywords": compliance.fix_tags(keywords, meta.title, " ".join(meta.hashtags)),
        # The standing comment is used unless this one is picked.
        "first_comment": "",
        "suggested_comment": meta.first_comment,
        "alt_titles": meta.alt_titles,
        "chapters": f["chapters"],
        "credits": f["credits"],
        "long_form": f["long_form"],
    }


def dumps(meta: dict) -> str:
    return json.dumps(meta)
