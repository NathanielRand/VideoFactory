"""Propose cuts that tighten a clip: dead air and filler words.

Retention on short-form is decided in the first seconds, and a clip that
opens with two seconds of "uhh..." has spent them. This finds the pauses
and the filler words and returns the ranges worth dropping.

It PROPOSES only. The ranges go into the editor's normal edit list, drawn
on the timeline like any hand-made cut, so they can be reviewed, adjusted
or undone before anything is rendered — cutting someone's video silently
on a guess is not a trade worth making.

Silence comes from FFmpeg's silencedetect rather than from gaps in the
transcript: a gap between words is not necessarily quiet, and cutting
music, laughter or a held reaction would be worse than leaving the pause.
"""

import re
import subprocess
from pathlib import Path

from core.binaries import ffmpeg

# Sounds with no meaning to lose. Deliberately short: "like", "so" and
# "you know" are frequently load-bearing in speech, and cutting them
# mangles sentences.
FILLERS = {"um", "uh", "umm", "uhh", "uhm", "erm", "ah", "eh", "hmm", "mm", "mmm"}

MIN_GAP = 0.55       # a pause shorter than this is natural speech rhythm
KEEP_BREATH = 0.15   # leave this much of the pause, so cuts don't clip breath
MIN_CUT = 0.20       # not worth a cut below this
MIN_KEEP = 0.30      # never leave an unwatchable sliver behind


def _detect_silence(
    path: Path, noise_db: int = -32, min_gap: float = MIN_GAP,
    start: float = 0.0, duration: float | None = None,
) -> list[list[float]]:
    """Quiet stretches in the audio, as [start, end] seconds (relative to
    `start` when a window is given, so a stretch of a long source can be read
    without decoding the rest)."""
    window = (["-ss", f"{start:.3f}"] if start else []) + (["-t", f"{duration:.3f}"] if duration else [])
    cmd = [
        ffmpeg(), "-hide_banner", "-nostats", *window, "-i", str(path.resolve()),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_gap:g}", "-f", "null", "-",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
        )
    except Exception:
        return []
    out: list[list[float]] = []
    start = None
    for m in re.finditer(r"silence_(start|end):\s*(-?[\d.]+)", r.stderr or ""):
        kind, value = m.group(1), float(m.group(2))
        if kind == "start":
            start = value
        elif start is not None:
            out.append([max(0.0, start), value])
            start = None
    if start is not None:  # silence running to the end of the clip
        out.append([max(0.0, start), float("inf")])
    return out


def _filler_ranges(words: list[dict]) -> list[list[float]]:
    """Spans of standalone filler sounds, as [start, end] seconds."""
    out = []
    for w in words or []:
        token = re.sub(r"[^\w']", "", str(w.get("word", ""))).lower()
        if token in FILLERS:
            out.append([float(w["start"]), float(w["end"])])
    return out


def _merge(ranges: list[list[float]], duration: float) -> list[list[float]]:
    clean = sorted(
        [max(0.0, a), min(duration, b)] for a, b in ranges if b > a
    )
    merged: list[list[float]] = []
    for a, b in clean:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def propose(
    clip_path: Path,
    words: list[dict],
    duration: float,
    drop_silence: bool = True,
    drop_fillers: bool = True,
) -> dict:
    """Keep-ranges with the dead air and filler words taken out.

    Returns {keep, removed_seconds, cuts, new_duration}; `keep` is exactly
    the shape render_opts["edit"]["keep"] expects."""
    cuts: list[list[float]] = []

    if drop_silence:
        for a, b in _detect_silence(clip_path):
            b = min(b, duration)
            # Leave a breath at each end so speech doesn't start abruptly.
            a2, b2 = a + KEEP_BREATH, b - KEEP_BREATH
            # A pause at the very start or end can go entirely — there is no
            # speech on the outer side to protect.
            if a <= 0.05:
                a2 = 0.0
            if b >= duration - 0.05:
                b2 = duration
            if b2 - a2 >= MIN_CUT:
                cuts.append([a2, b2])

    if drop_fillers:
        cuts.extend(_filler_ranges(words))

    cuts = _merge(cuts, duration)

    keep: list[list[float]] = []
    cursor = 0.0
    for a, b in cuts:
        if a - cursor >= MIN_KEEP:
            keep.append([round(cursor, 2), round(a, 2)])
        cursor = max(cursor, b)
    if duration - cursor >= MIN_KEEP:
        keep.append([round(cursor, 2), round(duration, 2)])

    if not keep:  # everything looked droppable — clearly wrong, change nothing
        return {"keep": [[0.0, round(duration, 2)]], "removed_seconds": 0.0,
                "cuts": 0, "new_duration": round(duration, 2)}

    kept = sum(b - a for a, b in keep)
    return {
        "keep": keep,
        "removed_seconds": round(duration - kept, 2),
        "cuts": len(cuts),
        "new_duration": round(kept, 2),
    }


# ---- automatic tightening ----------------------------------------------------
#
# propose() above is for the editor's button: silence and filler words, shown to
# the user to accept. This is what runs on every clip BEFORE it is rendered, for
# footage where silence is not the fluff. In gameplay the audio never goes quiet
# (the game keeps playing), so silencedetect finds nothing; what is missing is
# anything HAPPENING: nobody speaking, nothing announced on screen, nothing loud.
# Those stretches are cut and the sections either side are joined with a short
# crossfade, so the clip is the moments, not the walk between them.

DEAD_RUN = 2.0          # a stretch must be dead this long before it is a cut
DEAD_LEAD = 0.35        # context kept at each end of a cut, so nothing is clipped
AUTO_MIN_CUT = 1.0      # not worth a crossfade for less than this
AUTO_MIN_KEEP = 1.5     # no section shorter than this survives a cut
AUTO_MAX_CUTS = 4       # a clip with more joins than this feels chopped
AUTO_MAX_FRACTION = 0.4  # never remove more than this share of a clip
AUTO_MIN_RESULT = 8.0   # a clip shorter than this after cutting is not a clip
AUTO_GAP = 0.9          # a pause must be this long before it is cut (propose() uses 0.55)
TRANSITION = 0.2        # crossfade seconds


def _per_second(ranges: list[list[float]], n: int) -> list[bool]:
    flags = [False] * n
    for a, b in ranges:
        for sec in range(max(0, int(a)), min(n, int(b) + 1)):
            flags[sec] = True
    return flags


def dead_ranges(speech: list[bool], active: list[bool], duration: float) -> list[list[float]]:
    """Cut ranges for stretches where nobody speaks AND nothing is happening.

    `speech` and `active` are per-second flags. A run of dead seconds at least
    DEAD_RUN long becomes a cut, minus DEAD_LEAD at each end (the second before
    a moment is part of it; a cut that starts on the beat clips it)."""
    n = len(speech)
    dead = [not speech[i] and not active[i] for i in range(n)]
    out, i = [], 0
    while i < n:
        if not dead[i]:
            i += 1
            continue
        j = i
        while j < n and dead[j]:
            j += 1
        if j - i >= DEAD_RUN:
            a = float(i) if i == 0 else i + DEAD_LEAD          # nothing precedes the very start
            b = duration if j >= n else j - DEAD_LEAD          # nothing follows the very end
            out.append([a, min(b, duration)])
        i = j
    return out


def choose_cuts(
    cuts: list[list[float]], duration: float, floor: float = 0.0
) -> list[list[float]]:
    """Cap and clean candidate cuts so the clip stays whole and watchable.

    Longest first, because a long dead stretch is worth a join and a short one is
    not; at most AUTO_MAX_CUTS of them and AUTO_MAX_FRACTION of the clip; and any
    cut that would leave a sliver of video between two joins is dropped.

    `floor` is the shortest the finished clip may be (the job's minimum length:
    61 s when making TikTok-length clips, where being over a minute is the point).
    Each cut costs its length plus one crossfade overlap, and the total may never
    take the clip below the floor."""
    floor = max(floor, AUTO_MIN_RESULT)
    budget = min(duration * AUTO_MAX_FRACTION, duration - floor)
    cuts = [c for c in _merge(cuts, duration) if c[1] - c[0] >= AUTO_MIN_CUT]
    chosen: list[list[float]] = []
    removed = 0.0
    for a, b in sorted(cuts, key=lambda c: c[0] - c[1]):
        cost = (b - a) + TRANSITION
        if len(chosen) >= AUTO_MAX_CUTS or removed + cost > budget:
            continue
        chosen.append([a, b])
        removed += cost
    chosen.sort()

    def sliver() -> int | None:
        prev = 0.0
        for idx, (a, b) in enumerate(chosen):
            # The run before a cut may be empty only at the very start of the clip.
            gap = a - prev
            if gap < AUTO_MIN_KEEP and not (idx == 0 and gap <= 0.05):
                return idx
            prev = b
        if chosen and 0.05 < duration - prev < AUTO_MIN_KEEP:
            return len(chosen) - 1
        return None

    while chosen and (bad := sliver()) is not None:
        chosen.pop(bad)            # the cut that left the sliver goes; the clip stays whole
    if duration - sum(b - a + TRANSITION for a, b in chosen) < floor:
        return []
    return chosen


def keep_from_cuts(cuts: list[list[float]], duration: float) -> list[list[float]]:
    keep, cursor = [], 0.0
    for a, b in cuts:
        if a - cursor > 0.05:
            keep.append([round(cursor, 2), round(a, 2)])
        cursor = max(cursor, b)
    if duration - cursor > 0.05:
        keep.append([round(cursor, 2), round(duration, 2)])
    return keep


def _audio_rms(source: Path, start: float, duration: float) -> list[float]:
    """Loudness per second of a stretch of the source (RMS)."""
    import numpy as np

    cmd = [
        ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    except Exception:
        return []
    a = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    n = int(a.size // 8000)
    if n == 0:
        return []
    return [float(np.sqrt(np.mean(a[i * 8000:(i + 1) * 8000] ** 2))) for i in range(n)]


def _window_motion(source: Path, start: float, duration: float) -> list[float]:
    """Frame-to-frame change per second of a stretch of the source."""
    import numpy as np

    w, h, fps = 160, 90, 2
    cmd = [
        ffmpeg(), "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
        "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    except Exception:
        return []
    n = len(raw) // (w * h)
    if n < 2:
        return []
    f = np.frombuffer(raw[: n * w * h], dtype=np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.concatenate([[0.0], np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))])
    secs = n // fps
    return [float(d[i * fps:(i + 1) * fps].mean()) for i in range(secs)]


def _relative_words(words: list[dict], start: float, end: float) -> list[dict]:
    """The words inside the window, timed from the window's start."""
    return [
        {**w, "start": w["start"] - start, "end": w["end"] - start}
        for w in (words or []) if w["end"] > start and w["start"] < end
    ]


def auto_plan(
    source: Path, start: float, end: float, words: list[dict], profile=None, floor: float = 0.0
) -> dict | None:
    """The edit for one clip with its dead air cut, or None if it is already tight.

    {"keep": [[a, b], ...], "transition": 0.2, "removed_seconds", "cuts"}, in
    seconds from the clip's start: exactly what render_opts["edit"] stores, so
    it shows on the editor's timeline like a hand-made cut and can be undone.

    A second counts as ACTIVE when someone speaks, the game announces something
    on screen, the sound jumps, or the picture is moving unusually fast. Anything
    else for DEAD_RUN seconds running is dead air. Silent gaps (the talking-head
    case) are cut too, at a longer threshold than the editor's button uses
    because here nobody is reviewing each one first.

    `floor` is the job's minimum clip length; the result never goes below it."""
    import numpy as np

    duration = end - start
    if duration < max(floor, AUTO_MIN_RESULT) + AUTO_MIN_CUT:
        return None
    n = int(duration)

    rel = _relative_words(words, start, end)
    speech = _per_second([[w["start"], w["end"]] for w in rel], n)
    if not rel:
        # No word timings (a transcript cached before they were stored, or a
        # music clip). Without them "nobody is speaking" can not be known, so
        # speech is assumed and only the silent-gap path may cut.
        speech = [True] * n

    active = [False] * n
    rms = _audio_rms(source, start, duration)
    if len(rms) >= 3:
        base = float(np.median(rms)) or 1e-6
        for i in range(min(n, len(rms))):
            active[i] = active[i] or rms[i] > 1.35 * base       # gunfire, a shout, an explosion
    motion = _window_motion(source, start, duration)
    if len(motion) >= 3:
        hot = float(np.percentile(motion, 85))
        for i in range(min(n, len(motion))):
            active[i] = active[i] or motion[i] > hot             # fast action
    if profile is not None and profile.activity:
        from genres.events import hud_activity

        hud = hud_activity(source, profile, start, duration).get("hud")
        if hud is not None:
            for i in range(min(n, hud.size)):
                active[i] = active[i] or bool(hud[i] > 0.25)     # a kill / cash popup

    cuts = dead_ranges(speech, active, duration)
    for a, b in _detect_silence(source, min_gap=AUTO_GAP, start=start, duration=duration):
        b = min(b, duration)
        a2 = 0.0 if a <= 0.05 else a + KEEP_BREATH
        b2 = duration if b >= duration - 0.05 else b - KEEP_BREATH
        if b2 - a2 >= AUTO_MIN_CUT:
            cuts.append([a2, b2])
    cuts.extend(_filler_ranges(rel))

    cuts = choose_cuts(cuts, duration, floor)
    if not cuts:
        return None
    return {
        "keep": keep_from_cuts(cuts, duration), "transition": TRANSITION,
        "removed_seconds": round(sum(b - a for a, b in cuts), 2), "cuts": len(cuts),
    }
