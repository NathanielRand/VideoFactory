"""Download the Ollama runtime the installer ships.

Ollama runs the language model that scores and titles clips. It used to be the
creator's job: the setup wizard stopped and asked them to go to ollama.com,
download a second installer, run it, and come back. That is the single biggest
thing standing between "downloaded Video Factory" and "made a clip", and it is
the kind of errand people abandon.

So the app carries its own copy. This fetches the standalone build for this
OS -- a zip on Windows, a tgz on macOS (one universal binary), a tar.zst on
Linux, never the ollama.com installer -- and drops it into vendor/ollama/,
where core.binaries looks for it and the packaging step picks it up. The
standalone build matters: it needs no admin rights, edits no PATH, registers no
service, and leaves any Ollama the creator already installed completely alone.

    python scripts/fetch_ollama.py            # fetch if missing
    python scripts/fetch_ollama.py --force    # re-download

The binaries are NOT committed -- they're build inputs, fetched on the machine
that builds the installer.

Licensing note: Ollama is MIT, so shipping it unmodified is straightforward.
MIT still requires the copyright notice and licence text travel with it, so
vendor/ollama/README-OLLAMA.txt is written next to the binaries and the
installer ships it.
"""

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "ollama"

# Pinned so a build is reproducible and a surprise upstream change can't
# silently alter what ships. Bump deliberately -- a runtime upgrade can change
# GPU behaviour and model compatibility, which is not something to discover
# from a build that happened to run on a Tuesday.
OLLAMA_VERSION = "v0.32.6"
RELEASES_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
RELEASE_BASE = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}"

# asset name, archive kind, and where the executable lands under vendor/ollama/.
# Linux keeps Ollama's own bin/ + lib/ollama/ layout: the executable finds its
# GPU libraries relative to itself, so that layout has to survive packaging.
ASSETS = {
    "win32": ("ollama-windows-amd64.zip", "zip", "ollama.exe"),
    "darwin": ("ollama-darwin.tgz", "tar", "ollama"),
    "linux-x86_64": ("ollama-linux-amd64.tar.zst", "tar.zst", "bin/ollama"),
    "linux-aarch64": ("ollama-linux-arm64.tar.zst", "tar.zst", "bin/ollama"),
}

# Records which version is sitting in vendor/ollama/, so bumping the constant
# above re-downloads instead of silently keeping the old runtime.
STAMP = "VERSION.txt"


def target_key(plat: str | None = None, machine: str | None = None) -> str:
    """Which entry of ASSETS this machine uses, or exit saying what exists."""
    plat = plat or sys.platform
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if plat == "win32":
        key = "win32"
    elif plat == "darwin":
        key = "darwin"
    else:
        key = f"linux-{arch}"
    if key not in ASSETS:
        raise SystemExit(f"No Ollama build is configured for {plat}/{machine}. "
                         f"Supported: {', '.join(ASSETS)}")
    return key


def notice(asset: str) -> str:
    return f"""Ollama
------
This application bundles an unmodified Ollama runtime ({OLLAMA_VERSION},
{asset}).

Ollama is free software licensed under the MIT License. Video Factory starts
it as a separate program on a private port and does not link against it.

Source code and releases:
    https://github.com/ollama/ollama
    {RELEASE_BASE}/{asset}

---

MIT License

Copyright (c) Ollama

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def _latest_tag() -> str | None:
    """The newest published Ollama tag, for the error message only.

    A pinned URL that 404s means the release was retagged or withdrawn, and
    the useful thing to say is which version to pin instead -- not just that
    a download failed.
    """
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("tag_name")
    except Exception:
        return None


def _extract_within(archive: zipfile.ZipFile, dest: Path) -> None:
    """Unpack `archive` into `dest`, refusing anything that escapes it.

    ZipFile.extractall() writes wherever the entry names point, so a member
    called `..\\..\\Windows\\System32\\something.dll` lands there. This
    particular archive comes from a GitHub release rather than a stranger —
    but "the source is trusted" is the assumption that quietly stops holding
    the day a mirror, a proxy or a compromised token gets between the two, and
    checking costs nothing next to a 1.5 GB download.
    """
    root = dest.resolve()
    for member in archive.infolist():
        target = (root / member.filename).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"archive entry escapes the destination: {member.filename}")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _extract_member_within(t: tarfile.TarFile, member: tarfile.TarInfo, dest: Path) -> None:
    """Extract one tar member into `dest`, refusing anything that escapes it.

    The tar counterpart of _extract_within(). Checked by hand rather than with
    tarfile's "data" filter because that only exists from Python 3.11.4 and a
    build machine may have older; the filter is still applied where it exists.

    Resolving the target follows any symlink an EARLIER member created, so a
    link pointing outside cannot be used to redirect a later file through it.
    The macOS archive does carry symlinks (libggml.dylib -> libggml.0.dylib),
    so links must work, but only ones that stay inside.
    """
    root = dest.resolve()
    target = (root / member.name).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"archive entry escapes the destination: {member.name}")
    if member.issym():
        link = (target.parent / member.linkname).resolve()
    elif member.islnk():
        link = (root / member.linkname).resolve()
    else:
        link = None
    if link is not None and not link.is_relative_to(root):
        raise ValueError(f"archive link points outside the destination: {member.name}")
    if member.isdev():
        raise ValueError(f"archive contains a device file: {member.name}")
    member.mode &= 0o755  # no setuid/setgid/sticky, nothing group- or world-writable
    if hasattr(tarfile, "data_filter"):
        t.extract(member, dest, filter="data")
    else:
        t.extract(member, dest)


def _extract_tar_within(path: Path, dest: Path, zstd: bool) -> None:
    """Unpack a tar (optionally zstd-compressed) into `dest`, member by member."""
    if not zstd:
        with tarfile.open(path, "r:*") as t:
            for member in t:
                _extract_member_within(t, member, dest)
        return
    try:
        import zstandard
    except ImportError:
        raise SystemExit("Extracting .tar.zst needs the zstandard package: "
                         "pip install -r requirements-build.txt") from None
    with open(path, "rb") as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as t:
                for member in t:
                    _extract_member_within(t, member, dest)


def _installed_version() -> str | None:
    try:
        return (DEST / STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _expected_sha(asset: str) -> str | None:
    """`asset`'s SHA-256 from the release's own sha256sum.txt, if reachable.

    A mirror, a proxy or a bad download can all hand back the wrong bytes under
    the right name. None when the list cannot be fetched: the caller warns and
    carries on rather than making a flaky network fail every build.
    """
    try:
        with urllib.request.urlopen(f"{RELEASE_BASE}/sha256sum.txt", timeout=60) as r:
            for line in r.read().decode("utf-8", "replace").splitlines():
                digest, _, name = line.strip().partition(" ")
                if name.strip().lstrip("*") == asset:
                    return digest.lower()
    except Exception:
        pass
    return None


def _download_to(url: str, path: Path) -> str:
    """Stream `url` to `path` and return its SHA-256. Streaming, because the
    Linux asset is 1.4 GB and holding it in memory just to hash it is waste."""
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as r, open(path, "wb") as out:
        while chunk := r.read(1 << 20):
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def fetch(force: bool = False) -> int:
    asset, kind, exe_rel = ASSETS[target_key()]
    url = f"{RELEASE_BASE}/{asset}"
    exe = DEST / exe_rel

    if exe.exists() and _installed_version() == OLLAMA_VERSION and not force:
        total = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
        print(f"  present: {exe} ({OLLAMA_VERSION}, {total / 1e6:.0f} MB total)")
        print("Already vendored — use --force to re-download.")
        return 0

    if exe.exists():
        # A version bump, not a fresh fetch. The old runner libraries under
        # lib/ are version-matched to the executable, so leaving them in place
        # would mix two builds together.
        print(f"Replacing {_installed_version() or 'an unstamped build'} with {OLLAMA_VERSION}")
        shutil.rmtree(DEST, ignore_errors=True)

    DEST.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Ollama {OLLAMA_VERSION}")
    print(f"  {url}")
    with tempfile.TemporaryDirectory(dir=DEST.parent) as tmp:
        archive = Path(tmp) / asset
        try:
            got = _download_to(url, archive)
        except Exception as e:
            print(f"\nDownload failed: {type(e).__name__}: {e}")
            latest = _latest_tag()
            if latest and latest != OLLAMA_VERSION:
                print(f"The newest published release is {latest}.")
                print(f"If {OLLAMA_VERSION} was withdrawn, set OLLAMA_VERSION to {latest} in this file.")
            print("Or fetch it by hand and unpack it into:")
            print(f"  {DEST}")
            return 1
        print(f"  got {archive.stat().st_size / 1e6:.0f} MB")

        want = _expected_sha(asset)
        if want is None:
            print("  WARNING: could not read sha256sum.txt, so this download is unverified")
        elif want != got:
            print(f"\nSHA-256 mismatch for {asset}\n  expected {want}\n  got      {got}")
            shutil.rmtree(DEST, ignore_errors=True)
            return 1
        else:
            print("  sha256 verified against the release's sha256sum.txt")

        # Extract everything, not a chosen few files: the executable is useless
        # on its own. The GPU runners and libraries beside it are what make it
        # faster than CPU inference, and they must keep their layout.
        if kind == "zip":
            with zipfile.ZipFile(archive) as z:
                _extract_within(z, DEST)
        else:
            _extract_tar_within(archive, DEST, zstd=kind == "tar.zst")

    if not exe.exists():
        print(f"\n{asset} did not contain {exe_rel} — archive layout changed?")
        return 1
    if sys.platform != "win32":
        exe.chmod(0o755)

    (DEST / STAMP).write_text(OLLAMA_VERSION, encoding="utf-8")
    (DEST / "README-OLLAMA.txt").write_text(notice(asset), encoding="utf-8")
    total = sum(p.stat().st_size for p in DEST.rglob("*") if p.is_file())
    print(f"  extracted {total / 1e6:.0f} MB to {DEST}")
    print(f"  wrote {DEST / 'README-OLLAMA.txt'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    sys.exit(fetch(ap.parse_args().force))
