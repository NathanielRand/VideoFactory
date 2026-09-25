"""Compilations: one video assembled from segments of many sources.

    recipe.py   the JSON schema, validated into dataclasses
    credits.py  per-segment credit lower-thirds (ASS)
    render.py   segment render -> join (concat or transitions) -> banner
    store.py    SQLite CRUD for compilations and templates

Same rules as the rest of the engine: stages talk through dataclasses and
files, and a malformed recipe is refused before any FFmpeg runs, never halfway
through a render.
"""
