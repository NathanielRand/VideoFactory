"""HTTP routes for compilations, their templates, and source credits.

Installed from create_app() like the publisher modules. A recipe is validated
on every save that asks for it and always before a render is queued, so a bad
one fails at the button press with a readable reason, not an hour into the
queue.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from compilation import store
from compilation.recipe import CANVASES, FITS, POSITIONS, TRANSITIONS, RecipeError
from core import queue
from core.paths import cached_source


class CompilationIn(BaseModel):
    title: str = ""
    recipe: dict | None = None
    template_id: int | None = None


class CompilationPatch(BaseModel):
    title: str | None = None
    recipe: dict | None = None


class TemplateIn(BaseModel):
    name: str = ""
    config: dict = {}


class CreditIn(BaseModel):
    channel_name: str | None = None
    channel_url: str | None = None
    rights: str | None = None


def install(app, *, config, db, data_dir: Path, worker, broadcaster) -> None:
    downloads = Path(data_dir) / "downloads"

    def _get(d, comp_id: int) -> dict:
        comp = store.get(d, comp_id)
        if comp is None:
            raise HTTPException(404, "no such compilation")
        return comp

    def _problem(d, recipe: dict, *, require_segments: bool) -> str:
        try:
            store.validate(d, recipe, config, require_segments=require_segments)
            return ""
        except RecipeError as e:
            return str(e)

    # ---- options the editor offers ------------------------------------------

    @app.get("/compilations/options")
    def compilation_options():
        return {
            "canvases": {k: {"width": w, "height": h} for k, (w, h) in CANVASES.items()},
            "fits": list(FITS),
            "transitions": list(TRANSITIONS),
            "credit_positions": list(POSITIONS),
            "rights": list(store_rights()),
        }

    @app.get("/compilations/library")
    def compilation_library():
        """Every library video a segment can come from, with what its credit
        will say and whether its source file is still on disk."""
        d = db()
        try:
            rows = d.conn.execute(
                "SELECT video_id, title, channel_name, channel_url, source_url, rights, duration, created_at "
                "FROM videos ORDER BY created_at DESC"
            ).fetchall()
        finally:
            d.close()
        return [
            {**dict(r), "has_source": cached_source(downloads, r["video_id"]) is not None}
            for r in rows
        ]

    @app.get("/compilations/source/{video_id}")
    def compilation_source(video_id: str):
        """The imported source file itself, for scrubbing to pick a segment's
        in and out points. Only ever a file already in downloads/."""
        path = cached_source(downloads, video_id)
        if path is None:
            raise HTTPException(404, "source file not on disk")
        return FileResponse(path, media_type="video/mp4")

    # ---- compilations ---------------------------------------------------------

    @app.get("/compilations")
    def list_compilations():
        d = db()
        try:
            return store.list_all(d)
        finally:
            d.close()

    @app.post("/compilations", status_code=201)
    def create_compilation(body: CompilationIn):
        d = db()
        try:
            recipe = dict(body.recipe or {})
            if body.template_id is not None:
                tpl = store.get_template(d, body.template_id)
                if tpl is None:
                    raise HTTPException(404, "no such template")
                recipe = store.recipe_mod.apply_template(tpl["config"], recipe)
            comp_id = store.create(d, body.title, recipe)
            return store.get(d, comp_id)
        finally:
            d.close()

    @app.get("/compilations/{comp_id}")
    def get_compilation(comp_id: int):
        d = db()
        try:
            comp = _get(d, comp_id)
            comp["problem"] = _problem(d, comp["recipe"], require_segments=False)
            return comp
        finally:
            d.close()

    @app.patch("/compilations/{comp_id}")
    def patch_compilation(comp_id: int, body: CompilationPatch):
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "This compilation is rendering. Wait for it to finish, or cancel it.")
            store.update(d, comp_id, title=body.title, recipe=body.recipe)
            comp = store.get(d, comp_id)
            comp["problem"] = _problem(d, comp["recipe"], require_segments=False)
            return comp
        finally:
            d.close()

    @app.delete("/compilations/{comp_id}")
    def delete_compilation(comp_id: int):
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "Cancel the render before deleting this compilation.")
            store.delete(d, comp_id)
            return {"ok": True}
        finally:
            d.close()

    @app.post("/compilations/{comp_id}/render")
    def render_compilation(comp_id: int):
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "Already queued.")
            try:
                parsed = store.validate(d, comp["recipe"], config)
                store.sources_for(d, [s.video_id for s in parsed.segments], downloads)
            except RecipeError as e:
                raise HTTPException(400, str(e))
            job_id = queue.enqueue(d, "compile", {"compilation_id": comp_id}, title=comp["title"])
            store.set_status(d, comp_id, "queued")
            # Pressing Render is the go-ahead for THIS job; it never starts
            # other videos someone staged in a paused queue.
            started = queue.start_if_alone(d, job_id)
        finally:
            d.close()
        worker.notify()
        broadcaster.publish({"type": "queue"})
        return {"job_id": job_id, "started": started}

    @app.get("/compilations/{comp_id}/media")
    def compilation_media(comp_id: int):
        d = db()
        try:
            comp = _get(d, comp_id)
        finally:
            d.close()
        if not comp["output_path"]:
            raise HTTPException(404, "not rendered yet")
        path = Path(comp["output_path"]).resolve()
        if not path.exists() or Path(data_dir).resolve() not in path.parents:
            raise HTTPException(404, "rendered file missing")
        return FileResponse(path, media_type="video/mp4")

    # ---- templates --------------------------------------------------------------------

    @app.get("/compilation-templates")
    def list_compilation_templates():
        d = db()
        try:
            return store.list_templates(d)
        finally:
            d.close()

    @app.post("/compilation-templates", status_code=201)
    def create_compilation_template(body: TemplateIn):
        d = db()
        try:
            problem = _problem(d, body.config, require_segments=False)
            if problem:
                raise HTTPException(400, problem)
            tid = store.save_template(d, body.name, body.config)
            return store.get_template(d, tid)
        finally:
            d.close()

    @app.put("/compilation-templates/{template_id}")
    def update_compilation_template(template_id: int, body: TemplateIn):
        d = db()
        try:
            if store.get_template(d, template_id) is None:
                raise HTTPException(404, "no such template")
            problem = _problem(d, body.config, require_segments=False)
            if problem:
                raise HTTPException(400, problem)
            store.save_template(d, body.name, body.config, template_id)
            return store.get_template(d, template_id)
        finally:
            d.close()

    @app.delete("/compilation-templates/{template_id}")
    def delete_compilation_template(template_id: int):
        d = db()
        try:
            if not store.delete_template(d, template_id):
                raise HTTPException(404, "no such template")
            return {"ok": True}
        finally:
            d.close()

    # ---- source credits -----------------------------------------------------------

    @app.patch("/videos/{video_id}/credit")
    def patch_video_credit(video_id: str, body: CreditIn):
        d = db()
        try:
            try:
                found = d.set_video_credit(
                    video_id, channel_name=body.channel_name, channel_url=body.channel_url, rights=body.rights
                )
            except ValueError as e:
                raise HTTPException(400, str(e))
            if not found:
                raise HTTPException(404, "no such video")
            row = d.conn.execute(
                "SELECT video_id, title, channel_name, channel_url, source_url, rights FROM videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            return dict(row)
        finally:
            d.close()


def store_rights() -> tuple[str, ...]:
    from core.state import StateDB

    return StateDB.RIGHTS
