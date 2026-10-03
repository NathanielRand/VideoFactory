"""Does a rater rank the clips people kept above the clips people did not?

Every claim that a new scale, prompt or model "picks better clips" is a guess
until it is held against something the user actually did. This module gathers
that something and scores a rater against it, offline: no rendering, no
publishing, and with the built-in raters no model call at all.

Labels, strongest last-resort first (each item records where its label came from):
  * views    a published clip whose platform numbers are in: above / below the
             median for what has been measured. The only label that is the
             audience's rather than the user's.
  * exported the user took the file out of the app.
  * flagged  the user ticked "Not a good moment".

The measure is AUC: the chance that a random kept clip outscores a random
rejected one. 0.5 is a coin, 1.0 is perfect. It is used rather than accuracy
because a rater's scale is arbitrary (a 0-100 score and a 0-10 rubric are
compared on order alone), and because it is stable when kept clips are rare.

With a handful of labels the number is noise, and `report` says so instead of
printing a confident decimal: `reliable` is False below MIN_EACH of either
class. Pure and stdlib-only, so it tests on a CI runner.
"""

from __future__ import annotations

import json
from typing import Callable

MIN_EACH = 8          # labelled clips of each class before the AUC means anything
CHANNELS = ("text", "audio", "visual", "reaction", "engagement")


def auc(pos: list[float], neg: list[float]) -> float | None:
    """P(a random positive outscores a random negative); ties count half.
    None when either side is empty."""
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            wins += 1.0 if p > n else 0.5 if p == n else 0.0
    return wins / (len(pos) * len(neg))


def precision_at(items: list[dict], scores: list[float], k: int) -> float | None:
    """Of the k highest-scored clips, the share that were kept."""
    if not items or k <= 0:
        return None
    order = sorted(range(len(items)), key=lambda i: scores[i], reverse=True)[:k]
    return sum(items[i]["label"] for i in order) / len(order)


def _row_scores(raw) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def labelled_clips(db, creator_id: int | None = None) -> list[dict]:
    """Clips with a label, each {id, video_id, start, end, score, scores,
    label (1 kept / 0 not), source, creator_id}. A clip with both kinds of
    evidence takes the flag: 'the user said it was not a moment' outweighs
    that they exported it while fixing it."""
    by_clip: dict[int, dict] = {}

    where, args = "", ()
    if creator_id is not None:
        where, args = " AND v.creator_id = ?", (creator_id,)

    # views: above the median of everything measured, per platform.
    rows = db.conn.execute(
        # Posts come from two places: the publishers, and the older direct
        # YouTube upload path, which keeps its own table.
        "SELECT p.clip_id, p.platform, s.views, c.video_id, c.start_s, c.end_s, c.score, c.scores,"
        " v.creator_id FROM ("
        "   SELECT clip_id, platform, post_id FROM clip_publishes"
        "   UNION SELECT clip_id, 'youtube', youtube_id FROM uploads WHERE youtube_id != ''"
        " ) p"
        " JOIN publish_stats s ON s.platform = p.platform AND s.post_id = p.post_id"
        " JOIN clips c ON c.id = p.clip_id LEFT JOIN videos v ON v.video_id = c.video_id"
        " WHERE s.views IS NOT NULL" + where, args,
    ).fetchall()
    by_platform: dict[str, list[int]] = {}
    for r in rows:
        by_platform.setdefault(r["platform"], []).append(int(r["views"]))
    medians = {p: sorted(v)[len(v) // 2] for p, v in by_platform.items()}
    for r in rows:
        # With one clip on a platform the median is itself: nothing to compare.
        if len(by_platform[r["platform"]]) < 2:
            continue
        by_clip[r["clip_id"]] = {
            "id": r["clip_id"], "video_id": r["video_id"], "start": r["start_s"], "end": r["end_s"],
            "score": r["score"], "scores": _row_scores(r["scores"]), "creator_id": r["creator_id"],
            "label": 1 if int(r["views"]) > medians[r["platform"]] else 0, "source": "views",
        }

    for r in db.conn.execute(
        "SELECT DISTINCT f.clip_id, c.video_id, c.start_s, c.end_s, c.score, c.scores, v.creator_id"
        " FROM clip_feedback f JOIN clips c ON c.id = f.clip_id"
        " LEFT JOIN videos v ON v.video_id = c.video_id WHERE f.action = 'exported'" + where, args,
    ).fetchall():
        by_clip.setdefault(r["clip_id"], {
            "id": r["clip_id"], "video_id": r["video_id"], "start": r["start_s"], "end": r["end_s"],
            "score": r["score"], "scores": _row_scores(r["scores"]), "creator_id": r["creator_id"],
            "label": 1, "source": "exported",
        })

    # Flags outlive the clip row, so they carry their own snapshot.
    for r in db.conn.execute(
        "SELECT f.id, f.clip_id, f.video_id, f.reasons, f.snapshot, v.creator_id FROM clip_flags f"
        " LEFT JOIN videos v ON v.video_id = f.video_id WHERE 1 = 1" + where, args,
    ).fetchall():
        try:
            reasons = set(json.loads(r["reasons"] or "[]"))
            snap = json.loads(r["snapshot"] or "{}")
        except (TypeError, ValueError):
            continue
        if "not_a_moment" not in reasons:
            continue
        clip = snap.get("clip") or {}
        key = r["clip_id"] if r["clip_id"] is not None else -int(r["id"])
        by_clip[key] = {
            "id": key, "video_id": r["video_id"], "start": clip.get("start"), "end": clip.get("end"),
            "score": clip.get("score"), "scores": snap.get("scoring") or {}, "creator_id": r["creator_id"],
            "label": 0, "source": "flagged",
        }
    return [v for v in by_clip.values() if v["start"] is not None and v["end"] is not None]


def stored_score(item: dict) -> float:
    return float(item["score"] or 0)


def channel_scorer(channel: str) -> Callable[[dict], float]:
    return lambda item: float((item["scores"] or {}).get(channel, 0) or 0)


def rubric_stored(item: dict) -> float:
    return float((item["scores"] or {}).get("rubric", 0) or 0)


def evaluate(items: list[dict], raters: dict[str, Callable[[dict], float] | list[float]]) -> dict:
    """Score each rater against the labels. A rater is a function of one item,
    or a list of scores in item order (for one that needs the whole batch, as
    a model call does)."""
    pos_n = sum(1 for i in items if i["label"])
    neg_n = len(items) - pos_n
    out: dict = {
        "clips": len(items), "kept": pos_n, "rejected": neg_n,
        "sources": {s: sum(1 for i in items if i["source"] == s) for s in {i["source"] for i in items}},
        "reliable": pos_n >= MIN_EACH and neg_n >= MIN_EACH,
        "raters": {},
    }
    for name, rater in raters.items():
        scores = list(rater) if isinstance(rater, list) else [rater(i) for i in items]
        out["raters"][name] = {
            "auc": auc([s for s, i in zip(scores, items) if i["label"]],
                       [s for s, i in zip(scores, items) if not i["label"]]),
            "precision_at_5": precision_at(items, scores, 5),
        }
    return out


def default_raters(items: list[dict]) -> dict:
    """What can be judged with no model: the stored score, each signal on its
    own, and the rubric where an earlier run stored one."""
    raters: dict = {"stored score": stored_score}
    for ch in CHANNELS:
        if any((i["scores"] or {}).get(ch) is not None for i in items):
            raters[f"{ch} channel"] = channel_scorer(ch)
    if any((i["scores"] or {}).get("rubric") for i in items):
        raters["rubric (stored)"] = rubric_stored
    return raters


def report(result: dict) -> str:
    """The result as text a person can read, with the honesty caveat first."""
    lines = [f"{result['clips']} labelled clips: {result['kept']} kept, {result['rejected']} not "
             f"({', '.join(f'{n} {s}' for s, n in sorted(result['sources'].items())) or 'none'})"]
    if not result["reliable"]:
        lines.append(f"  Too few to trust: need at least {MIN_EACH} of each. Treat the numbers below as noise.")
    for name, r in result["raters"].items():
        a = "n/a" if r["auc"] is None else f"{r['auc']:.2f}"
        p = "n/a" if r["precision_at_5"] is None else f"{r['precision_at_5']:.0%}"
        lines.append(f"  {name:<22} AUC {a}   top-5 kept {p}")
    return "\n".join(lines)


def _clip_text(data_dir, item: dict) -> str:
    from pathlib import Path

    path = Path(data_dir) / "transcripts" / f"{item['video_id']}.json"
    try:
        segments = json.loads(path.read_text(encoding="utf-8")).get("segments") or []
    except (OSError, ValueError):
        return ""
    start, end = float(item["start"]), float(item["end"])
    return " ".join(str(s.get("text", "")).strip() for s in segments
                    if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end)


def run(config: dict, db, creator_id: int | None = None, with_rubric: bool = False) -> dict:
    """Evaluate the raters that need no model, and with `with_rubric` the
    rubric run live over the same clips (one model call per 6 of them)."""
    items = labelled_clips(db, creator_id)
    raters = default_raters(items)
    if with_rubric and items:
        from analysis import rubric
        from llm.registry import create_backend
        from llm.stages import StageModels

        llm = create_backend(config["llm"])
        model = StageModels(config["llm"], llm).for_stage("rubric")
        texts = [(f"{i['start']:.0f}s-{i['end']:.0f}s", _clip_text(config["paths"]["data_dir"], i)) for i in items]
        graded = rubric.rate(texts, model)
        # A clip with no transcript or a failed batch has no grade: the mean of
        # the rest, which neither helps nor hurts it.
        known = [g["score"] for g in graded if g]
        mean = sum(known) / len(known) if known else 0.0
        raters["rubric (live)"] = [g["score"] if g else mean for g in graded]
    return evaluate(items, raters)
