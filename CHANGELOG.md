# Changelog

## Unreleased

- **Video Factory** name, logo and icon; `VIDEO_FACTORY_*` env vars; data under
  `%LOCALAPPDATA%\Video Factory`.
- Compilation mode: multi-source recipes, credit plates and fonts, render
  versions, portrait layout.
- One project renders a version per platform (9:16, 16:9, 4:5, 1:1).
- The end card is off by default. Feedback reports save locally.
- GPU transcription fix: `nvidia-cublas-cu12` for ctranslate2 on cu130 PyTorch.
- Dev mode runs the backend from the repo's `.venv`.
