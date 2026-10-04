# Video Factory on Linux

x86_64 (aarch64 builds are configured for FFmpeg and Ollama but not packaged
or tested). Distributed as an **AppImage** and a **.deb**.

> **Status: written, not yet run on a desktop.** The backend's tests and the
> bundled-binary logic ran on Linux in a container, and the Electron
> configuration was validated against electron-builder's schema. The packaged
> app has not been launched on a Linux desktop yet.

## Installing

```sh
# .deb (Debian, Ubuntu)
sudo apt install ./VideoFactory-<version>-amd64.deb

# AppImage
chmod +x VideoFactory-<version>-x86_64.AppImage
./VideoFactory-<version>-x86_64.AppImage
```

**Ubuntu 24.04 and newer: the AppImage may not start**, because unprivileged
user namespaces are restricted and Electron's sandbox needs them. Either use the
`.deb` (it installs the sandbox helper setuid), or:

```sh
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

We do not ship the AppImage with `--no-sandbox`: that would turn off a security
boundary for everyone to fix it for some.

## GPU

- **NVIDIA:** used for tracking, transcription and NVENC encoding when the build
  carries CUDA PyTorch and your driver is recent enough. Otherwise everything
  falls back to the CPU and the Dashboard says why.
- **AMD / Intel:** the encoder tries AMF and QSV where their drivers exist, then
  libx264. **VAAPI is not used**: it needs `-vaapi_device` ahead of the input and
  an `hwupload` filter, and the encoder layer only supplies `-c:v`. PyTorch runs
  on the CPU.

## Where things live

- Library, settings, models: `$XDG_DATA_HOME/Video Factory`, which is
  `~/.local/share/Video Factory` by default.
- Connected-account credentials: a `0600` file under that folder. There is no
  Secret Service integration, because a typical headless install has none to use.

## Developing

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/bin/python -m pip install -r requirements.txt pytest ruff httpx2
sudo apt install ffmpeg
cd ui && corepack enable && pnpm install && pnpm run dev
```

The Docker image (see `DOCKER.md`) runs the engine and tests without any of it.
