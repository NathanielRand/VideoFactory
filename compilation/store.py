"""Compilations and templates in SQLite, and the job that renders one.

The tables live in core/state.py's SCHEMA; everything that reads or writes
them lives here, so the rest of the engine only ever calls these functions.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from compilation import recipe as recipe_mod
from compilation.render import SourceInfo, render
from core.paths import cached_source, resolve_data_dir

STATUSES = ("draft", "queued", "rendering", "done", "failed")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    try:
        d["recipe"] = json.loads(d.get("recipe") or "{}")
    except ValueError:
        d["recipe"] = {}
    return d


# ---- compilations ---------------------------------------------------------------


def create(db, title: str, recipe: dict | None = None) -> int:
    cur = db.conn.execute(
        "INSERT INTO compilations (title, recipe, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (title.strip() or "Untitled compilation", json.dumps(recipe or {}), _now(), _now()),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def get(db, comp_id: int) -> dict | None:
    return _row(db.conn.execute("SELECT * FROM compilations WHERE id = ?", (comp_id,)).fetchone())


def list_all(db) -> list[dict]:
    rows = db.conn.execute("SELECT * FROM compilations ORDER BY updated_at DESC, id DESC").fetchall()
    return [_row(r) for r in rows]


def update(db, comp_id: int, *, title: str | None = None, recipe: dict | None = None) -> bool:
    sets, args = [], []
    if title is not None:
        sets.append("title = ?")
        args.append(title.strip() or "Untitled compilation")
    if recipe is not None:
        sets.append("recipe = ?")
        args.append(json.dumps(recipe))
        # An edited recipe no longer matches the rendered file.
        sets.append("status = CASE WHEN status IN ('done', 'failed') THEN 'draft' ELSE status END")
    if not sets:
        return get(db, comp_id) is not None
    cur = db.conn.execute(
        f"UPDATE compilations SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, _now(), comp_id)
    )
    db.conn.commit()
    return cur.rowcount > 0


def set_status(db, comp_id: int, status: str, *, output_path: str | None = None, error: str = "") -> None:
    assert status in STATUSES, status
    if output_path is None:
        db.conn.execute(
            "UPDATE compilations SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, _now(), comp_id),
        )
    else:
        db.conn.execute(
            "UPDATE compilations SET status = ?, error = ?, output_path = ?, updated_at = ? WHERE id = ?",
            (status, error, output_path, _now(), comp_id),
        )
    db.conn.commit()


def delete(db, comp_id: int) -> bool:
    """Removes the compilation. The rendered file is left on disk: it may
    already be posted or copied somewhere, and deleting a record should never
    silently delete someone's video."""
    cur = db.conn.execute("DELETE FROM compilations WHERE id = ?", (comp_id,))
    db.conn.commit()
    return cur.rowcount > 0


# ---- templates ---------------------------------------------------------------------


def list_templates(db) -> list[dict]:
    rows = db.conn.execute("SELECT * FROM compilation_templates ORDER BY name, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["config"] = json.loads(d.get("config") or "{}")
        except ValueError:
            d["config"] = {}
        out.append(d)
    return out


def get_template(db, template_id: int) -> dict | None:
    r = db.conn.execute("SELECT * FROM compilation_templates WHERE id = ?", (template_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["config"] = json.loads(d.get("config") or "{}")
    return d


def save_template(db, name: str, config: dict, template_id: int | None = None) -> int:
    cfg = json.dumps(recipe_mod.template_of(config))
    if template_id is None:
        cur = db.conn.execute(
            "INSERT INTO compilation_templates (name, config, created_at) VALUES (?, ?, ?)",
            (name.strip() or "Template", cfg, _now()),
        )
        db.conn.commit()
        return int(cur.lastrowid)
    db.conn.execute(
        "UPDATE compilation_templates SET name = ?, config = ? WHERE id = ?",
        (name.strip() or "Template", cfg, template_id),
    )
    db.conn.commit()
    return template_id


def delete_template(db, template_id: int) -> bool:
    cur = db.conn.execute("DELETE FROM compilation_templates WHERE id = ?", (template_id,))
    db.conn.commit()
    return cur.rowcount > 0


# ---- library lookups -------------------------------------------------------------------------


def library_durations(db, video_ids) -> dict[str, float]:
    ids = list(dict.fromkeys(video_ids))
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = db.conn.execute(
        f"SELECT video_id, duration FROM videos WHERE video_id IN ({marks})", ids
    ).fetchall()
    return {r["video_id"]: float(r["duration"] or 0) for r in rows}


def sources_for(db, video_ids, downloads: Path) -> dict[str, SourceInfo]:
    """Every source file a recipe needs, with its credit details. Raises with
    the video named when a file is missing, before any rendering starts."""
    ids = list(dict.fromkeys(video_ids))
    out: dict[str, SourceInfo] = {}
    for vid in ids:
        row = db.conn.execute(
            "SELECT title, channel_name, channel_url, source_url FROM videos WHERE video_id = ?", (vid,)
        ).fetchone()
        path = cached_source(downloads, vid)
        if path is None:
            name = (row["title"] if row else "") or vid
            raise recipe_mod.RecipeError(
                f"The source file for {name!r} is no longer on disk. Re-import it to use it here."
            )
        out[vid] = SourceInfo(
            path=path,
            channel=(row["channel_name"] if row else "") or "",
            title=(row["title"] if row else "") or "",
            url=((row["channel_url"] or row["source_url"]) if row else "") or "",
        )
    return out


def validate(db, data: dict, config: dict, *, require_segments: bool = True) -> recipe_mod.Recipe:
    parsed = recipe_mod.parse(
        data,
        durations=library_durations(db, [s.get("video_id") for s in data.get("segments") or [] if isinstance(s, dict)]),
        require_segments=require_segments,
    )
    return parsed


def resolve_banner(db, banner: dict | None) -> dict | None:
    """A recipe's banner may name a saved branding profile instead of
    carrying the config inline."""
    if not banner:
        return None
    if "profile_id" in banner:
        row = db.get_branding(int(banner["profile_id"]))
        if row is None:
            raise recipe_mod.RecipeError("The banner's branding profile no longer exists.")
        return json.loads(row["config"] or "{}")
    return banner


def _file_name(title: str, comp_id: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(". ")[:60].strip()
    return f"{cleaned or 'Compilation'} [{comp_id}].mp4"


def output_dir(config: dict) -> Path:
    return resolve_data_dir(config) / "compilations"


def run(db, comp_id: int, config: dict, on_progress=None) -> Path:
    """The `compile` job: validate, render, record the result."""
    comp = get(db, comp_id)
    if comp is None:
        raise ValueError(f"compilation {comp_id} no longer exists")
    data_dir = resolve_data_dir(config)
    set_status(db, comp_id, "rendering")
    try:
        parsed = validate(db, comp["recipe"], config)
        sources = sources_for(db, [s.video_id for s in parsed.segments], data_dir / "downloads")
        out = render(
            parsed,
            sources,
            output_dir(config) / _file_name(comp["title"], comp_id),
            banner=resolve_banner(db, parsed.banner),
            banner_assets=data_dir / "branding" / "assets",
            cancel_key=cancel_key(comp_id),
            on_progress=on_progress,
        )
    except Exception as e:
        set_status(db, comp_id, "failed", error=str(e)[:2000])
        raise
    set_status(db, comp_id, "done", output_path=str(out))
    return out


def cancel_key(comp_id: int) -> str:
    return f"compilation_{comp_id}"
