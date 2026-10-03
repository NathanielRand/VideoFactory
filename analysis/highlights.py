"""LLM-based highlight detection with strict duplicate prevention.

Scoring asks the model for 0-100 viral potential against an explicit
framework (hooks, emotional peaks, opinion bombs, revelations, conflict,
quotable lines, payoff structure), with long videos chunked and overlapped
so a moment on a chunk boundary is never lost.

Duplicate prevention is three independent checks, applied highest-score-first:
  1. timestamp overlap   — reject if >40% of the shorter clip overlaps a kept clip
  2. transcript similarity — reject if the spoken text is >70% similar to a kept clip
  3. segment reuse       — reject if >40% of the candidate's transcript segments
                           are already claimed by kept clips
Every rejection carries a reason so the pipeline can log it for auditing.

All robustness (JSON parsing, timestamp validation, duration enforcement)
lives here in deterministic Python, NOT in the LLM backend — so swapping
models never changes this module.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from core import cancel, progress
from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend, generate_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_clips.txt"
WINDOWS_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_windows.txt"


def find_highlights(
    segments: list[Segment],
    llm: LLMBackend,
    *,
    min_score: int = 60,
    max_clips: int = 3,
    min_duration: float = 10.0,
    max_duration: float = 60.0,
    max_overlap: float = 0.4,
    max_text_similarity: float = 0.7,
    max_segment_reuse: float = 0.4,
    chunk_seconds: float = 1200.0,
    chunk_overlap_seconds: float = 60.0,
    long_video_threshold_seconds: float = 1800.0,
    events: list[tuple[float, str]] | None = None,
    guidance: str = "",
) -> tuple[list[ClipCandidate], list[Rejection]]:
    """Returns (selected clips, rejected candidates with reasons).
    `guidance` is standing advice from the person who runs this channel,
    shown to the model ahead of the task.
    `events` is an optional multimodal timeline [(second, description)] shown
    to the model alongside each chunk's transcript."""
    if not segments:
        return [], []

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    video_end = segments[-1].end

    chunks = _chunk_segments(segments, chunk_seconds, chunk_overlap_seconds, long_video_threshold_seconds)
    print(f"  Analyzing {len(chunks)} chunk(s) with {llm.name}...")

    candidates: list[ClipCandidate] = []
    for i, chunk in enumerate(chunks, 1):
        # One LLM call per chunk, and a 12b model on a long stream makes each
        # of those minutes long. Checking here bounds how long Cancel takes to
        # land at roughly one chunk instead of the entire analysis stage.
        cancel.check_active()
        progress.emit(stage="analyze", current=i, total=len(chunks))
        transcript_text = "\n".join(f"[{s.start:.1f} - {s.end:.1f}] {s.text}" for s in chunk)
        prompt = prompt_template.replace("{transcript}", transcript_text)
        prompt = prompt.replace("{events}", _events_block(events, chunk[0].start, chunk[-1].end))
        prompt = prompt.replace("{min_duration}", str(int(min_duration)))
        prompt = prompt.replace("{max_duration}", str(int(max_duration)))
        prompt = guidance_block(guidance) + prompt
        raw = _generate_with_retry(llm, prompt)
        parsed = _parse_clips_json(raw)
        if parsed is None:
            snippet = " ".join(raw.split())[:150]
            print(f"  Chunk {i}/{len(chunks)}: unparseable LLM output, skipping chunk (got: {snippet!r})")
            continue
        print(f"  Chunk {i}/{len(chunks)}: {len(parsed)} candidate(s)")
        candidates.extend(parsed)

    candidates = [c for c in candidates if _valid_range(c, video_end)]
    candidates = [_fit_to_segments(c, segments, min_duration, max_duration) for c in candidates]
    candidates = [c for c in candidates if c.duration >= min_duration - 1]

    return _select_unique(
        candidates,
        segments,
        min_score=min_score,
        max_clips=max_clips,
        max_overlap=max_overlap,
        max_text_similarity=max_text_similarity,
        max_segment_reuse=max_segment_reuse,
    )


def guidance_block(guidance: str) -> str:
    """Standing advice for the model, or nothing. It is the channel owner's own
    words, approved by them (creator/reviewer.py), so it is framed as theirs."""
    if not (guidance or "").strip():
        return ""
    return ("STANDING GUIDANCE from the person who runs this channel. Follow it when judging "
            "which moments are worth clipping:\n" + guidance.strip() + "\n\n")


# One call per batch, not one for every window. A 30-window prompt made a call
# of several minutes on a partly-CPU model, the bar sat at one value the whole
# time, and its ~30 JSON entries overran the output budget, so the reply was cut
# off mid-object and every window fell back to a neutral 50.
WINDOW_BATCH_SIZE = 6


def score_windows(
    segments: list[Segment],
    llm: LLMBackend,
    windows: list[tuple[float, float]],
    events: list[tuple[float, str]] | None = None,
    batch_size: int = WINDOW_BATCH_SIZE,
    guidance: str = "",
) -> list[ClipCandidate]:
    """Score specific time windows (signal peaks fusion found) in batched LLM
    calls, so signal candidates get real text/engagement scores and grounded
    hooks instead of placeholders."""
    if not windows:
        return []
    batch_size = max(1, batch_size)
    n_batches = (len(windows) + batch_size - 1) // batch_size
    print(f"  Scoring {len(windows)} signal-peak window(s) with the model "
          f"({n_batches} batch(es))...")
    template = WINDOWS_PROMPT_PATH.read_text(encoding="utf-8")

    results: list[ClipCandidate] = []
    for bi in range(n_batches):
        cancel.check_active()
        progress.emit(stage="analyze", current=bi + 1, total=n_batches)
        batch = windows[bi * batch_size : (bi + 1) * batch_size]

        blocks = []
        for i, (start, end) in enumerate(batch):
            text = " ".join(s.text for s in segments if s.end > start and s.start < end) or "(no speech)"
            ev = _events_block(events, start, end)
            blocks.append(f"WINDOW {i} [{start:.1f}s - {end:.1f}s]:\n{text}\n{ev}".strip())
        prompt = guidance_block(guidance) + template.replace("{windows}", "\n\n".join(blocks))

        raw = _generate_with_retry(llm, prompt)
        parsed = _parse_clips_json(raw)
        # A batch the model fails keeps its windows anyway with neutral text
        # scores; their audio/visual signals still let strong moments compete.
        by_index = dict(enumerate(parsed)) if parsed is not None else {}
        for i, (start, end) in enumerate(batch):
            c = by_index.get(i)
            if c is not None:
                # Trust the model's score/hook but keep OUR window timestamps —
                # these came from the signals, not from the model.
                results.append(ClipCandidate(start=start, end=end, score=c.score, hook=c.hook,
                                             reason=c.reason, source="signal", engagement=c.engagement))
            else:
                results.append(ClipCandidate(start=start, end=end, score=50,
                                             hook="High-energy moment", source="signal"))
    return results


REFINE_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "refine_bounds.txt"
REFINE_SCHEMA = {
    "type": "object",
    "properties": {"start_index": {"type": "integer"}, "end_index": {"type": "integer"}},
    "required": ["start_index", "end_index"],
    "additionalProperties": False,
}
# Context shown around a clip when the model picks its edges.
REFINE_BEFORE_SECONDS = 20.0
REFINE_AFTER_SECONDS = 15.0


def refine_boundaries(
    c: ClipCandidate,
    segments: list[Segment],
    llm: LLMBackend,
    min_duration: float,
    max_duration: float,
) -> bool:
    """Ask the model where this clip's setup begins and its payoff ends.

    The model picks segment INDEXES, never timestamps, so its answer always
    lands on a real transcript boundary and small local models can do it
    reliably. A reply that is unusable or breaks the duration range leaves the
    clip untouched. Returns True when the clip moved.
    """
    lo_all = [i for i, s in enumerate(segments) if s.end > c.start - REFINE_BEFORE_SECONDS]
    if not lo_all:
        return False
    first = lo_all[0]
    last = max((i for i, s in enumerate(segments) if s.start < c.end + REFINE_AFTER_SECONDS), default=first)
    inside = {i for i, s in enumerate(segments) if s.end > c.start and s.start < c.end}
    if not inside or last <= first:
        return False

    lines = []
    for i in range(first, last + 1):
        s = segments[i]
        mark = ">>" if i in inside else "  "
        lines.append(f"{mark} [{i}] ({s.start:.1f}-{s.end:.1f}) {s.text.strip()}")
    prompt = (
        REFINE_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{min_duration}", str(int(min_duration)))
        .replace("{max_duration}", str(int(max_duration)))
        .replace("{lines}", "\n".join(lines))
    )
    try:
        data = json.loads(_json_object(generate_json(llm, prompt, REFINE_SCHEMA)))
        a, b = int(data["start_index"]), int(data["end_index"])
    except Exception:
        return False
    if not (first <= a <= b <= last):
        return False
    start, end = segments[a].start, segments[b].end
    if not (min_duration - 1 <= end - start <= max_duration):
        return False
    # Ignore a wobble: only a real move is worth re-cutting the clip for.
    if abs(start - c.start) < 0.5 and abs(end - c.end) < 0.5:
        return False
    c.start, c.end = round(start, 2), round(end, 2)
    return True


def _json_object(raw: str) -> str:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    s, e = text.find("{"), text.rfind("}")
    return text[s : e + 1] if s != -1 and e > s else "{}"


def _events_block(events: list[tuple[float, str]] | None, start: float, end: float) -> str:
    if not events:
        return ""
    lines = [f"[{sec:.0f}s] {desc}" for sec, desc in events if start <= sec <= end]
    if not lines:
        return ""
    return "AUDIO/VISUAL EVENTS (from signal analysis):\n" + "\n".join(lines)


# ---- duplicate prevention ------------------------------------------------


def _select_unique(
    candidates: list[ClipCandidate],
    segments: list[Segment],
    *,
    min_score: int,
    max_clips: int,
    max_overlap: float,
    max_text_similarity: float,
    max_segment_reuse: float,
) -> tuple[list[ClipCandidate], list[Rejection]]:
    kept: list[ClipCandidate] = []
    rejections: list[Rejection] = []
    claimed_segments: set[int] = set()

    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        if c.score < min_score:
            rejections.append(Rejection(c, "below_min_score"))
            continue
        if len(kept) >= max_clips:
            rejections.append(Rejection(c, "over_limit"))
            continue

        rejection = _check_against_kept(
            c, kept, segments, claimed_segments,
            max_overlap, max_text_similarity, max_segment_reuse,
        )
        if rejection:
            rejections.append(rejection)
            continue

        kept.append(c)
        claimed_segments |= _covered_segments(c, segments)

    return kept, rejections


def _check_against_kept(
    c: ClipCandidate,
    kept: list[ClipCandidate],
    segments: list[Segment],
    claimed_segments: set[int],
    max_overlap: float,
    max_text_similarity: float,
    max_segment_reuse: float,
) -> Rejection | None:
    for k in kept:
        if c.overlap_ratio(k) > max_overlap:
            return Rejection(c, "timestamp_overlap", kept=k)
        if _text_similarity(c, k, segments) > max_text_similarity:
            return Rejection(c, "transcript_similarity", kept=k)

    covered = _covered_segments(c, segments)
    if covered:
        reuse = len(covered & claimed_segments) / len(covered)
        if reuse > max_segment_reuse:
            return Rejection(c, "segment_reuse")
    return None


def _covered_segments(c: ClipCandidate, segments: list[Segment]) -> set[int]:
    """Indices of transcript segments that fall inside the clip window."""
    return {
        i for i, s in enumerate(segments)
        if s.end > c.start and s.start < c.end
    }


def _clip_text(c: ClipCandidate, segments: list[Segment]) -> str:
    return " ".join(s.text for s in segments if s.end > c.start and s.start < c.end)


def _text_similarity(a: ClipCandidate, b: ClipCandidate, segments: list[Segment]) -> float:
    """Content-level duplicate check: catches the model re-proposing the same
    moment with shifted boundaries (low timestamp overlap, same words)."""
    text_a, text_b = _clip_text(a, segments), _clip_text(b, segments)
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


# ---- chunking --------------------------------------------------------------


def _chunk_segments(
    segments: list[Segment],
    chunk_seconds: float,
    overlap_seconds: float,
    long_threshold: float,
) -> list[list[Segment]]:
    total = segments[-1].end
    if total <= long_threshold:
        return [segments]

    chunks = []
    start = 0.0
    while start < total:
        end = start + chunk_seconds
        chunk = [s for s in segments if s.end > start and s.start < end]
        if chunk:
            chunks.append(chunk)
        start = end - overlap_seconds
    return chunks


# ---- LLM I/O robustness -----------------------------------------------------


# The shape score_clips.txt and score_windows.txt already ask for. A cloud
# model on the user's key is held to it; Ollama is sent exactly what it always
# was (see llm.base.generate_json). _parse_clips_json still checks every item.
_CLIP = {
    "type": "object",
    "properties": {
        "start": {"type": "number"}, "end": {"type": "number"},
        "score": {"type": "number"}, "engagement": {"type": "number"},
        "trending": {"type": "boolean"},
        "hook": {"type": "string"}, "reason": {"type": "string"},
    },
    "required": ["start", "end", "score", "engagement", "trending", "hook", "reason"],
    "additionalProperties": False,
}
CLIPS_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": _CLIP}},
    "required": ["clips"],
    "additionalProperties": False,
}


def _generate_with_retry(llm: LLMBackend, prompt: str) -> str:
    raw = generate_json(llm, prompt, CLIPS_SCHEMA)
    if _parse_clips_json(raw) is not None:
        return raw
    # One retry with an explicit reminder — local models sometimes wrap
    # JSON in prose or markdown fences on the first attempt.
    retry_prompt = prompt + "\n\nIMPORTANT: Respond with ONLY the JSON object. No markdown, no explanation."
    return generate_json(llm, retry_prompt, CLIPS_SCHEMA)


def _parse_clips_json(raw: str) -> list[ClipCandidate] | None:
    """Tolerant parse: strips code fences, finds the outermost JSON object.
    Returns None if nothing usable, [] if the model validly found no clips."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    clips_raw = data.get("clips")
    if not isinstance(clips_raw, list):
        return None

    clips = []
    for item in clips_raw:
        try:
            engagement = item.get("engagement")
            clips.append(
                ClipCandidate(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    score=max(0, min(100, int(item["score"]))),
                    hook=str(item.get("hook", "")),
                    reason=str(item.get("reason", "")),
                    engagement=max(0, min(100, int(engagement))) if engagement is not None else None,
                    trending=bool(item.get("trending", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # drop malformed entries, keep the rest
    return clips


# ---- candidate normalization ------------------------------------------------


def _valid_range(c: ClipCandidate, video_end: float) -> bool:
    return 0 <= c.start < c.end <= video_end + 5  # small slack for rounding


# A finished thought, as far as the transcript shows it. Closing quotes and
# brackets count — Whisper writes 〈he said "stop."〉 with the stop inside.
_SENTENCE_END = (".", "!", "?", "…")

# How far back a clip's start may reach to open on a sentence start.
START_REACH_SECONDS = 6.0


def _ends_sentence(seg) -> bool:
    text = (getattr(seg, "text", "") or "").strip().rstrip("\"')]»”’")
    return text.endswith(_SENTENCE_END)


def _fit_to_segments(
    c: ClipCandidate,
    segments: list[Segment],
    min_duration: float,
    max_duration: float,
    target_duration: float | None = None,
    lead_in: bool = False,
) -> ClipCandidate:
    """Fit a candidate to whole-sentence boundaries with a NATURAL length.

    Snaps outward to full sentences, then grows (preferring forward, to finish
    the thought) toward target_duration, capped at max_duration. The target
    defaults to the candidate's own length (so an LLM pick that was already a
    good 30s stays ~30s) but never less than a sensible clip length — this is
    what stops everything collapsing to the bare minimum. Landing on sentence
    edges gives varied natural lengths across the 10-60s range.

    lead_in: grow backwards as much as forwards. A signal peak marks the
    REACTION (the laugh, the shout); the thing reacted to came before it, so
    forward-first growth starts the clip after its own setup.
    """
    if not segments:
        return c
    if c.proposed is None:
        c.proposed = (c.start, c.end)
    idxs = [i for i, s in enumerate(segments) if s.end > c.start and s.start < c.end]
    if not idxs:  # candidate fell between segments — anchor to the nearest one
        idxs = [min(range(len(segments)), key=lambda i: abs(segments[i].start - c.start))]
    lo, hi = idxs[0], idxs[-1]

    def dur() -> float:
        return segments[hi].end - segments[lo].start

    # Aim for the candidate's own span, but at least a real clip length (18s),
    # so short LLM picks and tiny signal peaks grow into watchable clips.
    target = target_duration if target_duration is not None else max(c.end - c.start, 18.0)
    target = max(min_duration, min(target, max_duration))

    lo0, hi0 = lo, hi
    while dur() < target:
        can_fwd = hi + 1 < len(segments) and (segments[hi + 1].end - segments[lo].start) <= max_duration
        can_back = lo > 0 and (segments[hi].end - segments[lo - 1].start) <= max_duration
        # Back first while the lead-in is the shorter side, so the peak ends up
        # roughly 60/40 into the clip instead of at its very start.
        back_first = lead_in and (
            segments[lo0].start - segments[lo].start <= 1.5 * (segments[hi].end - segments[hi0].end)
            or not (hi + 1 < len(segments))
        )
        if can_back and (back_first or not can_fwd):
            lo -= 1
        elif can_fwd:
            hi += 1
        else:
            break
    # Trim whole trailing sentences if somehow over the cap.
    while dur() > max_duration and hi > lo:
        hi -= 1

    # Land the END on a finished thought. Whisper splits segments on PAUSES,
    # not grammar, so a segment boundary is often mid-sentence — that is how
    # clips ended on "...manufactured in a fighter jet hangar in" and simply
    # stopped. Reach forward to the next sentence end if the cap allows,
    # otherwise fall back to the last one that still leaves a usable clip.
    # Transcripts with no punctuation at all (fast casual speech) match
    # neither and are left exactly as they were.
    if not _ends_sentence(segments[hi]):
        j = hi
        while (
            j + 1 < len(segments)
            and not _ends_sentence(segments[j])
            and (segments[j + 1].end - segments[lo].start) <= max_duration
        ):
            j += 1
        if _ends_sentence(segments[j]):
            hi = j
        else:
            k = hi
            while k > lo and not _ends_sentence(segments[k]):
                k -= 1
            if k > lo and (segments[k].end - segments[lo].start) >= min_duration:
                hi = k

    # Start on a fresh thought too. Reach a few seconds OUTWARD first, because a
    # clip that opens mid-sentence has lost its setup; only when no sentence
    # start is that close move INWARD, since reaching further back would pull
    # in unrelated talk just to find a full stop.
    if lo > 0 and not _ends_sentence(segments[lo - 1]):
        j = lo
        while (
            j > 0
            and not _ends_sentence(segments[j - 1])
            and segments[lo].start - segments[j - 1].start <= START_REACH_SECONDS
            and (segments[hi].end - segments[j - 1].start) <= max_duration
        ):
            j -= 1
        if j == 0 or _ends_sentence(segments[j - 1]):
            lo = j
        else:
            k = lo
            while k < hi and not _ends_sentence(segments[k - 1]):
                k += 1
            if k < hi and (segments[hi].end - segments[k].start) >= min_duration:
                lo = k

    start = segments[lo].start
    end = segments[hi].end

    # Everything above moves in whole segments, which silently assumes the
    # transcript offers a boundary near where one is needed. Twice it does
    # not, and both failures shipped real clips from a gym stream:
    #
    #   TOO SHORT — "Yo." runs 9.35-9.87 and the next segment is at 74.26.
    #   Growing forward would make a 66s clip (over the cap) and there is
    #   nothing behind it, so the loop gives up holding 0.52 seconds.
    #
    #   TOO LONG — Whisper emits one 153s segment for a long unpunctuated
    #   stretch. With lo == hi the trailing-trim loop cannot run (it needs
    #   hi > lo), so the whole segment ships as one clip.
    #
    # Sentence boundaries are a preference. The duration range is a promise.
    # When they conflict, the clock wins: a clip landing mid-sentence is a
    # blemish, while half a second is not a clip and 153 seconds is not a
    # short. Both fall back to clock time, anchored on the candidate — it
    # marks where the actual moment was, which a 153s segment does not.
    if end - start > max_duration:
        focus = min(max(c.start, start), end)
        start = max(start, focus - max_duration * 0.2)  # a little lead-in
        end = min(end, start + max_duration)

    if end - start < min_duration:
        video_end = max(segments[-1].end, c.end)
        # Prefer growing forward — an action beat plays out after its peak.
        # Pull the start back only when there is not enough room ahead.
        end = min(video_end, start + min_duration)
        if end - start < min_duration:
            start = max(0.0, end - min_duration)

    c.start = round(start, 2)
    c.end = round(end, 2)
    return c
