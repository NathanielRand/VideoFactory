"""The compilation recipe: what a user builds, as data.

Stored as JSON on the `compilations` row and validated here into dataclasses
before anything renders. Every limit is enforced by clamping or refusing in
this one place, so render.py can trust what it is handed.

    {
      "canvas": "16:9",                 # 16:9 | 9:16 | 1:1 | 4:5 (the primary format)
      "outputs": ["16:9", "9:16"],      # every format to render; default [canvas]
      "fit": "blur",                    # blur | pad | crop: filling a mismatched aspect
      "segments": [
        {"video_id": "abc123", "start": 42.0, "end": 58.5,
         "credit": true, "volume": 1.0,
         "blur_regions": [[0.0, 0.85, 1.0, 0.15]]}   # x, y, w, h as source fractions
      ],
      "transition": {"type": "fade", "duration": 0.5},
      "credits": {"enabled": true, "template": "Clip: {channel}",
                  "seconds": 4.0, "whole_clip": false,   # whole_clip ignores seconds
                  "position": "bottom_left", "font_size": 44,   # or middle_*, or "custom" + x, y
                  "inset_x": 0.06, "inset_y": 0.14,      # optional: distance in from the edges
                  "x": 0.5, "y": 0.5,                    # "custom": the credit's centre
                  "font": "Arial", "bold": true, "italic": false, "color": "#FFFFFF",
                  "backing": "box",                      # box | outline | none
                  "bg_image": "<hash>.png" | null,       # a branding asset: a plate
                  "bg_scale": 2.4,                       # plate height / font size
                  "bg_text_x": 0.5, "bg_text_y": 0.5},   # text centre on the plate
      "banner": {...watermark config...} | {"profile_id": 3} | {"profile_id": 3, "custom": {...}} | null,
      "credits_custom": bool,   # false: the banner profile's credit is used; true: "credits" above
      "intro": {"path": "D:/brand/intro.mp4"} | null,
      "outro": {"path": "D:/brand/outro.mp4"} | null,
      "normalize_audio": true,          # match every part's loudness (compilation/loudness.py)
      "loudness_target": -14            # LUFS: -14 | -16 | -12
    }

A TEMPLATE is the same object without "segments": the reusable look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import safe_name
from formats.profiles import CANVASES
from video.captions import FONTS

FITS = ("blur", "pad", "crop")

# FFmpeg xfade transition names we expose. "none" is a hard cut, joined
# losslessly with the concat demuxer instead of re-encoding.
TRANSITIONS = (
    "none", "fade", "fadeblack", "fadewhite", "dissolve",
    "wipeleft", "wiperight", "slideleft", "slideright",
    "smoothleft", "smoothright", "circleopen", "zoomin",
)
BACKINGS = ("box", "outline", "none")
POSITIONS = (
    "bottom_left", "bottom_right", "top_left", "top_right", "bottom_center", "top_center",
    "middle_left", "middle_right", "middle_center", "custom",
)
# How far in from the edges a credit sits, as a fraction of the frame's width
# (inset_x) and height (inset_y). None keeps the original 4.5% of the short edge.
MAX_INSET = 0.45

MIN_SEGMENT = 0.5          # seconds; shorter is a flash, not a clip
MAX_SEGMENTS = 200
MAX_TRANSITION = 2.0
MAX_BLUR_REGIONS = 8
TEMPLATE_KEYS = ("canvas", "outputs", "fit", "transition", "credits", "credits_custom", "banner", "intro",
                 "outro", "normalize_audio", "loudness_target")
# Loudness targets offered (LUFS): what YouTube, TikTok and Instagram
# normalise to, a quieter podcast-style level, and a louder one.
LOUDNESS_TARGETS = (-14.0, -16.0, -12.0)
# A segment's volume is a multiplier; 4.0 is +12 dB, the most it may add.
MAX_VOLUME = 4.0


class RecipeError(ValueError):
    """The recipe cannot be rendered; the message says why, in words."""


@dataclass
class SegmentSpec:
    video_id: str
    start: float
    end: float
    credit: bool = True
    volume: float = 1.0
    blur_regions: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class CreditStyle:
    enabled: bool = True
    template: str = "Clip: {channel}"
    seconds: float = 4.0
    whole_clip: bool = False  # show for the whole segment; `seconds` is ignored
    position: str = "bottom_left"
    font_size: int = 44
    font: str = "Arial"
    bold: bool = True
    italic: bool = False
    color: str = "#FFFFFF"
    backing: str = "box"  # behind the text when there is no bg_image
    # A background image (a branding asset filename) the text sits centred
    # on, scaled to `bg_scale` x the font size tall, aspect kept.
    bg_image: str | None = None
    bg_scale: float = 2.4
    # Where the text's centre sits on the plate, as fractions of it — for an
    # image with artwork on one side.
    bg_text_x: float = 0.5
    bg_text_y: float = 0.5
    # Distance from the frame edges, so a credit can clear a platform's own
    # overlays (YouTube's top bar, the title and buttons on a Short). None is
    # the original margin. Ignored for "custom", which is placed by x / y.
    inset_x: float | None = None
    inset_y: float | None = None
    # "custom": where the credit's centre sits, as fractions of the frame.
    x: float = 0.5
    y: float = 0.5


@dataclass
class Recipe:
    canvas: str = "16:9"
    outputs: list[str] = field(default_factory=list)  # every format; [0] is `canvas`
    fit: str = "blur"
    segments: list[SegmentSpec] = field(default_factory=list)
    transition: str = "none"
    transition_duration: float = 0.5
    credits: CreditStyle = field(default_factory=CreditStyle)
    banner: dict | None = None
    intro: Path | None = None
    outro: Path | None = None
    normalize_audio: bool = True
    loudness_target: float = -14.0  # LUFS every part is matched to (compilation/loudness.py)

    @property
    def size(self) -> tuple[int, int]:
        return CANVASES[self.canvas]


def _num(value, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise RecipeError(f"{name} must be a number, got {value!r}") from None


def _bumper(raw, name: str, check_files: bool) -> Path | None:
    if not raw:
        return None
    path_str = raw.get("path") if isinstance(raw, dict) else raw
    if not isinstance(path_str, str) or not path_str.strip():
        return None
    path = Path(path_str.strip())
    if check_files and not path.is_file():
        raise RecipeError(f"The {name} file does not exist: {path}")
    return path


def _credit_look(cr: dict) -> dict:
    font = str(cr.get("font") or "Arial")
    if font not in FONTS:
        raise RecipeError(f"credit font must be one of {', '.join(FONTS)}")
    color = str(cr.get("color") or "#FFFFFF")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise RecipeError("credit colour must be #RRGGBB")
    backing = str(cr.get("backing") or "box")
    if backing not in BACKINGS:
        raise RecipeError(f"credit backing must be one of {', '.join(BACKINGS)}")
    bg_image = None
    if cr.get("bg_image"):
        # A filename in the branding assets folder, never a path: it is
        # handed to FFmpeg as an input.
        bg_image = safe_name(str(cr["bg_image"]))
        if bg_image is None:
            raise RecipeError("credit background image must be an uploaded asset name")
    return {
        "font": font,
        "bold": bool(cr.get("bold", True)),
        "italic": bool(cr.get("italic", False)),
        "color": color.upper(),
        "backing": backing,
        "bg_image": bg_image,
        "bg_scale": max(1.2, min(6.0, _num(cr.get("bg_scale", 2.4), "credit background size"))),
        "bg_text_x": max(0.1, min(0.9, _num(cr.get("bg_text_x", 0.5), "credit text position"))),
        "bg_text_y": max(0.1, min(0.9, _num(cr.get("bg_text_y", 0.5), "credit text position"))),
    }


def _inset(raw) -> float | None:
    if raw is None or raw == "":
        return None
    return max(0.0, min(MAX_INSET, _num(raw, "credit edge distance")))


def credit_style(cr: dict | None) -> CreditStyle:
    """A CreditStyle from its stored dict, validated and clamped. Shared by
    compilations (recipe["credits"]) and clips (a branding profile's "credit")."""
    cr = cr or {}
    position = str(cr.get("position") or "bottom_left")
    if position not in POSITIONS:
        raise RecipeError(f"credits position must be one of {', '.join(POSITIONS)}")
    template = str(cr.get("template") if cr.get("template") is not None else "Clip: {channel}")
    return CreditStyle(
        enabled=bool(cr.get("enabled", True)),
        template=template[:120],
        seconds=max(1.0, min(15.0, _num(cr.get("seconds", 4.0), "credit seconds"))),
        whole_clip=bool(cr.get("whole_clip", False)),
        position=position,
        font_size=int(max(16, min(120, _num(cr.get("font_size", 44), "credit font size")))),
        inset_x=_inset(cr.get("inset_x")),
        inset_y=_inset(cr.get("inset_y")),
        x=max(0.0, min(1.0, _num(cr.get("x", 0.5), "credit position"))),
        y=max(0.0, min(1.0, _num(cr.get("y", 0.5), "credit position"))),
        **_credit_look(cr),
    )


def _regions(raw, where: str) -> list[tuple[float, float, float, float]]:
    out = []
    for r in (raw or [])[:MAX_BLUR_REGIONS]:
        try:
            x, y, w, h = (max(0.0, min(1.0, float(v))) for v in r)
        except (TypeError, ValueError):
            raise RecipeError(f"{where}: a blur region must be [x, y, w, h] fractions") from None
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        if w > 0.005 and h > 0.005:
            out.append((x, y, w, h))
    return out


def parse(
    data: dict,
    *,
    durations: dict[str, float] | None = None,
    check_files: bool = True,
    require_segments: bool = True,
) -> Recipe:
    """Validate a recipe dict. `durations` maps video_id -> source length and,
    when given, every segment must exist in it and fit inside it."""
    if not isinstance(data, dict):
        raise RecipeError("The recipe must be a JSON object.")

    canvas = str(data.get("canvas") or "16:9")
    if canvas not in CANVASES:
        raise RecipeError(f"canvas must be one of {', '.join(CANVASES)}")
    raw_outputs = data.get("outputs") or [canvas]
    if not isinstance(raw_outputs, list):
        raise RecipeError("outputs must be a list of formats")
    outputs: list[str] = []
    for o in raw_outputs:
        if str(o) not in CANVASES:
            raise RecipeError(f"output format {o!r} is not one of {', '.join(CANVASES)}")
        if str(o) not in outputs:
            outputs.append(str(o))
    # The primary format is always the first output.
    canvas = outputs[0]
    fit = str(data.get("fit") or "blur")
    if fit not in FITS:
        raise RecipeError(f"fit must be one of {', '.join(FITS)}")

    raw_segments = data.get("segments") or []
    if not isinstance(raw_segments, list):
        raise RecipeError("segments must be a list")
    if len(raw_segments) > MAX_SEGMENTS:
        raise RecipeError(f"A compilation can hold at most {MAX_SEGMENTS} segments.")
    segments = []
    for i, s in enumerate(raw_segments, 1):
        where = f"Segment {i}"
        if not isinstance(s, dict) or not str(s.get("video_id") or "").strip():
            raise RecipeError(f"{where} has no video_id.")
        vid = str(s["video_id"]).strip()
        start = max(0.0, _num(s.get("start", 0), f"{where} start"))
        end = _num(s.get("end", 0), f"{where} end")
        if durations is not None:
            if vid not in durations:
                raise RecipeError(f"{where}: video {vid!r} is not in the library (import it first).")
            if durations[vid] > 0:
                end = min(end, durations[vid])
        if end - start < MIN_SEGMENT:
            raise RecipeError(f"{where} is shorter than {MIN_SEGMENT}s (start {start:g}, end {end:g}).")
        segments.append(SegmentSpec(
            video_id=vid,
            start=round(start, 3),
            end=round(end, 3),
            credit=bool(s.get("credit", True)),
            volume=max(0.0, min(MAX_VOLUME, _num(s.get("volume", 1.0), f"{where} volume"))),
            blur_regions=_regions(s.get("blur_regions"), where),
        ))
    if require_segments and not segments:
        raise RecipeError("Add at least one segment before rendering.")

    tr = data.get("transition") or {}
    if isinstance(tr, str):
        tr = {"type": tr}
    ttype = str(tr.get("type") or "none")
    if ttype not in TRANSITIONS:
        raise RecipeError(f"transition must be one of {', '.join(TRANSITIONS)}")
    tdur = max(0.1, min(MAX_TRANSITION, _num(tr.get("duration", 0.5), "transition duration")))
    if ttype != "none" and segments:
        # A transition eats `tdur` from BOTH neighbours, so it can never be
        # longer than half the shortest part it touches.
        shortest = min(sg.duration for sg in segments)
        tdur = min(tdur, shortest / 2 - 0.05)
        if tdur < 0.1:
            ttype = "none"

    credits = credit_style(data.get("credits"))

    banner = data.get("banner")
    if banner is not None and not isinstance(banner, dict):
        raise RecipeError("banner must be an object (a watermark config or {profile_id})")

    return Recipe(
        canvas=canvas,
        outputs=outputs,
        fit=fit,
        segments=segments,
        transition=ttype,
        transition_duration=round(tdur, 3),
        credits=credits,
        banner=banner or None,
        intro=_bumper(data.get("intro"), "intro", check_files),
        outro=_bumper(data.get("outro"), "outro", check_files),
        normalize_audio=bool(data.get("normalize_audio", True)),
        loudness_target=_loudness_target(data.get("loudness_target")),
    )


def _loudness_target(value) -> float:
    if value is None:
        return LOUDNESS_TARGETS[0]
    target = _num(value, "loudness target")
    if target not in LOUDNESS_TARGETS:
        raise RecipeError(
            f"loudness target must be one of {', '.join(f'{t:g}' for t in LOUDNESS_TARGETS)} LUFS"
        )
    return target


def template_of(data: dict) -> dict:
    """The reusable look of a recipe: everything but its segments."""
    return {k: data[k] for k in TEMPLATE_KEYS if k in data}


def apply_template(template: dict, recipe: dict) -> dict:
    """A recipe with the template's look laid over it, keeping its segments."""
    merged = dict(recipe)
    merged.update(template_of(template))
    return merged
