"""Build the installer for this OS (Windows, macOS or Linux), end to end.

    python scripts/build_installer.py

Runs the whole chain in order and stops at the first failure with an
explanation rather than a stack trace:

    1. check the build tools are present
    2. fetch FFmpeg, Ollama and the Whisper weights if they aren't vendored yet
    3. freeze the Python engine to build/dist/backend/api (api.exe on Windows)
    4. build the Electron front end
    5. wrap both with electron-builder -> release/
       (NSIS on Windows, a dmg on macOS, an AppImage and deb on Linux)

On Windows the result is release/VideoFactory-Setup-<version>.exe, which installs the app,
the Python engine, FFmpeg, the Ollama runtime, and the YOLO, TalkNet and
Whisper weights together. A creator installs nothing else: the one remaining
download is the language model, which the app pulls itself on first launch
behind a progress bar, because it is 5 GB and the right one depends on their
GPU.

Flags:
    --skip-backend    reuse the frozen backend from a previous run
    --skip-ui         reuse the previously built renderer
    --backend-only    stop after freezing the engine (fast iteration)
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import host  # noqa: E402  (stdlib only, so safe before the build deps exist)

UI = ROOT / "ui"
BACKEND_OUT = ROOT / "build" / "dist" / "backend"
API_EXE = host.exe_name("api")
# electron-builder's flag for the OS this machine builds for. Installers are
# built natively on each OS: PyInstaller cannot cross-compile.
BUILDER_PLATFORM = "--win" if host.is_windows() else "--mac" if host.is_mac() else "--linux"


def say(step: str, message: str) -> None:
    print(f"\n=== {step}: {message}", flush=True)


def run(cmd: list[str], cwd: Path, what: str) -> None:
    # pnpm, npm and npx are .CMD batch files on Windows, and CreateProcess cannot
    # run those by bare name — it needs the resolved path. Resolving here
    # (rather than using shell=True) keeps arguments from being re-parsed by
    # cmd.exe, which matters for paths with spaces.
    exe = shutil.which(str(cmd[0]))
    if exe is None:
        sys.exit(f"\n{what} failed: '{cmd[0]}' is not on PATH.")
    resolved = [exe, *[str(c) for c in cmd[1:]]]

    print(f"    $ {' '.join(resolved)}", flush=True)
    started = time.time()
    try:
        result = subprocess.run(resolved, cwd=cwd, shell=False)
    except OSError as e:
        sys.exit(f"\n{what} failed to start: {e}")
    if result.returncode != 0:
        sys.exit(f"\n{what} failed (exit {result.returncode}). Nothing was packaged.")
    print(f"    done in {time.time() - started:.0f}s", flush=True)


def check_tools(skip_ui: bool) -> None:
    say("1/5", "checking build tools")
    missing = []

    try:
        import PyInstaller  # noqa: F401

        print("    PyInstaller: ok")
    except ImportError:
        missing.append("PyInstaller — run: pip install -r requirements-build.txt")

    # Which CUDA architectures ship is decided by the index this machine
    # installed torch from, and it cannot be expressed in requirements.txt: the
    # Windows PyPI wheel is CPU-only. Get it wrong and the build succeeds, the
    # installer works, and it silently has no GPU support at all — found by
    # users rather than here, two and a half hours too late.
    try:
        import torch

        archs = torch.cuda.get_arch_list()
        if host.is_mac():
            # No CUDA on a Mac: what matters is that Metal is in the build.
            if not torch.backends.mps.is_built():
                missing.append("a PyTorch build with Metal (MPS) support - "
                               "run: pip install torch torchvision")
            else:
                print(f"    PyTorch: {torch.__version__} (Metal)")
        elif not archs and not host.is_windows():
            # Linux: a CPU-only engine is a legitimate, much smaller build.
            print(f"    PyTorch: {torch.__version__} (CPU only - the Linux build "
                  "will run without CUDA)")
        elif not archs:
            missing.append(
                "CUDA PyTorch — this environment has the CPU-only build, so "
                "the installer would ship with no GPU support. Run: pip "
                "install torch torchvision --index-url "
                "https://download.pytorch.org/whl/cu130"
            )
        else:
            print(f"    PyTorch: {torch.__version__} ({archs[0]} to {archs[-1]})")
            if host.is_windows() and "sm_120" not in archs:
                print("    WARNING: built without sm_120 — RTX 50-series cards "
                      "will fall back to CPU. Install from the cu130 index to "
                      "include them.")
    except ImportError:
        missing.append("PyTorch — run: pip install -r requirements.txt")

    if not skip_ui:
        npm = shutil.which("npm")
        print(f"    npm: {npm or 'NOT FOUND'}")
        if not npm:
            missing.append("npm — install Node.js 18+ from https://nodejs.org")

    if missing:
        sys.exit("\nMissing build tools:\n  - " + "\n  - ".join(missing))


def _vendored_size(folder: Path) -> float:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1e6


def ensure_vendored() -> None:
    """Fetch everything the installer carries but the repo doesn't store.

    These are the difference between a one-click install and a scavenger hunt.
    Each fetch script is idempotent and prints what it already has, so a
    rebuild costs a few seconds rather than re-downloading gigabytes.
    """
    say("2/5", "checking bundled dependencies")

    # (label, marker that proves it is already there, fetch script)
    wanted = [
        ("FFmpeg", ROOT / "vendor" / "ffmpeg" / host.exe_name("ffprobe"), "fetch_ffmpeg.py"),
        ("Ollama", ROOT / "vendor" / "ollama" / ("bin" if host.is_linux() else ".")
         / host.exe_name("ollama"), "fetch_ollama.py"),
        ("Whisper weights", ROOT / "vendor" / "whisper", "fetch_whisper.py"),
    ]
    for label, marker, script in wanted:
        if marker.exists():
            folder = marker if marker.is_dir() else marker.parent
            if label == "Ollama" and folder.name == "bin":
                folder = folder.parent
            print(f"    {label}: present ({_vendored_size(folder):.0f} MB)")
            continue
        print(f"    {label}: not vendored yet — fetching")
        run([sys.executable, str(ROOT / "scripts" / script)], ROOT, f"{label} download")


def freeze_backend() -> None:
    say("3/5", "freezing the Python engine (several minutes, PyTorch is large)")
    run(
        [sys.executable, "-m", "PyInstaller", str(ROOT / "video-factory.spec"),
         "--noconfirm", "--distpath", str(ROOT / "build" / "dist"),
         "--workpath", str(ROOT / "build" / "work"), "--log-level", "WARN"],
        ROOT,
        "Freezing the backend",
    )
    exe = BACKEND_OUT / API_EXE
    if not exe.exists():
        sys.exit(f"\nExpected {exe} but it wasn't produced.")
    total = sum(f.stat().st_size for f in BACKEND_OUT.rglob("*") if f.is_file())
    print(f"    backend: {exe} ({total / 1e9:.2f} GB unpacked)")


def smoke_test_backend() -> None:
    """A frozen build that can't import its own dependencies is the classic
    PyInstaller failure, and it only shows up at runtime. Catch it here
    rather than in an installer someone already downloaded."""
    say("3b/5", "smoke-testing the frozen engine")
    exe = BACKEND_OUT / API_EXE
    result = subprocess.run([str(exe), "status"], capture_output=True, text=True,
                            timeout=300, cwd=ROOT)
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        print(output[-2000:])
        sys.exit("\nThe frozen engine failed to run. Fix the spec before packaging.")
    print(f"    engine runs (exit 0, {len(output)} bytes of output)")


def build_ui() -> None:
    say("4/5", "building the desktop front end")
    if not (UI / "node_modules").exists():
        run(["pnpm", "install", "--frozen-lockfile"], UI, "pnpm install")
    run(["pnpm", "run", "build"], UI, "Renderer build")


def _refresh_sandbox_test(release: Path) -> None:
    """Put the setup that was just built where scripts/test-install.wsb maps.

    The folder is emptied first, never added to, and that is deliberate twice
    over:

    * A leftover setup from the previous version means the sandbox installs the
      OLD build. That happened, cost a full install cycle, and looked like a
      broken release rather than a stale file.
    * The payload must stay out. An installer that finds
      video-factory-<v>-x64.nsis.7z sitting next to it has no reason to download
      one, and the download is the thing the sandbox run exists to prove.

    So: exactly one file in here, always the current one.
    """
    setups = list(release.glob("nsis-web/VideoFactory-Web-Setup-*.exe"))
    if not setups:
        return  # nsis-web target disabled; nothing to stage

    dest = release / "sandbox-test"
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)

    newest = max(setups, key=lambda p: p.stat().st_mtime)
    shutil.copy2(newest, dest / newest.name)
    print(f"\n    Sandbox test ready: {newest.name} -> release/sandbox-test/")


def package_installer() -> None:
    say("5/5", "packaging the installer")
    run(["npx", "electron-builder", BUILDER_PLATFORM, "--config", "electron-builder.yml"],
        UI, "electron-builder")
    release = ROOT / "release"
    if not release.exists():
        sys.exit("\nelectron-builder reported success but produced no release/ folder")

    # The web setup is small on purpose: it fetches the .7z payload from the
    # url in electron-builder.yml's publish block at install time. Both have to
    # be published together or the installer has nothing to download.
    artifacts = [p for p in sorted(release.iterdir())
                 if p.is_file() and p.suffix.lower() in (".exe", ".zip", ".7z", ".dmg",
                                                          ".appimage", ".deb")
                 and not p.name.startswith("__")]
    if not artifacts:
        sys.exit("\nelectron-builder reported success but produced no installer")

    # A GitHub release asset is capped at 2 GiB, and both large artifacts are
    # well past it. Saying which file goes where — and flagging anything that
    # would simply be rejected — is more use than a list of sizes.
    github_cap = 2 * 1024**3

    print()
    for path in artifacts:
        size = path.stat().st_size
        unit = f"{size / 1e9:.2f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
        where = "GitHub release" if size <= github_cap else "Hugging Face (over GitHub's 2 GiB cap)"
        print(f"    ARTIFACT: {path.name}  ({unit})  ->  {where}")
    if host.is_windows():
        _refresh_sandbox_test(release)

    print(
        "\n    Publishing (see docs/RELEASING.md):\n"
        "      Hugging Face   the .7z payload, the .zip, the Web Setup .exe,\n"
        "                     and latest.yml LAST — it is the trigger, and an\n"
        "                     install that reads it starts downloading at once.\n"
        "      GitHub release the Web Setup .exe and the notes. Nothing else\n"
        "                     fits; the payload upload is rejected outright.\n"
        "    The .zip is the offline alternative: unzip and run Video Factory.exe."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-backend", action="store_true",
                    help="reuse the frozen backend from a previous run")
    ap.add_argument("--skip-ui", action="store_true",
                    help="reuse the previously built renderer")
    ap.add_argument("--backend-only", action="store_true",
                    help="stop after freezing the engine")
    args = ap.parse_args()

    started = time.time()
    check_tools(args.skip_ui)
    ensure_vendored()

    if args.skip_backend:
        print("\n=== 3/5: skipped (reusing existing backend)")
        if not (BACKEND_OUT / API_EXE).exists():
            sys.exit("--skip-backend given but no frozen backend exists yet.")
    else:
        freeze_backend()
        smoke_test_backend()

    if args.backend_only:
        print(f"\nBackend only — stopped after freezing. {time.time() - started:.0f}s")
        return

    if args.skip_ui:
        print("\n=== 4/5: skipped (reusing existing renderer build)")
    else:
        build_ui()

    package_installer()
    print(f"\nBuild finished in {(time.time() - started) / 60:.1f} minutes.")


if __name__ == "__main__":
    main()
