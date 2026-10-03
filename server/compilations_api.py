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

from server.media import video_response
from pydantic import BaseModel

from compilation import store
from compilation.recipe import CANVASES, FITS, POSITIONS, TRANSITIONS, RecipeError
from core import queue
from core.paths import cached_source, discard


class CompilationIn(BaseModel):
    title: str = ""
    recipe: dict | None = None
    template_id: int | None = None


class CompilationPatch(BaseModel):
    title: str | None = None
    recipe: dict | None = None


class SegmentIn(BaseModel):
    video_id: str
    # Both absent: the whole video. The Clips tab sends a clip's own range.
    start: float | None = None
    end: float | None = None


class TemplateIn(BaseModel):
    name: str = ""
    config: dict = {}


class TemplateRename(BaseModel):
    name: str


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
        return _check(d, recipe, require_segments=require_segments)[0]

    def _check(d, recipe: dict, *, require_segments: bool) -> tuple[str, dict]:
        """(problem, {format: [platforms it is too long for]})."""
        try:
            parsed = store.validate(d, recipe, config, require_segments=require_segments)
        except RecipeError as e:
            return str(e), {}
        return "", store.warnings(parsed)

    def _annotate(d, comp: dict) -> dict:
        comp["problem"], comp["warnings"] = _check(d, comp["recipe"], require_segments=False)
        return comp

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
        return video_response(path)

    # ---- compilations ---------------------------------------------------------

    @app.get("/compilations/progress")
    def compilation_progress():
        """Every compilation waiting to render or rendering, keyed by id: its
        job, and either how far through it is (percent, what it is doing, time
        left) or how many jobs are ahead of it. Declared before
        /compilations/{comp_id} so "progress" is not read as an id."""
        import json

        d = db()
        try:
            rows = d.conn.execute(
                "SELECT id, status, payload FROM jobs WHERE type = 'compile' "
                "AND status IN ('queued', 'running') ORDER BY id"
            ).fetchall()
            waiting = [r["id"] for r in d.queued_jobs()]
            paused = queue.is_paused(d)
        finally:
            d.close()
        out: dict[str, dict] = {}
        for r in rows:
            try:
                comp_id = int(json.loads(r["payload"] or "{}")["compilation_id"])
            except (ValueError, KeyError, TypeError):
                continue
            entry: dict = {"job_id": r["id"], "state": r["status"]}
            if r["status"] == "running":
                entry.update(worker.progress_snapshot(r["id"]) or {"percent": 0, "label": "Starting…"})
            else:
                entry["ahead"] = waiting.index(r["id"]) if r["id"] in waiting else 0
                entry["queue_paused"] = paused
            out[str(comp_id)] = entry
        return out

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
            return _annotate(d, _get(d, comp_id))
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
            if body.title is not None and body.title.strip() != comp["title"]:
                # Keep the files named after the compilation, so a rename
                # never leaves videos behind under the old name.
                store.rename_files(d, comp_id, store.get(d, comp_id)["title"])
            return _annotate(d, store.get(d, comp_id))
        finally:
            d.close()

    @app.post("/compilations/{comp_id}/segments")
    def append_compilation_segment(comp_id: int, body: SegmentIn):
        """Add one segment to the end, from outside the compilation editor:
        a clip from the Clips tab, or a whole upload from the Library. Done
        here rather than as a client read-modify-write, so it cannot race an
        edit the editor is saving at the same moment."""
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "This compilation is rendering. Wait for it to finish, or cancel it.")
            if body.start is None and body.end is None:
                duration = store.library_durations(d, [body.video_id]).get(body.video_id, 0.0)
                if duration <= 0:
                    raise HTTPException(400, "That video is not in the library yet, or its length is unknown.")
                seg = {"video_id": body.video_id, "start": 0.0, "end": round(duration, 2)}
            else:
                start = max(0.0, float(body.start or 0))
                end = float(body.end if body.end is not None else 0)
                if end <= start:
                    raise HTTPException(400, "A segment must end after it starts.")
                seg = {"video_id": body.video_id, "start": start, "end": end}
            dup = store.duplicate_of((comp["recipe"] or {}).get("segments") or [], seg)
            if dup is not None:
                raise HTTPException(409, f"Already in {comp['title']!r} (segment {dup + 1}).")
            store.append_segment(d, comp_id, seg)
            comp = _annotate(d, store.get(d, comp_id))
        finally:
            d.close()
        broadcaster.publish({"type": "compilation", "compilation_id": comp_id})
        return comp

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

    @app.post("/compilations/{comp_id}/loudness")
    def measure_loudness(comp_id: int):
        """How loud each part is and what evening-out will do to it: the
        measured level, the gain that matches it to the target, and the
        segment's own trim. Measurements are cached and shared with the
        render, so measuring here makes the render quicker, not slower."""
        from concurrent.futures import ThreadPoolExecutor

        from compilation import loudness
        from compilation.render import probe

        d = db()
        try:
            comp = _get(d, comp_id)
            try:
                parsed = store.validate(d, comp["recipe"], config, require_segments=False)
                sources = store.sources_for(d, [s.video_id for s in parsed.segments], downloads)
            except store.recipe_mod.RecipeError as e:
                raise HTTPException(400, str(e)) from e
        finally:
            d.close()
        cache = store.loudness_cache_path(Path(data_dir))

        jobs: list[tuple[str, int | None, Path, float, float, float]] = []
        for which in ("intro", "outro"):
            path = getattr(parsed, which)
            if path is not None and Path(path).exists():
                jobs.append((which, None, Path(path), 0.0, probe(Path(path))[0], 1.0))
        for i, seg in enumerate(parsed.segments):
            jobs.append(("segment", i, sources[seg.video_id].path, seg.start, seg.duration, seg.volume))

        def one(job):
            kind, index, path, start, duration, volume = job
            m = loudness.measure(path, start, duration, cache)
            return {"kind": kind, "index": index, **loudness.report(m, parsed.loudness_target, volume)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            parts = list(pool.map(one, jobs))
        levels = [p["lufs"] for p in parts if p["kind"] == "segment" and not p["silent"]]
        return {
            "target": parsed.loudness_target,
            "parts": parts,
            # The spread evening-out removes: how far apart the creators were.
            "spread": round(max(levels) - min(levels), 1) if len(levels) > 1 else 0.0,
        }

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

    @app.post("/compilations/{comp_id}/cancel")
    def cancel_render(comp_id: int):
        """Stop a render: a waiting one leaves the queue, a running one stops
        its FFmpeg pass at once. Either way the compilation goes back to how
        it was before Render, not to "failed"."""
        import json

        from core import cancel

        d = db()
        try:
            _get(d, comp_id)
            rows = d.conn.execute(
                "SELECT id, status, payload FROM jobs WHERE type = 'compile' "
                "AND status IN ('queued', 'running')"
            ).fetchall()
            job = next((r for r in rows
                        if json.loads(r["payload"] or "{}").get("compilation_id") == comp_id), None)
            if job is None:
                store.settle_cancelled(d, comp_id)
                state = "idle"
            elif job["status"] == "queued":
                d.conn.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))
                d.conn.commit()
                store.settle_cancelled(d, comp_id)
                state = "removed"
            else:
                cancel.request_cancel(store.cancel_key(comp_id))
                state = "cancelling"
        finally:
            d.close()
        broadcaster.publish({"type": "queue"})
        broadcaster.publish({"type": "compilation", "compilation_id": comp_id})
        return {"state": state}

    @app.get("/compilations/{comp_id}/media")
    def compilation_media(comp_id: int, canvas: str = ""):
        """The rendered file; `canvas` (e.g. 4x5 or 4:5) picks one format."""
        d = db()
        try:
            comp = _get(d, comp_id)
        finally:
            d.close()
        wanted = canvas.replace("x", ":")
        chosen = (comp.get("outputs") or {}).get(wanted) if wanted else comp["output_path"]
        if not chosen:
            raise HTTPException(404, "not rendered in that format yet")
        path = Path(chosen).resolve()
        if not path.exists() or Path(data_dir).resolve() not in path.parents:
            raise HTTPException(404, "rendered file missing")
        return video_response(path)

    # ---- versions: every render kept, with the recipe it was made from --------------

    def _serve(path_str: str | None) -> FileResponse:
        if not path_str:
            raise HTTPException(404, "not rendered in that format")
        path = Path(path_str).resolve()
        if not path.exists() or Path(data_dir).resolve() not in path.parents:
            raise HTTPException(404, "rendered file missing")
        return video_response(path)

    @app.get("/compilations/{comp_id}/renders")
    def list_compilation_renders(comp_id: int):
        d = db()
        try:
            _get(d, comp_id)
            return store.renders(d, comp_id)
        finally:
            d.close()

    @app.get("/compilations/{comp_id}/renders/{render_id}/media")
    def compilation_render_media(comp_id: int, render_id: int, canvas: str = ""):
        d = db()
        try:
            row = store.get_render(d, comp_id, render_id)
        finally:
            d.close()
        if row is None:
            raise HTTPException(404, "no such version")
        outputs = row["outputs"]
        wanted = canvas.replace("x", ":")
        return _serve(outputs.get(wanted) if wanted else next(iter(outputs.values()), None))

    @app.post("/compilations/{comp_id}/renders/{render_id}/restore")
    def restore_compilation_render(comp_id: int, render_id: int):
        """Put a version's settings and segments back as the current recipe."""
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "This compilation is rendering. Wait for it to finish, or cancel it.")
            row = store.get_render(d, comp_id, render_id)
            if row is None:
                raise HTTPException(404, "no such version")
            if row["recipe"] is None:
                raise HTTPException(400, "This version was rendered before settings were saved with each render.")
            store.update(d, comp_id, recipe=row["recipe"])
            return _annotate(d, store.get(d, comp_id))
        finally:
            d.close()

    @app.delete("/compilations/{comp_id}/renders/{render_id}")
    def delete_compilation_render(comp_id: int, render_id: int):
        d = db()
        try:
            comp = _get(d, comp_id)
            if comp["status"] in ("queued", "rendering"):
                raise HTTPException(409, "Wait for the render to finish before deleting versions.")
            if store.get_render(d, comp_id, render_id) is None:
                raise HTTPException(404, "no such version")
            gone = store.delete_render(d, comp_id, render_id)
            return {"deleted": gone, "compilation": _annotate(d, store.get(d, comp_id))}
        finally:
            d.close()

    @app.get("/compilation-settings")
    def get_compilation_settings():
        d = db()
        try:
            return store.load_settings(d)
        finally:
            d.close()

    @app.put("/compilation-settings")
    def put_compilation_settings(body: dict):
        """Saving a lower keep_versions prunes every compilation to it now,
        not only at its next render, so the space comes back when asked."""
        d = db()
        try:
            try:
                saved = store.save_settings(d, body)
            except (TypeError, ValueError):
                raise HTTPException(400, "keep_versions must be a whole number")
            removed = sum(
                store.prune(d, c["id"]) for c in store.list_all(d) if c["status"] not in ("queued", "rendering")
            )
            return {**saved, "removed": removed}
        finally:
            d.close()

    @app.get("/compilation-files/unused")
    def list_unused_compilation_files():
        d = db()
        try:
            files = store.unused_files(d, store.output_dir(config))
        finally:
            d.close()
        return [{"name": p.name, "bytes": p.stat().st_size} for p in files]

    @app.delete("/compilation-files/unused")
    def delete_unused_compilation_files():
        """Delete the videos no compilation points at. Listed again at the
        moment of deleting, so only files that are still unused go."""
        d = db()
        try:
            files = store.unused_files(d, store.output_dir(config))
        finally:
            d.close()
        deleted = [p.name for p in files if discard(p)]
        return {"deleted": deleted, "kept": [p.name for p in files if p.name not in deleted]}

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
            # Only the look is kept, so only the look is checked: a segment whose
            # video has since left the library must not block saving it.
            problem = _problem(d, store.recipe_mod.template_of(body.config), require_segments=False)
            if problem:
                raise HTTPException(400, problem)
            # Names are how templates are picked, so two of the same name
            # would be indistinguishable. Replacing one is a PUT to its id.
            if store.template_named(d, body.name) is not None:
                raise HTTPException(409, f"A template called {body.name.strip()!r} already exists")
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
            # Only the look is kept, so only the look is checked: a segment whose
            # video has since left the library must not block saving it.
            problem = _problem(d, store.recipe_mod.template_of(body.config), require_segments=False)
            if problem:
                raise HTTPException(400, problem)
            other = store.template_named(d, body.name)
            if other is not None and other != template_id:
                raise HTTPException(409, f"A template called {body.name.strip()!r} already exists")
            store.save_template(d, body.name, body.config, template_id)
            return store.get_template(d, template_id)
        finally:
            d.close()

    @app.patch("/compilation-templates/{template_id}")
    def rename_compilation_template(template_id: int, body: TemplateRename):
        """Rename only. The look is left exactly as stored and not re-checked,
        so a template pointing at something since removed (a branding profile,
        an intro file) can still be renamed."""
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "A template needs a name.")
        d = db()
        try:
            if store.get_template(d, template_id) is None:
                raise HTTPException(404, "no such template")
            other = store.template_named(d, name)
            if other is not None and other != template_id:
                raise HTTPException(409, f"A template called {name!r} already exists")
            d.conn.execute(
                "UPDATE compilation_templates SET name = ? WHERE id = ?", (name[:100], template_id)
            )
            d.conn.commit()
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
