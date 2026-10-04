# Changelog

## Unreleased

- **macOS and Linux port** (untested on real Mac/Linux desktops): per-OS data
  folders, Keychain credential storage, Metal tracking, VideoToolbox encoding,
  CPU Whisper on Mac, per-OS FFmpeg and Ollama bundling, dmg / AppImage / deb
  packaging, a cross-platform portability CI job and a "Build desktop" workflow.
  See docs/MACOS.md and docs/LINUX.md.

- **Video Factory** name, logo and icon; `VIDEO_FACTORY_*` env vars; data under
  `%LOCALAPPDATA%\Video Factory`.
- Compilation mode: multi-source recipes, credit plates and fonts, render
  versions, portrait layout.
- One project renders a version per platform (9:16, 16:9, 4:5, 1:1).
- The end card is off by default. Feedback reports save locally.
- GPU transcription fix: `nvidia-cublas-cu12` for ctranslate2 on cu130 PyTorch.
- Dev mode runs the backend from the repo's `.venv`.
