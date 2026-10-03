"""Serving video to the app's players without starving them.

The pipeline runs in a thread of the SAME process as this API, so a stretch of
Python-heavy work holds the interpreter lock, and every file chunk the server
reads has to win that lock back before it can be sent. Measured on a 28 MB clip
with one busy thread in the process:

    64 KB chunks, default switching      1 MB/s   (26 seconds: the player stalls)
    64 KB chunks, 0.2 ms switching      49 MB/s
     1 MB chunks, default switching      8 MB/s
     1 MB chunks, 0.2 ms switching     459 MB/s

So the two changes here go together: read in 1 MB pieces (a sixteenth of the
hand-offs), and let a waiting thread take the lock after 0.2 ms instead of 5 ms.

The second is NOT left on. Measured the other way, a 0.2 ms interval makes
several CPU-bound Python threads fight over the lock, and their combined
throughput fell 65% with three of them and 90% with six. So the short interval
is a lease (`boost_thread_switching`), taken when a playback request ARRIVES
(`BoostPlayback`, because a request is hundreds of small hand-offs and behind a
busy thread each waits out the full interval: the first byte took 3-6 s, the
rest of the clip 0.1 s) and while media is sent, and handed back about a second
and a half after the last of either. A paused player that is merely holding a
connection open does not keep it. Cold request for an 82 MB clip with a busy
thread in the process: 6.6 s before, 0.4 s after.

Posters live here too: a grid of a hundred `<video>` elements, each holding a
decoder and a decoded 1080x1920 frame, was the other way to make playback
stutter, so a card shows a small still and only plays on demand.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from starlette.responses import FileResponse

CHUNK = 1024 * 1024
SWITCH_INTERVAL = 0.0002       # seconds a thread may hold the interpreter before another can ask
# Revalidating on every seek is wasted work for a file that only changes by
# being replaced under a new clip id; a couple of minutes is short enough that
# the rare in-place rewrite (a backfill) is picked up on the next play.
CACHE_HEADER = "private, max-age=120"


class MediaResponse(FileResponse):
    chunk_size = CHUNK

    async def __call__(self, scope, receive, send) -> None:
        boost_thread_switching()                      # before the first read, which is the one that stalls

        async def boosted(message) -> None:
            if message.get("type") == "http.response.body" and message.get("body"):
                boost_thread_switching()
            await send(message)

        await super().__call__(scope, receive, boosted)


def video_response(path: Path | str, media_type: str = "video/mp4") -> MediaResponse:
    """A range-capable video response read in large pieces."""
    return MediaResponse(path, media_type=media_type, headers={"Cache-Control": CACHE_HEADER})


BOOST_HOLD = 1.5               # seconds the short interval outlasts the last chunk sent
_boost_lock = threading.Lock()
_boost = {"until": 0.0, "previous": None}


def boost_thread_switching(hold: float = BOOST_HOLD, interval: float = SWITCH_INTERVAL) -> None:
    """Lend the API's threads a short switch interval so they can take the
    interpreter back quickly from a busy worker, and hand it back `hold`
    seconds after the last call. Cheap to call on every chunk."""
    import time

    with _boost_lock:
        _boost["until"] = time.monotonic() + hold
        if _boost["previous"] is not None:
            # Already lent; the deadline moved. Re-apply in case something set
            # the interval back in the meantime.
            if sys.getswitchinterval() > interval:
                sys.setswitchinterval(interval)
            return
        _boost["previous"] = sys.getswitchinterval()
        if _boost["previous"] > interval:
            sys.setswitchinterval(interval)
    timer = threading.Timer(hold, _return_switching)
    timer.daemon = True
    timer.start()


def _return_switching() -> None:
    import time

    with _boost_lock:
        left = _boost["until"] - time.monotonic()
        if left > 0:                                  # more media was sent meanwhile: wait it out
            timer = threading.Timer(left + 0.05, _return_switching)
            timer.daemon = True
            timer.start()
            return
        previous, _boost["previous"] = _boost["previous"], None
        if previous is not None:
            sys.setswitchinterval(previous)


from video.poster import make_poster, poster, poster_path  # noqa: E402,F401  (re-exported)


# ---- draft previews ----------------------------------------------------------------


class TrackingCache:
    """Subject tracking for the editor's draft previews, kept between them.

    A preview goes through the real render path, and the slowest step of that
    is following the subject (`Following the subject`, about a third of the
    time). It depends on the cut and on what the edit does to the timeline, and
    on nothing else: caption text, caption style, colour, watermark and hook are
    all drawn afterwards. So while someone tunes those, every preview after the
    first can reuse the same path. The key holds everything that could change
    the tracked footage, so a changed trim or edit is simply a different entry.

    Bounded, because each entry is a list of points per frame sample and the
    editor is open for hours."""

    def __init__(self, size: int = 8):
        self.size = size
        self._items: dict[tuple, dict] = {}

    @staticmethod
    def key(clip_id: int, source: Path, start: float, end: float, edit, crop: str,
            podcast: bool, detector: str, sample_fps) -> tuple:
        import json

        st = source.stat()
        return (clip_id, st.st_mtime_ns, st.st_size, round(float(start), 2), round(float(end), 2),
                json.dumps(edit, sort_keys=True, default=str), crop, bool(podcast), detector, sample_fps)

    def for_key(self, key: tuple) -> dict:
        """The (possibly empty) cache dict for this key, for _render_files to
        fill. The newest entries are kept."""
        if key in self._items:
            self._items[key] = self._items.pop(key)        # most recently used goes last
        else:
            self._items[key] = {}
            while len(self._items) > self.size:
                self._items.pop(next(iter(self._items)))
        return self._items[key]


# ---- taking the lease before the request is handled -----------------------------------

import re  # noqa: E402

# What a player or the editor asks for: the video itself, its still, its words
# and captions. Each request is hundreds of small hand-offs of the interpreter
# (route, database, file checks), and behind a busy thread every one of them
# waits out the full switch interval, so the lease has to be taken BEFORE the
# request is handled, not when the response starts: measured, the first byte
# took 3-6 seconds while the rest of the clip took 0.1.
_PLAYBACK = re.compile(
    r"^/(media/|compilations/(source/|\d+/(media|renders))|"
    r"clips/\d+/(variants/[^/]+/media|words|captions|preview)|thumbnails/\d+/(frame|best-frames))"
)


class BoostPlayback:
    """ASGI middleware: playback requests get the short switch interval from
    the moment they arrive."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and _PLAYBACK.match(scope.get("path", "")):
            boost_thread_switching()
        await self.app(scope, receive, send)
