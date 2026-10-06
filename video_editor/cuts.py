"""Cut/split filter graph: keep-ranges -> FFmpeg trim + concat (or crossfade).

Produces one continuous video from the kept sections. The audio comes in as
a named stream (mutes are applied to it FIRST — see audio.py — so mute
coordinates stay in the original timeline).

With `overlap` > 0 the sections are joined by a short crossfade instead of a
hard cut, which is what makes dropping dead air read as one continuous clip
rather than a jump. A crossfade OVERLAPS the two sections, so the result is
shorter by `overlap` per join; EditList.final_duration() and remap() account
for that and must be given the same number (see EditList.overlap()).
"""


def concat_graph(
    keep: list[tuple[float, float]], audio_in: str, overlap: float = 0.0
) -> tuple[str, str, str]:
    """Filter-graph text for cutting to the keep-ranges.
    Returns (graph, video_out_label, audio_out_label)."""
    parts = []
    for i, (a, b) in enumerate(keep):
        parts.append(
            f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[{audio_in}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
    if overlap > 0 and len(keep) > 1:
        # Chain the joins left to right. Each xfade starts `overlap` before the
        # end of everything joined so far.
        joined = keep[0][1] - keep[0][0]
        vprev, aprev = "[v0]", "[a0]"
        for i in range(1, len(keep)):
            last = i == len(keep) - 1
            vout = "[vcut]" if last else f"[vx{i}]"
            aout = "[acut]" if last else f"[ax{i}]"
            parts.append(
                f"{vprev}[v{i}]xfade=transition=fade:duration={overlap:.3f}:"
                f"offset={joined - overlap:.3f}{vout}"
            )
            parts.append(f"{aprev}[a{i}]acrossfade=d={overlap:.3f}:c1=tri:c2=tri{aout}")
            joined += (keep[i][1] - keep[i][0]) - overlap
            vprev, aprev = vout, aout
        return ";".join(parts), "[vcut]", "[acut]"

    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    parts.append(f"{pairs}concat=n={len(keep)}:v=1:a=1[vcut][acut]")
    return ";".join(parts), "[vcut]", "[acut]"
