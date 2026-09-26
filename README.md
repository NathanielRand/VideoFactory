# Video Factory

<img src="docs/brand/logo.png" alt="" width="120" align="right">

**Source to post, in one flow.** Grab videos from a source, clip them or build a
compilation around them, add your branding and creator credits, render a version for
every platform, then post now or schedule.

Everything runs on your own PC: local transcription (Whisper), a local LLM (Ollama),
GPU tracking and rendering (YOLOv8, TalkNet, FFmpeg with NVENC/AMF/QSV).

> Video Factory is a fork of **[Clips Kitty](https://github.com/ColinGPT9/clips-studio)**
> by ColinGPT9, licensed AGPL-3.0. The clipping engine, speaker tracking, captions,
> editor and publishing come from there. See [NOTICE](NOTICE).

## Status

| Area | State |
|---|---|
| Import from YouTube / Twitch / Kick / local files | ✅ from upstream |
| AI clip detection, speaker-tracked 9:16, captions, editor | ✅ from upstream |
| Publish to YouTube; TikTok / IG / FB / X via Upload-Post or WoopSocial | ✅ from upstream |
| Compilation mode (multi-source, credits, transitions, effects) | 🚧 Phase 2 |
| One project → many platform formats (9:16, 16:9, 1:1, 4:5) | 🚧 Phase 3 |
| Unified per-platform post form, direct TikTok / IG APIs | 🚧 Phase 4 |

The roadmap is in [docs/plan.md](docs/plan.md). How the code fits together is in
[docs/architecture.md](docs/architecture.md), our fork's map, and
[ARCHITECTURE.md](ARCHITECTURE.md), the upstream deep-dive.

## Dev setup (Windows)

Needs Python **3.11**, Node 22+ with pnpm 11 (`corepack enable` picks the pinned version), FFmpeg on PATH, [Ollama](https://ollama.com), and an
NVIDIA GPU for the fast path (CPU works, slowly).

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.venv\Scripts\python -m pip install -r requirements.txt ruff pytest httpx2
ollama pull gemma:7b          # the model the scoring is tuned on (8 GB VRAM)

cd ui
pnpm install
pnpm run dev                   # Electron + the backend (uses ..\.venv automatically)
```

- The processing queue **starts paused** on purpose. Press Start once you've added videos.
- Backend only: `.venv\Scripts\python main.py serve`, then `GET http://127.0.0.1:8765/health/preflight`.
- Tests: `.venv\Scripts\python -m pytest -m "not slow"`. UI: `cd ui && pnpm run typecheck`.
- Settings: `config/settings.yaml`. Every LLM prompt: `config/prompts/`.
- Local API reference: [docs/API.md](docs/API.md).

## License

AGPL-3.0-or-later, the same as upstream: see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Using the app puts no obligations on you. If you distribute a modified version or run
it as a network service for others, you must offer them its source under the same
license.
