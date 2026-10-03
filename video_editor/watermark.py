"""Watermark & branding: burn a logo and/or text into a rendered clip.

Two mechanisms, matching how the rest of the app burns overlays:
  * TEXT  -> an ASS event (like captions and the hook title). It folds into
    the clip's existing subtitle burn at zero extra cost, scales to the
    canvas, and supports outline/shadow via libass. Works on every render
    path that burns an ASS file.
  * IMAGE -> a logo overlaid via FFmpeg (transparent PNG, aspect preserved,
    never stretched). Runs as one extra pass ONLY when an image is set;
    text-only branding costs nothing.

Config schema (stored in a branding profile or a clip's render_opts.watermark):
  {
    "type": "image" | "text" | "both",
    "text": "@YourChannel", "font": "Arial", "font_size": 42,
    "color": "#FFFFFF", "opacity": 0.85,
    "position": "bottom_right",   # + top_left/top_right/bottom_left/center
    "padding": 0.04,              # fraction of the SHORTER edge
    "scale": 0.18,               # image width as a fraction of the frame width
    "rotation": 0, "shadow": true,
    "image_asset": "<hash>.png",  # filename under the branding assets dir
    "frame": "free",              # logo crop: free (as drawn) | square | circle
    "cta": {                      # optional timed call to action (see below)
      "enabled": true, "kind": "discord", "text": "Send us your clips on Discord",
      "anchor": "start", "at": 3, "duration": 4, "repeat": 0,
      "position": "top", "color": "#FFFFFF", "bg": "#5865F2"
    }
  }

The CTA is a boxed line of text that pops in for `duration` seconds, starting
`at` seconds after the clip starts (anchor "start") or before it ends (anchor
"end"), and again every `repeat` seconds when that is set. Like watermark text
it is an ASS event, so it rides the existing subtitle burn for free.

Positions are computed against the OUTPUT frame (1080x1920 or 1920x1080), so
one config scales correctly for both vertical Shorts and horizontal video.
"""

import math
import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from core.paths import discard, safe_name
from video.encoding import video_encoder_args

# ASS numpad alignment per named position (7 8 9 / 4 5 6 / 1 2 3).
_ALIGN = {
    "top_left": 7, "top_right": 9,
    "bottom_left": 1, "bottom_right": 3,
    "center": 5,
}
# overlay x:y expressions per position, `P` = padding px, W/H = frame, w/h = logo.
_OVERLAY_XY = {
    "top_left": ("{p}", "{p}"),
    "top_right": ("W-w-{p}", "{p}"),
    "bottom_left": ("{p}", "H-h-{p}"),
    "bottom_right": ("W-w-{p}", "H-h-{p}"),
    "center": ("(W-w)/2", "(H-h)/2"),
}

# "moving" (TikTok-style anti-crop): the watermark stays at one side edge-
# centre, drifting in a small circle, then TELEPORTS to the other side —
# never top/bottom (platform UI covers those).
#
# It used to slide across the frame, which dragged the logo straight through
# the middle of the shot and over the subject's face. Teleporting keeps the
# anti-crop property — the mark occupies both sides over time, so no single
# crop removes it — without ever entering the picture's centre. The little
# orbit is what catches the eye, replacing the movement the slide provided.
MOVE_DWELL = 3.4        # seconds at a side = one full revolution
CIRCLE_FRAC = 0.022     # orbit radius as a fraction of the short frame edge
CIRCLE_STEPS = 16       # segments approximating the circle in ASS (text mode)


def has_text(cfg: dict) -> bool:
    return cfg.get("type") in ("text", "both") and bool(str(cfg.get("text", "")).strip())


def has_cta(cfg: dict) -> bool:
    cta = cfg.get("cta")
    return isinstance(cta, dict) and bool(cta.get("enabled")) and bool(_clean(cta.get("text")))


def has_overlay_text(cfg: dict) -> bool:
    """Anything that burns through the ASS file: watermark text and/or a CTA."""
    return has_text(cfg) or has_cta(cfg)


def has_image(cfg: dict, asset_dir: Path) -> bool:
    if cfg.get("type") not in ("image", "both"):
        return False
    name = safe_name(str(cfg.get("image_asset") or ""))
    return bool(name) and _asset_in(asset_dir, name) is not None


def _clean(text) -> str:
    """Plain text safe inside an ASS event: no override blocks, no escapes,
    and no raw line breaks, which would end the event mid-line and corrupt
    every event after it. A typed break becomes a space."""
    text = str(text or "").replace("\\", "").replace("{", "").replace("}", "")
    return " ".join(text.split())


def _ass_color(hex_rgb: str) -> str:
    h = str(hex_rgb).lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _text_style(cfg: dict, canvas: tuple[int, int]) -> str:
    """A watermark ASS style, scaled to the canvas height."""
    s = canvas[1] / 1920
    size = max(12, round(int(cfg.get("font_size", 42)) * s))
    color = _ass_color(cfg.get("color", "#FFFFFF"))
    align = _ALIGN.get(cfg.get("position", "bottom_right"), 3)
    # alpha in ASS is inverted (00 opaque, FF transparent) and prefixes colour.
    alpha = max(0, min(255, round((1 - float(cfg.get("opacity", 0.85))) * 255)))
    primary = f"&H{alpha:02X}{color[4:]}"  # splice alpha onto the BBGGRR colour
    outline = 2 if cfg.get("shadow", True) else 1
    shadow = 2 if cfg.get("shadow", True) else 0
    margin = round(float(cfg.get("padding", 0.04)) * min(canvas))
    font = str(cfg.get("font", "Arial"))
    angle = -float(cfg.get("rotation", 0) or 0)  # ASS angle is counter-clockwise
    return (
        f"Style: Watermark,{font},{size},{primary},{primary},&H00000000,&H7F000000,"
        f"0,0,0,0,100,100,0,{angle},1,{outline},{shadow},{align},"
        f"{margin},{margin},{margin},1"
    )


def _minimal_ass(style: str, event: str, canvas: tuple[int, int]) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {canvas[0]}\nPlayResY: {canvas[1]}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text\n" + event + "\n"
    )


def _ass_t(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _text_events(text: str, cfg: dict, duration: float, canvas: tuple[int, int]) -> str:
    """One whole-clip event, or hops between the side edge-centres ('moving'),
    or a fixed dragged point ('custom' with x,y as frame fractions)."""
    pos = cfg.get("position")
    if pos == "custom":
        cx = round(max(0.0, min(1.0, float(cfg.get("x", 0.5)))) * canvas[0])
        cy = round(max(0.0, min(1.0, float(cfg.get("y", 0.5)))) * canvas[1])
        return f"Dialogue: 2,0:00:00.00,9:59:59.99,Watermark,,0,0,0,,{{\\an5\\pos({cx},{cy})}}{text}"
    if pos != "moving":
        # Layer 2 so branding sits above captions; 9:59:59 = "whole clip".
        return f"Dialogue: 2,0:00:00.00,9:59:59.99,Watermark,,0,0,0,,{text}"
    # Orbit a small circle at one side edge-centre, then teleport to the
    # other. ASS has no expression language, so the circle is walked as a
    # ring of short \move segments; at CIRCLE_STEPS a revolution the corners
    # are invisible. Teleporting is simply the next event starting at the
    # other side with nothing tweening between the two.
    dur = max(MOVE_DWELL, duration or 60.0)
    cw, ch = canvas
    radius = max(2, round(min(cw, ch) * CIRCLE_FRAC))
    rx, lx, my = round(cw * 0.82), round(cw * 0.18), round(ch * 0.5)
    step = MOVE_DWELL / CIRCLE_STEPS

    def point(cx: int, k: int) -> tuple[int, int]:
        a = 2 * math.pi * (k % CIRCLE_STEPS) / CIRCLE_STEPS
        return round(cx + radius * math.cos(a)), round(my + radius * math.sin(a))

    events, t, side = [], 0.0, 0
    while t < dur:
        cx = rx if side % 2 == 0 else lx
        for k in range(CIRCLE_STEPS):
            if t >= dur:
                break
            end = min(t + step, dur)
            x1, y1 = point(cx, k)
            x2, y2 = point(cx, k + 1)
            tag = f"{{\\an5\\move({x1},{y1},{x2},{y2})}}"
            events.append(f"Dialogue: 2,{_ass_t(t)},{_ass_t(end)},Watermark,,0,0,0,,{tag}{text}")
            t = end
        side += 1
    return "\n".join(events)


# CTA vertical placement as a fraction of the frame height. "bottom" sits
# just above where bottom captions start, so the two never overlap.
_CTA_Y = {"top": 0.17, "middle": 0.5, "bottom": 0.62}
CTA_POP = 0.18  # seconds for the pop-in scale


def _cta_style(cta: dict, canvas: tuple[int, int]) -> str:
    """A boxed (BorderStyle 3) style: the box takes the CTA's background colour."""
    s = canvas[1] / 1920
    size = max(14, round(int(cta.get("font_size", 54)) * s))
    fg = _ass_color(cta.get("color", "#FFFFFF"))
    bg = _ass_color(cta.get("bg", "#111111"))
    pad = max(4, round(18 * s))  # box padding = outline width in style 3
    margin = round(canvas[0] * 0.08)
    font = str(cta.get("font", "Arial Black"))
    return (
        f"Style: CTA,{font},{size},{fg},{fg},{bg},{bg},"
        f"-1,0,0,0,100,100,0,0,3,{pad},0,5,{margin},{margin},0,1"
    )


def cta_windows(cta: dict, duration: float) -> list[tuple[float, float]]:
    """When the CTA shows: (start, end) pairs, clipped to the clip."""
    length = max(0.5, float(cta.get("duration", 4) or 4))
    at = max(0.0, float(cta.get("at", 0) or 0))
    total = duration if duration and duration > 0 else 60.0
    first = total - at - length if cta.get("anchor") == "end" else at
    first = max(0.0, first)
    repeat = max(0.0, float(cta.get("repeat", 0) or 0))
    out: list[tuple[float, float]] = []
    t = first
    while t < total and len(out) < 200:
        out.append((t, min(t + length, total)))
        if repeat <= 0:
            break
        t += max(repeat, length + 0.5)  # never overlap the previous showing
    return out


def _cta_events(cta: dict, duration: float, canvas: tuple[int, int]) -> str:
    text = _clean(cta.get("text"))
    x = round(canvas[0] / 2)
    y = round(_CTA_Y.get(cta.get("position", "top"), _CTA_Y["top"]) * canvas[1])
    pop = round(CTA_POP * 1000)
    # Pop in from 70% with a fade, fade out; layer 3 sits above the watermark.
    tag = f"{{\\an5\\pos({x},{y})\\fad(150,200)\\fscx70\\fscy70\\t(0,{pop},\\fscx100\\fscy100)}}"
    return "\n".join(
        f"Dialogue: 3,{_ass_t(a)},{_ass_t(b)},CTA,,0,0,0,,{tag}{text}"
        for a, b in cta_windows(cta, duration)
    )


def ensure_text(
    ass_path: Path | None,
    target: Path,
    cfg: dict,
    canvas: tuple[int, int],
    duration: float = 0.0,
    with_cta: bool = True,
) -> Path:
    """Merge the watermark text and the CTA into the clip's ASS file (or write
    a standalone one). Static by default; 'moving' position hops it around the
    edges (TikTok-style, anti-crop). Returns the ASS file to burn."""
    styles, events = [], []
    if has_text(cfg):
        styles.append(_text_style(cfg, canvas))
        events.append(_text_events(_clean(cfg["text"]), cfg, duration, canvas))
    if with_cta and has_cta(cfg):
        styles.append(_cta_style(cfg["cta"], canvas))
        events.append(_cta_events(cfg["cta"], duration, canvas))
    style, event = "\n".join(styles), "\n".join(e for e in events if e)
    if ass_path is not None and ass_path.exists():
        content = ass_path.read_text(encoding="utf-8")
        content = content.replace("\n[Events]", f"\n{style}\n\n[Events]", 1)
        content = content.rstrip("\n") + "\n" + event + "\n"
        ass_path.write_text(content, encoding="utf-8")
        return ass_path
    target.write_text(_minimal_ass(style, event, canvas), encoding="utf-8")
    return target


def _asset_in(asset_dir: Path, name: str) -> Path | None:
    """The asset file itself, found by listing the folder rather than by
    joining a string onto it. None when nothing in there is called that."""
    try:
        for entry in asset_dir.iterdir():
            if entry.name == name and entry.is_file():
                return entry
    except OSError:  # folder missing: no assets, same answer
        return None
    return None


def _frame_chain(frame: str | None, logo_w: int) -> str:
    """Scale the logo to `logo_w` and crop it to its frame. Square and circle
    take the centred square of the image; the circle then masks the corners
    to transparent with a one-pixel soft edge so it isn't jagged."""
    if frame not in ("square", "circle"):
        return f"scale={logo_w}:-1:flags=lanczos,format=rgba"
    chain = (
        "crop='min(iw,ih)':'min(iw,ih)',"
        f"scale={logo_w}:{logo_w}:flags=lanczos,format=rgba"
    )
    if frame == "circle":
        edge = "clip(W/2-hypot(X+0.5-W/2,Y+0.5-H/2)+0.5,0,1)"
        chain += f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='alpha(X,Y)*{edge}'"
    return chain


def apply_image(video_path: Path, cfg: dict, canvas: tuple[int, int], asset_dir: Path) -> None:
    """Overlay the logo onto the video IN PLACE (transparent PNG, aspect kept,
    positioned + scaled + faded per the config). One extra encode; runs only
    when an image watermark is set."""
    # The asset name comes from a saved branding profile, which is user
    # supplied JSON — not from the upload endpoint, which generates a content
    # hash. Nothing checked it, so "../../../<anything>" resolved happily and
    # was then handed to FFmpeg as an input file. Two checks: the name must be
    # a plain filename, and the result must still land inside the asset folder.
    name = safe_name(str(cfg.get("image_asset") or ""))
    if not name:
        raise ValueError(f"invalid branding asset name: {cfg.get('image_asset')!r}")
    # Take the path from the FOLDER rather than building it from the name.
    # Same file, same resulting command, but the value now originates in a
    # directory listing instead of in stored config — so it can only ever be
    # something that genuinely sits in the assets folder. The name check
    # above still runs first; this is the structural half of it.
    logo = _asset_in(asset_dir, name)
    if logo is None:
        raise ValueError(f"branding asset not found in the assets folder: {name!r}")
    w, h = canvas
    pad = round(float(cfg.get("padding", 0.04)) * min(w, h))
    logo_w = max(8, round(float(cfg.get("scale", 0.18)) * w))
    opacity = max(0.0, min(1.0, float(cfg.get("opacity", 0.85))))
    if cfg.get("position") == "custom":
        # Dragged point: x,y are frame fractions for the logo's CENTRE.
        fx = max(0.0, min(1.0, float(cfg.get("x", 0.5))))
        fy = max(0.0, min(1.0, float(cfg.get("y", 0.5))))
        xexpr, yexpr = f"W*{fx:.4f}-w/2", f"H*{fy:.4f}-h/2"
    elif cfg.get("position") == "moving":
        # Orbit a small circle at one side, then TELEPORT to the other. The
        # side is a step function of t — nothing interpolates between them,
        # so the logo never crosses the middle of the shot.
        radius = max(2, round(min(w, h) * CIRCLE_FRAC))
        inset = pad + radius            # keep the whole orbit inside the pad
        rx, lx = f"(W-w-{inset})", f"{inset}"
        d = MOVE_DWELL
        side = f"if(lt(mod(t,{2 * d:g}),{d:g}),{rx},{lx})"   # which edge, now
        ang = f"(2*PI*t/{d:g})"                              # one turn per stay
        xexpr = f"{side}+{radius}*cos({ang})"
        yexpr = f"(H-h)/2+{radius}*sin({ang})"
    else:
        xexpr, yexpr = _OVERLAY_XY.get(cfg.get("position", "bottom_right"), _OVERLAY_XY["bottom_right"])
        xexpr, yexpr = xexpr.format(p=pad), yexpr.format(p=pad)

    logo_chain = f"[1:v]{_frame_chain(cfg.get('frame'), logo_w)},colorchannelmixer=aa={opacity:.3f}"
    rot = float(cfg.get("rotation", 0) or 0)
    if abs(rot) > 0.1:
        logo_chain += f",rotate={rot}*PI/180:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180):c=none"
    # Commas inside the overlay x/y EXPRESSION (mod/if/…) must be escaped so
    # the filtergraph parser doesn't read them as filter separators.
    xe, ye = xexpr.replace(",", "\\,"), yexpr.replace(",", "\\,")
    graph = f"{logo_chain}[wm];[0:v][wm]overlay={xe}:{ye}"

    tmp = video_path.with_suffix(".wm.mp4")
    cmd = [
        ffmpeg(), "-y",
        "-i", str(video_path.resolve()),
        "-i", str(logo.resolve()),
        "-filter_complex", graph,
        "-map", "0:a?",   # keep audio untouched
        *video_encoder_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        discard(tmp)
        raise RuntimeError(f"watermark overlay failed:\n{result.stderr[-1500:]}")
    tmp.replace(video_path)
