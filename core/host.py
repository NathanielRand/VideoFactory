"""Which operating system this is, and where that OS keeps things.

Stdlib only, for the same reason as core/paths.py: answering "where does data
go?" must not import PyTorch.

Every answer is read from `sys.platform` and the environment AT CALL TIME, not
at import, so a test can monkeypatch `sys.platform` and exercise the macOS and
Linux branches from a Windows machine.

The per-user folder is named "Video Factory" on every platform, and the
Electron side (ui/src/main/index.ts, bundledModelsDir) computes the same path
with the same formula. The two must agree or the engine and the bundled Ollama
look for models in different places. Change one, change both.
"""

import os
import sys
from pathlib import Path

APP_FOLDER = "Video Factory"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_mac() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def user_data_base() -> Path:
    """The per-user directory apps keep their data in.

    Windows: %LOCALAPPDATA%            (the location the app has always used)
    macOS:   ~/Library/Application Support
    Linux:   $XDG_DATA_HOME, else ~/.local/share  (relative values are ignored,
             as the XDG spec requires)
    """
    home = Path.home()
    if is_windows():
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    if is_mac():
        return home / "Library" / "Application Support"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg)
    return home / ".local" / "share"


def user_data_root() -> Path:
    """`<user_data_base>/Video Factory`: the folder the installed app owns."""
    return user_data_base() / APP_FOLDER


def exe_name(name: str) -> str:
    """`name` with the platform's executable suffix."""
    return f"{name}.exe" if is_windows() else name


def venv_python(venv: Path) -> Path:
    """The interpreter inside a virtualenv directory."""
    if is_windows():
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"
