"""Small stills of finished clips, for the grid.

A card used to hold a live <video> to show its picture: a decoder and a decoded
1080x1920 frame each, which across a grid of a hundred is what made playback
everywhere else in the app stutter. A card now shows one of these instead, and
plays the clip only while the pointer rests on it.

They are made when a clip is finalised (`queue`, one at a time in the
background), so opening a grid finds them waiting; the route makes any that are
missing (clips from before this existed) on first ask. The file's name carries
the clip file's size and modification time, so a re-render is a new poster and
an old one is never shown for it.
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

POSTER_WIDTH = 360            # a grid card is ~200px wide; 2x for a sharp screen
POSTER_MAX = 3                # ffmpeg processes at once: a screenful of cards asks together
_slots = threading.BoundedSemaphore(POSTER_MAX)
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def poster_path(cache: Path, video: Path, width: int = POSTER_WIDTH) -> Path:
    """Where this video's poster is kept. The name carries the file's size and
    modification time, so a re-render (a new file) is a new poster and an old
    one is never shown for it."""
    st = video.stat()
    key = hashlib.sha1(str(video).encode("utf-8", "replace")).hexdigest()[:14]
    return cache / f"{key}_{width}_{st.st_mtime_ns}_{st.st_size}.jpg"


def make_poster(ffmpeg: str, video: Path, target: Path, width: int = POSTER_WIDTH) -> bool:
    """Write one small frame from early in the clip. A clip is cut on a
    sentence, so frame zero is often mid-blink or black; a little way in is the
    first frame with something on it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.jpg")
    for at in ("0.7", "0"):
        try:
            done = subprocess.run(
                [ffmpeg, "-y", "-v", "error", "-ss", at, "-i", str(video), "-frames:v", "1",
                 "-vf", f"scale={width}:-2", "-q:v", "5", str(tmp)],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(target)
            prefix = target.name.split("_")[0] + "_"
            for old in target.parent.glob(prefix + "*.jpg"):     # posters of earlier renders
                if old != target:
                    old.unlink(missing_ok=True)
            return True
    tmp.unlink(missing_ok=True)
    return False


def poster(ffmpeg: str, cache: Path, video: Path, width: int = POSTER_WIDTH) -> Path | None:
    """The poster for `video`, made on first ask and kept. Concurrent asks for
    the same clip share one ffmpeg run, and at most POSTER_MAX run at once."""
    target = poster_path(cache, video, width)
    if target.exists():
        return target
    with _locks_guard:
        lock = _locks.setdefault(str(target), threading.Lock())
    with lock:
        if target.exists():
            return target
        with _slots:
            ok = make_poster(ffmpeg, video, target, width)
    with _locks_guard:
        _locks.pop(str(target), None)
    return target if ok else None


# ---- making them as clips are finished --------------------------------------------

_queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="posters")


def queue(video: Path | str, data_dir: Path | str) -> None:
    """Make this clip's poster in the background. Never raises and never blocks:
    the caller is the render pipeline, and a missing poster only means the route
    makes it later."""
    def work() -> None:
        try:
            from core.binaries import ffmpeg

            poster(ffmpeg(), Path(data_dir) / "posters", Path(video))
        except Exception:
            pass

    try:
        _queue.submit(work)
    except RuntimeError:
        pass  # interpreter shutting down
