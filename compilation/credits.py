"""Per-segment credit lower-thirds, as ASS subtitles.

A credit is burned in the same FFmpeg pass that renders its segment, so it
costs nothing extra. Times are relative to the segment's own start. The style
is a boxed caption (BorderStyle 3) so it stays readable over any footage.

The template has three fields: {channel}, {title}, {url}. A segment with no
channel name falls back to the video's title, then to nothing at all, rather
than printing "Clip: " with an empty name.
"""

from __future__ import annotations

from compilation.recipe import CreditStyle

# ASS numpad alignment (1 2 3 bottom row, 7 8 9 top row).
_ALIGN = {
    "bottom_left": 1, "bottom_center": 2, "bottom_right": 3,
    "top_left": 7, "top_center": 8, "top_right": 9,
}
FADE_MS = 250


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


def build_ass(text: str, style: CreditStyle, canvas: tuple[int, int], duration: float) -> str:
    """A standalone ASS file showing `text` for the first `style.seconds`
    of a segment that lasts `duration`."""
    w, h = canvas
    scale = min(w, h) / 1080
    size = max(14, round(style.font_size * scale))
    margin = round(0.045 * min(w, h))
    outline = max(4, round(size * 0.35))  # box padding around the text
    align = _ALIGN.get(style.position, 1)
    end = min(style.seconds, max(0.5, duration - 0.1))
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
        "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        # White text on a 35%-transparent navy box (BorderStyle 3 = opaque box,
        # filled with OutlineColour). Colours are &HAABBGGRR.
        f"Style: Credit,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H59281A0A,&H00000000,"
        f"-1,0,0,0,100,100,0,0,3,{outline},0,{align},{margin},{margin},{margin},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 3,{_t(0.15)},{_t(end)},Credit,,0,0,0,,{{\\fad({FADE_MS},{FADE_MS})}}{text}\n"
    )
