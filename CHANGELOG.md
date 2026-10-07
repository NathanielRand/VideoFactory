# Changelog

## Unreleased

- **Monetization-safe text** (`publish/compliance.py`, settings: `compliance:` in
  `config/settings.yaml`). Hashtag spam, hashtags in titles, keyword stuffing and
  machine-sounding wording can disqualify a video or channel from the YouTube
  Partner Program. One linter now holds every title, description and tag list to
  the same limits, and the limits are settings with conservative defaults (YouTube
  publishes no numbers): at most 3 hashtags, none in the title, at most 8 hidden
  tags that do not repeat the title or a hashtag, no repeated-word stuffing, a
  `tone` check (`standard` / `strict` / `off`, per genre via `genre_overrides`).
  - New and edited uploads are fixed at the last gate (`build_insert_body`,
    translations included); generation prompts, the voice rules, hashtag and keyword
    caps, and the Publish-page SEO grade were rewritten to match. Machine-sounding
    output is rewritten once, then replaced by the clip's hook or dropped.
  - Hashtags are no longer copied into the hidden tags, "| Channel #Tag" titles
    lose the tag, and a keyword-filled tag list no longer scores higher.
  - **Metadata audit** on the Publish page: checks every video on the channel,
    shows the fix for each, can propose rewording for the rest (prose only, never
    links or timestamps), applies only what is shown, skips videos edited since,
    and can undo. Applying needs the full YouTube permission; 51 quota units each.
    API: `GET/POST /youtube/audit`, `POST /youtube/audit/rewrite|apply|undo`.

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
