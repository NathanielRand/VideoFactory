"""The POSIX half of core/lifetime.py: the parent-death watchdog.

The Windows half is a kernel Job Object and can only be checked on Windows.
"""

import os
import sys
import types

import pytest

from core import lifetime


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(lifetime, "_watchdog", None)
    # CI installs only the light dependencies, and psutil is not one of them.
    # A fake keeps these tests hermetic; each test sets pid_exists as needed.
    fake = types.SimpleNamespace(pid_exists=lambda _pid: True)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return fake


@pytest.mark.parametrize("value", [None, "", "abc", "0", "1"])
def test_no_watchdog_unless_asked_for(monkeypatch, value):
    """A backend under nohup, systemd or Docker must outlive its launcher, so
    nothing starts without an explicit parent pid."""
    if value is None:
        monkeypatch.delenv("VIDEO_FACTORY_PARENT_PID", raising=False)
    else:
        monkeypatch.setenv("VIDEO_FACTORY_PARENT_PID", value)
    assert lifetime._bind_posix() is False
    assert lifetime._watchdog is None


def test_watchdog_starts_for_a_real_pid(monkeypatch):
    monkeypatch.setenv("VIDEO_FACTORY_PARENT_PID", str(os.getpid()))
    monkeypatch.setattr(lifetime, "_watch_parent", lambda *_a, **_k: None)
    assert lifetime._bind_posix() is True
    lifetime._watchdog.join(timeout=2)
    # Asking twice does not start a second thread.
    first = lifetime._watchdog
    assert lifetime._bind_posix() is True
    assert lifetime._watchdog is first


def test_parent_gone_ends_children_and_exits(monkeypatch, _fresh):
    calls = []
    monkeypatch.setattr(lifetime, "_end_process_tree", lambda: calls.append("tree"))

    def fake_exit(code):
        calls.append(("exit", code))
        raise SystemExit

    monkeypatch.setattr(lifetime.os, "_exit", fake_exit)
    monkeypatch.setattr(lifetime.time, "sleep", lambda _s: None)
    monkeypatch.setattr(_fresh, "pid_exists", lambda _pid: False)
    with pytest.raises(SystemExit):
        lifetime._watch_parent(424242)
    assert calls == ["tree", ("exit", 0)]


def test_parent_alive_keeps_running(monkeypatch, _fresh):
    ticks = {"n": 0}

    def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] > 3:
            raise KeyboardInterrupt  # stop the otherwise endless loop

    monkeypatch.setattr(lifetime.time, "sleep", sleep)
    monkeypatch.setattr(_fresh, "pid_exists", lambda _pid: True)
    monkeypatch.setattr(lifetime, "_end_process_tree", lambda: pytest.fail("killed a live parent's child"))
    with pytest.raises(KeyboardInterrupt):
        lifetime._watch_parent(424242)
