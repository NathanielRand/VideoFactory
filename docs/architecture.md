# Video Factory — Architecture map (Phase 1)

This is our fork's map of the Clips Kitty codebase, written against [plan.md](plan.md).
Upstream's own deep-dive lives in [`/ARCHITECTURE.md`](../ARCHITECTURE.md). Read it before
changing the tracker, ASD, queue or scoring: it records several measured performance
traps.

Upstream base: `ColinGPT9/clips-studio` @ `4bb531d` (v1.2.0), remote `upstream`.

## Shape of the system

```
Electron + React (ui/)  ──HTTP/WS 127.0.0.1:8765──►  FastAPI (server/api.py, 2.6k lines)
                                                         │  one worker thread, SQLite-backed queue
                                                         ▼
                                     core/pipeline.py::process_video(url, config, db)
   sources/ → transcription/ → analysis/ (+ llm/) → video/ (track, crop, captions) → metadata
                                                         │
                          SQLite (core/state.py) · data/downloads · data/clips
```

- **Stages talk only through dataclasses (`core/models.py`) and files.** Each stage's
  completion is committed to SQLite before the next one starts, so a crash resumes at the
  failed stage.
- **Per-job settings** are a JSON snapshot in `jobs.payload`, applied onto a deep copy
  of `config/settings.yaml`.
- **The UI never touches Python or the filesystem.** Everything goes through the API.
  `npm run dev:web` runs the UI in a browser.

## Where each plan stage lives today

| Plan stage | Existing code | Status vs our plan |
|---|---|---|
| **Ingest** | `sources/dispatch.py` (URL → module), `youtube.py`, `twitch.py`, `kick.py`, `ytdlp_common.py`; watched channels in `sources/channel_feed.py` + `server/automation.py` | ✅ URLs, channels, local files. `VideoInfo.channel` (uploader name) is captured and stored as `videos.channel_name`. ❌ No channel URL, source URL or rights field kept for crediting. |
| **Analyze** | `transcription/transcriber.py`, `analysis/*` (fusion, highlights, rerank, dedupe), scene cuts already in `analysis/visual_features.py` | ✅ Reuse as-is. PySceneDetect probably isn't needed, because HSV scene cuts already exist. |
| **Compose: clips** | `core/pipeline.py::_render_files`, `video/cutter.py`, `video/cropper.py` | ✅ One source → N vertical clips. |
| **Compose: compilation** | `longform/assemble.py` (keep-ranges → concat demuxer → 1920×1080), `longform/highlight_select.py` | ⚠️ Closest analogue, but **one source only**, 16:9 only, and **no transitions** (a lossless concat join). |
| **Edit model** | `video_editor/timeline.py::EditList` (keep, mutes, volume, fades, speed, hook, music), applied by `video_editor/export.py` in one FFmpeg pass | ✅ A good per-segment model. Extend it rather than replace it. |
| **Branding** | `video_editor/watermark.py` (text via ASS, image via overlay, "moving" anti-crop mode), `branding_profiles` table, per-creator default branding | ✅ Reuse for our banner/logo. |
| **Outro** | `video/outro.py`: **Clips Kitty's own animated end card, ON by default**, appended with a lossless concat | ⚠️ Replace with a user-supplied outro (and an intro), keeping the same "format-matched, lossless append" trick. |
| **Effects** | `video/filters.py` (colour presets), blur-fill background in `cropper._render_fit_blur` and the letterbox path | ⚠️ No general blur-region, zoom or transition effects yet. |
| **Render formats** | `video/cropper.py` is **hard-coded 1080×1920**. `longform/` is hard-coded 1920×1080. `video/encoding.py` picks NVENC/AMF/QSV/CPU | ❌ No 1:1 or 4:5, and no "one project → many profiles". |
| **Review UI** | `ui/.../components/ClipEditor.tsx`, `TimelineEditor.tsx`, `EditChat.tsx`, `EditorModal.tsx` | ✅ Per-clip editor exists. ❌ No multi-segment compilation timeline. |
| **Publish** | `publish/base.py` (`Publisher` ABC, `PublishRequest`), `youtube_shorts.py`, `uploadpost.py` (TikTok, IG, FB, X, Threads, LinkedIn, Pinterest, Bluesky with per-platform titles and first comments), `woopsocial.py` | ✅ Stronger than expected. Per-platform metadata overrides already exist in Upload-Post. |
| **Schedule** | `publish/schedule.py`: **no local timer by design**. YouTube `publishAt` and Upload-Post's schedule field let the platform own the schedule | ✅ Keep this approach. It is more reliable than our planned SQLite scheduler because the PC can be off. Only add a local queue for platforms that can't schedule server-side. |

## Extension points, by phase

### Phase 2: compilation mode (built)
As built: `compilation/` holds `recipe.py` (schema and validation), `credits.py` (ASS lower-thirds),
`render.py` (one FFmpeg pass per part, then a concat copy for hard cuts or one xfade chain for
transitions, then the image banner) and `store.py` (CRUD and the `compile` job). Routes are in
`server/compilations_api.py`, and the UI is `pages/Compilations.tsx` + `lib/compilations.ts`. Every part
renders at identical parameters (canvas size, 30fps CFR, yuv420p, 48 kHz stereo AAC), so hard cuts
join losslessly. Render starts a paused queue only when nothing else is waiting (`queue.start_if_alone`).
Tests: `tests/test_compilation.py`, including real multi-source renders.

Original design notes:
- **New package `compilation/`**, a sibling of `longform/`, built on its patterns:
  - `recipe.py`: dataclasses and validation for the recipe (segments across **many**
    `video_id`s, template ref, outputs). Stored as a new `compilations` table plus
    `compilation_segments` (additive migration in `core/state.py::_migrate()`).
  - `assemble.py`: cut each segment to identical encode params, as
    `longform/assemble.py` does. Transitions are then applied **only at the joins**:
    re-encode a short tail+head window with `xfade`/`acrossfade`, and keep the lossless
    concat for everything else. This keeps upstream's "no giant filter graph" rule.
  - `credits.py`: per-segment credit lower-third as an ASS event (the same mechanism as
    watermark text), filled from `videos.channel_name` plus new ingest metadata.
- **Ingest additions:** store `channel_url`, `source_url` and a `rights` field on
  `videos` (additive columns). Set from `VideoInfo` in `sources/*`.
- **Intro/outro:** generalize `video/outro.py`'s append-with-matched-format into
  "user bumper clips". Default Clips Kitty's own card to **off** in our fork.
- **Effects:** add `blur_regions` and `zoom` to `EditList`, applied in
  `video_editor/export.py`.
- **API:** `POST/GET/PATCH /compilations`, `POST /compilations/{id}/render`, as a job type
  alongside `process` and `render` in `server/jobs.py`.
- **UI:** new `pages/Compilations` + a segment timeline. Reuse `TimelineEditor.tsx` pieces.

### Phase 3: multi-format render
- Parameterize the canvas in `video/cropper.py` (`W×H` instead of literal 1080×1920).
  The 9:16 "never reshape the crop window" contract generalizes to "crop at the target
  aspect".
- Add `render/profiles.py`: `yt-short-9x16`, `yt-16x9`, `ig-reel-9x16`, `ig-feed-4x5`,
  `square-1x1`, `tiktok-9x16`, with size, fps, bitrate and duration caps.
- **The TalkNet/tracker crop path is computed once per clip** and reused for every
  vertical profile. Only the crop aspect and scale differ.

### Phase 4: publishing
- `Publisher` ABC is already the plugin point: add `publish/tiktok.py` and
  `publish/instagram.py` for direct APIs. Keep Upload-Post as the default fan-out.
- A per-platform metadata editor already partly exists (`uploadpost.py` overrides,
  `YouTubeMetadataForm.tsx`). Unify it into one "post everywhere" form per rendered
  version.

## Fork housekeeping (done: rebranded to Video Factory)
- **Names:** "Clips Kitty" and "Clips Studio" are "Video Factory" everywhere a person reads them
  (UI, 19 locales, backend messages, docs). Env vars are `VIDEO_FACTORY_*`, and the installed data
  folder is `%LOCALAPPDATA%\Video Factory`. `appId` `com.videofactory.app` and package
  name `video-factory` are now **frozen**: renaming them later would orphan users' settings.
- **Logo:** `scripts/make_logo.py` draws the logo and writes the UI logo, `ui/build/icon.ico`
  (installer, taskbar and tray) and the appx tiles. `scripts/make_mascot.py` no longer
  touches the icon.
- **Removed upstream infrastructure:** `site/`, `whop-app/`, `web/`, `feedback-relay/`,
  `twitch-proxy/`, `packaging/winget`, Microsoft Store docs and art, stats, mirror, Pages and MSIX
  workflows, CODEOWNERS, PayPal donate window, WoopSocial affiliate link, and the auto-update feed
  (`publish: null`). All of it can be restored from `upstream` if ever needed.
- **Still upstream-branded (on purpose, disabled):** `video/outro.py` + `video/mascot_art.py` +
  `assets/outro/*.mp4`, the Clippy end card. It is off by default, and two of its artwork tests
  are skipped. Phase 2 replaces it with user intro/outro bumpers and can then delete it.
- **Legal:** `LICENSE` is unchanged. `NOTICE` has a fork header above upstream's notice, which is
  otherwise unchanged. The app footer shows "based on Clips Kitty" and links to the upstream source.
- **Upstream merges:** the rename touched about 140 files, so `git merge upstream/main` will
  conflict. Cherry-pick specific upstream fixes instead.

## Local dev environment
- Python **3.11** venv at `.venv` (matches CI). Your default `python` is 3.14, which is
  too new for parts of the ML stack.
- PyTorch CUDA 13 build: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130`,
  then `pip install -r requirements.txt ruff pytest`.
- GPU: RTX 2070 SUPER (8 GB), driver 616.64. NVENC is available. 8 GB of VRAM fits a
  7B model in Ollama alongside Whisper, but not with much room to spare.
- `ollama serve` must be running, and a model must be pulled (default `gemma:7b`, the one the app recommends for 8 GB).
- **cuBLAS 12 fix (ours):** the current `ctranslate2` wheel needs `cublas64_12.dll`, but cu130 PyTorch only ships cuBLAS 13.
  `nvidia-cublas-cu12` is now in `requirements.txt`, and `transcription/transcriber.py::_add_gpu_dlls` puts its
  `bin/` on the DLL path **and** `PATH` (ctranslate2 loads it lazily with a plain `LoadLibrary`).
- Backend only: `.venv/Scripts/python main.py serve`, then `GET /health/preflight` reports anything missing.
- **The queue starts paused by design.** Press Start in the UI or `POST /queue/resume`.
- URLs go to `POST /jobs {"url"}`. Local files go to `POST /videos/local {"path","title","channel"}`, and
  `channel` there is what our credit captions will read.
- Tests: `.venv/Scripts/python -m pytest -m "not slow"` (940 pass). Two upstream scan tests needed
  `.venv` added to their skip lists, and the outro tests now force the card on inside their fixture.
- UI: `cd ui && npm install && npm run dev`. This launches Electron and spawns the backend. We patched
  `ui/src/main/index.ts` so dev mode uses `.venv/Scripts/python.exe` when it exists (upstream calls the bare PATH
  `python`, which here is 3.14 with no dependencies). Stop any standalone `main.py serve` first, because both use port 8765.
- The Electron dev renderer is pinned to **port 5273** (`strictPort`), which is in the engine's CORS list.
  Vite's default 5173 clashed with another local project, and the silent fallback to 5174 broke every API call.
