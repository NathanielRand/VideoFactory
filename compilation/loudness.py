"""Evening out loudness between the creators in a compilation.

The render used to run FFmpeg's single-pass `loudnorm` on each part. That
filter is DYNAMIC when it has not measured the audio first: it rides the gain
up and down as it plays, so a quiet start swells and a shout ducks the
sentence after it (audible pumping), and on a 20-second segment it barely
settles before the segment ends, so parts did not come out matched either.
It also came after the segment's own volume, so a volume change was undone
by it every time.

Now each part is MEASURED first (integrated loudness per EBU R128, the
standard every platform normalises by) and then given one fixed gain to the
target: the creator's own dynamics are untouched, only the level moves. A
limiter after the gain catches the peaks a boost would push over, and the
segment's own volume is a trim applied on top, so "0 dB" means "matched to
the others" and a nudge up or down is kept.

Measurements are cached by file, time range and file identity, so the
several formats of one render, a re-render, and the editor's Measure button
all share them.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from core.binaries import ffmpeg

TARGETS = (-14.0, -16.0, -12.0)
DEFAULT_TARGET = -14.0
PEAK_CEILING_DB = -1.5            # true-peak headroom, as video/encoding.py uses
SILENT_BELOW = -50.0              # LUFS: treated as silence, never boosted
MAX_BOOST_DB = 18.0               # past this a quiet track is mostly noise floor
MAX_CUT_DB = -30.0
MIN_TRIM_DB, MAX_TRIM_DB = -24.0, 12.0


@dataclass(frozen=True)
class Measure:
    lufs: float        # integrated loudness
    peak: float        # true peak, dBTP
    lra: float         # loudness range, LU


_lock = threading.Lock()
_memory: dict[str, Measure | None] = {}


def _key(path: Path, start: float, duration: float) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{start:.2f}|{duration:.2f}"


def _load(cache: Path | None) -> dict:
    if cache is None or not cache.exists():
        return {}
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cache: Path | None, data: dict) -> None:
    if cache is None:
        return
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Newest few thousand only; this is a cache, not a record.
        if len(data) > 4000:
            data = dict(list(data.items())[-3000:])
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(cache)
    except OSError:
        pass


def parse_loudnorm(stderr: str) -> Measure | None:
    """The measurement FFmpeg's loudnorm prints as JSON at the end of stderr."""
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
        lufs, peak, lra = float(got["input_i"]), float(got["input_tp"]), float(got["input_lra"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (lufs, peak, lra)):
        # "-inf": digital silence.
        return Measure(lufs=-70.0, peak=-70.0, lra=0.0)
    return Measure(lufs=lufs, peak=peak, lra=lra)


def measure(path: Path, start: float, duration: float, cache: Path | None = None) -> Measure | None:
    """Integrated loudness of `duration` seconds from `start`. None when the
    file has no audio or cannot be read. Audio only, so it is quick."""
    try:
        key = _key(path, start, duration)
    except OSError:
        return None
    with _lock:
        if key in _memory:
            return _memory[key]
        stored = _load(cache).get(key)
    if stored is not None:
        result = Measure(**stored) if stored else None
    else:
        r = subprocess.run(
            [ffmpeg(), "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
             "-i", str(path), "-vn", "-sn", "-dn",
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, errors="replace",
        )
        result = parse_loudnorm(r.stderr) if r.returncode == 0 else None
        with _lock:
            data = _load(cache)
            data[key] = asdict(result) if result else {}
            _save(cache, data)
    with _lock:
        _memory[key] = result
    return result


def trim_db(volume: float) -> float:
    """A segment's volume (a multiplier, as stored) as dB. 0 = mute."""
    if volume <= 0:
        return -math.inf
    return max(MIN_TRIM_DB, min(MAX_TRIM_DB, 20 * math.log10(volume)))


def gain_db(m: Measure | None, target: float, trim: float = 0.0) -> float:
    """The fixed gain a part gets: to the target, plus its own trim.

    Silence (or no measurement) is never boosted: raising -60 LUFS to -14
    would make a hiss of the noise floor. A big boost is capped for the same
    reason."""
    if m is None or m.lufs < SILENT_BELOW:
        base = 0.0
    else:
        base = max(MAX_CUT_DB, min(MAX_BOOST_DB, target - m.lufs))
    return base + trim


def audio_chain(gain: float) -> str:
    """The filter that applies a fixed gain without clipping: the gain, then
    a limiter that only acts on peaks above the ceiling."""
    if gain == -math.inf:
        return "volume=0"
    ceiling = 10 ** (PEAK_CEILING_DB / 20)
    return (
        f"volume={gain:.2f}dB,"
        f"alimiter=limit={ceiling:.4f}:attack=5:release=60:level=disabled"
    )


def report(m: Measure | None, target: float, volume: float) -> dict:
    """One part as the editor shows it."""
    trim = trim_db(volume)
    return {
        "lufs": None if m is None else round(m.lufs, 1),
        "peak": None if m is None else round(m.peak, 1),
        "silent": m is None or m.lufs < SILENT_BELOW,
        "match_db": None if m is None else round(gain_db(m, target), 1),
        "trim_db": None if trim == -math.inf else round(trim, 1),
        "muted": trim == -math.inf,
    }
