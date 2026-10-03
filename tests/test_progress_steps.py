"""A re-render names its steps, so the bar can move while it works."""

from core import progress


def _capture():
    seen = []
    progress.set_handler(seen.append)
    return seen


def test_steps_are_silent_unless_a_caller_opted_in():
    seen = _capture()
    try:
        progress.step("Cutting the clip", 0.1, 0.2)
        progress.sub(0.5)
        assert seen == []
    finally:
        progress.set_handler(None)


def test_step_and_sub_report_inside_the_step_window(monkeypatch):
    seen = _capture()
    monkeypatch.setattr(progress, "_STEP_GAP", 0.0)
    try:
        progress.begin_steps(video_id="v1", title="T")
        progress.step("Following the subject", 0.2, 0.6)
        progress.sub(0.5)
        progress.end_steps()
        progress.sub(0.9)  # after the render: nothing more
    finally:
        progress.set_handler(None)
    assert [(e["message"], e["fraction"], e["ceil"]) for e in seen] == [
        ("Following the subject", 0.2, 0.6),
        ("Following the subject", 0.4, 0.6),
    ]
    assert all(e["stage"] == "rerender" and e["video_id"] == "v1" for e in seen)
