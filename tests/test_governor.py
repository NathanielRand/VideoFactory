"""The resource governor's judgement: when it backs off, and by how much.

Sampling is replaced with fixed readings, so these pin the thresholds and the
render budget without depending on what the test machine is doing.
"""

import threading
import time

import pytest

from core.governor import Governor

GB = 1_000_000_000


def _sample(cpu=20.0, ram=50.0, own_cpu=10.0, free_gb=8.0, vram=None, temp=None):
    gpu = None
    if vram is not None:
        gpu = {"name": "test", "vram_used": vram * GB // 100, "vram_total": GB,
               "gpu_percent": 50, "temp_c": temp}
    return {"t": time.time(), "cpu": cpu, "ram": ram, "ram_free": free_gb * GB,
            "ram_total": 16 * GB, "own_cpu": own_cpu, "own_ram": 0, "gpu": gpu}


@pytest.fixture
def gov(tmp_path):
    g = Governor({"paths": {"data_dir": str(tmp_path)}, "video": {"parallel_renders": 3}})
    return g


def _feed(gov, n=5, **reading):
    with gov._lock:
        for _ in range(n):
            gov._samples.append(_sample(**reading))
            gov._judge()


def test_quiet_machine_runs_as_configured(gov):
    _feed(gov)
    assert gov.level == "ok"
    assert gov._render_budget() == 3


def test_our_own_cpu_load_is_not_pressure(gov):
    # Our renders saturating the CPU at below-normal priority is the job working.
    _feed(gov, cpu=99, own_cpu=95)
    assert gov.level == "ok"


def test_other_apps_wanting_cpu_backs_off(gov):
    _feed(gov, cpu=90, own_cpu=20)
    assert gov.level == "high"
    assert gov._render_budget() == 1
    assert not gov._must_wait()


def test_ram_running_out_is_critical(gov):
    _feed(gov, ram=95)
    assert gov.level == "critical"
    assert gov._render_budget() == 0
    assert gov._must_wait()


def test_low_free_ram_is_critical_whatever_the_percentage(gov):
    _feed(gov, ram=70, free_gb=0.5)
    assert gov.level == "critical"


def test_vram_and_temperature(gov):
    _feed(gov, vram=98)
    assert gov.level == "critical"
    _feed(gov, vram=50, temp=84)  # under the critical exit (85), over the high one (78)
    assert gov.level == "high"


def test_hysteresis_holds_until_clearly_below(gov):
    _feed(gov, ram=94)
    assert gov.level == "critical"
    _feed(gov, ram=91)  # below enter (93), above exit (89)
    assert gov.level == "critical"
    _feed(gov, ram=88)
    assert gov.level == "high"
    _feed(gov, ram=70)
    assert gov.level == "ok"


def test_eco_mode_waits_on_high_pressure(gov):
    gov.mode = "eco"
    _feed(gov)
    assert gov._render_budget() == 1
    _feed(gov, cpu=90, own_cpu=20)
    assert gov._must_wait()


def test_max_mode_still_stops_at_critical(gov):
    gov.mode = "max"
    _feed(gov, cpu=90, own_cpu=20)
    assert gov._render_budget() == 2
    _feed(gov, ram=96)
    assert gov._must_wait()


def test_mode_is_remembered(tmp_path):
    g = Governor({"paths": {"data_dir": str(tmp_path)}})
    g.set_mode("eco")
    assert Governor({"paths": {"data_dir": str(tmp_path)}}).mode == "eco"
    with pytest.raises(ValueError):
        g.set_mode("turbo")


def test_without_the_sampler_nothing_waits(gov):
    # CLI runs and tests never start the thread; checkpoints must not block.
    _feed(gov, ram=99)
    gov.checkpoint()
    with gov.render_slot():
        pass


def test_checkpoint_releases_when_pressure_clears(gov):
    gov._thread = threading.current_thread()  # pretend the sampler is running
    _feed(gov, ram=96)
    released = threading.Event()

    def job():
        gov.checkpoint()
        released.set()

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert not released.wait(0.3)
    assert gov.snapshot()["waiting"]
    _feed(gov, ram=60)
    with gov._lock:
        gov._changed.notify_all()
    assert released.wait(2)


def test_checkpoint_lets_a_cancel_through(gov):
    gov._thread = threading.current_thread()
    _feed(gov, ram=96)

    class Stop(Exception):
        pass

    def check():
        raise Stop

    with pytest.raises(Stop):
        gov.checkpoint(check)
    assert gov.waiting == 0


# ---- commit charge: the 2026-09-27 crash (WinError 1455) --------------------


def _commit_reading(gov, used_gb, limit_gb=50):
    with gov._lock:
        for _ in range(5):
            s = _sample()
            s["commit_used"], s["commit_limit"] = used_gb * GB, limit_gb * GB
            gov._samples.append(s)
            gov._judge()


def test_commit_near_the_limit_is_critical(gov):
    _commit_reading(gov, 47.5)  # 2.5 GB left: the next torch load could crash the PC
    assert gov.level == "critical"
    assert "commit" in gov.reasons[0]


def test_commit_high_backs_off(gov):
    _commit_reading(gov, 43)  # 86%
    assert gov.level == "high"


def _refusal():
    e = OSError("[WinError 1455] The paging file is too small for this operation to complete")
    e.winerror = 1455
    return e


def test_load_heavy_frees_the_llm_and_waits_for_headroom(gov, monkeypatch):
    import core.governor as g

    free = iter([1 * GB, 1 * GB, 8 * GB, 8 * GB])
    monkeypatch.setattr(gov, "commit_free", lambda: next(free))
    monkeypatch.setattr(g.time, "sleep", lambda _s: None)
    unloaded = []
    gov.unload_models = lambda: unloaded.append(True)
    with g.pipeline_work():
        assert gov.load_heavy("torch", 2.5, lambda: "loaded") == "loaded"
    assert unloaded == [True]


def test_load_heavy_retries_a_1455_instead_of_failing(gov, monkeypatch):
    import core.governor as g

    monkeypatch.setattr(gov, "commit_free", lambda: 20 * GB)
    monkeypatch.setattr(g.time, "sleep", lambda _s: None)
    calls = []

    def load():
        calls.append(1)
        if len(calls) < 3:
            raise _refusal()
        return "loaded"

    with g.pipeline_work():
        assert gov.load_heavy("torch", 2.5, load) == "loaded"
    assert len(calls) == 3


def test_load_heavy_does_not_swallow_other_errors(gov, monkeypatch):
    monkeypatch.setattr(gov, "commit_free", lambda: 20 * GB)

    def load():
        raise OSError("some other failure")

    with pytest.raises(OSError, match="other"):
        gov.load_heavy("torch", 2.5, load)


def test_a_ui_request_gives_up_with_a_reason(gov, monkeypatch):
    import core.governor as g

    monkeypatch.setattr(gov, "commit_free", lambda: 1 * GB)
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(g.time, "time", lambda: next(clock))
    monkeypatch.setattr(g.time, "sleep", lambda _s: None)
    gov.llm_busy = lambda: True
    with pytest.raises(MemoryError, match="page file"):
        gov.load_heavy("torch", 2.5, lambda: "loaded")


def test_torch_out_of_memory_is_not_cached_as_no_gpu(monkeypatch):
    import builtins

    from core import gpu

    real_import = builtins.__import__

    def refuse(name, *a, **k):
        if name == "torch":
            raise _refusal()
        return real_import(name, *a, **k)

    monkeypatch.setattr(gpu, "_DEVICE", None)
    monkeypatch.setattr(builtins, "__import__", refuse)
    assert gpu.torch_device() == "cpu"
    assert gpu._DEVICE is None  # asked again next time, once memory is back
