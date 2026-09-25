"""Render a validated Recipe into one video.

    1. Every part (intro, each segment, outro) is rendered on its own, in ONE
       FFmpeg pass each: cut -> blur regions -> fit to the canvas -> credit and
       text banner burned (ASS) -> loudness -> the GPU encoder. Every part
       comes out with identical parameters (size, 30fps CFR, yuv420p, 48 kHz
       stereo AAC).
    2. The parts are joined:
         * hard cuts: the concat demuxer with `-c copy`, lossless and
           instant, which is how longform/assemble.py joins too;
         * transitions: one xfade/acrossfade chain, a single re-encode.
    3. An IMAGE banner is overlaid on the joined video (one extra pass, only
       when there is one). A TEXT banner cost nothing: it rode along in step 1.

Cancellation lands between parts, like longform assembly.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from compilation import credits as credits_mod
from compilation.recipe import Recipe, SegmentSpec
from core import cancel
from core.binaries import ffmpeg, ffprobe
from core.paths import discard
from video.encoding import LOUDNORM, video_encoder_args

FPS = 30
Progress = Callable[[int, int, str], None]


@dataclass
class SourceInfo:
    """What the renderer needs to know about one library video."""

    path: Path
    channel: str = ""
    title: str = ""
    url: str = ""


# ---- probing -------------------------------------------------------------------


def probe(path: Path) -> tuple[float, bool]:
    """(duration seconds, has an audio stream)."""
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"could not read {path.name}: {r.stderr.strip()[-300:]}")
    info = json.loads(r.stdout or "{}")
    duration = float((info.get("format") or {}).get("duration") or 0)
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams") or [])
    return duration, has_audio


# ---- filter graph pieces --------------------------------------------------------------


def blur_chain(regions, src: str, out: str) -> str:
    """Blur rectangles given as fractions of the SOURCE frame. Empty when
    there are none, so callers can skip the stage entirely."""
    if not regions:
        return ""
    parts, cur = [], src
    for i, (x, y, w, h) in enumerate(regions):
        base, piece, blurred, nxt = f"bb{i}", f"bp{i}", f"bz{i}", (out if i == len(regions) - 1 else f"bo{i}")
        parts.append(f"[{cur}]split[{base}][{piece}]")
        parts.append(
            f"[{piece}]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
            f"boxblur=luma_radius=20:luma_power=3:chroma_radius=10:chroma_power=3[{blurred}]"
        )
        parts.append(f"[{base}][{blurred}]overlay=x=main_w*{x:.4f}:y=main_h*{y:.4f}[{nxt}]")
        cur = nxt
    return ";".join(parts)


def fit_chain(fit: str, canvas: tuple[int, int], src: str, out: str) -> str:
    """Put a frame of any shape onto the canvas without distorting it."""
    w, h = canvas
    if fit == "crop":
        return (f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}[{out}]")
    if fit == "pad":
        return (f"[{src}]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[{out}]")
    # blur: the frame, fitted, over a blurred copy of itself that covers the
    # canvas. Blur at 1/8 size and scale back up: same look as a big-radius
    # blur at full size, a fraction of the cost (the trick video/cropper uses).
    sw, sh = max(2, w // 8 // 2 * 2), max(2, h // 8 // 2 * 2)
    return (
        f"[{src}]split[fg0][bg0];"
        f"[bg0]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"scale={sw}:{sh},gblur=sigma=6,scale={w}:{h}:flags=bilinear[bg1];"
        f"[fg0]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos[fg1];"
        f"[bg1][fg1]overlay=(W-w)/2:(H-h)/2[{out}]"
    )


# ---- one part ------------------------------------------------------------------------------


def render_part(
    source: Path,
    output: Path,
    *,
    start: float,
    duration: float,
    recipe: Recipe,
    has_audio: bool,
    volume: float = 1.0,
    blur_regions=(),
    ass: Path | None = None,
) -> Path:
    canvas = recipe.size
    graph = []
    cur = "0:v"
    blur = blur_chain(list(blur_regions), cur, "vblur")
    if blur:
        graph.append(blur)
        cur = "vblur"
    graph.append(fit_chain(recipe.fit, canvas, cur, "vfit"))
    tail = f"fps={FPS},format=yuv420p,setsar=1"
    if ass is not None:
        # Referenced by name with cwd set to its folder: FFmpeg's filter
        # parser mangles Windows drive paths ("C:") inside a filtergraph.
        tail += f",subtitles={ass.name}"
    graph.append(f"[vfit]{tail}[vout]")

    inputs = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source.resolve())]
    if has_audio:
        level = f",{LOUDNORM}" if recipe.normalize_audio else ""
        graph.append(
            f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={volume:.3f}{level},"
            f"aresample=48000[aout]"
        )
        audio_map = "[aout]"
    else:
        # Silent source: a matching silent track keeps every part identical,
        # which the lossless concat join depends on.
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_map = "1:a"

    cmd = [
        ffmpeg(), "-y", *inputs,
        "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-map", audio_map,
        "-t", f"{duration:.3f}",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        str(output.resolve()),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ass.parent if ass else None)
    if r.returncode != 0:
        raise RuntimeError(f"rendering {output.name} failed:\n{r.stderr[-1500:]}")
    return output


# ---- joining --------------------------------------------------------------------------


def join_concat(parts: list[Path], output: Path, work: Path) -> Path:
    listfile = work / "concat.txt"
    listfile.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    r = subprocess.run(
        [ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listfile.resolve()),
         "-c", "copy", "-movflags", "+faststart", str(output.resolve())],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"joining parts failed:\n{r.stderr[-1500:]}")
    return output


def xfade_graph(durations: list[float], transition: str, t: float) -> str:
    """The filtergraph joining N inputs with an xfade (video) and acrossfade
    (audio) at every boundary. Each join overlaps the neighbours by `t`, so
    the next offset is the running length minus `t`."""
    n = len(durations)
    graph = [f"[{i}:v]settb=AVTB,fps={FPS}[v{i}]" for i in range(n)]
    vprev, aprev, total = "v0", "0:a", durations[0]
    for i in range(1, n):
        vout = "vout" if i == n - 1 else f"vx{i}"
        aout = "aout" if i == n - 1 else f"ax{i}"
        offset = max(0.0, total - t)
        graph.append(f"[{vprev}][v{i}]xfade=transition={transition}:duration={t:.3f}:offset={offset:.3f}[{vout}]")
        graph.append(f"[{aprev}][{i}:a]acrossfade=d={t:.3f}:c1=tri:c2=tri[{aout}]")
        vprev, aprev = vout, aout
        total = total + durations[i] - t
    return ";".join(graph)


def join_xfade(parts: list[Path], output: Path, transition: str, t: float) -> Path:
    durations = [probe(p)[0] for p in parts]
    inputs = []
    for p in parts:
        inputs += ["-i", str(p.resolve())]
    cmd = [
        ffmpeg(), "-y", *inputs,
        "-filter_complex", xfade_graph(durations, transition, t),
        "-map", "[vout]", "-map", "[aout]",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-fps_mode", "cfr", "-movflags", "+faststart",
        str(output.resolve()),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"joining with {transition} transitions failed:\n{r.stderr[-1500:]}")
    return output


# ---- whole compilation ----------------------------------------------------------------------


def _segment_ass(
    seg: SegmentSpec, src: SourceInfo, recipe: Recipe, banner_text: dict | None, work: Path, index: int
) -> Path | None:
    """The ASS file for one segment: its credit and/or the text banner.
    None when there is nothing to burn, so the pass skips libass."""
    from video_editor import watermark

    ass = work / f"seg_{index:04d}.ass"
    wrote = False
    if recipe.credits.enabled and seg.credit:
        text = credits_mod.credit_text(recipe.credits, channel=src.channel, title=src.title, url=src.url)
        if text:
            ass.write_text(credits_mod.build_ass(text, recipe.credits, recipe.size, seg.duration), encoding="utf-8")
            wrote = True
    if banner_text:
        watermark.ensure_text(ass if wrote else None, ass, banner_text, recipe.size, seg.duration)
        wrote = True
    return ass if wrote else None


def render(
    recipe: Recipe,
    sources: dict[str, SourceInfo],
    output: Path,
    *,
    banner: dict | None = None,
    banner_assets: Path | None = None,
    cancel_key: str = "",
    on_progress: Progress | None = None,
) -> Path:
    """Render the recipe to `output`. `banner` is a resolved watermark config
    (the recipe's {"profile_id"} already looked up by the caller)."""
    from video_editor import watermark

    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / (output.stem + ".parts")
    work.mkdir(exist_ok=True)

    banner_text = banner if banner and watermark.has_text(banner) else None
    plan: list[tuple[str, dict]] = []
    if recipe.intro:
        plan.append(("intro", {"path": recipe.intro}))
    for seg in recipe.segments:
        plan.append(("segment", {"seg": seg}))
    if recipe.outro:
        plan.append(("outro", {"path": recipe.outro}))
    total = len(plan) + 1

    def step(i: int, label: str) -> None:
        if cancel_key:
            cancel.check(cancel_key)
        if on_progress:
            on_progress(i, total, label)

    try:
        parts: list[Path] = []
        seg_no = 0
        for i, (kind, item) in enumerate(plan):
            part = work / f"part_{i:04d}.mp4"
            if kind == "segment":
                seg: SegmentSpec = item["seg"]
                src = sources[seg.video_id]
                seg_no += 1
                step(i, f"Segment {seg_no}/{len(recipe.segments)}: {src.title or seg.video_id}")
                _, has_audio = probe(src.path)
                render_part(
                    src.path, part,
                    start=seg.start, duration=seg.duration, recipe=recipe, has_audio=has_audio,
                    volume=seg.volume, blur_regions=seg.blur_regions,
                    ass=_segment_ass(seg, src, recipe, banner_text, work, i),
                )
            else:
                step(i, kind.capitalize())
                duration, has_audio = probe(item["path"])
                render_part(item["path"], part, start=0.0, duration=duration, recipe=recipe, has_audio=has_audio)
            parts.append(part)

        step(len(plan), "Joining")
        joined = work / "joined.mp4"
        if recipe.transition == "none" or len(parts) == 1:
            join_concat(parts, joined, work)
        else:
            join_xfade(parts, joined, recipe.transition, recipe.transition_duration)

        if banner and banner_assets is not None and watermark.has_image(banner, banner_assets):
            watermark.apply_image(joined, banner, recipe.size, banner_assets)

        joined.replace(output)
        if on_progress:
            on_progress(total, total, "Done")
        return output
    finally:
        for f in work.glob("*"):
            discard(f)
        try:
            work.rmdir()
        except OSError:
            pass
