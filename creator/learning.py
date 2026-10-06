"""Preference learning: what does this creator's user actually keep?

Positive signals from clip_feedback (exports are the strongest "this one was
worth posting"; caption/timestamp edits mean the user invested in the clip).
We compare the subscore profile of those clips against the creator's library
average: channels that run consistently hotter in kept clips get a bounded
up-weight in fusion.

Guardrails (this must never destabilize scoring):
  * inert below MIN_SIGNALS positive events — no data, no opinion;
  * each weight can shift at most MAX_SHIFT (20%) from its configured value;
  * weights are renormalized, so the total influence budget never changes;
  * derived from the user's OWN actions only — never from the LLM.

Flags (server/flags_api.py) are the negative half. They only ever nudge, and
only past a minimum count, for the same reason:
  * `not_a_moment` flags shave weight (at most MAX_PENALTY) off channels that
    scored those clips higher than the library average;
  * `starts_late` / `ends_early` (net of `runs_long`) widen this creator's clip
    edges by PAD_STEP per flag past MIN_FLAGS, capped at MAX_PAD;
  * `needs_wide` / `crop_jumps`, on clips rendered with the default crop, change
    the default crop for this creator's next clips.
Only the newest MAX_FLAGS flags count, so a fixed problem stops pulling.
"""

import json

from core.state import StateDB

MIN_SIGNALS = 8      # positive feedback events required before any bias
MAX_SHIFT = 0.20     # a weight may move at most this fraction of itself
_ACTION_WEIGHT = {"exported": 2.0, "captions_edited": 1.0, "timestamps_adjusted": 1.0}
_CHANNELS = ("text", "audio", "visual", "reaction", "engagement")

MAX_FLAGS = 40       # only the newest flags for a creator are read
MIN_FLAGS = 3        # of one kind, before it changes anything
MIN_MOMENT_FLAGS = 5  # `not_a_moment` flags before the weights move
MAX_PENALTY = 0.10   # flags alone can shave at most this fraction off a weight
PAD_STEP = 0.5       # seconds added per flag past MIN_FLAGS - 1
MAX_PAD = 3.0        # seconds of extra lead-in / tail, per side
_FLAG_CROP = {"needs_wide": "letterbox", "crop_jumps": "center"}


def _baseline(db: StateDB, creator_id: int) -> dict[str, float]:
    """Mean subscore per channel over ALL of this creator's clips."""
    lib = db.conn.execute(
        "SELECT cl.scores FROM clips cl JOIN videos v ON v.video_id = cl.video_id"
        " WHERE v.creator_id = ? AND cl.scores != ''",
        (creator_id,),
    ).fetchall()
    baseline: dict[str, list[float]] = {ch: [] for ch in _CHANNELS}
    for r in lib:
        try:
            scores = json.loads(r["scores"])
        except json.JSONDecodeError:
            continue
        for ch in _CHANNELS:
            if isinstance(scores.get(ch), (int, float)):
                baseline[ch].append(float(scores[ch]))
    return {ch: (sum(v) / len(v)) for ch, v in baseline.items() if v}


def _recent_flags(db: StateDB, creator_id: int) -> list[tuple[set, dict]]:
    """(reasons, snapshot) for this creator's newest flags. Flags are keyed by
    video, not by creator, and outlive the clip they were raised on."""
    rows = db.conn.execute(
        "SELECT f.reasons, f.snapshot FROM clip_flags f"
        " JOIN videos v ON v.video_id = f.video_id"
        " WHERE v.creator_id = ? ORDER BY f.id DESC LIMIT ?",
        (creator_id, MAX_FLAGS),
    ).fetchall()
    out = []
    for r in rows:
        try:
            out.append((set(json.loads(r["reasons"] or "[]")), json.loads(r["snapshot"] or "{}")))
        except json.JSONDecodeError:
            continue
    return out


def _flag_penalty(flags: list[tuple[set, dict]], base_mean: dict[str, float]) -> dict[str, float]:
    """Per-channel multiplier <= 1 for channels that rated `not_a_moment` clips
    above the library average. Never above 1: a flag says what to avoid, not
    what to chase."""
    profiles = [
        {ch: snap["scoring"][ch] for ch in _CHANNELS
         if isinstance((snap.get("scoring") or {}).get(ch), (int, float))}
        for reasons, snap in flags if "not_a_moment" in reasons
    ]
    profiles = [p for p in profiles if p]
    if len(profiles) < MIN_MOMENT_FLAGS:
        return {}
    out = {}
    for ch in _CHANNELS:
        vals = [p[ch] for p in profiles if ch in p]
        if not vals or base_mean.get(ch, 0) <= 0:
            continue
        ratio = (sum(vals) / len(vals)) / base_mean[ch]
        if ratio > 1:
            out[ch] = max(1 - MAX_PENALTY, 1 / ratio)
    return out


def _pad(n: int) -> float:
    return min(MAX_PAD, PAD_STEP * (n - MIN_FLAGS + 1)) if n >= MIN_FLAGS else 0.0


def _flag_adjustments(flags: list[tuple[set, dict]]) -> dict:
    """{'boundary': {'lead': s, 'tail': s}, 'crop': mode|None} from reasons."""

    def count(reason: str) -> int:
        return sum(1 for r, _ in flags if reason in r)

    # `runs_long` argues the other way from `ends_early`; only the surplus counts.
    tail = _pad(count("ends_early") - count("runs_long"))
    lead = _pad(count("starts_late"))

    # Only clips that used the default crop say anything about it: a flag on a
    # clip the user had already set to letterbox is not evidence for letterbox.
    tally: dict[str, int] = {}
    for reasons, snap in flags:
        if snap.get("crop", "track") != "track":
            continue
        for reason, mode in _FLAG_CROP.items():
            if reason in reasons:
                tally[mode] = tally.get(mode, 0) + 1
    crop = None
    ranked = sorted(tally.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][1] >= MIN_FLAGS and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        crop = ranked[0][0]
    return {"boundary": {"lead": lead, "tail": tail}, "crop": crop}


def preferences(db: StateDB, creator_id: int, use_flags: bool = True) -> dict | None:
    """{'weight_bias': {channel: multiplier}, 'preferred_duration': float|None,
    'signals': n, 'flags': n, 'boundary': {'lead': s, 'tail': s},
    'crop': mode|None} or None when there isn't enough data to say anything.

    None as well when the creator has switched learning off."""
    enabled = db.conn.execute(
        "SELECT learning_enabled FROM creators WHERE creator_id = ?", (creator_id,)
    ).fetchone()
    if enabled is not None and not enabled["learning_enabled"]:
        return None

    rows = db.conn.execute(
        "SELECT action, clip_meta FROM clip_feedback WHERE creator_id = ?", (creator_id,)
    ).fetchall()

    kept_profiles: list[tuple[float, dict]] = []
    durations: list[float] = []
    for r in rows:
        w = _ACTION_WEIGHT.get(r["action"])
        if w is None:
            continue
        try:
            meta = json.loads(r["clip_meta"] or "{}")
        except json.JSONDecodeError:
            continue
        scores = meta.get("scores") or {}
        profile = {ch: scores[ch] for ch in _CHANNELS if isinstance(scores.get(ch), (int, float))}
        if profile:
            kept_profiles.append((w, profile))
        if isinstance(meta.get("duration"), (int, float)) and r["action"] == "exported":
            durations.append(float(meta["duration"]))

    n_signals = sum(w for w, _ in kept_profiles)
    flags = _recent_flags(db, creator_id) if use_flags else []
    adj = _flag_adjustments(flags)

    # What the user has APPROVED from a flag review (creator/reviewer.py) is
    # their own decision, so it replaces the counted value for that setting.
    from creator import reviewer

    ov = reviewer.approved_overrides(db, creator_id)
    for key, side in (("pad_lead", "lead"), ("pad_tail", "tail")):
        if key in ov:
            adj["boundary"][side] = ov[key]
    if "crop" in ov:
        adj["crop"] = ov["crop"]
    approved = bool(ov["guidance"] or ov.get("min_score_delta") or any(
        k in ov for k in ("pad_lead", "pad_tail", "crop")))
    base_mean = _baseline(db, creator_id) if (n_signals >= MIN_SIGNALS or flags) else {}

    # Weighted mean subscores of KEPT clips vs the baseline -> multiplier per
    # channel. Flag penalties multiply in, and the clamp to
    # [1 - MAX_SHIFT, 1 + MAX_SHIFT] applies to the product.
    penalty = _flag_penalty(flags, base_mean)
    bias: dict[str, float] = {}
    for ch in _CHANNELS:
        b = 1.0
        pairs = [(w, p[ch]) for w, p in kept_profiles if ch in p]
        if n_signals >= MIN_SIGNALS and pairs and base_mean.get(ch, 0) > 0:
            kept_mean = sum(w * v for w, v in pairs) / sum(w for w, _ in pairs)
            b = kept_mean / base_mean[ch]
        bias[ch] = max(1 - MAX_SHIFT, min(1 + MAX_SHIFT, b * penalty.get(ch, 1.0)))

    learned_flags = bool(penalty or adj["crop"] or any(adj["boundary"].values()) or approved)
    if n_signals < MIN_SIGNALS and not learned_flags:
        return None
    preferred = (sorted(durations)[len(durations) // 2]
                 if n_signals >= MIN_SIGNALS and len(durations) >= 5 else None)
    return {"weight_bias": bias, "preferred_duration": preferred, "signals": int(n_signals),
            "flags": len(flags), **adj,
            "overrides": {"min_score_delta": ov.get("min_score_delta", 0), "guidance": ov["guidance"]}}


def adjust_boundaries(candidates: list, boundary: dict | None, segments: list,
                      video_duration: float | None, max_duration: float) -> None:
    """Widen each candidate by the learned lead-in/tail, in place.

    Edges move outward to the nearest segment boundary so a word is not cut,
    never into a neighbouring clip, never past the video, and never past
    max_duration (the tail gives way first). Each one records `learned_pad`
    in its subscores so a later flag shows what was applied."""
    lead, tail = (boundary or {}).get("lead", 0.0), (boundary or {}).get("tail", 0.0)
    if not candidates or (lead <= 0 and tail <= 0):
        return
    ordered = sorted(candidates, key=lambda c: c.start)
    for i, c in enumerate(ordered):
        floor = ordered[i - 1].end if i else 0.0
        ceil = ordered[i + 1].start if i + 1 < len(ordered) else float("inf")
        if video_duration:
            ceil = min(ceil, float(video_duration))

        start = max(floor, c.start - lead)
        end = min(ceil, c.end + tail)
        # Snap outward onto whole segments, unless that leaves the window.
        for s in segments:
            if s.start < start < s.end and s.start >= floor:
                start = s.start
            if s.start < end < s.end and s.end <= ceil:
                end = s.end
        over = (end - start) - max_duration
        if over > 0:
            end -= min(over, end - c.end)
            over = (end - start) - max_duration
            if over > 0:
                start += min(over, c.start - start)
        if start >= c.start and end <= c.end:
            continue
        if c.proposed is None:
            c.proposed = (c.start, c.end)
        c.subscores = {**(c.subscores or {}),
                       "learned_pad": [round(c.start - start, 2), round(end - c.end, 2)]}
        c.start, c.end = start, end


def apply_bias(weights: dict, bias: dict | None) -> dict:
    """Fusion weights nudged toward what this creator's user keeps, then
    renormalized so they still sum to the same total."""
    if not bias or all(abs(m - 1.0) < 1e-9 for m in bias.values()):
        return weights
    shifted = {ch: w * bias.get(ch, 1.0) for ch, w in weights.items()}
    total_before = sum(weights.values())
    total_after = sum(shifted.values())
    if total_after <= 0:
        return weights
    return {ch: w * total_before / total_after for ch, w in shifted.items()}


# ---- telling the user what a flag will do -----------------------------------

_LABEL = {
    "starts_late": "Starts too late",
    "ends_early": "Ends too early",
    "runs_long": "Runs on too long",
    "needs_wide": "Needed the whole frame",
    "crop_jumps": "Crop jumps",
    "not_a_moment": "Not a good moment",
}


def flag_feedback(db: StateDB, creator_id: int | None, reasons) -> dict:
    """What flagging with these reasons has done so far, in plain sentences.

    {'applied': [...], 'pending': [...], 'recorded': [...]}: what already
    changes this creator's next clips, what would after a few more flags, and
    what is only stored. Read from the same counts preferences() uses, so it
    cannot promise something the learning would not do."""
    reasons = set(reasons)
    out: dict = {"applied": [], "pending": [], "recorded": []}
    if creator_id is None:
        out["recorded"].append("Saved. This video has no creator profile yet, so nothing carries over to future clips.")
        return out
    flags = _recent_flags(db, creator_id)
    count = lambda reason: sum(1 for r, _ in flags if reason in r)  # noqa: E731
    adj = _flag_adjustments(flags)

    def need(n: int, floor: int) -> str:
        left = max(0, floor - n)
        return f"{left} more" if left else "no more"

    def edge(reason: str, net: int, amount: float, word: str) -> None:
        if amount > 0:
            out["applied"].append(f"Future clips from this creator now {word} ({amount:g}s), from {net} flags.")
        else:
            out["pending"].append(f"{need(net, MIN_FLAGS)} '{_LABEL[reason]}' flag(s) and future clips will {word}.")

    if "starts_late" in reasons:
        edge("starts_late", count("starts_late"), adj["boundary"]["lead"], "start earlier")
    if reasons & {"ends_early", "runs_long"}:
        net = count("ends_early") - count("runs_long")
        edge("ends_early", max(net, 0), adj["boundary"]["tail"], "end later")
    for reason in reasons & set(_FLAG_CROP):
        mode = _FLAG_CROP[reason]
        if adj["crop"] == mode:
            out["applied"].append(f"Future clips from this creator now default to '{mode}' framing.")
        else:
            out["pending"].append(f"{need(count(reason), MIN_FLAGS)} '{_LABEL[reason]}' flag(s) on default-framed clips and the framing default will change.")
    if "not_a_moment" in reasons:
        n = count("not_a_moment")
        if n >= MIN_MOMENT_FLAGS:
            out["applied"].append("Scoring for this creator is now nudged away from what these clips had in common.")
        else:
            out["pending"].append(f"{need(n, MIN_MOMENT_FLAGS)} 'Not a good moment' flag(s) and scoring will start adjusting.")
    other = reasons - {"starts_late", "ends_early", "runs_long", "not_a_moment"} - set(_FLAG_CROP)
    if other:
        out["recorded"].append("Also saved for review; those reasons do not change anything automatically.")
    return out


# ---- fixing the flagged clip itself -----------------------------------------

RECUT_STEP = 1.5   # seconds a single flag moves an edge, when no size was given
# What the answer to "how much?" moves an edge by: past the middle of each band,
# so the fix lands beyond the problem instead of just short of it.
DETAIL_SECONDS = {"0-3s": 2.5, "3-9s": 6.5, "9s+": 11.0}
# Where a "who / which side" answer points the crop, as framing names the
# renderer understands.
DETAIL_FRAMING = {"left": "bias_left", "right": "bias_right", "center": "center"}
# Complaints about who or what is in the picture, none of which names a fix.
FRAMING_FAULTS = {"subject_cut_off", "wrong_person", "wrong_angle"}


def _profile(opts: dict | None):
    from genres import profiles

    game = (opts or {}).get("game")
    return profiles.get(game) if game and game != "none" else None


def framing_ladder(opts: dict | None) -> list[str]:
    """The framings worth trying for this clip, best guess first. Each one is
    a different picture, or it does not belong here.

    Footage with a game profile (genres/profiles.py) starts with the profile's
    own answer, where the player aims. Then the plain subject tracker, which
    follows whoever is on screen. A fixed centre only if the profile does not
    already aim at the middle (it would be the same picture as the lock), and
    a nudge to either side of the crosshair. Letterbox is deliberately absent:
    see recut_plan.
    """
    profile = _profile(opts)
    if profile is None:
        return ["track", "center", "bias_left", "bias_right"]
    centre_is_lock = abs(profile.focus_x - 0.5) < 0.01
    return ["lock", "track", *([] if centre_is_lock else ["center"]), "bias_left", "bias_right"]


def current_framing(opts: dict | None) -> str:
    """What a clip is framed with right now. A game clip with no framing chosen
    is on the profile's lock, not on "track": that is what the renderer does."""
    chosen = (opts or {}).get("crop")
    if chosen:
        return chosen
    return "lock" if _profile(opts) is not None else "track"


def recut_plan(reasons, start: float, end: float, opts: dict, segments: list,
               video_duration: float | None, max_duration: float, details: dict | None = None) -> dict:
    """The corrections one clip's own flags ask for, applied to that clip.

    Unlike preferences(), this needs no minimum count: the user pointed at THIS
    clip and said what was wrong with it. Edges move by RECUT_STEP onto whole
    segments; the crop follows needs_wide / needs_tight / crop_jumps.
    {'start', 'end', 'render_opts', 'changes'}; changes is empty when nothing
    the flags said can be acted on."""
    reasons = set(reasons)
    details = details or {}
    new_start, new_end, changes = start, end, []

    def step(reason: str) -> float:
        return DETAIL_SECONDS.get(details.get(reason), RECUT_STEP)

    def seg_start(t: float) -> float:
        for s in segments:
            if s.start <= t < s.end:
                return s.start
        return t

    def seg_end(t: float) -> float:
        for s in segments:
            if s.start < t <= s.end:
                return s.end
        return t

    def sentence_end_before(t: float) -> float | None:
        ends = [s.end for s in segments
                if s.end <= t and s.end > start + 3 and (s.text or "").strip().endswith((".", "!", "?", "…"))]
        return max(ends) if ends else None

    if "starts_late" in reasons:
        new_start = seg_start(max(0.0, new_start - step("starts_late")))
        changes.append(f"starts {start - new_start:.1f}s earlier")
    if "ends_early" in reasons:
        new_end = seg_end(new_end + step("ends_early"))
        if video_duration:
            new_end = min(new_end, float(video_duration))
        changes.append(f"ends {new_end - end:.1f}s later")
    elif "runs_long" in reasons:
        cut = sentence_end_before(new_end - step("runs_long"))
        if cut is not None:
            new_end = cut
            changes.append(f"ends {end - new_end:.1f}s sooner")
    if "mid_sentence" in reasons:
        # "Where?" says which edge; unanswered, both are fitted as before.
        where = details.get("mid_sentence", "both")
        s2 = seg_start(new_start) if where in ("start", "both") else new_start
        e2 = seg_end(new_end) if where in ("end", "both") else new_end
        if (s2, e2) != (new_start, new_end):
            new_start, new_end = s2, e2
            changes.append("edges moved to whole sentences")
    if new_end - new_start > max_duration:
        new_end = new_start + max_duration
    if abs(new_start - start) < 0.05 and abs(new_end - end) < 0.05:
        new_start, new_end = start, end
        changes = [c for c in changes if not c.startswith(("starts", "ends", "edges"))]

    render_opts: dict = {}
    note = ""
    current = current_framing(opts)
    target = None
    for reason, mode in (("needs_wide", "letterbox"), ("crop_jumps", "center"), ("needs_tight", "track")):
        if reason in reasons and current != mode:
            target = mode
            break
    if target is None:
        # An answer to "who / which side" is a direction, so use it before guessing.
        for reason in ("wrong_person", "subject_cut_off"):
            mode = DETAIL_FRAMING.get(details.get(reason, ""))
            if reason in reasons and mode and current != mode:
                target = mode
                break
    if target is None and reasons & FRAMING_FAULTS:
        # "The subject is cut off / the wrong person / the wrong angle" does not
        # say which framing is right, only that this one is not. So try a
        # different real framing, one per press of Re-cut, and remember which
        # have been tried so a press never lands on one that was already wrong.
        #
        # Letterbox is NOT a step on this ladder. It shows the whole frame
        # because nothing was found, which is the opposite of an answer, and it
        # used to be the first thing a flag produced. It is the fallback once
        # every real framing has been tried, and otherwise only when the user
        # asks for it (needs_wide).
        tried = list(dict.fromkeys([*(opts or {}).get("framing_tried", []), current]))
        target = next((m for m in framing_ladder(opts) if m not in tried), None)
        if target is None and "letterbox" not in tried:
            target = "letterbox"
            changes.append("every other framing has been tried, so this is the whole frame")
        elif target is None:
            note = "Every framing has been tried. Adjust the layout by hand in the editor."
        if target:
            render_opts["framing_tried"] = tried
    if target:
        render_opts["crop"] = target
        changes.append(f"framing set to {target}")
    return {"start": new_start, "end": new_end, "render_opts": render_opts, "changes": changes, "note": note}
