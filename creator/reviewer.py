"""Turn a creator's flags, and what people wrote on them, into changes they can approve.

learning.py acts on flags by counting: three "starts too late" and the clips
start earlier. That is safe and it is blunt. It cannot read the note somebody
wrote ("it always misses the question that starts the story"), it cannot see
that the same channel keeps getting flagged for one thing, and it never
proposes anything it was not built with a rule for.

A review is one model call over the creator's recent flags: reasons, notes, the
numbers the pipeline decided, and a line of what was said. It answers with a
short list of proposed changes, each from a FIXED menu:

  pad_lead          seconds added before every clip (0-3)
  pad_tail          seconds added after every clip (0-3)
  crop              the default framing: letterbox, center or track
  min_score_delta   move this creator's quality bar by -10..+10
  guidance          one sentence of standing advice shown to the model when it
                    picks moments for this creator

Nothing is applied by the review itself. Each proposal waits, with its reason,
until the person approves it; approving is what changes the next run, and
rejecting an approved one takes it back. The menu is closed on purpose: the
model can be wrong about anything it says, and none of these can do harm beyond
"clips came out a bit differently".

The pure parts (validation, evidence, merging approvals) run without a model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.state import StateDB, _now

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "review_flags.txt"

MIN_FLAGS = 2          # a review of one flag is an opinion of one flag
MAX_FLAGS = 12         # shown to the model, newest first
MAX_PROPOSALS = 5
CROPS = ("letterbox", "center", "track")
GUIDANCE_CHARS = 200
# Guidance goes into the moment-picking prompt, where a sentence about framing
# does nothing. The prompt says so; this is for a model that ignores it.
_FRAMING = re.compile(r"\b(crop|cropp\w*|frame|framing|zoom\w*|camera|cursor|crosshair|center\w*|centre\w*|"
                      r"pixelat\w*|letterbox\w*|track\w*|angle)\b", re.IGNORECASE)

SCHEMA = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"type": "string"}, "value": {"type": "string"},
                               "rationale": {"type": "string"}},
                "required": ["kind", "value", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


def _number(text, lo: float, hi: float) -> float | None:
    try:
        v = float(str(text).strip().rstrip("s"))
    except ValueError:
        return None
    return round(max(lo, min(hi, v)), 1)


def clean_proposals(raw) -> list[dict]:
    """The proposals that are on the menu, with values clamped into range.
    {kind, value (a string, as stored), rationale}."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        value = str(entry.get("value") if entry.get("value") is not None else "").strip()
        why = re.sub(r"\s+", " ", str(entry.get("rationale") or "")).strip()[:240]
        if kind in ("pad_lead", "pad_tail"):
            n = _number(value, 0.0, 3.0)
            value = "" if n is None or n <= 0 else f"{n:g}"
        elif kind == "min_score_delta":
            n = _number(value, -10, 10)
            value = "" if n is None or int(n) == 0 else str(int(n))
        elif kind == "crop":
            value = value.lower()
            value = value if value in CROPS else ""
        elif kind == "guidance":
            value = re.sub(r"\s+", " ", value)[:GUIDANCE_CHARS].strip()
            value = value if len(value) >= 10 and not _FRAMING.search(value) else ""
        else:
            continue
        if not value or (kind, value) in seen:
            continue
        seen.add((kind, value))
        out.append({"kind": kind, "value": value, "rationale": why})
    return out[:MAX_PROPOSALS]


def evidence(db: StateDB, creator_id: int, data_dir: Path | None = None) -> str | None:
    """What the model is shown, or None when there are too few flags to review."""
    from creator.learning import _recent_flags

    rows = db.conn.execute(
        "SELECT f.reasons, f.note, f.snapshot FROM clip_flags f"
        " JOIN videos v ON v.video_id = f.video_id WHERE v.creator_id = ?"
        " ORDER BY f.id DESC LIMIT ?", (creator_id, MAX_FLAGS),
    ).fetchall()
    if len(rows) < MIN_FLAGS:
        return None
    name = db.conn.execute("SELECT display_name FROM creators WHERE creator_id = ?", (creator_id,)).fetchone()
    tally: dict[str, int] = {}
    for reasons, _ in _recent_flags(db, creator_id):
        for r in reasons:
            tally[r] = tally.get(r, 0) + 1
    lines = [f"CREATOR: {name['display_name'] if name else creator_id}",
             "FLAG COUNTS: " + ", ".join(f"{k} x{n}" for k, n in sorted(tally.items(), key=lambda kv: -kv[1]))]
    kept = db.conn.execute(
        "SELECT COUNT(*) FROM clip_feedback WHERE creator_id = ? AND action = 'exported'", (creator_id,)
    ).fetchone()[0]
    lines.append(f"CLIPS THE USER EXPORTED (kept): {kept}")
    lines.append("\nFLAGGED CLIPS, newest first:")
    for i, r in enumerate(rows, 1):
        try:
            reasons = json.loads(r["reasons"] or "[]")
            snap = json.loads(r["snapshot"] or "{}")
        except ValueError:
            continue
        clip = snap.get("clip") or {}
        spoken = " ".join((snap.get("transcript") or "").split())
        # The clip's own lines are marked ">>" in the snapshot; show those.
        inside = " ".join(p.split("] ", 1)[-1] for p in (snap.get("transcript") or "").splitlines()
                          if p.startswith(">>"))[:300] or spoken[:300]
        note = " ".join((r["note"] or "").split())[:300]
        lines.append(
            f"{i}. reasons: {', '.join(reasons)} | {clip.get('duration', '?')}s, score {clip.get('score', '?')}, "
            f"framing {snap.get('crop', 'track')}" + (f" | note: {note}" if note else "") +
            (f" | said: \"{inside}\"" if inside else ""))
    return "\n".join(lines)


def review(db: StateDB, creator_id: int, llm) -> dict:
    """Ask the model for proposals and store the new ones as pending.
    {'created': [...], 'skipped': n, 'reason': str} — `reason` explains an
    empty result."""
    from llm.base import generate_json

    text = evidence(db, creator_id)
    if text is None:
        return {"created": [], "skipped": 0,
                "reason": f"Not enough flags to review yet: flag at least {MIN_FLAGS} clips from this creator."}
    prompt = PROMPT_PATH.read_text(encoding="utf-8").replace("{evidence}", text)
    raw = generate_json(llm, prompt, SCHEMA)
    lo, hi = raw.find("{"), raw.rfind("}")
    try:
        found = clean_proposals(json.loads(raw[lo: hi + 1]).get("proposals"))
    except (ValueError, AttributeError):
        found = []
    if not found:
        return {"created": [], "skipped": 0,
                "reason": "The model did not suggest any change it was sure about."}
    have = {(p["kind"], p["value"]) for p in list_proposals(db, creator_id) if p["status"] != "rejected"}
    created = []
    for p in found:
        if (p["kind"], p["value"]) in have:
            continue
        created.append(add_proposal(db, creator_id, p["kind"], p["value"], p["rationale"]))
    return {"created": created, "skipped": len(found) - len(created), "reason": ""}


# ---- storage ---------------------------------------------------------------------


def add_proposal(db: StateDB, creator_id: int, kind: str, value: str, rationale: str) -> dict:
    cur = db.conn.execute(
        "INSERT INTO learning_proposals (creator_id, kind, value, rationale, status, created_at)"
        " VALUES (?, ?, ?, ?, 'pending', ?)", (creator_id, kind, value, rationale, _now()))
    db.conn.commit()
    return get_proposal(db, int(cur.lastrowid))


def get_proposal(db: StateDB, proposal_id: int) -> dict | None:
    row = db.conn.execute("SELECT * FROM learning_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return dict(row) if row else None


def list_proposals(db: StateDB, creator_id: int, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM learning_proposals WHERE creator_id = ?", [creator_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    return [dict(r) for r in db.conn.execute(sql + " ORDER BY id DESC", args).fetchall()]


def decide(db: StateDB, proposal_id: int, status: str) -> dict | None:
    """approved | rejected. Rejecting an approved proposal takes it back."""
    if status not in ("approved", "rejected"):
        raise ValueError(status)
    db.conn.execute("UPDATE learning_proposals SET status = ?, decided_at = ? WHERE id = ?",
                    (status, _now(), proposal_id))
    db.conn.commit()
    return get_proposal(db, proposal_id)


def approved_overrides(db: StateDB, creator_id: int) -> dict:
    """What this creator's approved proposals change, newest approval winning
    where two disagree: {pad_lead, pad_tail, crop, min_score_delta, guidance}."""
    out: dict = {"guidance": []}
    rows = db.conn.execute(
        "SELECT kind, value FROM learning_proposals WHERE creator_id = ? AND status = 'approved'"
        " ORDER BY decided_at, id", (creator_id,)).fetchall()
    for r in rows:
        kind, value = r["kind"], r["value"]
        if kind in ("pad_lead", "pad_tail"):
            out[kind] = float(value)
        elif kind == "min_score_delta":
            out[kind] = int(float(value))
        elif kind == "crop" and value in CROPS:
            out[kind] = value
        elif kind == "guidance" and value not in out["guidance"]:
            out["guidance"].append(value)
    return out
