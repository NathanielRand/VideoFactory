"""Render an AI clip in other shapes: one clip -> a version per platform.

Each variant is the clip rendered again from the SOURCE (not a crop of the
finished 9:16 file), so a 16:9 version shows the full frame and a 1:1 or 4:5
version gets a crop of its own shape that follows the same subject. Captions,
hook text and branding are laid out for each canvas, because every render
builds them at that canvas's size.

The expensive part, tracking the subject, does not depend on the output
shape, so it runs once: the first variant renders alone and fills the shared
cache, then the rest render in parallel (video.parallel_renders at most).
Variants sit beside the clip, tagged: clip_00096-00107.4x5.mp4.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from core.paths import cached_source
from formats.profiles import CANVASES, normalize

Progress = Callable[[int, int, str], None]


def clip_canvas(render_opts: dict) -> str:
    """The shape a clip's own file already has."""
    if render_opts.get("canvas") in CANVASES:
        return render_opts["canvas"]
    return "16:9" if render_opts.get("profile") else "9:16"


def list_for(db, clip_id: int) -> list[dict]:
    rows = db.conn.execute(
        "SELECT * FROM clip_variants WHERE clip_id = ? ORDER BY canvas", (clip_id,)
    ).fetchall()
    return [{**dict(r), "exists": Path(r["path"]).exists()} for r in rows]


def get(db, clip_id: int, canvas: str) -> dict | None:
    r = db.conn.execute(
        "SELECT * FROM clip_variants WHERE clip_id = ? AND canvas = ?", (clip_id, canvas)
    ).fetchone()
    return dict(r) if r else None


def _record(db, clip_id: int, canvas: str, path: Path) -> None:
    db.conn.execute(
        "INSERT OR REPLACE INTO clip_variants (clip_id, canvas, path, stale, created_at) VALUES (?, ?, ?, 0, ?)",
        (clip_id, canvas, str(path), datetime.now().isoformat(timespec="seconds")),
    )
    db.conn.commit()


def render(db, clip_id: int, canvases, config: dict, on_progress: Progress | None = None) -> dict[str, str]:
    """Render `clip_id` in each canvas it does not already have. Returns
    {canvas: path} for what was rendered."""
    from core.models import ClipCandidate, Segment
    from core.pipeline import _render_files
    from transcription.transcriber import detected_language

    clip = db.get_clip(clip_id)
    if clip is None:
        raise ValueError(f"No clip with id {clip_id}")
    if not clip["path"]:
        raise ValueError("This clip has no rendered file to take its settings from.")
    opts = json.loads(clip["render_opts"]) if clip["render_opts"] else {}
    wanted = [c for c in normalize(canvases) if c != clip_canvas(opts)]
    if not wanted:
        return {}

    data_dir = Path(config["paths"]["data_dir"])
    video_id = clip["video_id"]
    source = cached_source(data_dir / "downloads", video_id)
    if source is None:
        raise FileNotFoundError(f"The source video for this clip is no longer on disk ({video_id}).")
    transcript = json.loads((data_dir / "transcripts" / f"{video_id}.json").read_text(encoding="utf-8"))
    segments = [Segment(**s) for s in transcript["segments"]]
    candidate = ClipCandidate(
        start=float(clip["start_s"]), end=float(clip["end_s"]),
        score=clip["score"], hook=clip["hook"] or "",
    )
    clip_dir = Path(clip["path"]).parent
    language = detected_language(video_id, data_dir / "transcripts")
    cache: dict = {}
    total = len(wanted)
    done: dict[str, str] = {}

    def one(canvas: str) -> tuple[str, Path]:
        path, _ = _render_files(
            source, candidate, segments, clip_dir, config,
            {**opts, "canvas": canvas}, language, tracking_cache=cache,
        )
        return canvas, path

    def finished(canvas: str, path: Path) -> None:
        _record(db, clip_id, canvas, path)
        done[canvas] = str(path)
        if on_progress:
            on_progress(len(done), total, f"Rendered {canvas}")

    # First alone: it computes the tracking every other shape reuses.
    if on_progress:
        on_progress(0, total, f"Rendering {wanted[0]}")
    finished(*one(wanted[0]))
    rest = wanted[1:]
    if rest:
        workers = max(1, min(len(rest), int((config.get("video") or {}).get("parallel_renders", 2))))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="variant") as pool:
            # Results are recorded here, on this thread: the sqlite connection
            # belongs to it and must not be used from the pool's threads.
            for canvas, path in pool.map(one, rest):
                finished(canvas, path)
    return done
