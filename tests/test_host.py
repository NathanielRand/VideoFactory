"""Per-OS locations, exercised from any machine by faking sys.platform.

The Windows answer is frozen: it is where every existing install keeps its
library. macOS and Linux follow their own conventions, and the Electron side
(ui/src/main/index.ts userDataBase) must compute the same paths.
"""

import sys
from pathlib import Path

import pytest

from core import host, paths


@pytest.fixture
def fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("LOCALAPPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_windows_uses_localappdata(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "LA"))
    assert host.user_data_root() == fake_home / "LA" / "Video Factory"


def test_windows_without_localappdata_falls_back_to_the_profile(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "win32")
    assert host.user_data_base() == fake_home / "AppData" / "Local"


def test_macos_uses_application_support(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert host.user_data_root() == fake_home / "Library" / "Application Support" / "Video Factory"


def test_linux_honours_xdg_data_home(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / "xdg"))
    assert host.user_data_base() == fake_home / "xdg"


def test_linux_ignores_a_relative_xdg_value(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "relative/dir")
    assert host.user_data_base() == fake_home / ".local" / "share"


def test_linux_default_is_local_share(monkeypatch, fake_home):
    monkeypatch.setattr(sys, "platform", "linux")
    assert host.user_data_root() == fake_home / ".local" / "share" / "Video Factory"


@pytest.mark.parametrize("platform,suffix", [("win32", ".exe"), ("darwin", ""), ("linux", "")])
def test_exe_name(monkeypatch, platform, suffix):
    monkeypatch.setattr(sys, "platform", platform)
    assert host.exe_name("ffmpeg") == f"ffmpeg{suffix}"


def test_venv_python_location(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    assert host.venv_python(tmp_path) == tmp_path / "Scripts" / "python.exe"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert host.venv_python(tmp_path) == tmp_path / "bin" / "python"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_installed_build_data_dir_off_windows(monkeypatch, fake_home, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    got = paths.resolve_data_dir({"paths": {"data_dir": "data"}})
    assert got == host.user_data_root() / "data"
    assert fake_home / "AppData" not in got.parents
