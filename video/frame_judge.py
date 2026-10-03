"""Let a vision model pick the thumbnail among the frames the arithmetic liked.

video/thumbnail.py ranks frames on things it can measure: a face, sharpness,
open eyes, no blocking. It cannot tell a great moment from a merely clean one,
and on footage with no faces (gameplay, scenery) it has nothing to go on but
sharpness, which favours foliage. A model that can see can answer the question
a person would: which of these is the frame you would click.

It is a second opinion on a short list, never the first filter: a handful of
frames, one call, and the arithmetic order stands whenever the model is not
configured, cannot see, or answers with something unusable.

    llm:
      vision_model: ollama/qwen2.5vl:7b     # any Ollama model that reads images

Local Ollama vision models only for now. A cloud provider is refused by name
rather than silently sent frames of somebody's video.
"""

from __future__ import annotations

import io
import json
import re

from llm.base import LLMBackend

MAX_SIDE = 768       # frames are shown small: a ranking, not an inspection
MAX_FRAMES = 4

PROMPT = (
    "You are choosing the thumbnail for a video. You are shown {n} frames from it, numbered 0 to "
    "{last} in the order they are attached.\n"
    "Order them from BEST thumbnail to WORST. Prefer a frame that is sharp and in focus (never "
    "motion-blurred), where the main subject is large and clear, at a peak moment: a strong "
    "expression, real action, a reaction. Avoid blurry transitions, black or washed-out frames, "
    "a face caught mid-blink, and frames where nothing is happening.\n"
    'Respond with ONLY valid JSON listing every index once: {{"order": [2, 0, 1]}}'
)


def shrink(jpeg: bytes, side: int = MAX_SIDE) -> bytes:
    """The frame no larger than `side` on its long edge, as JPEG."""
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img.thumbnail((side, side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return out.getvalue()


def parse_order(raw: str, n: int) -> list[int] | None:
    """A full ordering of range(n), or None. A partial answer is completed with
    the missing indexes in their original order, so a model that names only its
    favourite still moves it to the front."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    lo, hi = text.find("{"), text.rfind("}")
    if lo == -1 or hi <= lo:
        return None
    try:
        got = [int(i) for i in json.loads(text[lo: hi + 1])["order"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    seen: list[int] = []
    for i in got:
        if 0 <= i < n and i not in seen:
            seen.append(i)
    if not seen:
        return None
    return seen + [i for i in range(n) if i not in seen]


def vision_backend(config: dict) -> LLMBackend | None:
    """The configured vision model, or None (unset, cloud, or cannot be built)."""
    spec = str((config.get("llm") or {}).get("vision_model") or "").strip()
    if not spec:
        return None
    from llm.spec import is_local

    if not is_local(spec):
        print(f"      (llm.vision_model '{spec}' is a cloud model; frames are only sent to local models)")
        return None
    try:
        from llm.registry import create_backend

        return create_backend({**config["llm"], "backend": spec if "/" in spec else f"ollama/{spec}"})
    except Exception as e:
        print(f"      (could not set up the vision model: {e})")
        return None


def judge(llm: LLMBackend, jpegs: list[bytes]) -> list[int] | None:
    """The frames' indexes best first, or None when the model cannot say."""
    if len(jpegs) < 2 or not getattr(llm, "can_see", lambda: False)():
        return None
    frames = jpegs[:MAX_FRAMES]
    try:
        raw = llm.generate(
            PROMPT.format(n=len(frames), last=len(frames) - 1),
            json_mode=True,
            images=[shrink(j) for j in frames],
        )
    except Exception:
        return None
    return parse_order(raw, len(frames))
