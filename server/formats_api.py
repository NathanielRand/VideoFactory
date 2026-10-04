"""HTTP routes for output formats: the canvas/platform table, and rendering an
AI clip in other shapes. Installed from create_app() like the other modules."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from core import queue
from formats import profiles, variants
from server.media import video_response


class VariantsIn(BaseModel):
    canvases: list[str] = []


def install(app, *, db, data_dir: Path, worker, broadcaster) -> None:
    root = Path(data_dir).resolve()

    @app.get("/formats")
    def formats():
        return {
            "canvases": {k: {"width": w, "height": h} for k, (w, h) in profiles.CANVASES.items()},
            "profiles": profiles.PROFILES,
        }

    @app.get("/clips/{clip_id}/variants")
    def clip_variants(clip_id: int):
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            opts = json.loads(clip["render_opts"]) if clip["render_opts"] else {}
            duration = float(clip["end_s"]) - float(clip["start_s"])
            own = variants.clip_canvas(opts)
            return {
                "canvas": own,
                "variants": variants.list_for(d, clip_id),
                "warnings": {c: profiles.length_warnings(c, duration) for c in profiles.CANVASES},
            }
        finally:
            d.close()

    @app.post("/clips/{clip_id}/variants")
    def render_clip_variants(clip_id: int, body: VariantsIn):
        try:
            canvases = profiles.normalize(body.canvases)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not canvases:
            raise HTTPException(400, "Pick at least one format.")
        d = db()
        try:
            clip = d.get_clip(clip_id)
            if clip is None:
                raise HTTPException(404, "no such clip")
            job_id = queue.enqueue(
                d, "variants", {"clip_id": clip_id, "canvases": canvases},
                # No video_id: that column is the queue's "this video is being
                # processed" guard, and a variants job must not block one.
                title=f"{clip['title'] or 'Clip'} ({', '.join(canvases)})",
            )
            started = queue.start_if_alone(d, job_id)
        finally:
            d.close()
        worker.notify()
        broadcaster.publish({"type": "queue"})
        return {"job_id": job_id, "started": started}

    @app.get("/clips/{clip_id}/variants/{canvas_tag}/media")
    def clip_variant_media(clip_id: int, canvas_tag: str):
        canvas = canvas_tag.replace("x", ":")
        d = db()
        try:
            row = variants.get(d, clip_id, canvas)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such variant")
        path = Path(row["path"]).resolve()
        if not path.exists() or root not in path.parents:
            raise HTTPException(404, "variant file missing")
        return video_response(path)
