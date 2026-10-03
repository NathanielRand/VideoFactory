"""Process-wide progress event hook.

The pipeline emits structured events at stage boundaries; by default nothing
listens (CLI runs just print as before). The API server installs a handler
that broadcasts events to UI clients over WebSocket.

Deliberately tiny: no queues, no threads — just a settable callback that can
never break the pipeline.
"""

import threading
from collections.abc import Callable

_handler: Callable[[dict], None] | None = None
_local = threading.local()


def set_handler(handler: Callable[[dict], None] | None) -> None:
    global _handler
    _handler = handler


def set_thread_tags(**tags) -> None:
    """Override fields on every event emitted from THIS thread. The download
    prefetcher uses this to restamp its events (stage='prefetch') so the UI
    never attributes them to the job that is currently running."""
    _local.tags = tags or None


def emit(**event) -> None:
    if _handler is None:
        return
    tags = getattr(_local, "tags", None)
    if tags:
        event = {**event, **tags}
    try:
        _handler(event)
    except Exception:
        pass  # a broken UI listener must never kill a render


# ---- steps inside one render ---------------------------------------------------
# A re-render is one job with no natural events: it used to report nothing, so
# the bar sat at 0% and jumped to 100%. The render code now names the step it is
# on and the share of the job that step is worth (`step`), and the long loops
# inside a step report how far through it they are (`sub`). Both do nothing
# unless the caller opted in with `begin_steps`, so a batch run — which already
# reports per clip — is unchanged.

_STEP_GAP = 0.25  # seconds between sub-step events: a live bar, not a flood


def begin_steps(**extra) -> None:
    """`extra` (video_id, title) rides on every event, so the bar can name what
    it is working on and offer Cancel."""
    _local.steps = {"label": "", "lo": 0.0, "hi": 0.0, "at": 0.0, "extra": extra}


def end_steps() -> None:
    _local.steps = None


def step(label: str, lo: float, hi: float) -> None:
    """Now on `label`, which takes the job from `lo` to `hi` (0..1). `hi` is
    also sent as `ceil`, so a client can ease toward it between events."""
    st = getattr(_local, "steps", None)
    if st is None:
        return
    st.update(label=label, lo=lo, hi=hi, at=0.0)
    emit(stage="rerender", message=label, fraction=lo, ceil=hi, **st["extra"])


def sub(within: float) -> None:
    """How far through the current step (0..1). Throttled."""
    import time

    st = getattr(_local, "steps", None)
    if st is None:
        return
    now = time.monotonic()
    if now - st["at"] < _STEP_GAP:
        return
    st["at"] = now
    frac = st["lo"] + (st["hi"] - st["lo"]) * max(0.0, min(1.0, within))
    emit(stage="rerender", message=st["label"], fraction=round(frac, 4), ceil=st["hi"], **st["extra"])
