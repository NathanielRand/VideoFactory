# Video Factory — Plan

One app, one flow: **grab videos from a source → clip them or build a compilation around them → brand, credit and style → render per platform → set per-platform metadata → post now or schedule.**

## Decision: fork Clips Kitty, don't fork OpenShot

**Base:** [Clips Kitty](https://github.com/ColinGPT9/clips-studio) (formerly Clips Studio).
Stack: Python 3.10+ backend, Electron + React/TypeScript UI, FFmpeg with hardware encoding (NVENC / AMF / QSV).

What it already covers:

| Need | Clips Kitty today |
|---|---|
| Import from a source | YouTube, Twitch VODs, Kick VODs, local files |
| Transcription | faster-whisper, word-level timestamps |
| AI clipping | Scoring the transcript and video with an LLM through Ollama (local) |
| Vertical framing | YOLOv8 pose + TalkNet active-speaker tracking, crops to 9:16 |
| Captions | Editable captions |
| Editing model | Non-destructive: edits are stored as operations and applied at render time |
| Publishing | YouTube (with scheduling). TikTok / IG / FB / X / etc. through Upload-Post and WoopSocial |

**Why not OpenShot:** its slowness comes from libopenshot (C++ engine) and its Qt/QtWebEngine preview and timeline. Fixing that is a C++ project in its own right, and merging a PyQt UI into Electron would be a rewrite anyway. We borrow **ideas** from OpenShot (its catalogue of effects and transitions), not its code.

**Escape hatch for heavy manual edits:** export the project as MLT XML so it opens in Kdenlive or Shotcut, which use a much faster engine than OpenShot.

**Licensing:** Clips Kitty is AGPL-3.0 (OpenShot is GPL-3.0, which is compatible one way). Personal use: no obligations. If we distribute the app or offer it as a hosted service, our source must be published under AGPL-3.0.

## Target flow

```
Source ─► Ingest ─► Analyze ─► Compose ─► Render (per platform) ─► Review ─► Publish / Schedule
```

### 1. Ingest
- yt-dlp for single URLs, playlists and whole channels. Local files too.
- Save **creator metadata** at import (creator name, channel URL, source URL, upload date). This feeds the automatic credits.
- Save a **rights field** per source: `own | licensed | permission | fair-use-claim | unknown`.

### 2. Analyze
- Keep Clips Kitty's pipeline: transcript → multimodal scoring → face tracking.
- Scene cuts already exist (`analysis/visual_features.py`), so compilations can trim on them without adding PySceneDetect.

### 3. Compose (main new work)
Each project is a **recipe** (JSON) in one of two modes:

- **Clips mode:** one source → N shorts (existing behaviour).
- **Compilation mode:** a sequence of segments from many sources plus:
  - intro / outro
  - a persistent banner or logo overlay
  - a per-segment credit caption (`🎥 @creator`), filled in from the ingest metadata
  - transitions between segments (FFmpeg `xfade`: fade, wipe, slide, etc.)
  - effects: blur (`gblur` / `boxblur`), blurred background fill when the aspect ratio doesn't match, zoom / punch-in, speed changes
  - captions (reusing Clips Kitty's caption system)

**Templates:** reusable presets for branding, credit style, transitions, captions and intro/outro. The goal is a few clicks from source to finished video.

Sketch of a recipe:

```json
{
  "mode": "compilation",
  "template": "weekly-highlights",
  "segments": [
    { "source_id": "yt:abc123", "in": 42.1, "out": 58.7, "credit": true },
    { "source_id": "local:clip2.mp4", "in": 0, "out": 12.0, "effects": [{ "type": "blur", "region": [0, 0, 1, 0.1] }] }
  ],
  "transitions": { "default": { "type": "fade", "duration": 0.4 } },
  "outputs": ["yt-short-9x16", "yt-16x9", "ig-reel-9x16", "tiktok-9x16"]
}
```

### 4. Render
- **Output profiles:** 9:16 (1080×1920), 16:9 (1920×1080), 1:1 (1080×1080), 4:5 (1080×1350), each with platform-specific bitrate and duration limits.
- Selecting several profiles renders several versions, in parallel, on the GPU.
- Changing the aspect ratio uses smart reframe (speaker tracking) or blur-fill (the existing Clips Kitty machinery).

### 5. Review
- A simple timeline: reorder, trim, swap segments, toggle credits and effects. Not a full editor.
- Preview each output profile.
- "Export to Kdenlive/Shotcut" (MLT XML) for anything the simple timeline can't do.

### 6. Publish / Schedule
- **Per-platform metadata:** title, description, tags, thumbnail, privacy, playlist, category. AI-drafted from the transcript, then editable.
- **Scheduling is owned by the platform, not by us.** YouTube `publishAt` and Upload-Post's schedule field hold the schedule, so the PC can be off. This is upstream's design (`publish/schedule.py`) and we keep it. We add a local queue only for a platform that can't schedule server-side.
- Platform limits:
  - **YouTube Data API:** default quota of 10k units/day, and an upload costs about 1,600, so ~6 uploads/day. Request a quota increase early.
  - **TikTok Content Posting API:** until the app passes TikTok's audit, posts are private-only. Use Upload-Post until the audit is done.
  - **Instagram Reels (Graph API):** needs a Business or Creator account, and the video must be at a public URL when publishing.

## Phases

### Phase 1: Fork and map
- [x] Fork Clips Kitty into this repo (keep `upstream` remote for pulling fixes).
- [x] Get it building and running locally (Windows, GPU acceleration on). Verified end to end: YouTube + local import → GPU Whisper → gemma:7b scoring → GPU YOLO tracking → NVENC 1080×1920 render.
- [x] Map the architecture: ingest, pipeline, edit-operation model, render and export, publishing.
  Written up in [architecture.md](architecture.md).
- [x] Identify the extension points for compilation mode, multi-profile render and publishing.
- [x] Fork housekeeping: Clips Kitty's end card defaults to off, and the upstream feedback relay is blanked. (The auto-updater only runs in packaged builds; re-point it before we ever distribute.)
- [x] Rebrand to **Video Factory**: name, logo and icon, env vars, data folder; upstream infrastructure, donations, affiliate links and the update feed removed. (Details are in [architecture.md](architecture.md#fork-housekeeping-done-rebranded-to-video-factory).)

### Phase 2: Compilation mode and templates
- [x] Recipe schema + multi-source timeline model (`compilation/recipe.py`, `compilations` table).
- [x] Ingest credit metadata: `source_url`, `channel_url`, `rights` per video; editable via `PATCH /videos/{id}/credit`.
- [x] Credit captions from ingest metadata, banner (branding profile: text burned per segment, image overlaid once), intro/outro files.
- [x] Transitions (12 xfade types + hard cut), blur regions, blur-fill / pad / crop for mismatched shapes, per-segment volume, loudness evening.
- [x] Template save/load (`compilation_templates`: the recipe minus its segments).
- [x] Compilations page: library (AI-found moments or scrub-and-pick ranges), segment list, look settings, render, preview.
- [ ] Zoom / punch-in effect.
- [ ] Drag-to-draw custom blur regions (presets only for now: top, bottom, both).

### Phase 3: Multi-platform render
- [ ] Output profile definitions.
- [ ] Parallel GPU render of the selected profiles.
- [ ] Per-profile preview.

### Phase 4: Publish and schedule
- [ ] Per-platform metadata editor (AI drafts).
- [x] ~~SQLite queue + scheduler + retry.~~ Dropped: the platforms own scheduling (YouTube `publishAt`, Upload-Post), and upstream already retries and never double-posts.
- [ ] Direct YouTube, TikTok and Instagram integrations (keep Upload-Post as a fallback).

### Phase 5: Nice-to-haves
- [ ] Auto thumbnails (best frame + title overlay).
- [ ] Channel watchers → compilations drafted automatically.
- [ ] MLT export to Kdenlive/Shotcut.
- [ ] Analytics pull-back (views per version/platform).

## Alternatives considered
- **[OpenShorts](https://github.com/mutonby/openshorts)** (MIT, Docker, has MCP/API): permissive license, but web/Docker-first and less mature as a desktop editor.
- **[ClipsAI](https://github.com/ClipsAI/clipsai)** (Python library): useful clipping logic, but no app, render pipeline or publishing.
- **Forking OpenShot:** rejected (see above).
