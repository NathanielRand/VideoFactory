# Changelog

## Unreleased

- **Clip generation**:
  - Game profiles (`genres/profiles.py`, first one: Wardogs). Footage is framed
    on the crosshair instead of on the soldiers the tracker finds, and the
    on-screen kill/cash popup is read as a selection signal. A facecam layout
    still wins.
  - Dead air is cut from new clips (nobody speaking, nothing on screen, nothing
    loud) and the sections are joined with a 0.2 s crossfade. The cuts are stored
    as an ordinary edit, so they show in the editor and can be undone.
    `clips.auto_tighten: false` turns it off.
  - Flagging a framing fault no longer produces a letterbox. Each Re-cut tries a
    different real framing and remembers which were tried; letterbox comes last,
    or when "Needed the whole frame" is flagged.
- **Storage management**: Settings > Library location shows which drive the
  library is on (label, filesystem, removable/network), its free space, what
  each folder costs, and every other drive with room left. The library can be
  moved to another drive (copy, rewrite stored paths, verify, switch on restart;
  the old copy is kept), started fresh, or pointed at an existing library. An
  unplugged drive is reported instead of silently creating an empty library.
  API: `GET /storage/location`, `/storage/volumes`, `/storage/move`;
  `POST /storage/check`, `/storage/location`, `/storage/reset`.
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
