"""Resource governor: keep Video Factory inside what the machine can spare.

A long job used to take whatever it could reach — three parallel renders,
Whisper and YOLO on the GPU, a 7B model resident in VRAM — regardless of what
else the creator had open. Alongside a game or an editor that is how a PC
runs out of RAM or VRAM and falls over, and a hard crash mid-job costs far
more than a slower job ever would.

So one thread samples the machine once a second and turns what it sees into a
pressure level:

    ok        the job runs as configured
    high      something else wants the machine: renders drop to one at a time
    critical  close to running out: the pipeline waits at the next safe point
              until the pressure clears, and an idle model is unloaded

Waiting happens only at checkpoints the pipeline already treats as safe (stage
boundaries, between LLM calls, before a render starts). Nothing is killed
mid-write, so a paused job resumes exactly where it stopped.

The level is judged on the WHOLE machine, but CPU is judged on everyone
else's share. Our own renders saturating the CPU is the job doing its work at
below-normal priority, which Windows already yields to the desktop; other
applications wanting the CPU is the signal that we should back off. RAM, VRAM
and GPU temperature count whoever is using them, because running out kills
everything regardless of whose fault it was.

The critical checks apply in every mode, including "max". That is the point
of the module.

COMMIT CHARGE is watched separately from RAM, because it is what actually ran
out. Windows promises every allocation a place in RAM or the page file up
front; when those promises reach the commit limit, allocations fail in EVERY
process at once, drivers and games included. That is a hard crash, and it is
what took a machine down on 2026-09-27: a game holding 24 GB, a 7B model in
Ollama, and then the reactions stage importing torch, whose CUDA libraries
alone take about 2.4 GB of commit (measured). Windows logged "Virtual Memory
Minimum Too Low" while it tried to grow the page file, and the import died
with WinError 1455 ("the paging file is too small").

So heavy loads (torch, YOLO, Whisper, the speaker model) go through
load_heavy(): it waits until the commit headroom is there, frees an idle LLM
first if that makes room, and if a load still fails for lack of commit it
waits and retries instead of failing the job.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

MODES = ("auto", "eco", "max")

# (enter, exit) pairs. Exit sits below enter so a reading hovering around a
# threshold does not flap the level every second.
_RAM_HIGH = (85.0, 80.0)
_RAM_CRIT = (93.0, 89.0)
_RAM_FREE_CRIT_GB = 1.0      # absolute floor, whatever the percentage says
_VRAM_HIGH = (92.0, 86.0)
_VRAM_CRIT = (97.0, 93.0)
_GPU_TEMP_HIGH = (83, 78)
_GPU_TEMP_CRIT = (90, 85)
_COMMIT_HIGH = (85.0, 80.0)       # % of the commit limit
_COMMIT_CRIT = (92.0, 88.0)
_COMMIT_FREE_CRIT_GB = 3.0        # below this, the next model load can crash the PC
_HEADROOM_MARGIN_GB = 2.0         # left free on top of what a load needs
_OTHERS_CPU_HIGH = (55.0, 40.0)   # other apps' share of the whole CPU
_CPU_CRIT = (98.0, 92.0)          # total, and only while others want it too

_HISTORY = 180          # samples kept for the chart: three minutes at 1 Hz
_SMOOTH = 5             # seconds averaged before judging CPU
_PRIORITY_EVERY = 15    # seconds between re-applying process priority


def _level_rank(level: str) -> int:
    return {"ok": 0, "high": 1, "critical": 2}[level]


class Governor:
    def __init__(self, config: dict):
        self.config = config
        data_dir = Path(config.get("paths", {}).get("data_dir", "data"))
        self._prefs_path = data_dir / "performance.json"
        self.mode = self._load_mode()
        self._samples: deque[dict] = deque(maxlen=_HISTORY)
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self.level = "ok"
        self.reasons: list[str] = []
        self.waiting = 0            # threads currently held at a checkpoint
        self.blocked_on = ""        # the heavy load waiting for memory, if any
        self._renders_active = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._nvml = None
        self._own: dict[int, object] = {}   # pid -> psutil.Process, for cpu_percent deltas
        self._last_priority = 0.0
        # Hooks set by the server: whether an LLM call is in flight, and how to
        # unload an idle model under critical pressure.
        self.llm_busy = lambda: False
        self.unload_models = lambda: None
        self._unloaded_at = 0.0

    # ---- mode ---------------------------------------------------------------

    def _load_mode(self) -> str:
        try:
            mode = json.loads(self._prefs_path.read_text(encoding="utf-8")).get("mode")
            return mode if mode in MODES else "auto"
        except Exception:
            return "auto"

    def set_mode(self, mode: str) -> str:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        with self._lock:
            self.mode = mode
            self._changed.notify_all()
        try:
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            self._prefs_path.write_text(json.dumps({"mode": mode}), encoding="utf-8")
        except Exception:
            pass  # applies until restart even if it cannot be saved
        self._apply_priority(force=True)
        return mode

    # ---- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._apply_priority(force=True)
        self._thread = threading.Thread(target=self._run, daemon=True, name="resource-governor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import psutil

        psutil.cpu_percent(None)  # prime: the first call always returns 0.0
        while not self._stop.wait(1.0):
            try:
                sample = self._sample()
            except Exception as e:  # a bad reading must never stop the governor
                print(f"governor: sample failed ({e})")
                continue
            with self._lock:
                self._samples.append(sample)
                self._judge()
                self._changed.notify_all()
            if time.time() - self._last_priority > _PRIORITY_EVERY:
                self._apply_priority()
            self._relieve_vram()

    # ---- sampling -----------------------------------------------------------

    def _gpu(self) -> dict | None:
        try:
            import pynvml

            if self._nvml is None:
                pynvml.nvmlInit()
                self._nvml = pynvml.nvmlDeviceGetHandleByIndex(0)
            h = self._nvml
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            name = pynvml.nvmlDeviceGetName(h)
            return {
                "name": name.decode() if isinstance(name, bytes) else name,
                "vram_used": mem.used,
                "vram_total": mem.total,
                "gpu_percent": util.gpu,
                "temp_c": temp,
            }
        except Exception:
            self._nvml = None
            return None

    def _own_processes(self):
        """This backend, everything it spawned (FFmpeg), and Ollama — the
        processes whose load is ours rather than the creator's."""
        import psutil

        procs = []
        try:
            me = psutil.Process(os.getpid())
            procs = [me, *me.children(recursive=True)]
        except Exception:
            pass
        for p in _ollama_processes():
            if all(p.pid != q.pid for q in procs):
                procs.append(p)
        return procs

    def _sample(self) -> dict:
        import psutil

        cores = psutil.cpu_count() or 1
        cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()

        own_cpu = 0.0
        own_rss = 0
        live: dict[int, object] = {}
        for p in self._own_processes():
            cached = self._own.get(p.pid, p)
            try:
                own_cpu += cached.cpu_percent(None)
                own_rss += cached.memory_info().rss
                live[p.pid] = cached
            except Exception:
                continue
        self._own = live
        own_cpu = min(cpu, own_cpu / cores)

        commit_used, commit_limit = _commit()
        return {
            "t": time.time(),
            "commit_used": commit_used,
            "commit_limit": commit_limit,
            "cpu": round(cpu, 1),
            "ram": round(vm.percent, 1),
            "ram_free": vm.available,
            "ram_total": vm.total,
            "own_cpu": round(own_cpu, 1),
            "own_ram": own_rss,
            "gpu": self._gpu(),
        }

    # ---- judging ------------------------------------------------------------

    def _judge(self) -> None:
        """Set self.level and self.reasons from recent samples. Lock held."""
        recent = list(self._samples)[-_SMOOTH:]
        last = recent[-1]
        cpu = sum(s["cpu"] for s in recent) / len(recent)
        others = sum(max(0.0, s["cpu"] - s["own_cpu"]) for s in recent) / len(recent)
        ram = last["ram"]
        free_gb = last["ram_free"] / 1e9
        gpu = last["gpu"] or {}
        vram = 100 * gpu["vram_used"] / gpu["vram_total"] if gpu.get("vram_total") else 0.0
        temp = gpu.get("temp_c") or 0

        was = self.level

        def over(value, pair, level: str) -> bool:
            # Hysteresis: once at or above `level`, stay until below the exit.
            enter, leave = pair
            held = _level_rank(was) >= _level_rank(level)
            return value >= (leave if held else enter)

        crit, high = [], []
        if over(ram, _RAM_CRIT, "critical") or free_gb < _RAM_FREE_CRIT_GB:
            crit.append(f"RAM {ram:.0f}% ({free_gb:.1f} GB free)")
        elif over(ram, _RAM_HIGH, "high"):
            high.append(f"RAM {ram:.0f}%")
        limit = last.get("commit_limit") or 0
        if limit:
            commit = 100 * last["commit_used"] / limit
            commit_free = (limit - last["commit_used"]) / 1e9
            if over(commit, _COMMIT_CRIT, "critical") or commit_free < _COMMIT_FREE_CRIT_GB:
                crit.append(f"memory commit {commit:.0f}% ({commit_free:.1f} GB left)")
            elif over(commit, _COMMIT_HIGH, "high"):
                high.append(f"memory commit {commit:.0f}%")
        if gpu:
            if over(vram, _VRAM_CRIT, "critical"):
                crit.append(f"VRAM {vram:.0f}%")
            elif over(vram, _VRAM_HIGH, "high"):
                high.append(f"VRAM {vram:.0f}%")
            if temp and over(temp, _GPU_TEMP_CRIT, "critical"):
                crit.append(f"GPU {temp}°C")
            elif temp and over(temp, _GPU_TEMP_HIGH, "high"):
                high.append(f"GPU {temp}°C")
        if over(cpu, _CPU_CRIT, "critical") and others >= _OTHERS_CPU_HIGH[1]:
            crit.append(f"CPU {cpu:.0f}%, {others:.0f}% other apps")
        elif over(others, _OTHERS_CPU_HIGH, "high"):
            high.append(f"other apps using {others:.0f}% CPU")

        if crit:
            self.level, self.reasons = "critical", crit
        elif high:
            self.level, self.reasons = "high", high
        else:
            self.level, self.reasons = "ok", []
        if self.level != was:
            print(f"governor: {was} -> {self.level}" + (f" ({'; '.join(self.reasons)})" if self.reasons else ""))

    def _configured_renders(self) -> int:
        return max(1, int(self.config.get("video", {}).get("parallel_renders", 2) or 1))

    def _render_budget(self) -> int:
        """How many renders may run at once right now. Lock held."""
        full = self._configured_renders()
        if self.level == "critical":
            return 0
        if self.mode == "eco":
            return 0 if self.level == "high" else 1
        if self.level == "high":
            return max(1, full - 1) if self.mode == "max" else 1
        return full

    def _must_wait(self) -> bool:
        """Whether a checkpoint should hold the pipeline right now. Lock held."""
        if self.level == "critical":
            return True
        return self.mode == "eco" and self.level == "high"

    # ---- what the pipeline calls --------------------------------------------

    def checkpoint(self, check=None) -> None:
        """Wait here while the machine is under critical pressure.

        `check` is called about once a second while waiting, so a cancel still
        lands: pass cancel.check_active. Returns at once when running without
        the sampler (CLI runs, tests), where there is nothing to judge by.
        """
        if self._thread is None:
            return
        with self._lock:
            if not self._must_wait():
                return
            self.waiting += 1
            try:
                while self._must_wait() and not self._stop.is_set():
                    self._changed.wait(timeout=1.0)
                    if check is not None:
                        self._lock.release()
                        try:
                            check()
                        finally:
                            self._lock.acquire()
            finally:
                self.waiting -= 1

    @contextmanager
    def render_slot(self, check=None):
        """Hold one of the renders the current budget allows.

        The budget moves with the pressure, so a pool sized for three renders
        runs one at a time while a game is open and three again once it
        closes. Renders already running are never interrupted; a smaller
        budget only stops new ones starting.
        """
        if self._thread is None:
            yield
            return
        with self._lock:
            counted = False
            while self._renders_active >= self._render_budget() and not self._stop.is_set():
                if not counted:
                    self.waiting += 1
                    counted = True
                self._changed.wait(timeout=1.0)
                if check is not None:
                    self._lock.release()
                    try:
                        check()
                    except BaseException:
                        self._lock.acquire()
                        self.waiting -= 1
                        raise
                    self._lock.acquire()
            if counted:
                self.waiting -= 1
            self._renders_active += 1
        try:
            yield
        finally:
            with self._lock:
                self._renders_active -= 1
                self._changed.notify_all()

    def may_start_job(self) -> bool:
        """Whether the worker should claim a new job now."""
        if self._thread is None:
            return True
        with self._lock:
            return not self._must_wait()

    # ---- heavy loads --------------------------------------------------------

    def commit_free(self) -> float | None:
        """Bytes of commit left before the limit, or None if unknown."""
        used, limit = _commit()
        return (limit - used) if limit else None

    def ensure_headroom(self, need_gb: float, what: str, check=None,
                        max_wait: float | None = None) -> None:
        """Wait until loading `what` (about `need_gb` of commit) leaves a
        margin, freeing an idle LLM first when that is what is in the way.

        Pipeline work waits as long as it takes; a cancel still lands through
        `check`. Anything else (a request from the UI) waits at most
        `max_wait` seconds and then raises MemoryError with a plain reason,
        because a request that hangs looks like the app is broken.
        """
        need = (need_gb + _HEADROOM_MARGIN_GB) * 1e9
        free = self.commit_free()
        if free is None or free >= need:
            return
        start = time.time()
        freed_llm = False
        self.blocked_on = what
        with self._lock:
            self.waiting += 1
        try:
            while True:
                free = self.commit_free()
                if free is None or free >= need:
                    return
                if not freed_llm and not self.llm_busy():
                    freed_llm = True
                    print(f"governor: {free / 1e9:.1f} GB of commit left, {what} needs "
                          f"~{need_gb:.1f} GB; unloading the idle model to make room")
                    try:
                        self.unload_models()
                    except Exception:
                        pass
                    time.sleep(2)
                    continue
                if max_wait is not None and time.time() - start > max_wait:
                    raise MemoryError(
                        f"Not enough memory to load {what}: {free / 1e9:.1f} GB of commit "
                        f"left, needs about {need_gb + _HEADROOM_MARGIN_GB:.0f} GB. Close "
                        "other applications or enlarge the Windows page file."
                    )
                if check is not None:
                    check()
                time.sleep(1.0)
        finally:
            with self._lock:
                self.waiting -= 1
            self.blocked_on = ""

    def load_heavy(self, what: str, need_gb: float, fn, check=None, attempts: int = 4):
        """Run `fn` (a heavy import or model load) with commit headroom, and
        retry it if Windows still refuses the memory.

        A refusal is WinError 1455 or a MemoryError. Retrying is safe: a failed
        import is removed from sys.modules, and DLLs that did load stay loaded,
        so the second attempt needs less than the first.
        """
        interactive = not pipeline_thread()
        for attempt in range(1, attempts + 1):
            self.ensure_headroom(need_gb, what, check, max_wait=30 if interactive else None)
            try:
                return fn()
            except (OSError, MemoryError) as e:
                if not _out_of_commit(e) or attempt == attempts:
                    raise
                print(f"governor: loading {what} ran out of commit (attempt {attempt}); "
                      "freeing memory and waiting before retrying")
                if not self.llm_busy():
                    try:
                        self.unload_models()
                    except Exception:
                        pass
                # Windows may be growing the page file right now; give it time.
                time.sleep(10 * attempt)

    # ---- relief -------------------------------------------------------------

    def _apply_priority(self, force: bool = False) -> None:
        """Run below normal priority unless the creator chose "max".

        Windows hands the desktop, a game or an editor the CPU first and gives
        the rest to us, so a job can use every idle cycle without making the
        machine feel busy. FFmpeg inherits the class from this process.
        Ollama is a separate server, so its processes are set directly.
        """
        self._last_priority = time.time()
        try:
            import psutil
        except Exception:
            return
        low = self.mode != "max"
        if os.name == "nt":
            value = psutil.BELOW_NORMAL_PRIORITY_CLASS if low else psutil.NORMAL_PRIORITY_CLASS
        else:
            value = 10 if low else 0
        try:
            me = psutil.Process(os.getpid())
            targets = [me, *me.children(recursive=True), *_ollama_processes()]
        except Exception:
            return
        for p in targets:
            try:
                if force or p.nice() != value:
                    p.nice(value)
            except Exception:
                continue  # exited, or not ours to change

    def _relieve_vram(self) -> None:
        """Under critical pressure, free the model's memory if nothing is
        using it. Ollama reloads it on the next request, which costs seconds;
        the alternative is the whole machine locking up."""
        with self._lock:
            critical = self.level == "critical"
        if not critical or time.time() - self._unloaded_at < 60:
            return
        try:
            if self.llm_busy():
                return
            self._unloaded_at = time.time()
            self.unload_models()
        except Exception:
            pass

    # ---- reporting ----------------------------------------------------------

    def snapshot(self, history: int = 90) -> dict:
        with self._lock:
            samples = list(self._samples)[-history:]
            return {
                "mode": self.mode,
                "level": self.level,
                "reasons": list(self.reasons),
                "waiting": self.waiting > 0,
                "blocked_on": self.blocked_on,
                "renders_active": self._renders_active,
                "render_budget": self._render_budget(),
                "renders_configured": self._configured_renders(),
                "samples": [
                    {
                        "t": s["t"],
                        "cpu": s["cpu"],
                        "ram": s["ram"],
                        "own_cpu": s["own_cpu"],
                        "gpu": (s["gpu"] or {}).get("gpu_percent"),
                        "commit": (
                            round(100 * s["commit_used"] / s["commit_limit"], 1)
                            if s.get("commit_limit") else None
                        ),
                        "vram": (
                            round(100 * s["gpu"]["vram_used"] / s["gpu"]["vram_total"], 1)
                            if s["gpu"] and s["gpu"].get("vram_total") else None
                        ),
                    }
                    for s in samples
                ],
                "latest": samples[-1] if samples else None,
            }

    def latest(self) -> dict | None:
        with self._lock:
            return self._samples[-1] if self._samples else None


def _out_of_commit(e: BaseException) -> bool:
    """WinError 1455 (the paging file is too small) or a MemoryError."""
    return isinstance(e, MemoryError) or getattr(e, "winerror", None) == 1455


_perf_struct = None


def _commit() -> tuple[int, int]:
    """(committed bytes, commit limit) for the whole machine; (0, 0) if unknown.

    Windows: GetPerformanceInfo, the same numbers Task Manager shows as
    "Committed". Elsewhere, RAM plus swap in use against RAM plus swap, the
    nearest equivalent.
    """
    global _perf_struct
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as w

            if _perf_struct is None:
                class PERFORMANCE_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("cb", w.DWORD), ("CommitTotal", ctypes.c_size_t),
                        ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                        ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t),
                        ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                        ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                        ("PageSize", ctypes.c_size_t), ("HandleCount", w.DWORD),
                        ("ProcessCount", w.DWORD), ("ThreadCount", w.DWORD),
                    ]
                _perf_struct = PERFORMANCE_INFORMATION
            info = _perf_struct()
            info.cb = ctypes.sizeof(info)
            if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
                return 0, 0
            return info.CommitTotal * info.PageSize, info.CommitLimit * info.PageSize
        import psutil

        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        return vm.total - vm.available + sw.used, vm.total + sw.total
    except Exception:
        return 0, 0


_ollama_cache: tuple[float, list] = (0.0, [])


def _ollama_processes():
    """Every running Ollama process: the server and its model runners.

    Walking the process table costs tens of milliseconds on Windows, too much
    to do every second, so the list is reused for a few seconds. A runner
    that starts in between is picked up on the next walk.
    """
    global _ollama_cache
    stamp, cached = _ollama_cache
    if time.time() - stamp < 5:
        return [p for p in cached if p.is_running()]
    try:
        import psutil
    except Exception:
        return []
    found = _ollama_via_tasklist(psutil) if os.name == "nt" else None
    if found is None:
        found = []
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "").lower()
            # "ollama app.exe" is the tray icon, not the server: leave it alone.
            if name.startswith("ollama") and "app" not in name:
                found.append(p)
    _ollama_cache = (time.time(), found)
    return found


def _ollama_via_tasklist(psutil):
    """The same list without walking the process table in this process.

    psutil.process_iter on Windows holds the interpreter for ~450 ms per walk,
    and the API shares this process: every walk froze video playback (measured:
    a 50 KB range read stalled 280-400 ms every ~5 s). tasklist is a separate
    process, so waiting on it leaves the interpreter free. None on any failure,
    so the caller falls back to the walk."""
    import csv
    import io
    import subprocess

    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq ollama*"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return None
        found = []
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row) < 2 or not row[1].isdigit():
                continue
            name = row[0].lower()
            if name.startswith("ollama") and "app" not in name:
                found.append(psutil.Process(int(row[1])))
        return found
    except Exception:
        return None


# One per process: the pipeline, the server and the LLM backend all consult it.
_instance: Governor | None = None


def install(config: dict) -> Governor:
    global _instance
    if _instance is None:
        _instance = Governor(config)
        _instance.start()
    return _instance


def get() -> Governor | None:
    return _instance


_local = threading.local()


@contextmanager
def pipeline_work():
    """Mark this thread as running queued pipeline work, the only work that
    is allowed to wait at a checkpoint. A request from the UI waiting on the
    governor would just look like the app hanging."""
    _local.pipeline = True
    try:
        yield
    finally:
        _local.pipeline = False


def pipeline_thread() -> bool:
    return getattr(_local, "pipeline", False)


def checkpoint(check=None) -> None:
    if _instance is not None:
        _instance.checkpoint(check)


def may_start_job() -> bool:
    return _instance is None or _instance.may_start_job()


def load_heavy(what: str, need_gb: float, fn, check=None):
    """Governor.load_heavy when the governor runs. Otherwise (CLI runs) a
    plain call that still waits and retries once on WinError 1455."""
    if _instance is not None and _instance._thread is not None:
        return _instance.load_heavy(what, need_gb, fn, check)
    try:
        return fn()
    except (OSError, MemoryError) as e:
        if not _out_of_commit(e):
            raise
        time.sleep(15)
        return fn()


@contextmanager
def render_slot(check=None):
    if _instance is None:
        yield
        return
    with _instance.render_slot(check):
        yield
