"""Where finished videos can go beyond this PC.

Two routes, neither of which needs an account in Video Factory:

  Sync folders  OneDrive, Google Drive for desktop, Dropbox, iCloud Drive and
                Box each keep a folder on this PC in step with the cloud. A
                file copied into it is uploaded by the provider's own app, with
                its own sign-in, retries and bandwidth rules. Found here by
                looking where each one puts its folder.
  rclone        When rclone is installed, every remote configured in it (S3,
                Cloudflare R2, Backblaze B2, Google Drive through its API,
                SFTP and about seventy more) is a destination. Video Factory
                never sees those credentials: rclone keeps them.

Everything that touches the disk or runs a program takes it as an argument,
so tests do neither.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

PROVIDERS = {
    "onedrive": "OneDrive",
    "google_drive": "Google Drive",
    "dropbox": "Dropbox",
    "icloud": "iCloud Drive",
    "box": "Box",
}

# rclone remote names: letters, digits, space, and _ - . + @. Refused before
# they are put on a command line.
_REMOTE = re.compile(r"^[\w .+@-]{1,64}$")


@dataclass
class SyncFolder:
    provider: str
    label: str
    path: str


def sync_folders(
    *,
    home: Path | None = None,
    env: dict | None = None,
    platform: str = sys.platform,
    exists: Callable[[Path], bool] | None = None,
) -> list[SyncFolder]:
    """Every cloud sync folder on this PC, one per folder found."""
    home = home or Path.home()
    env = os.environ if env is None else env
    exists = exists or (lambda p: p.is_dir())
    found: list[SyncFolder] = []
    seen: set[str] = set()

    def add(provider: str, path: Path, label: str = "") -> None:
        key = str(path).lower()
        if key in seen or not exists(path):
            return
        seen.add(key)
        found.append(SyncFolder(provider, label or PROVIDERS[provider], str(path)))

    # OneDrive: the client sets these; a personal and a work account can both
    # be signed in, and each is its own folder.
    for var, label in (("OneDriveConsumer", "OneDrive"), ("OneDriveCommercial", "OneDrive (work)"),
                       ("OneDrive", "OneDrive")):
        if env.get(var):
            add("onedrive", Path(env[var]), label)
    for p in sorted(home.glob("OneDrive*")) if exists(home) else []:
        add("onedrive", p, "OneDrive" if p.name == "OneDrive" else p.name.replace("OneDrive - ", "OneDrive: "))

    # Dropbox writes where its folders are to info.json.
    for base in (env.get("APPDATA"), env.get("LOCALAPPDATA"), str(home / ".dropbox")):
        if not base:
            continue
        info = Path(base) / "Dropbox" / "info.json" if not base.endswith(".dropbox") else Path(base) / "info.json"
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for account, meta in (data or {}).items():
            if isinstance(meta, dict) and meta.get("path"):
                add("dropbox", Path(meta["path"]), "Dropbox" if account == "personal" else f"Dropbox ({account})")
    add("dropbox", home / "Dropbox")

    # Google Drive for desktop mounts a drive letter on Windows ("G:\My Drive")
    # and a CloudStorage folder on a Mac; the older Backup and Sync used ~/Google Drive.
    if platform == "win32":
        for letter in string.ascii_uppercase:
            add("google_drive", Path(f"{letter}:/My Drive"))
    for p in sorted((home / "Library" / "CloudStorage").glob("GoogleDrive-*")) if platform == "darwin" else []:
        add("google_drive", p / "My Drive", f"Google Drive ({p.name.removeprefix('GoogleDrive-')})")
    add("google_drive", home / "Google Drive")

    add("icloud", home / "iCloudDrive")
    add("icloud", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs")
    add("box", home / "Box")
    return found


# ---- rclone ---------------------------------------------------------------------------


Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def rclone_path() -> str | None:
    return shutil.which("rclone")


def rclone_info(*, binary: str | None = None, run: Runner | None = None) -> dict:
    """Whether rclone is here, which version, and its remotes with their
    types: {"available", "version", "remotes": [{"name", "type"}]}."""
    binary = binary if binary is not None else rclone_path()
    if not binary:
        return {"available": False, "version": "", "remotes": []}
    run = run or _run
    try:
        version = run([binary, "version"]).stdout.splitlines()[0].strip()
        listed = run([binary, "listremotes", "--long"])
    except (OSError, subprocess.SubprocessError, IndexError) as e:
        return {"available": False, "version": "", "remotes": [], "error": str(e)[:200]}
    remotes = []
    for line in listed.stdout.splitlines():
        name, _, kind = line.partition(":")
        if name.strip():
            remotes.append({"name": name.strip(), "type": kind.strip()})
    return {"available": True, "version": version, "remotes": remotes}


def rclone_target(remote: str, path: str) -> str:
    """ "remote:path", refusing names that are not an rclone remote's."""
    if not _REMOTE.match(remote or ""):
        raise ValueError("That isn't an rclone remote name.")
    clean = (path or "").strip().strip("/")
    if ".." in clean.split("/"):
        raise ValueError("The path can't go up a folder.")
    return f"{remote}:{clean}"


_PERCENT = re.compile(r"(\d{1,3})%")


def rclone_copy(
    source: Path,
    target: str,
    name: str,
    *,
    binary: str | None = None,
    on_fraction: Callable[[float], None] | None = None,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> str:
    """Upload one file to `target` ("remote:folder") as `name`. Returns where
    it went. Progress is read from rclone's once-a-second stats line."""
    binary = binary or rclone_path()
    if not binary:
        raise RuntimeError("rclone isn't installed on this PC any more.")
    dest = f"{target.rstrip('/')}/{name}" if not target.endswith(":") else f"{target}{name}"
    cmd = [binary, "copyto", str(source), dest, "--stats", "1s", "--stats-one-line",
           "--stats-log-level", "NOTICE"]
    proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    tail: list[str] = []
    for line in proc.stderr:  # type: ignore[union-attr]
        tail = (tail + [line.rstrip()])[-20:]
        m = _PERCENT.search(line)
        if m and on_fraction:
            on_fraction(min(1.0, int(m.group(1)) / 100))
    if proc.wait() != 0:
        raise RuntimeError("rclone couldn't upload it: " + " ".join(tail[-3:])[-400:])
    return dest


def check_rclone(target: str, *, binary: str | None = None, run: Runner | None = None) -> str:
    """"" when the remote folder can be written to, else why not. mkdir is
    harmless when it is already there and proves both the credentials and
    the path."""
    binary = binary or rclone_path()
    if not binary:
        return "rclone isn't installed on this PC."
    try:
        r = (run or _run)([binary, "mkdir", target])
    except (OSError, subprocess.SubprocessError) as e:
        return f"rclone didn't answer: {e}"[:300]
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["rclone refused it."])[-1][:300]


def check_folder(path: Path) -> str:
    """"" when files can be written into `path` (made if missing), else why not."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".video-factory-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        return f"Can't write there: {e.strerror or e}"
    return ""


def as_dicts(folders: list[SyncFolder]) -> list[dict]:
    return [asdict(f) for f in folders]
