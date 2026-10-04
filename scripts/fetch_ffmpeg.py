"""Download the FFmpeg binaries the installer ships.

Creators don't have FFmpeg and won't install it, so the packaged app carries
its own. This fetches a static build for the machine it runs on and drops
ffmpeg and ffprobe into vendor/ffmpeg/, where core.binaries looks for them and
the packaging step picks them up.

    python scripts/fetch_ffmpeg.py            # fetch if missing
    python scripts/fetch_ffmpeg.py --force    # re-download

Where each platform's build comes from:

    Windows        gyan.dev release-essentials (zip)
    Linux          BtbN FFmpeg-Builds, GPL, pinned to a release branch (tar.xz)
    macOS arm64    osxexperts.net static build (two zips). There is no official
                   or well-known Apple Silicon static build, so these are
                   pinned by SHA-256: a different file under the same URL makes
                   the build fail instead of shipping. Re-pin deliberately.

The binaries are NOT committed — they're build inputs, fetched on the
machine that builds the installer.

Licensing note: these are GPL builds, because CPU encoding uses libx264 which
is GPL. Video Factory only runs FFmpeg as a separate process and does not link
against it, so shipping the unmodified binary alongside the app is mere
aggregation — but the GPL still requires that users can get FFmpeg's source.
vendor/ffmpeg/README-FFMPEG.txt is written next to the binaries with that
offer, and the installer ships it.
"""

import argparse
import hashlib
import io
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "ffmpeg"
SOURCE_URL = "https://www.ffmpeg.org/download.html"

# Pinned so a build is reproducible and a surprise upstream change can't
# silently alter what ships. Bump deliberately.
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest"
_OSXEXPERTS = "https://www.osxexperts.net"

SPECS: dict[str, dict] = {
    "win32": {
        "name": "gyan.dev ffmpeg-release-essentials (GPL)",
        "kind": "zip",
        "urls": ["https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"],
        "sha256": {},
        "wanted": ("ffmpeg.exe", "ffprobe.exe"),
    },
    "linux-x86_64": {
        "name": "BtbN FFmpeg-Builds n8.1 linux64-gpl (GPL)",
        "kind": "tar",
        "urls": [f"{_BTBN}/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"],
        "sha256": {},
        "wanted": ("ffmpeg", "ffprobe"),
    },
    "linux-aarch64": {
        "name": "BtbN FFmpeg-Builds n8.1 linuxarm64-gpl (GPL)",
        "kind": "tar",
        "urls": [f"{_BTBN}/ffmpeg-n8.1-latest-linuxarm64-gpl-8.1.tar.xz"],
        "sha256": {},
        "wanted": ("ffmpeg", "ffprobe"),
    },
    "darwin-arm64": {
        "name": "osxexperts.net ffmpeg 7.1 arm64 static (GPL)",
        "kind": "zip",
        "urls": [f"{_OSXEXPERTS}/ffmpeg71arm.zip", f"{_OSXEXPERTS}/ffprobe71arm.zip"],
        "sha256": {
            "ffmpeg71arm.zip": "0878f3313311c2c1b2c818e7c955c0bd828c97b357fa86211b42a5c36d01e36f",
            "ffprobe71arm.zip": "156a2c4da546e7d86877dd204df026eeda79aee8a80af8f04cd00f9b02687aa0",
        },
        "wanted": ("ffmpeg", "ffprobe"),
    },
}


def target_key(plat: str | None = None, machine: str | None = None) -> str:
    """Which entry of SPECS this machine uses, or raise with what is supported."""
    plat = plat or sys.platform
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}.get(machine, machine)
    if plat == "win32":
        key = "win32"
    elif plat == "darwin":
        key = f"darwin-{'arm64' if arch in ('arm64', 'aarch64') else arch}"
    elif plat.startswith("linux"):
        key = f"linux-{arch}"
    else:
        key = plat
    if key not in SPECS:
        raise SystemExit(f"No FFmpeg build is configured for {plat}/{machine}. "
                         f"Supported: {', '.join(SPECS)}")
    return key


def notice(spec: dict) -> str:
    urls = "\n    ".join([SOURCE_URL, *spec["urls"]])
    return f"""FFmpeg
------
This application bundles unmodified FFmpeg binaries ({spec['name']}).

FFmpeg is free software licensed under the GNU General Public License
version 3 or later, because this build includes GPL components (libx264).
Video Factory invokes FFmpeg as a separate program and does not link
against it.

The complete corresponding source code for FFmpeg is available from:
    {urls}

FFmpeg is a trademark of Fabrice Bellard.
"""


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()


def _check_sha(url: str, blob: bytes, expected: dict[str, str]) -> None:
    want = expected.get(url.rsplit("/", 1)[-1])
    if want is None:
        return
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise ValueError(
            f"{url} does not match the pinned SHA-256.\n  expected {want}\n  got      {got}\n"
            "The file changed upstream. Inspect it before re-pinning in this script."
        )


def _extract(kind: str, blob: bytes, wanted: tuple[str, ...], dest: Path) -> int:
    """Write each wanted file from the archive into `dest` by its base name.

    Matching on the base name alone means an entry can never choose where it
    lands, so a hostile archive cannot write outside `dest`. Entries under
    __MACOSX/ (resource-fork litter in Mac zips) are skipped.
    """
    found = 0
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for member in z.namelist():
                if member.startswith("__MACOSX/") or member.endswith("/"):
                    continue
                name = member.rsplit("/", 1)[-1]
                if name in wanted:
                    with z.open(member) as src, open(dest / name, "wb") as out:
                        shutil.copyfileobj(src, out)
                    found += 1
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as t:
            for member in t:
                if not member.isfile():
                    continue
                name = member.name.rsplit("/", 1)[-1]
                # BtbN archives hold bin/ffmpeg and also, say, bin/ffplay;
                # take only the named binaries from bin/.
                if name in wanted and "/bin/" in f"/{member.name}":
                    with t.extractfile(member) as src, open(dest / name, "wb") as out:
                        shutil.copyfileobj(src, out)
                    found += 1
    for name in wanted:
        path = dest / name
        if path.exists():
            path.chmod(0o755)  # a no-op on Windows; required elsewhere
    return found


def fetch(force: bool = False) -> int:
    key = target_key()
    spec = SPECS[key]
    wanted = spec["wanted"]

    DEST.mkdir(parents=True, exist_ok=True)
    have = [n for n in wanted if (DEST / n).exists()]
    if len(have) == len(wanted) and not force:
        for n in wanted:
            print(f"  present: {DEST / n} ({(DEST / n).stat().st_size / 1e6:.0f} MB)")
        print("Already vendored — use --force to re-download.")
        return 0

    print(f"Downloading {spec['name']}")
    found = 0
    for url in spec["urls"]:
        print(f"  {url}")
        try:
            blob = _download(url)
        except Exception as e:
            print(f"\nDownload failed: {type(e).__name__}: {e}")
            print(f"Fetch it manually and put {' + '.join(wanted)} in:")
            print(f"  {DEST}")
            return 1
        print(f"  got {len(blob) / 1e6:.0f} MB")
        try:
            _check_sha(url, blob, spec["sha256"])
        except ValueError as e:
            print(f"\n{e}")
            return 1
        found += _extract(spec["kind"], blob, wanted, DEST)

    for name in wanted:
        if (DEST / name).exists():
            print(f"  extracted {name} ({(DEST / name).stat().st_size / 1e6:.0f} MB)")

    if found != len(wanted):
        print(f"\nExpected {len(wanted)} binaries, found {found} — archive layout changed?")
        return 1

    (DEST / "README-FFMPEG.txt").write_text(notice(spec), encoding="utf-8")
    print(f"  wrote {DEST / 'README-FFMPEG.txt'}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    sys.exit(fetch(ap.parse_args().force))
