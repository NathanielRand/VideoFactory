"""What a game looks like on screen, shared by every channel that plays it.

The tracker (video/tracker.py) finds PEOPLE. In gameplay footage the people it
finds are the soldiers in the game, so on a first-person shooter it pans after
whoever runs across the screen and the crop is off. A profile says what the
footage is actually built around, in terms that hold for every creator who
plays that game:

* where the player's attention is (the crosshair),
* where the interface sits (killfeed, minimap, money, the centre event feed),
* which of those regions change when something worth clipping happens.

Regions are normalised (x0, y0, x1, y1) in 0..1 of the frame, measured on 16:9
gameplay. They are data, so adding a game is one entry and no new code. A
profile is chosen by keyword (title or channel) and can be forced or switched
off per clip with render_opts["game"] ("none" switches it off).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

Region = tuple[float, float, float, float]


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    keywords: tuple[str, ...]
    # Where the camera should sit horizontally: where the player aims.
    focus_x: float = 0.5
    # Named interface regions (normalised x0, y0, x1, y1).
    regions: dict[str, Region] = field(default_factory=dict)
    # The regions whose change means "something happened" (see game_events).
    activity: tuple[str, ...] = ()


PROFILES: dict[str, Profile] = {
    p.id: p
    for p in (
        Profile(
            id="wardogs",
            name="Wardogs",
            keywords=("wardogs", "war dogs"),
            # The crosshair is the middle of the screen.
            focus_x=0.5,
            regions={
                "chat": (0.015, 0.02, 0.20, 0.18),          # top-left text chat
                "killfeed": (0.01, 0.44, 0.30, 0.58),       # left, mid-height
                "minimap": (0.015, 0.68, 0.16, 0.96),       # bottom-left
                "money": (0.88, 0.005, 0.995, 0.055),       # top-right cash + counter
                # The player's own kill / cash-earned popup, just below centre.
                "event_feed": (0.40, 0.60, 0.60, 0.78),
            },
            # Not the money counter: it moves when you BUY, and a buy menu is not
            # a moment. The kill reward is announced in event_feed anyway.
            activity=("event_feed", "killfeed"),
        ),
    )
}


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def detect(*texts: str) -> Profile | None:
    """The profile whose keyword appears (as whole words) in any of `texts`."""
    haystack = f" {' '.join(_words(t) for t in texts if t)} "
    for profile in PROFILES.values():
        for kw in profile.keywords:
            if f" {_words(kw)} " in haystack:
                return profile
    return None


def get(profile_id: str | None) -> Profile | None:
    return PROFILES.get(profile_id or "")


def for_source(source: Path, config: dict, override: str | None = None) -> Profile | None:
    """The profile for the video at `source` (downloads/<video_id>.<ext>).

    `override` is render_opts["game"]: a profile id forces it, "none" switches
    profiles off, empty means detect. Reads the title and channel through its
    own read-only connection, so it is safe to call from a render thread.
    """
    if override == "none":
        return None
    forced = get(override)
    if forced:
        return forced
    try:
        db_path = Path(config["paths"]["data_dir"]) / "state.db"
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT title, channel_name FROM videos WHERE video_id = ?", (Path(source).stem,)
            ).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, KeyError, OSError):
        return None
    return detect(*(row or ()))
