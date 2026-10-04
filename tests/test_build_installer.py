"""The post-freeze check that bundled executables are still executable.

POSIX only: Windows has no exec bit, and the function is a no-op there.
"""

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no exec bit on Windows")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def build(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("build_installer", ROOT / "scripts" / "build_installer.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "BACKEND_OUT", tmp_path)
    return mod


def _layout(root, ollama_sub, ffmpeg_ok=True):
    internal = root / "_internal"
    for rel in ("ffmpeg/ffmpeg", "ffmpeg/ffprobe", f"ollama/{ollama_sub}ollama"):
        path = internal / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "#!/bin/sh\necho ffmpeg version 9\n" if ffmpeg_ok else "#!/bin/sh\nexit 3\n"
        path.write_text(body)
        path.chmod(0o644)  # what a freeze that drops the mode bits leaves behind
    return internal


def test_a_lost_executable_bit_is_restored(build, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build.host, "is_linux", lambda: True)
    internal = _layout(tmp_path, "bin/")
    build.verify_bundled_executables()
    for rel in ("ffmpeg/ffmpeg", "ffmpeg/ffprobe", "ollama/bin/ollama"):
        assert os.access(internal / rel, os.X_OK), rel
    assert "restoring" in capsys.readouterr().out


def test_the_mac_layout_has_ollama_at_the_top_of_its_folder(build, tmp_path, monkeypatch):
    monkeypatch.setattr(build.host, "is_linux", lambda: False)
    internal = _layout(tmp_path, "")
    build.verify_bundled_executables()
    assert os.access(internal / "ollama" / "ollama", os.X_OK)


def test_a_missing_binary_fails_the_build(build, tmp_path, monkeypatch):
    monkeypatch.setattr(build.host, "is_linux", lambda: True)
    internal = _layout(tmp_path, "bin/")
    (internal / "ffmpeg" / "ffprobe").unlink()
    with pytest.raises(SystemExit, match="not there"):
        build.verify_bundled_executables()


def test_an_ffmpeg_that_will_not_run_fails_the_build(build, tmp_path, monkeypatch):
    monkeypatch.setattr(build.host, "is_linux", lambda: True)
    _layout(tmp_path, "bin/", ffmpeg_ok=False)
    with pytest.raises(SystemExit, match="will not run"):
        build.verify_bundled_executables()


def test_already_executable_files_are_left_alone(build, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build.host, "is_linux", lambda: True)
    internal = _layout(tmp_path, "bin/")
    for rel in ("ffmpeg/ffmpeg", "ffmpeg/ffprobe", "ollama/bin/ollama"):
        (internal / rel).chmod(0o755)
    build.verify_bundled_executables()
    assert "restoring" not in capsys.readouterr().out
    assert stat.S_IMODE((internal / "ffmpeg" / "ffmpeg").stat().st_mode) == 0o755
