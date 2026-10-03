"""The thumbnail designer's routes, for clips and compilations alike.

The design is drawn in the app (ui ThumbnailStudio) on a canvas, because a
designer has to redraw on every drag and a server round-trip per frame is not
a designer. This module supplies what the canvas cannot make itself, and
stores the result:

* frames from the rendered video, and which ones are worth using;
* the subject cut-out (video/cutout.py), and text ideas from the model;
* the finished JPEG, saved as THE thumbnail: the same file every publisher
  already sends (`thumbnails/clip_<id>_chosen.jpg`), so nothing downstream
  changes, plus the design as JSON so it opens again as layers.

`publish_id` is a clip id, or the negative of a compilation's id, as
everywhere in publishing (StateDB.get_publishable). Unlike the YouTube picker
these routes do not need YouTube connected: a thumbnail is used by every
publisher that takes one.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.paths import within

# Frames are served at most this wide or tall: sharp enough to zoom into for
# a 1280x720 thumbnail, small enough to move around a canvas quickly.
FRAME_MAX = 1920
MAX_DESIGN_BYTES = 256 * 1024

IDEAS_SCHEMA = {
    "type": "object",
    "properties": {"ideas": {"type": "array", "items": {"type": "string"}}},
    "required": ["ideas"],
    "additionalProperties": False,
}


EMOJIS = ("😱", "🔥", "😂", "🤯", "💀", "👀", "❗", "❓", "💯", "🏆", "😡", "🥶")
MOODS = ("hype", "shock", "funny", "serious", "wholesome")

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "sets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "kicker": {"type": "string"},
                    "badge": {"type": "string"},
                    "emoji": {"type": "string"},
                    "mood": {"type": "string"},
                },
                "required": ["headline", "kicker", "badge", "emoji", "mood"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sets"],
    "additionalProperties": False,
}


# Tags every channel puts on everything. They say nothing about THIS video, so
# a thumbnail that carried one would be a thumbnail about nothing.
GENERIC_TAGS = {
    "shorts", "short", "fyp", "foryou", "foryoupage", "viral", "trending", "reels", "explore",
    "youtubeshorts", "youtube", "tiktok", "clips", "clip", "video", "videos", "funny", "fy",
    "subscribe", "like", "follow", "new", "best", "top", "highlights", "moments", "epic",
}

# Headlines that fit any video, so they tell a viewer nothing.
FILLER_HEADLINES = {
    "NO WAY", "INSANE", "CRAZY", "WOW", "OMG", "UNREAL", "WAIT WHAT", "WHAT", "BRUH", "LOL",
    "WATCH THIS", "JUST WATCH", "UNBELIEVABLE", "SHOCKING", "YOU WONT BELIEVE THIS", "EPIC",
    "AMAZING", "INCREDIBLE", "WILD", "MUST WATCH",
}

MAX_BADGE_CHARS = 14


def _keyword_form(raw: str) -> str:
    """One tag or keyword as a thumbnail word: no '#', no separators, and
    camelCase split so '#PokerNight' reads as two words."""
    text = re.sub(r"^[#@]+", "", str(raw or "").strip())
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def core_keywords(keywords, hashtags, creator: str = "", ignore=()) -> list[str]:
    """The words this video is about, most specific first: its own search
    keywords, then its hashtags, then the creator. `ignore` is the always-on
    tags (settings) so a standing '#shorts' is not mistaken for the topic."""
    skip = {re.sub(r"\W", "", _keyword_form(t)).casefold() for t in ignore} | GENERIC_TAGS
    out: list[str] = []
    for raw in [*(keywords or []), *(hashtags or []), creator]:
        text = _keyword_form(raw)
        squashed = re.sub(r"\W", "", text).casefold()
        if len(squashed) < 3 or squashed.isdigit() or squashed in skip:
            continue
        if squashed not in {re.sub(r"\W", "", o).casefold() for o in out}:
            out.append(text)
    return out[:12]


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text, re.IGNORECASE) is not None


def mentions_keyword(entry: dict, keywords) -> str:
    """The keyword a text set already carries (whole word, any case), or ''."""
    text = " ".join(str(entry.get(k) or "") for k in ("headline", "kicker", "badge"))
    for word in keywords or []:
        if _has_word(text, word):
            return word
        # A multi-word keyword also counts when its longest word is present.
        longest = max(word.split(), key=len, default="")
        if longest and len(longest) >= 4 and word != longest and _has_word(text, longest):
            return word
    return ""


def with_keyword(sets: list[dict], keywords) -> list[dict]:
    """Every set carries one of the video's core keywords.

    Sets that already do come first. The rest get it as their badge, since a
    label is the one piece that can be swapped without breaking how the
    headline and kicker read together. A keyword too long for a badge is
    skipped in favour of a shorter one; if none fits the set is left alone."""
    if not keywords:
        return sets
    fits = [k for k in keywords if len(k) <= MAX_BADGE_CHARS and len(k.split()) <= 2]
    have, lack = [], []
    for entry in sets:
        found = mentions_keyword(entry, keywords)
        if found:
            have.append({**entry, "keyword": found})
        elif fits:
            have_lack = {**entry, "badge": fits[0], "keyword": fits[0]}
            lack.append(have_lack)
        else:
            lack.append(entry)
    return have + lack


class SaveIn(BaseModel):
    # The finished thumbnail, base64 (a data: URL prefix is accepted too).
    image: str = Field(min_length=1)
    design: dict | None = None
    # Made by the app on its own (the first render of a clip), not by a person.
    auto: bool = False


# A thumbnail is made for a clip automatically ONCE, when it is first rendered.
# This file says that has happened (or that it should not): written when any
# thumbnail is saved, when one is deleted on purpose, and when a clip is
# re-rendered. Without it a re-render, which gives the clip a new id and so no
# thumbnail, would look like a new clip that needs one, and the app would quietly
# replace a design somebody chose.
def auto_marker(folder: Path, clip_id: int) -> Path:
    return folder / f"clip_{int(clip_id)}_auto.done"


def carry_over(data_dir: Path | str, old_id: int, new_id: int, keep_design: bool = True) -> None:
    """A re-rendered clip keeps its thumbnail. The clip comes back under a new
    id, so the files are moved to it; the design goes too, unless the clip now
    starts somewhere else (its frames were picked against the old start), in
    which case the picture stays and the studio opens fresh. Whatever happened,
    the new clip counts as having had its automatic thumbnail: a re-render never
    asks for another."""
    folder = Path(data_dir) / "thumbnails"
    folder.mkdir(parents=True, exist_ok=True)
    moves = [("chosen.jpg", True), ("design.json", keep_design)]
    for suffix, keep in moves:
        src = folder / f"clip_{int(old_id)}_{suffix}"
        if src.exists():
            if keep:
                src.replace(folder / f"clip_{int(new_id)}_{suffix}")
            else:
                src.unlink(missing_ok=True)
    auto_marker(folder, new_id).write_text("", encoding="utf-8")
    auto_marker(folder, old_id).unlink(missing_ok=True)


def _keyword_rule(keywords) -> str:
    if not keywords:
        return ""
    return ("The video's core keywords are: " + ", ".join(keywords[:6]) + ". At least one of "
            "these MUST appear, spelled as given, in the text of every option.\n")


def clean_ideas(raw, keywords=()) -> list[str]:
    """Short, punchy, distinct: thumbnail text is read in a glance. Ideas that
    carry a core keyword come first."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        text = re.sub(r"\s+", " ", re.sub(r"[\"<>#]", "", str(entry))).strip(" .")
        if not text or len(text) > 32 or len(text.split()) > 5:
            continue
        if re.sub(r"[^\w ]", "", text).upper() in FILLER_HEADLINES:
            continue
        if text.upper() not in {o.upper() for o in out}:
            out.append(text)
    if keywords:
        out.sort(key=lambda t: 0 if mentions_keyword({"headline": t}, keywords) else 1)
    return out[:6]


def _line(raw, max_words: int, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[\"<>#]", "", str(raw or ""))).strip(" .")
    return text if text and len(text.split()) <= max_words and len(text) <= max_chars else ""


def clean_copy(raw, keywords=()) -> list[dict]:
    """Ranked text sets for one thumbnail, each usable on its own.

    A set is a headline plus optional supporting lines that were written to
    read together: a headline alone is a set, but a supporting line that is
    too long to read at thumbnail size is dropped rather than cut short (half
    a sentence reads as a mistake). Best first, as the model ranked them.

    Headlines that would fit any video ("NO WAY", "INSANE") are dropped, and
    every set that remains carries one of the video's core `keywords`."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        headline = _line(entry.get("headline"), 4, 28)
        if not headline:
            continue
        emoji = str(entry.get("emoji") or "").strip()
        mood = str(entry.get("mood") or "").strip().lower()
        if re.sub(r"[^\w ]", "", headline).upper() in FILLER_HEADLINES:
            continue
        out.append({
            "headline": headline,
            "kicker": _line(entry.get("kicker"), 5, 34),
            "badge": _line(entry.get("badge"), 2, 14),
            "emoji": emoji if emoji in EMOJIS else "",
            "mood": mood if mood in MOODS else "hype",
        })
    return with_keyword(out, keywords)[:3]


def install(app, *, config, db, data_dir: Path) -> None:
    folder = (Path(data_dir) / "thumbnails").resolve()
    cache = folder / "cache"

    def _item(pid: int):
        d = db()
        try:
            item = d.get_publishable(pid)
        finally:
            d.close()
        if item is None:
            raise HTTPException(404, "no such clip or compilation")
        return item

    def _video(item) -> Path:
        path = Path(item["path"] or "")
        if not path.exists():
            raise HTTPException(404, "This has not been rendered yet, so there are no frames to use.")
        return path

    def _media(item, use_render: bool = False) -> tuple[Path, float]:
        """Where thumbnail frames come from, and where the clip starts in it.

        The ORIGINAL video when it is still on disk: the render is vertical,
        so a 16:9 crop of it is a small upscaled slice with the captions and
        watermark burned in. The render is the fallback, and the answer when
        an edit list has moved things about (so clip time no longer maps onto
        the source) and for compilations. `use_render` asks for it outright:
        designs saved before frames came from the source were laid out on the
        render's frame, and reopen on it."""
        rendered = _video(item)
        if use_render:
            return rendered, 0.0
        try:
            opts = json.loads(item["render_opts"] or "{}") if "render_opts" in item.keys() else {}
        except (TypeError, ValueError):
            opts = {}
        if int(item["id"]) >= 0 and not opts.get("edit") and not opts.get("profile"):
            from core.paths import cached_source

            source = cached_source(Path(data_dir) / "downloads", item["video_id"])
            if source and source.exists():
                return source, float(item["start_s"])
        return rendered, 0.0

    def _segments(item):
        """The clip's transcript as Segments, [] for a compilation or when the
        transcript is gone."""
        if int(item["id"]) < 0:
            return []
        try:
            from core.models import Segment

            raw = json.loads((Path(data_dir) / "transcripts" / f"{item['video_id']}.json").read_text(encoding="utf-8"))
            return [Segment(**seg) for seg in raw.get("segments") or []]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _badge(item, text: str = "") -> str:
        """The badge word: one of the always-on hashtags without its '#', else
        the clip's own tag, a keyword, the channel (analysis/thumbcopy.py)."""
        from analysis.thumbcopy import pick_badge
        from server.publishing_api import _json_list, creator_of, load_settings

        d = db()
        try:
            always_on = list(load_settings(d).get("hashtags") or [])
            channel = creator_of(d, item)
        finally:
            d.close()
        return pick_badge(always_on, _json_list(item, "hashtags"), _json_list(item, "keywords"),
                          channel, text, index=abs(int(item["id"])))

    def _keywords(item) -> list[str]:
        from server.publishing_api import _json_list, creator_of, load_settings

        d = db()
        try:
            settings = load_settings(d)
            return core_keywords(
                _json_list(item, "keywords"), _json_list(item, "hashtags"),
                creator=creator_of(d, item),
                ignore=[*(settings.get("hashtags") or []), *(settings.get("channel_keywords") or [])],
            )
        finally:
            d.close()

    def _file(name: str) -> Path:
        """A file in the thumbnails folder. Names are built from integers and
        hashes only, so this is a belt to that brace."""
        target = (folder / name).resolve()
        if not within(folder, target):
            raise HTTPException(400, "bad thumbnail name")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _video_key(video: Path) -> str:
        """Changes when the video is re-rendered, so cached frames never
        outlive the render they came from."""
        st = video.stat()
        return hashlib.sha1(f"{video}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:10]

    def _frame(pid: int, video: Path, t: float, offset: float = 0.0) -> Path:
        ms = max(0, int(round(t * 1000)))
        target = _file(f"cache/clip_{int(pid)}_{_video_key(video)}_{ms}_{int(offset * 1000)}.jpg")
        if target.exists():
            return target
        from core.binaries import ffmpeg

        result = subprocess.run(
            [
                ffmpeg(), "-y", "-loglevel", "error",
                "-ss", f"{ms / 1000 + offset:.3f}", "-i", str(video),
                "-frames:v", "1",
                "-vf", f"scale='min({FRAME_MAX},iw)':'min({FRAME_MAX},ih)':force_original_aspect_ratio=decrease",
                "-q:v", "2",
                str(target),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not target.exists():
            raise HTTPException(500, "Could not read a frame at that moment.")
        return target

    def _duration(video: Path) -> float:
        try:
            from compilation.render import probe

            return float(probe(video)[0])
        except Exception:
            return 0.0

    @app.get("/thumbnails/status")
    def status(ids: str = ""):
        """Which of these clips (or, negative, compilations) have a saved
        thumbnail. Only checks for the file, so a page of rows can ask at
        once: /source runs ffprobe and 404s for anything unrendered."""
        found: dict[str, bool] = {}
        for raw in ids.split(",")[:500]:
            raw = raw.strip()
            if not re.fullmatch(r"-?\d{1,9}", raw):
                continue
            found[raw] = _file(f"clip_{int(raw)}_chosen.jpg").exists()
        return {"thumbnails": found}

    @app.get("/thumbnails/pending")
    def pending(ids: str = ""):
        """Which of these clips are new and have never had a thumbnail made: no
        thumbnail, no marker (see auto_marker), and a rendered file to take
        frames from. The app makes their first one on its own; nothing here is
        ever asked for a clip that was re-rendered or whose thumbnail was
        deleted."""
        out: list[int] = []
        d = db()
        try:
            for raw in ids.split(",")[:200]:
                raw = raw.strip()
                if not re.fullmatch(r"\d{1,9}", raw) or int(raw) <= 0:
                    continue
                cid = int(raw)
                if _file(f"clip_{cid}_chosen.jpg").exists() or auto_marker(folder, cid).exists():
                    continue
                item = d.get_publishable(cid)
                if item is not None and item["path"] and Path(item["path"]).exists():
                    out.append(cid)
        finally:
            d.close()
        return {"pending": out}

    @app.get("/thumbnails/{publish_id}/source")
    def source(publish_id: int):
        """What the designer needs to start: the video's length, and whether
        a saved design or thumbnail already exists."""
        item = _item(publish_id)
        video = _video(item)
        return {
            "duration": _duration(video),
            "title": item["title"] or item["hook"] or "",
            "keywords": _keywords(item),
            "has_design": _file(f"clip_{int(publish_id)}_design.json").exists(),
            "has_image": _file(f"clip_{int(publish_id)}_chosen.jpg").exists(),
        }

    @app.get("/thumbnails/{publish_id}/frame")
    def frame(publish_id: int, t: float = 0.0, src: str = ""):
        video, offset = _media(_item(publish_id), use_render=(src == "render"))
        return FileResponse(str(_frame(publish_id, video, max(0.0, t), offset)), media_type="image/jpeg")

    @app.get("/thumbnails/{publish_id}/best-frames")
    def best_frames(publish_id: int, count: int = 6):
        """The frames most worth building on, best first: a well-sized face
        with its eyes open and a lively expression, and above all a SHARP one
        (video.thumbnail.score_frame). Taken from the original video when it
        is still on disk, so they are clean of captions and full resolution."""
        from video.thumbnail import pick_frames

        item = _item(publish_id)
        video, offset = _media(item)
        length = _duration(_video(item)) or None
        wanted = max(1, min(int(count), 12))
        picks = pick_frames(video, count=wanted, start=offset, duration=length)

        # A second opinion from a vision model on the short list, when one is
        # configured (llm.vision_model) and can see. It only reorders the
        # leaders; anything short of a clear answer leaves the order alone.
        seen = False
        try:
            from video import frame_judge

            judge_llm = frame_judge.vision_backend(config)
            if judge_llm is not None and len(picks) > 1:
                lead = picks[: frame_judge.MAX_FRAMES]
                jpegs = [_frame(publish_id, video, r["t"], offset).read_bytes() for r in lead]
                order = frame_judge.judge(judge_llm, jpegs)
                if order:
                    picks = [lead[i] for i in order] + picks[len(lead):]
                    seen = True
        except Exception:
            pass  # a model that cannot help must never cost the user their frames
        return {"frames": [{"t": r["t"], "score": round(r["score"], 1), "face": r["face"] is not None,
                            "sharp": round(r["sharp"], 1)} for r in picks], "vision": seen}

    @app.post("/thumbnails/{publish_id}/cutout")
    def make_cutout(publish_id: int, t: float = 0.0, src: str = ""):
        """The people in the frame at `t`, as a full-frame PNG with everything
        else transparent. 422 when nobody is in the frame."""
        video, offset = _media(_item(publish_id), use_render=(src == "render"))
        frame_path = _frame(publish_id, video, max(0.0, t), offset)
        target = frame_path.with_name(frame_path.stem + "_cut.png")
        if not target.exists():
            from video.cutout import cutout

            try:
                found = cutout(frame_path, target)
            except Exception as e:
                raise HTTPException(503, f"The cut-out model could not run: {e}") from e
            if not found:
                raise HTTPException(422, "Nobody to cut out in this frame. Try a frame with a person in it.")
        return FileResponse(str(target), media_type="image/png")

    @app.post("/thumbnails/{publish_id}/text-ideas")
    def text_ideas(publish_id: int):
        """A handful of short thumbnail lines from what happens in the video."""
        from analysis.metadata import _parse
        from llm.base import generate_json
        from server.publishing_api import content_of

        item = _item(publish_id)
        keywords = _keywords(item)
        d = db()
        try:
            content = content_of(d, item, data_dir)
        finally:
            d.close()
        prompt = (
            "Write 6 different pieces of text for a YouTube thumbnail. Each is 1-4 words, "
            "readable in a glance at phone size, and makes someone want to click: a reaction, "
            "a bold claim, a number, a question, a name. It must match what actually happens; "
            "no clickbait the video does not deliver. No hashtags, no quotation marks, no emoji. "
            "Do not repeat the title word for word. Be SPECIFIC: use the actual names, numbers, "
            "objects and places from the video, never filler like INSANE, CRAZY, NO WAY or WOW.\n"
            f"{_keyword_rule(keywords)}\n"
            f"TITLE: {item['title'] or item['hook'] or ''}\n"
            f"WHAT HAPPENS:\n{content[:2500]}\n\n"
            'Respond with ONLY valid JSON: {"ideas": ["...", "..."]}'
        )
        segments = _segments(item)
        try:
            from llm.registry import create_backend

            ideas = clean_ideas((_parse(generate_json(create_backend(config["llm"]), prompt, IDEAS_SCHEMA)) or {}).get("ideas"), keywords)
        except Exception as e:
            if not segments:                    # real quotes below are still an answer
                raise HTTPException(503, f"The AI model could not write ideas: {e}") from e
            ideas = []
        if segments:
            from analysis import peaks

            quotes = [q["text"] for q in peaks.peak(segments, float(item["start_s"]), float(item["end_s"]), 4)]
            ideas = clean_ideas([*quotes, *ideas], keywords)
        if not ideas:
            raise HTTPException(503, "The AI model did not come up with usable text. Try again.")
        return {"ideas": ideas}

    @app.post("/thumbnails/{publish_id}/copy")
    def copy(publish_id: int):
        """Text for a whole thumbnail, written as one piece: a headline, a
        supporting line and a badge that read together, best of three first.
        Independent ideas (text-ideas) do not reliably fit side by side."""
        from analysis.metadata import _parse
        from llm.base import generate_json
        from server.publishing_api import content_of

        item = _item(publish_id)
        keywords = _keywords(item)

        # The words on a thumbnail are QUOTED from the clip's peak, and the
        # badge is an always-on hashtag (analysis/thumbcopy.py). The model, when
        # there is one, only ranks the quotes, by number.
        segments = _segments(item)
        if segments:
            from analysis import peaks, thumbcopy

            start, end = float(item["start_s"]), float(item["end_s"])
            cands = peaks.peak(segments, start, end, 8)
            choice = {"order": [], "mood": "", "emoji": ""}
            if len(cands) > 1:
                try:
                    from llm.base import generate_json
                    from llm.registry import create_backend

                    choice = thumbcopy.parse_choice(
                        generate_json(create_backend(config["llm"]), thumbcopy.choice_prompt(cands),
                                      thumbcopy.CHOICE_SCHEMA), len(cands))
                except Exception:
                    pass  # the ranking below is the answer without a model
            quoted = thumbcopy.quote_sets(
                segments, start, end, badge=_badge(item, " ".join(c["line"] for c in cands)),
                order=choice["order"], mood=choice["mood"], emoji=choice["emoji"], candidates=cands)
            if quoted:
                return {"sets": quoted, "keywords": keywords, "source": "quotes"}

        d = db()
        try:
            content = content_of(d, item, data_dir)
        finally:
            d.close()
        prompt = (
            "Write the text for a YouTube thumbnail, as 3 different options ranked best first. "
            "Each option is ONE message told in short pieces that must read together in "
            "a glance at phone size:\n"
            "- headline: 1-3 words, the hook (a reaction, a bold claim, a number, a question).\n"
            "- kicker: 2-5 words that complete or set up the headline, so the two make sense as "
            "one sentence or one idea.\n"
            "- badge: 1-2 words, a label such as the topic, a name, or a tag like REAL or LIVE.\n"
            f"- emoji: one of {' '.join(EMOJIS)}, or an empty string.\n"
            f"- mood: one of {', '.join(MOODS)}, matching the video's tone.\n"
            "Clickbait is fine, but it must be honest: everything has to be true of what "
            "actually happens in the video, and it must not promise something the video does "
            "not show. No hashtags, no quotation marks, no emoji inside the text pieces. Do not "
            "repeat the title word for word.\n"
            "Be SPECIFIC. Name the actual person, thing, number, place or outcome from the video. "
            "Never use filler that would fit any video (INSANE, CRAZY, NO WAY, WOW, OMG, UNREAL).\n"
            f"{_keyword_rule(keywords)}\n"
            f"TITLE: {item['title'] or item['hook'] or ''}\n"
            f"WHAT HAPPENS:\n{content[:2500]}\n\n"
            'Respond with ONLY valid JSON: {"sets": [{"headline": "...", "kicker": "...", '
            '"badge": "...", "emoji": "...", "mood": "..."}]}'
        )
        try:
            from llm.registry import create_backend

            sets = clean_copy((_parse(generate_json(create_backend(config["llm"]), prompt, COPY_SCHEMA)) or {}).get("sets"), keywords)
        except Exception as e:
            raise HTTPException(503, f"The AI model could not write the text: {e}") from e
        if not sets:
            raise HTTPException(503, "The AI model did not come up with usable text. Try again.")
        badge = _badge(item, content)
        for entry in sets:                      # never the model's own label
            entry["exchange"] = False
            if badge:
                entry["badge"] = badge
        return {"sets": sets, "keywords": keywords, "source": "model"}

    @app.get("/thumbnails/{publish_id}/design")
    def get_design(publish_id: int):
        path = _file(f"clip_{int(publish_id)}_design.json")
        if not path.exists():
            return {"design": None}
        try:
            return {"design": json.loads(path.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return {"design": None}

    @app.post("/thumbnails/{publish_id}")
    def save(publish_id: int, body: SaveIn):
        """Store the finished thumbnail as THE thumbnail, and its design."""
        from publish.errors import PublishError
        from publish.images import decode_thumbnail

        _item(publish_id)
        encoded = body.image.split(",", 1)[1] if body.image.startswith("data:") else body.image
        try:
            raw = decode_thumbnail(encoded)
        except PublishError as e:
            raise HTTPException(400, e.message) from e
        design = json.dumps(body.design or {})
        if len(design) > MAX_DESIGN_BYTES:
            raise HTTPException(400, "That design is too large to save.")
        _file(f"clip_{int(publish_id)}_chosen.jpg").write_bytes(raw)
        _file(f"clip_{int(publish_id)}_design.json").write_text(design, encoding="utf-8")
        auto_marker(folder, publish_id).write_text("", encoding="utf-8")     # its first one has been made
        return {"saved": True, "bytes": len(raw)}

    @app.get("/thumbnails/{publish_id}/image")
    def image(publish_id: int):
        """The current thumbnail, however it was chosen."""
        path = _file(f"clip_{int(publish_id)}_chosen.jpg")
        if not path.exists():
            raise HTTPException(404, "No thumbnail yet.")
        return FileResponse(str(path), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.delete("/thumbnails/{publish_id}")
    def remove(publish_id: int):
        for name in (f"clip_{int(publish_id)}_chosen.jpg", f"clip_{int(publish_id)}_design.json"):
            _file(name).unlink(missing_ok=True)
        # Deleted on purpose: the app must not quietly make another.
        auto_marker(folder, publish_id).write_text("", encoding="utf-8")
        return {"removed": True}
