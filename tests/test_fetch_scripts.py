"""The build-input fetchers: which archive each OS gets, and that unpacking one
can never write outside the vendor folder.

No network: archives are built in memory.
"""

import importlib.util
import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ffmpeg = _load("fetch_ffmpeg")
ollama = _load("fetch_ollama")


# ---- which build each machine gets -----------------------------------------


@pytest.mark.parametrize("plat,machine,key", [
    ("win32", "AMD64", "win32"),
    ("linux", "x86_64", "linux-x86_64"),
    ("linux", "aarch64", "linux-aarch64"),
    ("darwin", "arm64", "darwin-arm64"),
])
def test_ffmpeg_target(plat, machine, key):
    assert ffmpeg.target_key(plat, machine) == key


@pytest.mark.parametrize("plat,machine,key", [
    ("win32", "AMD64", "win32"),
    ("linux", "x86_64", "linux-x86_64"),
    ("linux", "arm64", "linux-aarch64"),
    ("darwin", "arm64", "darwin"),
])
def test_ollama_target(plat, machine, key):
    assert ollama.target_key(plat, machine) == key


def test_an_unsupported_machine_says_what_is_supported():
    with pytest.raises(SystemExit, match="Supported"):
        ffmpeg.target_key("darwin", "x86_64")  # Intel Macs are not a target
    with pytest.raises(SystemExit, match="Supported"):
        ollama.target_key("linux", "riscv64")


def test_the_mac_ffmpeg_download_is_pinned_by_hash():
    spec = ffmpeg.SPECS["darwin-arm64"]
    for url in spec["urls"]:
        assert url.rsplit("/", 1)[-1] in spec["sha256"], "no third-party binary ships unpinned"


# ---- ffmpeg extraction -------------------------------------------------------


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_zip_extraction_skips_mac_resource_fork_litter(tmp_path):
    blob = _zip({"ffmpeg": b"bin", "__MACOSX/._ffmpeg": b"junk"})
    assert ffmpeg._extract("zip", blob, ("ffmpeg",), tmp_path) == 1
    assert (tmp_path / "ffmpeg").read_bytes() == b"bin"
    assert not (tmp_path / "__MACOSX").exists()


def test_tar_extraction_takes_only_the_named_binaries_from_bin(tmp_path):
    blob = _tar({
        "ffmpeg-n8.1/bin/ffmpeg": b"ff", "ffmpeg-n8.1/bin/ffprobe": b"fp",
        "ffmpeg-n8.1/bin/ffplay": b"no", "ffmpeg-n8.1/doc/ffmpeg": b"docs-not-a-binary",
    })
    assert ffmpeg._extract("tar", blob, ("ffmpeg", "ffprobe"), tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ffmpeg", "ffprobe"]
    assert (tmp_path / "ffmpeg").read_bytes() == b"ff"


def test_a_hostile_member_name_cannot_pick_where_it_lands(tmp_path):
    inner = tmp_path / "vendor"
    inner.mkdir()
    blob = _zip({"../../evil/ffmpeg": b"x"})
    ffmpeg._extract("zip", blob, ("ffmpeg",), inner)
    assert (inner / "ffmpeg").exists()  # base name only, inside the folder
    assert not (tmp_path.parent / "evil").exists()


def test_a_changed_download_fails_the_pin():
    url = "https://example.test/ffmpeg71arm.zip"
    with pytest.raises(ValueError, match="pinned SHA-256"):
        ffmpeg._check_sha(url, b"tampered", {"ffmpeg71arm.zip": "0" * 64})
    ffmpeg._check_sha(url, b"anything", {})  # unpinned files are not checked


# ---- ollama extraction -------------------------------------------------------


def _tar_with(entries, mode="w:gz"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for name, kind, payload in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(payload)
                info.mode = 0o755
                t.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                t.addfile(info)
    return buf.getvalue()


def test_a_tar_cannot_escape_the_destination(tmp_path):
    dest = tmp_path / "vendor"
    dest.mkdir()
    archive = tmp_path / "a.tgz"
    archive.write_bytes(_tar_with([("../escaped", "file", b"x")]))
    with pytest.raises((ValueError, tarfile.TarError)):
        ollama._extract_tar_within(archive, dest, zstd=False)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_links_inside_the_archive_work_and_links_outside_are_refused(tmp_path):
    dest = tmp_path / "vendor"
    dest.mkdir()
    ok = tmp_path / "ok.tgz"
    ok.write_bytes(_tar_with([("libggml.0.dylib", "file", b"lib"),
                              ("libggml.dylib", "symlink", "libggml.0.dylib")]))
    ollama._extract_tar_within(ok, dest, zstd=False)
    assert (dest / "libggml.dylib").is_symlink()
    assert (dest / "libggml.dylib").read_bytes() == b"lib"

    bad = tmp_path / "bad.tgz"
    bad.write_bytes(_tar_with([("sneaky", "symlink", "../../etc/passwd")]))
    with pytest.raises((ValueError, tarfile.TarError)):
        ollama._extract_tar_within(bad, tmp_path / "vendor2", zstd=False)


def test_zstd_archives_unpack_and_keep_the_executable_bit(tmp_path):
    zstandard = pytest.importorskip("zstandard")
    raw = _tar_with([("bin/ollama", "file", b"exe"), ("lib/ollama/libx.so", "file", b"lib")], mode="w")
    archive = tmp_path / "o.tar.zst"
    archive.write_bytes(zstandard.ZstdCompressor().compress(raw))
    dest = tmp_path / "vendor"
    dest.mkdir()
    ollama._extract_tar_within(archive, dest, zstd=True)
    assert (dest / "bin" / "ollama").read_bytes() == b"exe"
    assert (dest / "lib" / "ollama" / "libx.so").exists()
    if os.name != "nt":
        assert os.access(dest / "bin" / "ollama", os.X_OK)
