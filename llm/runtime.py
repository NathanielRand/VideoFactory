"""Start, stop and unload the local Ollama runtime from inside the app.

"Ollama is not reachable" used to be the end of the road: the creator had to
find a terminal and start it themselves, then come back and retry. This owns
that instead. Nothing here guesses at a model: it only starts the server,
loads the model the app is already set to use, and frees memory.

Two kinds of server are handled:

  managed   one this backend started. Stopping it is ours to do, and it
            goes down with the backend.
  external  one that was already running: a system install, or the copy the
            desktop app starts. Stopping it means ending whatever is listening
            on the port, which is what the creator asked for when they press
            Stop. Ollama's tray app may start it again, and the status shows
            that honestly.
"""

from __future__ import annotations

import atexit
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Seconds for `ollama serve` to answer after launch. Usually a few, but GPU
# discovery on a machine already short of memory was measured at over 30.
_START_TIMEOUT = 90


class OllamaRuntime:
    def __init__(self, host: str, logs_dir: Path | None = None):
        self.host = host.rstrip("/")
        self.logs_dir = logs_dir
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.starting = False
        self.last_error = ""
        # LLM calls in flight, so the governor never unloads a model mid-answer.
        self._busy = 0
        self._busy_lock = threading.Lock()

    # ---- status ------------------------------------------------------------

    def reachable(self, timeout: float = 2.0) -> bool:
        try:
            return requests.get(f"{self.host}/api/version", timeout=timeout).ok
        except Exception:
            return False

    def loaded(self) -> list[dict]:
        """Models resident in memory right now, from Ollama's /api/ps."""
        try:
            r = requests.get(f"{self.host}/api/ps", timeout=3)
            r.raise_for_status()
        except Exception:
            return []
        return [
            {
                "name": m.get("name", ""),
                "size": m.get("size", 0),
                "size_vram": m.get("size_vram", 0),
                "expires_at": m.get("expires_at", ""),
            }
            for m in r.json().get("models", [])
        ]

    def status(self) -> dict:
        up = self.reachable()
        version = ""
        if up:
            try:
                version = requests.get(f"{self.host}/api/version", timeout=2).json().get("version", "")
            except Exception:
                pass
        return {
            "host": self.host,
            "running": up,
            "starting": self.starting and not up,
            "managed": self._managed_alive(),
            "version": version,
            "loaded": self.loaded() if up else [],
            "can_start": self._binary() is not None,
            "busy": self.busy(),
            "error": "" if up else self.last_error,
        }

    # ---- lifecycle ---------------------------------------------------------

    def _binary(self) -> str | None:
        try:
            from core import binaries

            exe = binaries.ollama()
        except Exception:
            return None
        if exe and Path(exe).exists():
            return exe
        # Not bundled and not on PATH. Ollama's Windows installer puts it
        # here, and a PATH that has not caught up since install is common.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            default = Path(local) / "Programs" / "Ollama" / "ollama.exe"
            if default.exists():
                return str(default)
        return None

    def _managed_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, wait: bool = True) -> bool:
        """Start `ollama serve` on this runtime's port unless something already
        answers there. Returns whether Ollama is reachable afterwards."""
        with self._lock:
            if self.reachable():
                self.last_error = ""
                return True
            if self._managed_alive() and self.starting:
                pass  # launched a moment ago, still coming up; just wait below
            else:
                exe = self._binary()
                if exe is None:
                    self.last_error = "Ollama is not installed (ollama.com/download)"
                    return False
                self._launch(exe)
        if not wait:
            return False
        return self._wait_up(_START_TIMEOUT)

    def _launch(self, exe: str) -> None:
        parsed = urlparse(self.host)
        env = dict(os.environ)
        env["OLLAMA_HOST"] = f"{parsed.hostname or '127.0.0.1'}:{parsed.port or 11434}"
        # The desktop app keeps its models under the data directory and says
        # where; a checkout uses Ollama's own default.
        models = os.environ.get("VIDEO_FACTORY_OLLAMA_MODELS")
        if models:
            env["OLLAMA_MODELS"] = models
        # One model resident, one request at a time. Clipping and translation
        # can name different models; letting Ollama hold both at once is how
        # a 12 GB card ends up full and the rest spills into system RAM.
        env.setdefault("OLLAMA_MAX_LOADED_MODELS", "1")
        env.setdefault("OLLAMA_NUM_PARALLEL", "1")
        # Flash attention plus an 8-bit KV cache roughly halves the memory the
        # context window takes. On an 8 GB card that is the difference between
        # the whole model on the GPU and half of it on the CPU, several times
        # slower. Ollama turns flash attention off itself for a model that
        # cannot use it.
        env.setdefault("OLLAMA_FLASH_ATTENTION", "1")
        env.setdefault("OLLAMA_KV_CACHE_TYPE", "q8_0")

        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        out = subprocess.DEVNULL
        if self.logs_dir is not None:
            try:
                self.logs_dir.mkdir(parents=True, exist_ok=True)
                out = open(self.logs_dir / "ollama.log", "ab")
            except Exception:
                out = subprocess.DEVNULL
        self.starting = True
        self.last_error = ""
        try:
            self._proc = subprocess.Popen(
                [exe, "serve"], env=env, stdout=out, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=flags,
            )
            print(f"Ollama: started {exe} serve on {env['OLLAMA_HOST']} (pid {self._proc.pid})")
        except Exception as e:
            self.starting = False
            self.last_error = f"could not start Ollama ({e})"

    def _wait_up(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if self.reachable(timeout=1.0):
                    self.last_error = ""
                    return True
                if self._proc is not None and self._proc.poll() is not None:
                    self.last_error = f"Ollama exited during startup (code {self._proc.returncode})"
                    return False
                time.sleep(0.5)
            self.last_error = f"Ollama did not answer within {int(timeout)} seconds"
            return False
        finally:
            self.starting = False

    def ensure_running(self) -> bool:
        """Reachable, starting it first if need be. For callers about to make
        an LLM request; costs one quick request when Ollama is already up."""
        return self.reachable() or self.start(wait=True)

    def stop(self) -> dict:
        """Unload every model, then end the server."""
        self.unload()
        stopped = False
        if self._managed_alive():
            _kill_tree(self._proc.pid)
            stopped = True
        else:
            pid = self._listener_pid()
            if pid:
                _kill_tree(pid)
                stopped = True
        self._proc = None
        self.starting = False
        self.last_error = ""
        deadline = time.time() + 5
        while time.time() < deadline and self.reachable(timeout=0.5):
            time.sleep(0.25)
        return {"stopped": stopped, "running": self.reachable()}

    def _listener_pid(self) -> int | None:
        """The process listening on this runtime's port, if we can see it."""
        port = urlparse(self.host).port or 11434
        try:
            import psutil

            for c in psutil.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
                    return c.pid
        except Exception:
            pass
        return None

    # ---- models ------------------------------------------------------------

    def load(self, model: str, keep_alive: str = "30m") -> None:
        """Load `model` into memory now, so the first real request is instant."""
        if not self.ensure_running():
            raise RuntimeError(self.last_error or "Ollama is not reachable")
        r = requests.post(
            f"{self.host}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": keep_alive},
            timeout=300,
        )
        r.raise_for_status()

    def unload(self, model: str | None = None) -> list[str]:
        """Free the memory a model holds, without stopping the server.
        Every loaded model when `model` is None."""
        names = [model] if model else [m["name"] for m in self.loaded()]
        freed = []
        for name in names:
            try:
                requests.post(
                    f"{self.host}/api/generate",
                    json={"model": name, "prompt": "", "keep_alive": 0},
                    timeout=30,
                ).raise_for_status()
                freed.append(name)
            except Exception:
                continue
        if freed:
            print(f"Ollama: unloaded {', '.join(freed)}")
        return freed

    # ---- in-flight tracking ------------------------------------------------

    def busy(self) -> bool:
        with self._busy_lock:
            return self._busy > 0

    def begin(self) -> None:
        with self._busy_lock:
            self._busy += 1

    def end(self) -> None:
        with self._busy_lock:
            self._busy = max(0, self._busy - 1)

    def shutdown(self) -> None:
        """Take a server we started down with us, so it cannot outlive the
        backend holding VRAM nobody is using."""
        if self._managed_alive():
            _kill_tree(self._proc.pid)


def _kill_tree(pid: int) -> None:
    # Ollama runs each model in a separate runner process; ending only the
    # server would leave a runner holding the VRAM.
    try:
        import psutil

        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        _, alive = psutil.wait_procs(procs, timeout=5)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
    except Exception:
        pass


_runtimes: dict[str, OllamaRuntime] = {}
_runtimes_lock = threading.Lock()


def get(host: str, logs_dir: Path | None = None) -> OllamaRuntime:
    """The runtime for `host`, one per address for the life of the process."""
    key = host.rstrip("/")
    with _runtimes_lock:
        rt = _runtimes.get(key)
        if rt is None:
            rt = _runtimes[key] = OllamaRuntime(key, logs_dir)
            atexit.register(rt.shutdown)
        elif logs_dir is not None and rt.logs_dir is None:
            rt.logs_dir = logs_dir
        return rt
