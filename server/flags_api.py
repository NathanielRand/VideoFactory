"""Flag a clip that came out wrong, and keep what is needed to review it.

The pipeline makes two kinds of decision the user can disagree with: WHERE a
moment starts and ends, and HOW it is framed. Neither is visible from the
finished file alone, so a flag stores the reasons the user ticked next to a
snapshot of what was decided (span, scores, what the model proposed, the crop
mode, the words spoken around it) and a few frames from the finished clip and
from the untouched source at the same moments. That is enough to see, for one
bad clip, whether the fault was the model's pick, the boundary fitting, or the
crop.

Each flag is a row in `clip_flags` and a self-contained folder,
`<data_dir>/flags/<id>/` (report.json + jpgs), that can be zipped up and sent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

# (id, label, group). The group is only how the UI lays them out.
REASONS: list[tuple[str, str, str]] = [
    ("starts_late", "Starts too late: the setup is missing", "moment"),
    ("ends_early", "Ends too early: the payoff is cut off", "moment"),
    ("mid_sentence", "Starts or ends mid-sentence", "moment"),
    ("runs_long", "Runs on past the point", "moment"),
    ("not_a_moment", "Not a good moment", "moment"),
    ("duplicate", "Same moment as another clip", "moment"),
    ("wrong_person", "Frames the wrong person", "framing"),
    ("subject_cut_off", "Subject cut off or off-centre", "framing"),
    ("wrong_angle", "Wrong camera angle after a cut", "framing"),
    ("needs_wide", "Needed the whole frame (letterbox)", "framing"),
    ("needs_tight", "Should be zoomed in tighter", "framing"),
    ("crop_jumps", "Crop jumps or drifts", "framing"),
    ("captions", "Captions are wrong", "other"),
    ("other", "Something else", "other"),
]
REASON_IDS = {r[0] for r in REASONS}
CONTEXT_SECONDS = 20.0
MAX_TRANSCRIPT_CHARS = 6000


class FlagIn(BaseModel):
    reasons: list[str] = Field(min_length=1, max_length=len(REASONS))
    note: str = Field(default="", max_length=2000)


def _frames(video: Path, times: list[float], out_dir: Path, prefix: str) -> list[str]:
    """One small jpg per time; whichever fail are skipped. Returns file names."""
    from core.binaries import ffmpeg

    names = []
    for i, t in enumerate(times):
        name = f"{prefix}_{i + 1}.jpg"
        try:
            r = subprocess.run(
                [ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
                 "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4", str(out_dir / name)],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0 and (out_dir / name).exists():
            names.append(name)
    return names


def _snapshot(config: dict, data_dir: Path, clip, video) -> dict:
    scores = json.loads(clip["scores"]) if clip["scores"] else {}
    opts = json.loads(clip["render_opts"]) if clip["render_opts"] else {}
    start, end = float(clip["start_s"]), float(clip["end_s"])

    words = ""
    tpath = data_dir / "transcripts" / f"{clip['video_id']}.json"
    if tpath.exists():
        try:
            segs = json.loads(tpath.read_text(encoding="utf-8"))["segments"]
        except (OSError, ValueError, KeyError):
            segs = []
        lines = []
        for s in segs:
            if s["end"] > start - CONTEXT_SECONDS and s["start"] < end + CONTEXT_SECONDS:
                mark = ">>" if s["end"] > start and s["start"] < end else "  "
                lines.append(f"{mark} [{s['start']:.1f}-{s['end']:.1f}] {s['text'].strip()}")
        words = "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]

    return {
        "clip": {"id": clip["id"], "start": start, "end": end, "duration": round(end - start, 2),
                 "score": clip["score"], "hook": clip["hook"], "path": clip["path"]},
        "video": {"id": clip["video_id"], "title": video["title"] if video else "",
                  "channel": video["channel_name"] if video else ""},
        # proposed_* is what the model/peak asked for; refined_from is where the
        # edge-refine pass moved it from. Together with start/end they show
        # which stage put the boundary where it is.
        "scoring": scores,
        "render_opts": opts,
        "crop": opts.get("crop", "track"),
        "podcast": bool(opts.get("podcast")),
        "model": config.get("model") or "",
        # ">>" marks lines inside the clip; the rest is context before/after.
        "transcript": words,
    }


def install(app, *, config, db, data_dir: Path, worker=None) -> None:
    folder = Path(data_dir) / "flags"

    def _plan(d, clip, reasons: set) -> dict:
        """What re-cutting this clip from these reasons would change: the same
        answer the Re-cut button acts on and the flag reply advertises."""
        from creator.learning import recut_plan

        video = d.conn.execute("SELECT duration FROM videos WHERE video_id = ?", (clip["video_id"],)).fetchone()
        segments = []
        tpath = Path(data_dir) / "transcripts" / f"{clip['video_id']}.json"
        if tpath.exists():
            try:
                from core.models import Segment

                segments = [Segment(**s) for s in json.loads(tpath.read_text(encoding="utf-8"))["segments"]]
            except (OSError, ValueError, KeyError, TypeError):
                segments = []
        opts = json.loads(clip["render_opts"]) if clip["render_opts"] else {}
        # Cuts and caption edits are stored as seconds from the clip's start, and
        # a render does not shift them when the start moves, so they would all
        # land 1.5s off. Leave the edges alone on such a clip and fix only what
        # does not depend on them.
        edited = bool(opts.get("edit") or opts.get("caption_lines"))
        if edited:
            reasons = reasons - {"starts_late", "ends_early", "runs_long", "mid_sentence"}
        plan = recut_plan(
            reasons, float(clip["start_s"]), float(clip["end_s"]), opts, segments,
            float(video["duration"]) if video and video["duration"] else None,
            float((config.get("clips") or {}).get("max_duration", 60)),
        )
        plan["edited"] = edited
        return plan

    @app.get("/flags/reasons")
    def flag_reasons():
        return {"reasons": [{"id": i, "label": label, "group": g} for i, label, g in REASONS]}

    @app.post("/clips/{clip_id}/flag")
    def flag_clip(clip_id: int, body: FlagIn):
        bad = [r for r in body.reasons if r not in REASON_IDS]
        if bad:
            raise HTTPException(400, f"unknown reason: {bad[0]}")
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            video = d.conn.execute(
                "SELECT title, channel_name FROM videos WHERE video_id = ?", (clip["video_id"],)
            ).fetchone()
            reasons = list(dict.fromkeys(body.reasons))
            snap = _snapshot(config, Path(data_dir), clip, video)
            flag_id = d.add_clip_flag(clip_id, clip["video_id"], reasons, body.note.strip(), snap)
            # Say what this flag has done and will do, from the same counts the
            # learning reads, so the answer to "does this fix anything?" is not
            # a guess. It is never allowed to fail the flag itself.
            try:
                from creator.learning import flag_feedback

                owner = d.conn.execute(
                    "SELECT creator_id FROM videos WHERE video_id = ?", (clip["video_id"],)
                ).fetchone()
                learning = flag_feedback(d, owner["creator_id"] if owner else None, reasons)
            except Exception:
                learning = {"applied": [], "pending": [], "recorded": []}
            # Whether the Re-cut button has anything to do, so the dialog only
            # offers it when it does (and says what it would change).
            try:
                open_reasons = {r for f in d.list_clip_flags(status="open", clip_id=clip_id)
                                for r in json.loads(f["reasons"] or "[]")}
                changes = _plan(d, clip, open_reasons)["changes"]
                recut = {"available": bool(changes), "changes": changes}
            except Exception:
                recut = {"available": False, "changes": []}
        finally:
            d.close()

        # Evidence is best-effort: a flag is worth keeping even with no frames.
        out = folder / str(flag_id)
        try:
            out.mkdir(parents=True, exist_ok=True)
            span = snap["clip"]["duration"]
            rendered = Path(clip["path"]) if clip["path"] else None
            files = {}
            if rendered and rendered.exists():
                files["clip"] = _frames(rendered, [span * f for f in (0.1, 0.5, 0.9)], out, "clip")
            source = Path(data_dir) / "downloads" / f"{clip['video_id']}.mp4"
            if source.exists():
                s = snap["clip"]["start"]
                files["source"] = _frames(source, [s + span * f for f in (0.1, 0.5, 0.9)], out, "source")
            (out / "report.json").write_text(
                json.dumps({"id": flag_id, "reasons": reasons, "note": body.note.strip(),
                            "frames": files, **snap}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass
        return {"id": flag_id, "folder": str(out), "learning": learning, "recut": recut}

    @app.post("/clips/{clip_id}/recut")
    def recut_clip(clip_id: int):
        """Fix THIS clip from its own open flags and render it again.

        Flags otherwise only teach future runs. This is the other half: the
        reasons ticked on this clip say what to change on it (start earlier,
        end later, different framing), so change that, now, with no minimum
        count."""
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            flags = d.list_clip_flags(status="open", clip_id=clip_id)
            reasons = {r for f in flags for r in json.loads(f["reasons"] or "[]")}
            if not reasons:
                raise HTTPException(409, "This clip has no open flags to act on.")
            plan = _plan(d, clip, reasons)
            if not plan["changes"]:
                edited = plan["edited"]
                raise HTTPException(
                    409,
                    "This clip has hand edits (cuts or captions) that would shift if its start moved, "
                    "so its edges are left alone. Open the editor to adjust them."
                    if edited else
                    "Nothing in those flags can be fixed automatically. Open the editor to adjust it by hand.",
                )
            payload = {"clip_id": clip_id, "start": plan["start"], "end": plan["end"]}
            if plan["render_opts"]:
                payload["render_opts"] = plan["render_opts"]
            job_id = d.add_job("render", json.dumps(payload))
            for f in flags:
                d.set_clip_flag_status(f["id"], "resolved")
        finally:
            d.close()
        if worker is not None:
            worker.notify()
        return {"job_id": job_id, "changes": plan["changes"]}

    @app.get("/flags")
    def list_flags(status: str | None = None, clip_id: int | None = None):
        d = db()
        try:
            rows = d.list_clip_flags(status=status, clip_id=clip_id)
        finally:
            d.close()
        return {
            "flags": [
                {"id": r["id"], "clip_id": r["clip_id"], "video_id": r["video_id"],
                 "reasons": json.loads(r["reasons"]), "note": r["note"], "status": r["status"],
                 "created_at": r["created_at"], "folder": str(folder / str(r["id"]))}
                for r in rows
            ]
        }

    @app.post("/flags/{flag_id}/resolve")
    def resolve_flag(flag_id: int):
        d = db()
        try:
            d.set_clip_flag_status(flag_id, "resolved")
        finally:
            d.close()
        return {"id": flag_id, "status": "resolved"}
