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
from compilation.render import SourceInfo, render_all
from core.paths import cached_source, discard, resolve_data_dir
from formats.profiles import length_warnings, tag

STATUSES = ("draft", "queued", "rendering", "done", "failed")
SETTINGS_KEY = "compilation_settings"
DEFAULT_SETTINGS = {"keep_versions": 5}  # 0 keeps every version


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for key in ("recipe", "outputs"):
        try:
            d[key] = json.loads(d.get(key) or "{}")
        except ValueError:
            d[key] = {}
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


def set_status(
    db, comp_id: int, status: str, *, output_path: str | None = None,
    outputs: dict[str, str] | None = None, error: str = "",
) -> None:
    assert status in STATUSES, status
    if outputs is not None:
        db.conn.execute("UPDATE compilations SET outputs = ? WHERE id = ?", (json.dumps(outputs), comp_id))
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
    # Its versions are forgotten with it; their files stay, and show up in
    # unused_files() for the user to clear when they choose.
    db.conn.execute("DELETE FROM compilation_renders WHERE compilation_id = ?", (comp_id,))
    db.conn.commit()
    return cur.rowcount > 0


# ---- settings -------------------------------------------------------------------------


def load_settings(db) -> dict:
    try:
        saved = json.loads(db.get_flag(SETTINGS_KEY) or "{}")
    except ValueError:
        saved = {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_settings(db, patch: dict) -> dict:
    merged = load_settings(db)
    if "keep_versions" in patch:
        merged["keep_versions"] = max(0, min(100, int(patch["keep_versions"])))
    db.set_flag(SETTINGS_KEY, json.dumps(merged))
    return merged


# ---- render versions ------------------------------------------------------------------
#
# Every render is kept as a numbered version, "<title> [<id>] v<n>.mp4", with
# the recipe it was made from, until pruning (keep_versions) removes it. The
# compilation's own outputs/output_path always point at the newest version.


def _render_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["outputs"] = json.loads(d.get("outputs") or "{}")
    d["recipe"] = json.loads(d["recipe"]) if d.get("recipe") else None
    return d


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def renders(db, comp_id: int) -> list[dict]:
    """Every version still recorded, newest first, with its size on disk."""
    adopt_legacy(db, comp_id)
    rows = db.conn.execute(
        "SELECT * FROM compilation_renders WHERE compilation_id = ? ORDER BY version DESC", (comp_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = _render_row(r)
        d["bytes"] = sum(_file_size(p) for p in d["outputs"].values())
        d["missing"] = [c for c, p in d["outputs"].items() if not Path(p).is_file()]
        out.append(d)
    return out


def get_render(db, comp_id: int, render_id: int) -> dict | None:
    r = db.conn.execute(
        "SELECT * FROM compilation_renders WHERE id = ? AND compilation_id = ?", (render_id, comp_id)
    ).fetchone()
    return _render_row(r) if r else None


def adopt_legacy(db, comp_id: int) -> None:
    """A compilation rendered before versions were kept has files but no
    versions: record them as version 1 (recipe unknown) so they show in the
    history and are pruned and renamed like any other."""
    if db.conn.execute("SELECT 1 FROM compilation_renders WHERE compilation_id = ? LIMIT 1", (comp_id,)).fetchone():
        return
    comp = get(db, comp_id)
    if comp is None:
        return
    outputs = dict(comp.get("outputs") or {})
    if not outputs and comp.get("output_path"):
        outputs = {(comp["recipe"] or {}).get("canvas", "16:9"): comp["output_path"]}
    outputs = {c: p for c, p in outputs.items() if p and Path(p).is_file()}
    if not outputs:
        return
    db.conn.execute(
        "INSERT INTO compilation_renders (compilation_id, version, recipe, outputs, created_at) "
        "VALUES (?, 1, NULL, ?, ?)",
        (comp_id, json.dumps(outputs), comp.get("updated_at") or _now()),
    )
    db.conn.commit()


def next_version(db, comp_id: int) -> int:
    adopt_legacy(db, comp_id)
    r = db.conn.execute(
        "SELECT MAX(version) AS v FROM compilation_renders WHERE compilation_id = ?", (comp_id,)
    ).fetchone()
    return int(r["v"] or 0) + 1


def record_render(db, comp_id: int, version: int, recipe: dict, outputs: dict[str, str]) -> int:
    cur = db.conn.execute(
        "INSERT INTO compilation_renders (compilation_id, version, recipe, outputs, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (comp_id, version, json.dumps(recipe), json.dumps(outputs), _now()),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def _delete_render_files(db, row: dict) -> bool:
    """Delete one version's files, then its row. A file that cannot be
    deleted (open in a player) keeps the row, so it is still tracked and the
    next prune tries again, rather than becoming an untracked leftover."""
    left = {c: p for c, p in row["outputs"].items() if not discard(Path(p))}
    if left:
        db.conn.execute("UPDATE compilation_renders SET outputs = ? WHERE id = ?", (json.dumps(left), row["id"]))
    else:
        db.conn.execute("DELETE FROM compilation_renders WHERE id = ?", (row["id"],))
    db.conn.commit()
    return not left


def _point_at_newest(db, comp_id: int) -> None:
    """Make the compilation's outputs the newest remaining version's."""
    r = db.conn.execute(
        "SELECT * FROM compilation_renders WHERE compilation_id = ? ORDER BY version DESC LIMIT 1", (comp_id,)
    ).fetchone()
    outputs = _render_row(r)["outputs"] if r else {}
    comp = get(db, comp_id)
    primary = ""
    if outputs:
        canvas = ((comp or {}).get("recipe") or {}).get("canvas")
        primary = outputs.get(canvas) or next(iter(outputs.values()))
    db.conn.execute(
        "UPDATE compilations SET outputs = ?, output_path = ?, "
        "status = CASE WHEN ? = '' AND status = 'done' THEN 'draft' ELSE status END WHERE id = ?",
        (json.dumps(outputs), primary, primary, comp_id),
    )
    db.conn.commit()


def delete_render(db, comp_id: int, render_id: int) -> bool:
    """Delete one version and its files. Deleting the newest is allowed: the
    compilation then shows the version before it."""
    row = get_render(db, comp_id, render_id)
    if row is None:
        return False
    gone = _delete_render_files(db, row)
    _point_at_newest(db, comp_id)
    return gone


def prune(db, comp_id: int, keep: int | None = None) -> int:
    """Delete versions beyond the newest `keep` (0 = keep all). The newest is
    never pruned. Returns how many were removed."""
    keep = load_settings(db)["keep_versions"] if keep is None else keep
    if keep <= 0:
        return 0
    rows = db.conn.execute(
        "SELECT * FROM compilation_renders WHERE compilation_id = ? ORDER BY version DESC", (comp_id,)
    ).fetchall()
    return sum(_delete_render_files(db, _render_row(r)) for r in rows[max(1, keep):])


def rename_files(db, comp_id: int, title: str) -> None:
    """After a rename, move every version's files to the new title's name so
    the folder matches the app. Best effort: a file open elsewhere keeps its
    old name and stays tracked under it."""
    moved: dict[str, str] = {}
    rows = db.conn.execute("SELECT * FROM compilation_renders WHERE compilation_id = ?", (comp_id,)).fetchall()
    for r in rows:
        row = _render_row(r)
        new_outputs = {}
        for canvas, old in row["outputs"].items():
            old_path = Path(old)
            new_path = old_path.with_name(_version_file(title, comp_id, row["version"], canvas, len(row["outputs"])))
            if new_path != old_path and old_path.is_file() and not new_path.exists():
                try:
                    old_path.rename(new_path)
                    moved[old] = str(new_path)
                except OSError:
                    new_path = old_path
            else:
                new_path = old_path
            new_outputs[canvas] = str(new_path)
        db.conn.execute("UPDATE compilation_renders SET outputs = ? WHERE id = ?", (json.dumps(new_outputs), row["id"]))
    if moved:
        comp = get(db, comp_id)
        outputs = {c: moved.get(p, p) for c, p in (comp.get("outputs") or {}).items()}
        db.conn.execute(
            "UPDATE compilations SET outputs = ?, output_path = ? WHERE id = ?",
            (json.dumps(outputs), moved.get(comp["output_path"], comp["output_path"]), comp_id),
        )
    db.conn.commit()


def _tracked_paths(db) -> set[Path]:
    paths: set[str] = set()
    for r in db.conn.execute("SELECT output_path, outputs FROM compilations").fetchall():
        paths.add(r["output_path"] or "")
        paths.update(json.loads(r["outputs"] or "{}").values())
    for r in db.conn.execute("SELECT outputs FROM compilation_renders").fetchall():
        paths.update(json.loads(r["outputs"] or "{}").values())
    return {Path(p).resolve() for p in paths if p}


def unused_files(db, out_dir: Path) -> list[Path]:
    """Videos in the compilations folder that no compilation or version
    points at: renders from before a rename, of a deleted compilation, and
    so on. A render in progress writes into a scratch folder, not a file
    here, so it is never listed."""
    if not out_dir.is_dir():
        return []
    for comp in list_all(db):  # older renders become versions, not leftovers
        adopt_legacy(db, comp["id"])
    tracked = _tracked_paths(db)
    return sorted(
        p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4" and p.resolve() not in tracked
    )


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


def template_named(db, name: str) -> int | None:
    """The id of the template called `name` (ignoring case and edge spaces)."""
    r = db.conn.execute(
        "SELECT id FROM compilation_templates WHERE lower(trim(name)) = lower(trim(?)) ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    return int(r["id"]) if r else None


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


def _base_name(title: str, comp_id: int) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(". ")[:60].strip()
    return f"{cleaned or 'Compilation'} [{comp_id}]"


def _version_file(title: str, comp_id: int, version: int, canvas: str, formats: int) -> str:
    """The file name render_all gives one format of one version."""
    base = f"{_base_name(title, comp_id)} v{version}"
    return f"{base}.mp4" if formats == 1 else f"{base} {tag(canvas)}.mp4"


def warnings(parsed: recipe_mod.Recipe) -> dict[str, list[str]]:
    """Per format, the platforms this compilation is too long for."""
    segs = parsed.segments
    total = sum(s.duration for s in segs)
    if parsed.transition != "none" and len(segs) > 1:
        total -= (len(segs) - 1) * parsed.transition_duration
    return {c: w for c in (parsed.outputs or [parsed.canvas]) if (w := length_warnings(c, total))}


def output_dir(config: dict) -> Path:
    return resolve_data_dir(config) / "compilations"


def run(db, comp_id: int, config: dict, on_progress=None) -> Path:
    """The `compile` job: validate, render, record the result."""
    comp = get(db, comp_id)
    if comp is None:
        raise ValueError(f"compilation {comp_id} no longer exists")
    data_dir = resolve_data_dir(config)
    set_status(db, comp_id, "rendering")
    version = next_version(db, comp_id)
    try:
        parsed = validate(db, comp["recipe"], config)
        sources = sources_for(db, [s.video_id for s in parsed.segments], data_dir / "downloads")
        outs = render_all(
            parsed,
            sources,
            output_dir(config),
            f"{_base_name(comp['title'], comp_id)} v{version}",
            banner=resolve_banner(db, parsed.banner),
            banner_assets=data_dir / "branding" / "assets",
            cancel_key=cancel_key(comp_id),
            on_progress=on_progress,
            parallel=int((config.get("video") or {}).get("parallel_renders", 2)),
        )
    except Exception as e:
        set_status(db, comp_id, "failed", error=str(e)[:2000])
        raise
    primary = outs[parsed.outputs[0]]
    outputs = {c: str(p) for c, p in outs.items()}
    record_render(db, comp_id, version, comp["recipe"], outputs)
    set_status(db, comp_id, "done", output_path=str(primary), outputs=outputs)
    prune(db, comp_id)
    return primary


def cancel_key(comp_id: int) -> str:
    return f"compilation_{comp_id}"
