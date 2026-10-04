# Video Factory on macOS

**Apple Silicon only, macOS 14 (Sonoma) or newer.** Intel Macs are not
supported: PyTorch no longer publishes Intel Mac wheels, and OpenCV's current
wheels need macOS 14 (both checked with `pip download --platform`).

> **Status: written, not yet run on a real Mac.** The logic is covered by tests
> that fake Metal and the platform, and the bundled FFmpeg and Ollama archives
> were unpacked and checked on Linux. Nothing here has been seen working on
> Apple hardware yet. If you have one, a report of what happened is the most
> useful contribution there is.

## Installing

The build is **not signed with an Apple Developer ID and not notarized**, so
macOS will not open it on the first try. It is ad-hoc signed (an arm64 app with
no signature at all would be reported as "damaged" with no way past it).

1. Open the `.dmg` and drag Video Factory to Applications.
2. Try to open it. macOS says it could not verify the app.
3. **System Settings → Privacy & Security**, scroll to the message about Video
   Factory, and press **Open Anyway**.

Or, from a terminal, clear the download quarantine flag once:

```sh
xattr -dr com.apple.quarantine "/Applications/Video Factory.app"
```

Notarization needs a paid Apple Developer account; when there is one the build
config says how to switch (`electron-builder.yml`, `mac:`).

## What uses the Apple GPU, and what does not

| Stage | Runs on |
|---|---|
| Subject tracking (YOLO) | Apple GPU via Metal (MPS), with a probe at load and CPU fallback |
| Active-speaker detection (TalkNet) | CPU. Its operations have not been validated on Metal |
| Speech recognition (Whisper) | CPU. CTranslate2 has no Metal backend; the `small` model is the default |
| Video encoding | VideoToolbox hardware H.264, CPU libx264 if the probe fails |
| Language model (Ollama) | Metal, through Ollama itself |

Expect transcription to be the slowest stage compared with an NVIDIA machine.

## Where things live

- Library, settings, models: `~/Library/Application Support/Video Factory`
- Connected-account credentials: the login Keychain (item service "Video
  Factory"), not a file.

## Developing on a Mac

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install torch torchvision
.venv/bin/python -m pip install -r requirements.txt pytest ruff httpx2
brew install ffmpeg     # or let the dev build find one on PATH
ollama pull gemma:7b

cd ui && corepack enable && pnpm install && pnpm run dev
```

Build the app with `python scripts/build_installer.py` (or the "Build desktop"
workflow). It downloads a pinned arm64 FFmpeg and verifies its SHA-256 before
using it.

## Known limits

- The in-app updater is not wired up for macOS: there is no update feed yet, and
  macOS auto-update requires a signed app.
- Switching processing from "gentle" back to "full speed" does not raise
  priority again: an unprivileged process cannot lower its own nice value.
