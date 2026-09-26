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
                  "seconds": 4.0, "position": "bottom_left", "font_size": 44},
      "banner": {...watermark config...} | {"profile_id": 3} | null,
      "intro": {"path": "D:/brand/intro.mp4"} | null,
      "outro": {"path": "D:/brand/outro.mp4"} | null,
      "normalize_audio": true
    }

A TEMPLATE is the same object without "segments": the reusable look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from formats.profiles import CANVASES

FITS = ("blur", "pad", "crop")

# FFmpeg xfade transition names we expose. "none" is a hard cut, joined
# losslessly with the concat demuxer instead of re-encoding.
TRANSITIONS = (
    "none", "fade", "fadeblack", "fadewhite", "dissolve",
    "wipeleft", "wiperight", "slideleft", "slideright",
    "smoothleft", "smoothright", "circleopen", "zoomin",
)
POSITIONS = ("bottom_left", "bottom_right", "top_left", "top_right", "bottom_center", "top_center")

MIN_SEGMENT = 0.5          # seconds; shorter is a flash, not a clip
MAX_SEGMENTS = 200
MAX_TRANSITION = 2.0
MAX_BLUR_REGIONS = 8
TEMPLATE_KEYS = ("canvas", "outputs", "fit", "transition", "credits", "banner", "intro", "outro", "normalize_audio")


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
    position: str = "bottom_left"
    font_size: int = 44


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
            volume=max(0.0, min(2.0, _num(s.get("volume", 1.0), f"{where} volume"))),
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

    cr = data.get("credits") or {}
    position = str(cr.get("position") or "bottom_left")
    if position not in POSITIONS:
        raise RecipeError(f"credits position must be one of {', '.join(POSITIONS)}")
    template = str(cr.get("template") if cr.get("template") is not None else "Clip: {channel}")
    credits = CreditStyle(
        enabled=bool(cr.get("enabled", True)),
        template=template[:120],
        seconds=max(1.0, min(15.0, _num(cr.get("seconds", 4.0), "credit seconds"))),
        position=position,
        font_size=int(max(16, min(120, _num(cr.get("font_size", 44), "credit font size")))),
    )

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
    )


def template_of(data: dict) -> dict:
    """The reusable look of a recipe: everything but its segments."""
    return {k: data[k] for k in TEMPLATE_KEYS if k in data}


def apply_template(template: dict, recipe: dict) -> dict:
    """A recipe with the template's look laid over it, keeping its segments."""
    merged = dict(recipe)
    merged.update(template_of(template))
    return merged
