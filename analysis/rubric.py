"""Grade a clip against a rubric instead of asking for one number.

The model is asked "0-100, how viral?" once per clip, and answers cluster
(most land in the 60s and 70s) because one number hides what the model is
actually weighing. A rubric splits the judgement into things it can answer
concretely, each on a small scale with anchors: does it hook, does it land,
does it stand alone, are its edges right, does it sag. The parts are combined
here, in code, with weights that can be tuned without touching a prompt.

Where the rubric is used is a setting (`scoring.rubric`):
  off     not run.
  shadow  run and STORED on each clip (subscores["rubric"]), but nothing about
          which clips are kept or how they rank changes. This is what lets
          `main.py eval` compare it with the score in use, on your own kept and
          flagged clips, before it is allowed to decide anything.
  on      the rubric is blended into the score and sets the rank order.

Pure parsing and arithmetic here; the model call is the one function that takes
a backend.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from llm.base import LLMBackend, generate_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "rubric.txt"

DIMENSIONS = ("hook", "payoff", "standalone", "clean_edges", "energy")
# What makes a clip land, in order of how much: opening and ending carry it.
WEIGHTS = {"hook": 0.30, "payoff": 0.30, "standalone": 0.15, "clean_edges": 0.15, "energy": 0.10}
BATCH = 6            # clips per call: enough to grade against each other, few enough to stay accurate
BLEND = 0.35         # share of the score the rubric takes when it is switched on

RUBRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, **{d: {"type": "integer"} for d in DIMENSIONS}},
                "required": ["index", *DIMENSIONS],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ratings"],
    "additionalProperties": False,
}


def rubric_score(ratings: dict) -> int:
    """The weighted rubric as 0-100."""
    return round(10 * sum(WEIGHTS[d] * ratings[d] for d in DIMENSIONS))


def parse_ratings(raw: str, n: int) -> dict[int, dict]:
    """{index: {dimension: 0-10, ..., 'score': 0-100}} for the entries that are
    complete. An entry missing a dimension, or naming a clip that was not in
    the batch, is dropped rather than guessed at."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        entries = json.loads(text[start: end + 1])["ratings"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    out: dict[int, dict] = {}
    for e in entries if isinstance(entries, list) else []:
        try:
            i = int(e["index"])
            vals = {d: max(0, min(10, int(round(float(e[d]))))) for d in DIMENSIONS}
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < n and i not in out:
            out[i] = {**vals, "score": rubric_score(vals)}
    return out


def _guidance_block(guidance: str) -> str:
    if not guidance.strip():
        return ""
    return ("CREATOR-SPECIFIC GUIDANCE (from the person who runs this channel; weigh it when grading):\n"
            f"{guidance.strip()}\n\n")


def rate(items: list[tuple[str, str]], llm: LLMBackend, guidance: str = "",
         batch: int = BATCH) -> list[dict | None]:
    """Grade each (label, text). One call per `batch` clips; None for a clip
    whose batch the model failed, so a bad reply costs the rubric on those
    clips and nothing else."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    out: list[dict | None] = [None] * len(items)
    for lo in range(0, len(items), batch):
        chunk = items[lo: lo + batch]
        lines = []
        for i, (label, text) in enumerate(chunk):
            text = " ".join(text.split())
            if len(text) > 600:
                text = text[:300] + " ... " + text[-300:]
            lines.append(f'{i}: [{label}] "{text}"')
        prompt = (template.replace("{count}", str(len(chunk)))
                  .replace("{guidance}", _guidance_block(guidance))
                  .replace("{clips}", "\n".join(lines)))
        try:
            got = parse_ratings(generate_json(llm, prompt, RUBRIC_SCHEMA), len(chunk))
        except Exception:
            got = {}
        for i, r in got.items():
            out[lo + i] = r
    return out


def blend(score: int, rubric: int, share: float = BLEND) -> int:
    """The score with the rubric mixed in."""
    return max(0, min(100, round((1 - share) * score + share * rubric)))
