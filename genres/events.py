"""Game moments read off the interface, not guessed from loudness.

A game announces the moments worth clipping on screen: a kill confirmed, cash
earned, a feed that scrolls. Those sit in fixed places (genres/profiles.py), so
a change there is a far better "something happened" than raw motion, which in a
first-person game is the player merely turning around.

This is deliberately small: it watches named screen regions at 2 frames a
second and reports how much each one changed relative to ITS OWN recent
baseline. It does not read the text (no OCR dependency), and it needs no
training: a popup appearing is a jump in bright, high-contrast pixels, and a
feed ticking over is a jump in how the region differs from a moment ago.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from core.binaries import ffmpeg
from genres.profiles import Profile

SAMPLE_FPS = 2.0
FRAME_W, FRAME_H = 320, 180      # enough to see a text popup, small enough to stream
_BRIGHT = 205                    # HUD text is near-white; most of the world is not
_BASELINE_SECONDS = 30


def _decode_regions(
    video: Path, profile: Profile, start: float = 0.0, duration: float | None = None
) -> dict[str, np.ndarray]:
    """Per-sample crops of each activity region, plus the whole frame under the
    key "_frame", streamed so a two-hour stream is never held in memory as full
    frames."""
    from video.encoding import hwaccel_input_args

    window = (["-ss", f"{start:.2f}"] if start else []) + (["-t", f"{duration:.2f}"] if duration else [])
    cmd = [
        ffmpeg(), "-v", "error", *hwaccel_input_args(), *window, "-i", str(video),
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_W}:{FRAME_H}",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    boxes = {}
    for name in profile.activity:
        x0, y0, x1, y1 = profile.regions[name]
        boxes[name] = (int(y0 * FRAME_H), max(int(y0 * FRAME_H) + 2, int(y1 * FRAME_H)),
                       int(x0 * FRAME_W), max(int(x0 * FRAME_W) + 2, int(x1 * FRAME_W)))
    out: dict[str, list[np.ndarray]] = {n: [] for n in boxes}
    out["_frame"] = []
    size = FRAME_W * FRAME_H
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(FRAME_H, FRAME_W)
            for name, (ya, yb, xa, xb) in boxes.items():
                out[name].append(frame[ya:yb, xa:xb].copy())
            out["_frame"].append(frame[::4, ::4].copy())
    finally:
        proc.stdout.close()
        proc.wait()
    return {n: np.stack(v) if v else np.zeros((0, 1, 1), np.uint8) for n, v in out.items()}


def _rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    if x.size == 0:
        return x
    pad = window // 2
    padded = np.pad(x, pad, mode="edge")
    return np.array([np.median(padded[i:i + window]) for i in range(x.size)], dtype=np.float32)


def region_signal(frames: np.ndarray) -> np.ndarray:
    """How much this region just changed, per sample, against its own baseline.

    Two cues, taken as the larger: bright text pixels appearing (a popup), and
    the region differing from the sample before (a feed moving). Each is
    measured as the amount ABOVE the region's recent median, so a region that
    is always busy does not read as always eventful.
    """
    n = frames.shape[0]
    if n < 2:
        return np.zeros(n, np.float32)
    f = frames.astype(np.float32)
    bright = (f > _BRIGHT).mean(axis=(1, 2))
    diff = np.concatenate([[0.0], np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))]).astype(np.float32)
    window = max(3, int(SAMPLE_FPS * _BASELINE_SECONDS) | 1)

    def lift(x: np.ndarray, floor: float) -> np.ndarray:
        base = _rolling_median(x, window)
        spread = _rolling_median(np.abs(x - base), window)
        return np.clip((x - base) / (spread * 4 + floor), 0.0, 6.0) / 6.0

    pop = lift(bright.astype(np.float32), 0.004)
    feed = lift(diff, 1.5)
    # A popup is the stronger and more specific cue, so it leads.
    return np.maximum(pop, 0.7 * feed).astype(np.float32)


def hud_activity(
    video: Path, profile: Profile, start: float = 0.0, duration: float | None = None
) -> dict[str, np.ndarray]:
    """Per-second HUD activity, 0..1: {"hud": combined, <region>: that region}.

    A popup changes a small patch of the screen; a menu, the inventory or a
    scoreboard changes all of it. Both light up a region, so the whole frame is
    watched too and a region's reading is discounted by how much the frame as a
    whole changed. Without that, opening the buy menu reads as the best moment
    of the match.

    Empty when the profile has no activity regions or the video cannot be read;
    callers treat that as "no signal", never as an error.
    """
    if not profile.activity:
        return {}
    try:
        crops = _decode_regions(Path(video), profile, start, duration)
    except OSError:
        return {}
    whole = crops.pop("_frame", np.zeros((0, 1, 1), np.uint8))
    menu = region_signal(whole)
    # A scene change in the world (a respawn, a hard cut) moves the whole frame
    # too, and is not an event either: only a patch of change counts.
    gate = np.clip(1.0 - 2.0 * menu, 0.0, 1.0)
    per_region = {}
    for name, frames in crops.items():
        sig = region_signal(frames)
        if gate.size >= sig.size:
            sig = sig * gate[: sig.size]
        k = int(SAMPLE_FPS)
        secs = sig.size // k
        if secs:
            per_region[name] = sig[: secs * k].reshape(secs, k).max(axis=1)
    if not per_region:
        return {}
    n = min(v.size for v in per_region.values())
    stack = np.stack([v[:n] for v in per_region.values()])
    # event_feed is the precise cue (a verified kill popup). killfeed also moves
    # when you scope in, so it supports a reading but cannot make one alone.
    weights = np.array([1.0 if k == "event_feed" else 0.5 for k in per_region])[:, None]
    combined = (stack * weights).max(axis=0)
    return {"hud": combined.astype(np.float32), **{k: v[:n] for k, v in per_region.items()}}
