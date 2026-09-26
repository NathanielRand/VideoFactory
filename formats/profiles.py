"""Canvases and platform profiles.

A CANVAS is what we render: a frame size. A PROFILE is where a video goes:
a platform surface with the canvas it wants and its length limit. Several
profiles share a canvas (Shorts, TikTok and Reels are all 9:16), so a render
is done once per canvas, never once per platform.

Limits are the platforms' own caps for these surfaces, checked in 2026. They
drive warnings, not refusals: an over-length Short still uploads, it just
stops being a Short.
"""

from __future__ import annotations

CANVASES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
DEFAULT_CANVAS = "9:16"

# id -> label, canvas, max seconds (None = no practical cap), what it is for.
PROFILES: dict[str, dict] = {
    "youtube_shorts": {"label": "YouTube Shorts", "canvas": "9:16", "max_seconds": 180},
    "tiktok": {"label": "TikTok", "canvas": "9:16", "max_seconds": 600},
    "instagram_reels": {"label": "Instagram Reels", "canvas": "9:16", "max_seconds": 180},
    "facebook_reels": {"label": "Facebook Reels", "canvas": "9:16", "max_seconds": 90},
    "youtube": {"label": "YouTube (long-form)", "canvas": "16:9", "max_seconds": None},
    "x": {"label": "X", "canvas": "16:9", "max_seconds": 140},
    "instagram_feed": {"label": "Instagram feed", "canvas": "4:5", "max_seconds": 60},
    "square": {"label": "Square (Facebook / LinkedIn feed)", "canvas": "1:1", "max_seconds": None},
}


def tag(canvas: str) -> str:
    """A filename-safe tag for a canvas: '4:5' -> '4x5'."""
    return canvas.replace(":", "x")


def profiles_for(canvas: str) -> list[str]:
    return [pid for pid, p in PROFILES.items() if p["canvas"] == canvas]


def length_warnings(canvas: str, seconds: float) -> list[str]:
    """Which platforms this canvas suits that a video this long is too long for."""
    out = []
    for p in PROFILES.values():
        cap = p["max_seconds"]
        if p["canvas"] == canvas and cap is not None and seconds > cap:
            out.append(f"{p['label']} allows up to {cap}s; this is {seconds:.0f}s.")
    return out


def normalize(canvases) -> list[str]:
    """Unique, known canvases in the order given. Raises on an unknown one."""
    out: list[str] = []
    for c in canvases or []:
        c = str(c)
        if c not in CANVASES:
            raise ValueError(f"unknown format {c!r}; use one of {', '.join(CANVASES)}")
        if c not in out:
            out.append(c)
    return out
