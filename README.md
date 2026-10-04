# Video Factory

<img src="docs/brand/logo.png" alt="" width="120" align="right">

**Source to post, in one flow.** Grab videos from a source, clip them or build a
compilation around them, add your branding and creator credits, render a version for
every platform, then post now or schedule.

Everything runs on your own PC: local transcription (Whisper), a local LLM (Ollama),
GPU tracking and rendering (YOLOv8, TalkNet, FFmpeg with NVENC/AMF/QSV).

## Status

| Area | State |
|---|---|
| Import from YouTube / Twitch / Kick / local files | ✅ Done |
| AI clip detection, speaker-tracked 9:16, captions, editor | ✅ Done |
| Publish to YouTube; TikTok / IG / FB / X via Upload-Post or WoopSocial | ✅ Done |
| Compilation mode (multi-source, credits, transitions, effects) | 🚧 Phase 2 |
| One project → many platform formats (9:16, 16:9, 1:1, 4:5) | 🚧 Phase 3 |
| Unified per-platform post form, direct TikTok / IG APIs | 🚧 Phase 4 |

The roadmap is in [docs/plan.md](docs/plan.md). How the code fits together is in
[docs/architecture.md](docs/architecture.md), the map of the codebase, and
[ARCHITECTURE.md](ARCHITECTURE.md), the engine deep-dive.

## Platforms

| | Status |
|---|---|
| Windows 10/11 + NVIDIA GPU | The reference platform. Installer builds and is tested. |
| macOS, Apple Silicon, macOS 14+ | Ported, **not yet run on a Mac**. [docs/MACOS.md](docs/MACOS.md) |
| Linux x86_64 | Ported, **not yet run on a desktop**. [docs/LINUX.md](docs/LINUX.md) |

Intel Macs are not supported. What uses the GPU differs by platform:

| | Windows | macOS (Apple Silicon) | Linux |
|---|---|---|---|
| Subject tracking | CUDA | Metal | CUDA, else CPU |
| Speaker detection | CUDA | CPU | CUDA, else CPU |
| Speech recognition | CUDA | CPU | CUDA, else CPU |
| Video encoding | NVENC, AMF, QSV | VideoToolbox | NVENC, AMF, QSV |
| Everything falls back to | CPU | CPU | CPU |

## Setup

You need the same four things on every OS: **Python 3.11**, **Node 22+** (pnpm 11
comes with `corepack enable`), **FFmpeg**, and **[Ollama](https://ollama.com)** for the
local language model. A GPU is optional: everything runs on the CPU, slowly.

The commands below run Video Factory **from source**, which is how to try it today.
Packaged apps (a Windows installer, a macOS `.dmg`, a Linux `.AppImage` or `.deb`)
come from `python scripts/build_installer.py` on that OS, or from the **Build
desktop** workflow on GitHub. See [Building an installable app](#building-an-installable-app).

### Windows

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python -m pip install -r requirements.txt ruff pytest httpx2
ollama pull gemma:7b          # the model the scoring is tuned on (8 GB VRAM)

cd ui
corepack enable
pnpm install
pnpm run dev                   # Electron + the backend (uses ..\.venv automatically)
```

FFmpeg must be on `PATH` (`winget install Gyan.FFmpeg`). The `cu130` PyTorch build
is for NVIDIA GPUs; with no NVIDIA GPU use the plain `pip install torch torchvision`.

### macOS (Apple Silicon, macOS 14+)

```sh
brew install python@3.11 node ffmpeg ollama
python3.11 -m venv .venv
.venv/bin/python -m pip install torch torchvision
.venv/bin/python -m pip install -r requirements.txt ruff pytest httpx2
ollama serve &                 # or open the Ollama app
ollama pull gemma:7b

cd ui
corepack enable
pnpm install
pnpm run dev
```

Tracking uses the Apple GPU (Metal). Speaker detection and speech recognition run
on the CPU, so transcription is the slow stage. Details: [docs/MACOS.md](docs/MACOS.md).

### Linux (x86_64)

```sh
# Debian / Ubuntu; use your distribution's equivalents elsewhere
sudo apt install python3.11 python3.11-venv ffmpeg libgl1
curl -fsSL https://ollama.com/install.sh | sh        # Node 22: https://nodejs.org or nvm

python3.11 -m venv .venv
# NVIDIA GPU: the default PyPI torch includes CUDA.
.venv/bin/python -m pip install torch torchvision
# No NVIDIA GPU (much smaller download):
#   .venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt ruff pytest httpx2
ollama pull gemma:7b

cd ui
corepack enable
pnpm install
pnpm run dev
```

On Linux the hardware encoder is NVENC, AMF or QSV where the driver exists; VAAPI is
not used. Details and the Ubuntu 24.04 AppImage quirk: [docs/LINUX.md](docs/LINUX.md).

### Running it

- The processing queue **starts paused** on purpose. Press Start once you've added videos.
- Backend only: `python main.py serve` (with the venv's Python), then
  `GET http://127.0.0.1:8765/health/preflight`.
- Tests: `python -m pytest -m "not slow"` (venv's Python). UI: `cd ui && pnpm run typecheck`.
- Settings: `config/settings.yaml`. Every LLM prompt: `config/prompts/`.
- Local API reference: [docs/API.md](docs/API.md).
- Your library, settings and downloaded models live in a per-user folder:
  `%LOCALAPPDATA%\Video Factory` (Windows), `~/Library/Application Support/Video Factory`
  (macOS), `$XDG_DATA_HOME/Video Factory` or `~/.local/share/Video Factory` (Linux).
  A source checkout keeps its data in the repo's `data/` instead.

### Building an installable app

Each OS builds its own, because the frozen Python engine cannot be cross-compiled.

```sh
pip install -r requirements-build.txt
python scripts/build_installer.py        # fetches FFmpeg, Ollama, Whisper weights; freezes; packages
```

Output lands in `release/`. The same script runs on all three systems and picks the
right target: NSIS installer (Windows), `.dmg` and `.zip` (macOS), `.AppImage` and
`.deb` (Linux). On Windows the script stops if PyTorch is the CPU-only build, because
the installer would ship with no GPU support. How releases are published:
[docs/RELEASING.md](docs/RELEASING.md).

**Installing a macOS build.** It is not signed with an Apple Developer ID, so macOS
blocks it at first: open System Settings → Privacy & Security and press **Open
Anyway**, or run `xattr -dr com.apple.quarantine "/Applications/Video Factory.app"`.

**Installing a Linux build.** `sudo apt install ./VideoFactory-<version>-amd64.deb`, or
`chmod +x` the AppImage and run it. On Ubuntu 24.04+ prefer the `.deb`.

**Updates.** The Windows installer and a Linux AppImage can update in place once an
update feed is configured (none is yet). A macOS build or a Linux `.deb` is updated by
installing the newer build over the old one; your library and settings are kept.

## License

AGPL-3.0-or-later: see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Using the app puts no obligations on you. If you distribute a modified version or run
it as a network service for others, you must offer them its source under the same
license.
