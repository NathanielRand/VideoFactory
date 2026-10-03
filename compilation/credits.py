"""Per-segment credit lower-thirds, as ASS subtitles, optionally on an image.

A credit is burned in the same FFmpeg pass that renders its segment, so it
costs nothing extra. Times are relative to the segment's own start.

Two looks:
  * TEXT ONLY: the text with a backing drawn by libass: a boxed caption
    (BorderStyle 3), an outline, or nothing.
  * ON A PLATE: an uploaded background image is overlaid (render.py) at the
    credit's corner, `bg_scale` x the font size tall, aspect kept, and the
    text is centred on it. A long name is shrunk to fit the plate rather than
    spilling off it.

The template has three fields: {channel}, {title}, {url}. A segment with no
channel name falls back to the video's title, then to nothing at all, rather
than printing "Clip: " with an empty name.

Everything visual about the credit (font, colours, box, padding, plate size,
fade) is decided in this file; render.py only places what it is handed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from compilation.recipe import CreditStyle

# ASS numpad alignment (1 2 3 bottom row, 7 8 9 top row).
_ALIGN = {
    "bottom_left": 1, "bottom_center": 2, "bottom_right": 3,
    "top_left": 7, "top_center": 8, "top_right": 9,
    "middle_left": 4, "middle_center": 5, "middle_right": 6,
}
FADE_MS = 250
START = 0.15  # seconds into the segment the credit appears
# How much of the plate's width the text may use; the rest is side padding.
PLATE_TEXT_WIDTH = 0.84

# Font files for measuring text (regular, bold) — the same stock-Windows set
# as video/captions.py FONTS. Only used to fit text onto a plate; libass
# finds the fonts by family name itself.
_FONT_FILES = {
    "Arial": ("arial.ttf", "arialbd.ttf"),
    "Arial Black": ("ariblk.ttf", "ariblk.ttf"),
    "Impact": ("impact.ttf", "impact.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf"),
    "Comic Sans MS": ("comic.ttf", "comicbd.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf"),
}


@dataclass(frozen=True)
class Plate:
    """Where the background image goes on the canvas, in pixels."""

    image: Path
    x: int
    y: int
    w: int
    h: int

    def anchor(self, style: CreditStyle) -> tuple[int, int]:
        """Where the text's centre goes: `bg_text_x/y` across the plate."""
        return self.x + round(self.w * style.bg_text_x), self.y + round(self.h * style.bg_text_y)

    def room(self, style: CreditStyle) -> float:
        """How wide the text may be: the padded plate, narrowed when the text
        is off-centre so it stays on the image on both sides."""
        return self.w * PLATE_TEXT_WIDTH * 2 * min(style.bg_text_x, 1 - style.bg_text_x)


def _clean(text: str) -> str:
    """ASS treats {} as override blocks and \\ as escapes; a creator name
    must never be able to restyle or break the subtitle file."""
    return " ".join(str(text).replace("\\", "").replace("{", "(").replace("}", ")").split())


def credit_text(style: CreditStyle, *, channel: str, title: str = "", url: str = "") -> str:
    name = _clean(channel) or _clean(title)
    if not name:
        return ""
    try:
        text = style.template.format(channel=name, title=_clean(title), url=_clean(url))
    except (KeyError, IndexError, ValueError):
        # A typo in the template ("{chanel}") degrades to the plain name
        # instead of failing the whole render.
        text = name
    return _clean(text)


def _t(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    return f"{h}:{m:02d}:{seconds % 60:05.2f}"


def _ass_colour(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def font_px(style: CreditStyle, canvas: tuple[int, int]) -> int:
    """The font size on this canvas: `font_size` is at 1080 on the short edge."""
    return max(14, round(style.font_size * min(canvas) / 1080))


def _margins(style: CreditStyle, canvas: tuple[int, int]) -> tuple[int, int]:
    """(left/right, top/bottom) distance from the frame edges in pixels.
    Unset insets keep the original 4.5% of the short edge on both axes."""
    legacy = round(0.045 * min(canvas))
    mx = legacy if style.inset_x is None else round(style.inset_x * canvas[0])
    my = legacy if style.inset_y is None else round(style.inset_y * canvas[1])
    return mx, my


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def show_window(style: CreditStyle, duration: float) -> tuple[float, float]:
    """(start, end) seconds within the segment."""
    full = max(0.5, duration - 0.1)
    return START, full if style.whole_clip else min(style.seconds, full)


def plate_for(style: CreditStyle, canvas: tuple[int, int], image: Path, image_size: tuple[int, int]) -> Plate:
    """Size and place the background image: `bg_scale` x the font size tall,
    aspect kept, in the credit's corner, never wider than the canvas allows."""
    w, h = canvas
    iw, ih = image_size
    mx, my = _margins(style, canvas)
    ph = round(font_px(style, canvas) * style.bg_scale)
    pw = round(ph * iw / max(1, ih))
    if pw > w - 2 * mx:  # a very wide image: cap the width, keep aspect
        pw = w - 2 * mx
        ph = round(pw * ih / max(1, iw))
    pw, ph = max(2, pw // 2 * 2), max(2, ph // 2 * 2)  # even, for yuv420p
    if style.position == "custom":
        # x / y is the plate's centre; keep the whole plate inside the frame.
        x = _clamp(round(style.x * w - pw / 2), 0, max(0, w - pw))
        y = _clamp(round(style.y * h - ph / 2), 0, max(0, h - ph))
        return Plate(image=image, x=x, y=y, w=pw, h=ph)
    vert, horiz = style.position.split("_")
    x = {"left": mx, "center": (w - pw) // 2, "right": w - pw - mx}[horiz]
    y = {"top": my, "middle": (h - ph) // 2, "bottom": h - ph - my}[vert]
    return Plate(image=image, x=x, y=y, w=pw, h=ph)


def text_width(text: str, style: CreditStyle, px: int) -> float:
    """Rendered width of `text` in pixels: measured with the real font when
    Pillow and the font file are there, estimated otherwise."""
    try:
        from PIL import ImageFont

        name = _FONT_FILES.get(style.font, _FONT_FILES["Arial"])[1 if style.bold else 0]
        font = ImageFont.truetype(str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name), px)
        return float(font.getlength(text))
    except Exception:
        return len(text) * px * (0.62 if style.bold else 0.56)


def fit_px(text: str, style: CreditStyle, canvas: tuple[int, int], plate: Plate) -> int:
    """The font size that keeps `text` inside the plate: the chosen size, or
    smaller for a long name. Never taller than the plate either."""
    px = min(font_px(style, canvas), round(plate.h * 0.8))
    room = plate.room(style)
    width = text_width(text, style, px)
    if width > room:
        px = int(px * room / width)
    return max(10, px)


def build_ass(
    text: str, style: CreditStyle, canvas: tuple[int, int], duration: float, plate: Plate | None = None
) -> str:
    """A standalone ASS file showing `text` for the credit's window of a
    segment that lasts `duration`: in its corner, or centred on `plate`."""
    w, h = canvas
    mx, my = _margins(style, canvas)
    start, end = show_window(style, duration)
    primary = _ass_colour(style.color)
    if plate is not None:
        size = fit_px(text, style, canvas, plate)
        # The plate is the backing; a thin shadow keeps text legible on a
        # busy image without boxing it in.
        border, outline, shadow, back = 1, 0, max(1, round(size * 0.04)), "&H80000000"
        align = 5
        cx, cy = plate.anchor(style)
        pos = f"\\pos({cx},{cy})"
    else:
        size = font_px(style, canvas)
        align = _ALIGN.get(style.position, 1)
        pos = ""
        if style.backing == "box":
            # BorderStyle 3 = opaque box filled with OutlineColour, a 35%
            # transparent navy; Outline is the box padding.
            border, outline, shadow, back = 3, max(4, round(size * 0.35)), 0, "&H00000000"
        elif style.backing == "outline":
            border, outline, shadow, back = 1, max(2, round(size * 0.08)), max(1, round(size * 0.05)), "&H80000000"
        else:
            border, outline, shadow, back = 1, 0, 0, "&H00000000"
        if style.position == "custom":
            # Centred on the chosen point, nudged in so the text and its box
            # stay inside the frame.
            align = 5
            half_w = text_width(text, style, size) / 2 + outline
            half_h = size * 0.6 + outline
            cx = _clamp(round(style.x * w), round(half_w), max(round(half_w), w - round(half_w)))
            cy = _clamp(round(style.y * h), round(half_h), max(round(half_h), h - round(half_h)))
            pos = f"\\pos({cx},{cy})"
    box = "&H59281A0A" if style.backing == "box" and plate is None else "&H00000000"
    bold = -1 if style.bold else 0
    italic = -1 if style.italic else 0
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        # On a plate the text is fitted to one line (WrapStyle 2: never wrap).
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: {2 if plate else 0}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Credit,{style.font},{size},{primary},{primary},{box},{back},"
        f"{bold},{italic},0,0,100,100,0,0,{border},{outline},{shadow},{align},{mx},{mx},{my},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 3,{_t(start)},{_t(end)},Credit,,0,0,0,,{{\\fad({FADE_MS},{FADE_MS}){pos}}}{text}\n"
    )
