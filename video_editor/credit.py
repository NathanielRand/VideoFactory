"""Credit the source channel on a rendered clip, like a compilation does.

A clip's branding profile may carry a "credit" block, the same shape as a
compilation recipe's "credits" (compilation/recipe.py CreditStyle): template,
timing, corner, font, colours, and an optional banner image ("plate") the text
sits on. `{channel}` is filled from the source video, so one profile credits
whichever creator the clip came from.

It runs as one extra pass on the finished clip, after the watermark, and only when a credit is enabled and the video has a name
to credit. The plate is overlaid first and the text burned on top of it, which is
why this is its own pass and not a line in the clip's shared caption file: that
file is burned BEFORE any image overlay, so a plate would cover the text.

Everything visual is decided by compilation/credits.py; this file only puts it
on a clip.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from compilation import credits as credits_mod
from compilation.recipe import CreditStyle, RecipeError, credit_style
from core.binaries import ffmpeg, ffprobe
from core.paths import discard, safe_name
from video.encoding import video_encoder_args


def style_of(cfg: dict | None) -> CreditStyle | None:
    """The clip credit a branding config asks for, or None when off or invalid.
    A bad stored block must never fail the render, so it reads as off."""
    block = (cfg or {}).get("credit")
    if not isinstance(block, dict) or not block.get("enabled"):
        return None
    try:
        return credit_style(block)
    except RecipeError:
        return None


def source_info(source: Path, config: dict) -> dict:
    """{channel, title, url} for the video this clip was cut from. Read with its
    own connection: renders run in worker threads and sqlite connections are
    not shareable across them."""
    empty = {"channel": "", "title": "", "url": ""}
    try:
        db_path = Path(config["paths"]["data_dir"]) / "state.db"
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT channel_name, title, channel_url, source_url FROM videos WHERE video_id = ?",
                (source.stem,),
            ).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, KeyError, OSError):
        return empty
    if not row:
        return empty
    return {"channel": row[0] or "", "title": row[1] or "", "url": row[2] or row[3] or ""}


def _image_size(path: Path) -> tuple[int, int]:
    import json

    r = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    s = json.loads(r.stdout or "{}")["streams"][0]
    return int(s["width"]), int(s["height"])


def apply(
    video_path: Path,
    style: CreditStyle,
    canvas: tuple[int, int],
    *,
    channel: str,
    title: str,
    url: str,
    duration: float,
    asset_dir: Path,
) -> bool:
    """Burn the credit into `video_path` in place. True when one was added.

    Nothing to credit (no channel and no title), or a banner image that has
    gone missing, skips the credit quietly: the clip is fine without it.
    """
    from video_editor.watermark import _asset_in

    text = credits_mod.credit_text(style, channel=channel, title=title, url=url)
    if not text:
        return False

    plate = None
    if style.bg_image:
        name = safe_name(style.bg_image)
        image = _asset_in(asset_dir, name) if name else None
        if image is not None:
            try:
                plate = credits_mod.plate_for(style, canvas, image, _image_size(image))
            except (OSError, ValueError, KeyError, IndexError):
                plate = None  # unreadable banner: fall back to the plain look

    ass = video_path.with_suffix(".credit.ass")
    out = video_path.with_suffix(".credit.mp4")
    ass.write_text(credits_mod.build_ass(text, style, canvas, duration, plate), encoding="utf-8")

    inputs = ["-i", str(video_path.resolve())]
    graph = []
    if plate is not None:
        start, end = credits_mod.show_window(style, duration)
        fade = credits_mod.FADE_MS / 1000
        inputs += ["-loop", "1", "-t", f"{duration + 1:.3f}", "-i", str(plate.image.resolve())]
        graph.append(
            f"[1:v]scale={plate.w}:{plate.h},format=rgba,"
            f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={max(start, end - fade):.3f}:d={fade:.3f}:alpha=1[plate]"
        )
        graph.append(
            f"[0:v][plate]overlay=x={plate.x}:y={plate.y}:eof_action=pass:"
            f"enable='between(t,{start:.3f},{end:.3f})',format=yuv420p[base]"
        )
        base = "[base]"
    else:
        base = "[0:v]"
    # By name with cwd set to its folder: FFmpeg's filter parser mangles
    # Windows drive paths ("C:") inside a filtergraph.
    graph.append(f"{base}subtitles={ass.name}[v]")

    cmd = [
        ffmpeg(), "-y", *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "0:a?",
        *video_encoder_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-shortest",
        str(out.resolve()),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=ass.parent)
        if result.returncode != 0:
            raise RuntimeError(f"credit overlay failed:\n{result.stderr[-1500:]}")
        out.replace(video_path)
    finally:
        discard(ass)
        discard(out)
    return True
