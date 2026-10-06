import type { CreditStyle } from './compilations'
/** Why a finished run produced the clips it did. Absent on videos processed
 *  before runs were summarised, so every reader must cope with null. */
export interface RunOutcome {
  clips: number
  candidates: number
  best_score: number | null
  min_score: number
  rejected: Record<string, number>
  measured: number
  nothing_detected: number
  /** Only set when no clips came out, and only when the evidence earns it. */
  cause: 'no_people' | 'duplicates' | 'below_threshold' | 'no_candidates' | 'render_failed' | null
  /** Clips that were selected but failed to render, and the last reason. */
  render_failed?: number
  render_error?: string | null
}

export interface Video {
  video_id: string
  channel_id: string
  channel_name: string
  title: string
  status: string
  created_at: string
  updated_at: string
  clip_count: number
  process_seconds: number
  creator_id: number | null
  creator_name: string | null
  outcome?: RunOutcome | null
}

export interface SubScores {
  text?: number
  audio?: number
  visual?: number
  reaction?: number
  engagement?: number
  source?: string
  rerank_position?: number
  /** Rubric grade 0-100 and its parts (0-10), when scoring.rubric is on or shadow. */
  rubric?: number
  rubric_detail?: Record<string, number>
  /** Seconds added before / after by what flags taught this creator. */
  learned_pad?: [number, number]
}

export interface CaptionLine {
  start: number
  end: number
  text: string
}

/** One clip's captions translated into one language, held for review before
 *  anything is written to disk. `edited` means a human corrected the text,
 *  which protects it from being overwritten by a later re-translation. */
export interface Translation {
  language: string
  lines: CaptionLine[]
  post: { title?: string; description?: string; hashtags?: string[] }
  edited: boolean
  updated_at: string
}

/** A translated language being previewed over the editor's video: the
 *  translated lines, the English they replace (used to mask the captions
 *  already burned into the clip file), and the font a burn would use —
 *  non-Latin scripts need a font that has the glyphs or the preview shows
 *  boxes where the real burn would show text. */
export interface TranslationPreview {
  language: string
  lines: CaptionLine[]
  source: CaptionLine[]
  font: string | null
  style: Required<CaptionStyle>
}

/** Pending text overlays (hook title / restyled captions) drawn live over
 *  the editor preview so users see them without waiting for a render.
 *  Times are original-clip seconds; keep ranges map to the preview file. */
export interface LiveOverlay {
  hook: { text: string; seconds: number } | null
  captions: { lines: CaptionLine[]; style: Required<CaptionStyle> } | null
  bakedKeep?: [number, number][] // edits already burned into the preview file
  bakedOverlap?: number // seconds each join of bakedKeep overlaps (crossfade)
  keep: [number, number][] // current pending edit — for hook timing
  // What's ALREADY burned into the preview file, so the overlay can mask it
  // with a blur strip while pending text is shown on top (otherwise the old
  // caption ghosts through behind the new one).
  burned?: { lines: CaptionLine[]; style: Required<CaptionStyle> } | null
  burnedHook?: { seconds: number } | null
}

export interface CaptionStyle {
  font?: string
  font_size?: number
  color?: string
  position?: 'bottom' | 'middle' | 'top'
  words_per_caption?: number
  uppercase?: boolean
  /** Light up each word as it is spoken (the short-form caption look). */
  highlight?: boolean
  highlight_color?: string
}

export type FilterName =
  | 'none'
  | 'vibrant'
  | 'warm'
  | 'cool'
  | 'cinematic'
  | 'vintage'
  | 'bw'
  | 'fade'
  | 'golden'
  | 'tealorange'
  | 'cleangirl'
  | 'pinkglow'
  | 'peachy'
  | 'dreamy'
  | 'coast'
  | 'glow'

export interface Adjust {
  brightness?: number // -0.5..0.5, 0 = unchanged
  saturation?: number // 0..3, 1 = unchanged
  contrast?: number // 0.5..2, 1 = unchanged
  temperature?: number // -1..1, 0 = neutral (- cool, + warm)
  tint?: number // -1..1, 0 = neutral (- green, + magenta)
  sharpen?: number // 0..1, 0 = off
  vignette?: number // 0..1, 0 = off
}

export interface WatermarkConfig {
  type: 'none' | 'text' | 'image' | 'both'
  text?: string
  font?: string
  font_size?: number
  color?: string
  opacity?: number
  position?: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | 'center' | 'moving' | 'custom'
  x?: number // custom position (0..1 fraction of frame width, watermark centre)
  y?: number // custom position (0..1 fraction of frame height)
  padding?: number
  scale?: number
  rotation?: number
  shadow?: boolean
  image_asset?: string
  frame?: 'free' | 'square' | 'circle' // logo crop; free = the image as drawn
  cta?: CtaConfig
  /** Credit the source channel on the clip (video_editor/credit.py). */
  credit?: CreditStyle
  /** Caption look for the clips this profile brands (clip profiles only). */
  captions?: CaptionStyle
}

/** A timed call to action burned over the clip (video_editor/watermark.py). */
export interface CtaConfig {
  enabled: boolean
  kind: 'discord' | 'subscribe' | 'vote' | 'follow' | 'custom'
  text: string
  anchor: 'start' | 'end' // `at` counts from the clip's start, or back from its end
  at: number // seconds
  duration: number // seconds on screen
  repeat: number // show again every N seconds; 0 = once
  position: 'top' | 'middle' | 'bottom'
  color: string
  bg: string
}

/** What a profile brands: single clips, or whole compilations (their own
 *  formats, so their own watermark and credit). */
export type BrandingKind = 'clip' | 'compilation'

export interface BrandingProfile {
  id: number
  name: string
  kind: BrandingKind
  config: WatermarkConfig
}

export interface MutedWord {
  start: number
  end: number
  word: string
}

/** Non-destructive manual edits (Shorts editor). All times are seconds
 *  relative to the clip start, on the clip's ORIGINAL timeline. */
export interface EditData {
  keep?: [number, number][]
  /** Crossfade seconds between kept sections (0 or absent = hard cut). */
  transition?: number
  mutes: [number, number][]
  muted_words: MutedWord[]
  volume: number
  mute_all: boolean
  fade_in: number
  fade_out: number
  speed: number
  hook: { text: string; seconds: number } | null
  music: { path: string; volume: number; duck: boolean } | null
}

export interface Word {
  start: number
  end: number
  word: string
}

export interface RenderOpts {
  crop?: 'track' | 'center' | 'bias_left' | 'bias_right' | 'letterbox' | 'lock'
  captions?: boolean
  caption_style?: CaptionStyle
  caption_lines?: CaptionLine[]
  filter?: FilterName
  adjust?: Adjust
  edit?: EditData | null
  profile?: string // longform rendering profile (16:9); absent = vertical Short
  watermark?: WatermarkConfig | null
  /** Which branding profile this clip follows. Each custom part is the
   *  clip's own (kept in `watermark`); the rest tracks the profile. Absent:
   *  branding as it was processed. */
  branding?: ClipBranding
}

export interface ClipBranding {
  profile_id: number | null
  custom_watermark?: boolean
  custom_credit?: boolean
  custom_captions?: boolean
}

export interface Clip {
  id: number
  video_id: string
  start_s: number
  end_s: number
  score: number
  hook: string
  path: string
  status: string
  scheduled_for: string | null
  title: string
  description: string
  hashtags: string[]
  /** Search phrases for YouTube's tags (analysis/metadata.py). */
  keywords?: string[]
  /** This clip's own first comment; empty means the standing one is used. */
  first_comment?: string
  /** The YouTube playlist this clip joins when published there; empty means
   *  the creator's rule (or none) applies. */
  playlist_id?: string
  /** The AI's comment for this clip, offered but only used when picked. */
  suggested_comment?: string
  /** Other generated titles, offered in the editor. */
  alt_titles?: string[]
  scores: SubScores
  render_opts: RenderOpts
  created_at: string
  /** When the clip was exported, '' if never. Exporting sets it, and so does its star. */
  exported_at: string
}

export interface Job {
  id: number
  type: 'process' | 'render' | 'translate'
  payload: string
  // 'cancelled' is written by the worker on a user cancel (server/jobs.py).
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  error: string
  created_at: string
  updated_at: string
}

/** One video's processing settings, snapshotted when it entered the queue.
 *  Editing one queued video's options can never reach another — each job
 *  owns its own copy. */
export interface JobOptions {
  force?: boolean
  captions?: boolean
  caption_style?: CaptionStyle
  long_clips?: boolean
  podcast?: boolean
  longform?: { mode: string } | null
  watermark_profile_id?: number | null
  /** Explicitly no branding — not even the creator's default. */
  no_watermark?: boolean
  filter?: FilterName
  min_score?: number
  max_clips?: number
  /** Into the library without making clips — for a compilation, or to
   *  decide later. Absent means make clips, as always. */
  import_only?: boolean
  /** Once it is in the library, append the whole video to this compilation. */
  add_to_compilation?: number
}

/** A compilation that uses a library video, and in how many segments. */
export interface LibraryUse {
  id: number
  title: string
  status: 'draft' | 'queued' | 'rendering' | 'done' | 'failed'
  segments: number
}

/** One upload in the Library, with everywhere it has been used. */
export interface LibraryItem {
  video_id: string
  title: string
  channel_name: string
  /** 'imported' = in the library, never clipped. */
  status: string
  duration: number
  source_url: string
  created_at: string
  creator_id: number | null
  creator_name: string | null
  clip_count: number
  published_clips: number
  local: boolean
  has_source: boolean
  compilations: LibraryUse[]
  /** 'queued' | 'running' while a job for it is in the queue, else ''. */
  in_queue: string
}

/** A queue row: the job, plus the video it is about. `display_title` comes
 *  from the videos table, so it is empty until the download names the video. */
export interface QueueJob {
  id: number
  type: 'process' | 'render' | 'translate'
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  error: string
  position: number
  video_id: string
  display_title: string
  channel: string
  source_seconds: number
  url: string
  settings: JobOptions
  interrupted: 0 | 1
  /** A cancel was requested and is waiting for the job's next checkpoint. */
  cancelling?: boolean
  attempts: number
  started_at: string
  finished_at: string
  created_at: string
  updated_at: string
  log_path: string
}

export interface QueueEstimate {
  /** Seconds for the WAITING videos only — the caller adds the running one. */
  queued_seconds: number
  per_video_seconds: number
  samples: number
  /** False until a few real runs exist; the UI softens its wording. */
  confident: boolean
}

export interface QueueSnapshot {
  processing: QueueJob[]
  queued: QueueJob[]
  completed: QueueJob[]
  failed: QueueJob[]
  paused: boolean
  estimate: QueueEstimate
  /** How many more videos may be queued right now. */
  capacity: number
  /** The cap on waiting + running videos (core/queue.py MAX_ACTIVE). */
  max_active: number
}

export interface InstalledModel {
  name: string
  size_gb: number
  /** An Ollama cloud model: runs on ollama.com, and size_gb is only the
   *  size of its manifest. */
  cloud?: boolean
  /** Billions of parameters and the family, as Ollama read them from the
   *  model file; null / '' when it did not say. */
  params_b?: number | null
  family?: string
}

/** One thing an install needs, and whether it has it. `fix` is written for
 *  a creator to act on, not a developer to debug. */
export interface PreflightCheck {
  name: string
  ok: boolean
  detail: string
  fix: string
  /** false = degraded but still usable (no GPU, low disk). */
  blocking: boolean
}

export interface Preflight {
  /** True when nothing BLOCKING is wrong — the app can make a clip. */
  ready: boolean
  checks: PreflightCheck[]
}

/** One entry in Settings → AI: this PC (local, first) or a cloud provider on
 *  the user's own key. Never carries a key: `key_tail` is the last four. */
export interface AIProvider {
  id: string
  label: string
  local: boolean
  key_label: string
  key_url: string
  pricing_url: string
  privacy: string
  stt: boolean
  stt_models?: string[]
  has_key: boolean
  key_tail: string
}

export interface AIStatus {
  active: { provider: string; model: string; local: boolean }
  transcription: { backend: string; model: string }
  providers: AIProvider[]
}

export interface AIModel {
  id: string
  name: string
  context: number
  json_schema: boolean
  tools: boolean
  note: string
}

export interface ModelsInfo {
  active: string
  installed: InstalledModel[]
  recommendations: { hardware: string; model: string; note: string }[]
  /** Models picked for a purpose rather than for how much VRAM you have.
   *  A separate list because folding them into `recommendations` put values
   *  like "Multilingual" under a "Your hardware" heading. */
  other_models?: { purpose: string; model: string; note: string }[]
  /** The one model to suggest for THIS machine, chosen server-side from the
   *  same table as `recommendations` so nothing can contradict it. */
  recommended?: { model: string; reason: string }
  /** e.g. "NVIDIA GeForce RTX 4070" or "Apple Silicon, 16 GB unified memory"; empty if none */
  hardware?: string
}

export interface GpuStats {
  name: string
  vram_used: number
  vram_total: number
  gpu_percent: number
  temp_c?: number | null
}

/** How hard the resource governor (core/governor.py) is holding the job back. */
export type PerformanceMode = 'auto' | 'eco' | 'max'
export type PressureLevel = 'ok' | 'high' | 'critical'

export interface ActivitySample {
  t: number
  cpu: number
  ram: number
  /** Video Factory's own share of the CPU (backend, FFmpeg, Ollama). */
  own_cpu: number
  gpu: number | null
  vram: number | null
  /** Windows commit charge as % of the commit limit: what ran out when the
   *  PC crashed with WinError 1455. */
  commit: number | null
}

export interface Activity {
  mode: PerformanceMode
  level: PressureLevel
  reasons: string[]
  /** A job is holding at a checkpoint until pressure clears. */
  waiting: boolean
  /** The heavy model load (torch, Whisper…) waiting for memory, if any. */
  blocked_on: string
  renders_active: number
  render_budget: number
  renders_configured: number
  samples: ActivitySample[]
}

export interface ModelRuntime {
  host: string
  running: boolean
  starting: boolean
  /** Started by this app, so it stops when the app does. */
  managed: boolean
  version: string
  loaded: { name: string; size: number; size_vram: number; expires_at: string }[]
  can_start: boolean
  busy: boolean
  error: string
  model: string
}

export interface SystemStats {
  cpu_percent: number
  ram_percent: number
  data_dir_bytes: number
  disk_free_bytes: number
  gpu: GpuStats | null
  /** Commit this backend PROCESS is running — not what is on disk. Python
   *  imports once, so a backend left running keeps executing the code it
   *  started with. Deliberately NOT shown in the UI: it means nothing to
   *  someone using the app. It is here for bug reports and for checking a
   *  running build during development. Empty in a packaged build (no git). */
  build_sha?: string
  uptime_seconds?: number
}

export interface Settings {
  model: string
  channel: string
  auto_upload: boolean
  privacy: string
  content_language: string // 'auto' or ISO code (es / pt / hi / id ...)
  translation_model: string // local model used for translation ('' = the main one)
}

/** Events arriving over the WebSocket. */
export interface StudioEvent {
  /** 'queue' is a bare ping meaning "the queue changed, re-read it".
   *  'publish' is a YouTube upload. It is deliberately NOT 'job': jobProgress
   *  resets the global processing bar on any 'job' event, and an upload has
   *  nothing to do with the video pipeline's progress. */
  /** 'library' and 'compilation' are bare pings like 'queue': an upload was
   *  imported, or a compilation's segments changed outside its editor. */
  type:
    | 'progress'
    | 'job'
    | 'model_pull'
    | 'queue'
    | 'publish'
    | 'automation'
    | 'library'
    | 'compilation'
    | 'exports'
  compilation_id?: number
  /** 'progress' events: where the current step of the job ends (0..1). */
  ceil?: number
  /** 'exports' events: a transfer moved (and, while copying, how far). */
  transfer?: number
  sent?: number
  bytes?: number
  /** On 'job' events: the job only imported the video, so no clips is right. */
  import_only?: boolean
  job_id?: number
  /** Present on 'job' events: lets the queue ignore re-renders. */
  job_type?: 'process' | 'render' | 'translate'
  /** Publish events only. */
  publish_job?: number
  clip_id?: number
  phase?: 'prepare' | 'upload' | 'metadata' | 'done' | 'failed' | 'cancelled' | 'checked'
  terminal?: 'done' | 'failed' | 'cancelled' | 'checked'
  youtube_id?: string
  url?: string
  warnings?: string[]
  locked_private?: boolean
  state?: string
  status?: string
  stage?: string
  message?: string
  video_id?: string
  title?: string
  clip?: number
  total?: number
  clips?: number
  current?: number
  fraction?: number
  duration?: number
  seconds?: number
  downloaded?: number
  error?: string
  tag?: string
  completed?: number
  /** Videos still waiting when a job ended — used by the finish notification. */
  remaining?: number
}

/** Creator intelligence */
export interface CreatorAccount {
  account_id: number
  platform: string
  username: string
}

export interface CreatorSummary {
  creator_id: number
  display_name: string
  aliases: string[]
  learning_enabled: number
  default_branding_id: number | null
  videos: number
  clips: number
  avg_score: number | null
  accounts: CreatorAccount[]
  /** A watched channel learns into this profile. */
  watched?: boolean
}

export interface CreatorSuggestion {
  creator_a: { id: number; name: string; platform: string }
  creator_b: { id: number; name: string; platform: string }
  reason: string
}

export interface CreatorKnowledgeItem {
  knowledge_id: number
  knowledge_type: string
  information: string
  confidence: string
  source_video: string | null
  created_at: string
  /** How many times the creator has actually been heard saying it. */
  times_seen: number
  last_seen: string | null
  /** 'active' = used for scoring/titles; 'candidate' = a phrase heard too few
   *  times to count yet; 'dormant' = not said again in a long time. */
  state: 'active' | 'candidate' | 'dormant'
}

export interface CreatorEvent {
  event_id: number
  event_name: string
  description: string
  status: string
  detected_date: string
}

/** A change suggested from a creator's flags; applies only once approved. */
export interface LearningProposal {
  id: number
  creator_id: number
  kind: 'pad_lead' | 'pad_tail' | 'crop' | 'min_score_delta' | 'guidance'
  value: string
  rationale: string
  status: 'pending' | 'approved' | 'rejected'
}

export interface CreatorDetail {
  creator_id: number
  display_name: string
  aliases: string[]
  learning_enabled: number
  default_branding_id: number | null
  accounts: CreatorAccount[]
  knowledge: CreatorKnowledgeItem[]
  events: CreatorEvent[]
  feedback: Record<string, number>
  /** Flagged clips from this creator's videos. */
  flag_count: number
  preferences: {
    weight_bias: Record<string, number>
    preferred_duration: number | null
    signals: number
    flags: number
    boundary: { lead: number; tail: number }
    crop: string | null
    overrides: { min_score_delta: number; guidance: string[] }
  } | null
}

/** One clip in a proposed batch of uploads. A plan is a proposal: nothing has
 *  been uploaded until somebody confirms it. */
export interface PublishPlanItem {
  clip_id: number
  title: string
  description: string
  privacy: string
  publish_at: string | null
}

// ---- watched channels (server/automation.py) ---------------------------------

export type WatchPlatform = 'youtube' | 'twitch' | 'kick'

/** What happens to a watched video's clips. Configured once per channel. */
export interface WatchPublish {
  /** off: leave them. ask: stop at "ready to publish". auto: publish at once. */
  mode: 'off' | 'ask' | 'auto'
  platforms: string[]
  /** Best clips of each video to post. 0 posts every clip. */
  max_posts: number
  /** False: post each clip as soon as it is made instead of spacing them. */
  spread: boolean
  /** A daily budget, queued behind everything already scheduled. */
  per_day: number
  gap_hours: number
  /** Local "HH:MM" for each day's first post, or "" for as soon as possible. */
  day_start: string
  /** The creator's own, set once. They lead every caption. */
  hashtags: string[]
  /** False: only the hashtags above, none of the AI's. */
  ai_hashtags: boolean
  /** Under each caption. {source_url}, {source_title}, {source_channel} and
   *  {source_platform} are filled in. */
  footer: string
  overrides: Record<string, Record<string, string | boolean>>
}

/** Where a watch's videos go when it feeds a compilation. */
export interface WatchCompilationTarget {
  compilation_id: number | null
  /** Sent only: make a compilation with this title (empty: named after the
   *  watch) instead of using compilation_id. */
  new_title?: string
  /** whole: each video start to end. clips: its best clips, which needs
   *  clips switched on too. */
  what: 'whole' | 'clips'
  /** Best clips of each video to add; 0 is all of them. */
  max_clips: number
  /** Read only: the compilation's title, null once it has been deleted. */
  title?: string | null
}

/** Catching up on what a channel or playlist already has, when it is first
 *  watched. Neither count nor since: earlier videos are listed, not taken. */
export interface WatchBackfill {
  /** The latest this many; with `since`, the most to look through. */
  count: number
  /** "YYYY-MM-DD": only videos posted on or after it. */
  since: string
  /** A playlist's listing has no dates: which end new videos are added to. */
  newest_at: 'bottom' | 'top'
}

/** What a watch does with each new video. Both off: it only lands in the
 *  Library. */
export interface WatchActions {
  clips: boolean
  compile: boolean
  compilation: WatchCompilationTarget
}

export interface Watch {
  id: number
  platform: WatchPlatform
  channel_key: string
  /** A YouTube playlist is watched the same way a channel is. */
  kind: 'channel' | 'playlist'
  name: string
  enabled: boolean
  preset: string
  options: JobOptions
  publish: WatchPublish
  actions: WatchActions
  backfill: WatchBackfill
  backlog: 'all' | 'newest' | 'day' | 'none'
  min_minutes: number
  last_ok_poll_at: number
  next_poll_at: number
  last_error: string
  counts: Record<string, number>
  /** The creator profile this channel's videos learn into, or null when the
   *  channel has no readable name to make one from. */
  creator?: WatchCreator | null
}

export interface WatchCreator {
  id: number
  name: string
  learning: boolean
  /** Videos of this creator finished so far, and facts and storyline events
   *  learned from them. */
  videos: number
  facts: number
}

export interface WatchDelivery {
  clip_id: number
  platform: string
  state: string
  post_url: string
  scheduled_for: string
  error: string
}

export interface WatchItem {
  id: number
  watch_id: number
  platform: WatchPlatform
  video_id: string
  url: string
  title: string
  published_at: number
  detected_at: number
  status:
    | 'earlier'
    | 'waiting_for_video'
    | 'waiting_for_queue'
    | 'queued'
    | 'processing'
    | 'complete'
    | 'failed'
    | 'cancelled'
    | 'skipped'
    | 'error'
  reason: string
  job_id: number
  publish_state: '' | 'off' | 'ask' | 'publishing' | 'done'
  publish_error: string
  clips?: number
  deliveries?: WatchDelivery[]
  progress?: { percent?: number; label?: string } | null
  waiting_behind?: number
  queue_paused?: boolean
  details?: string
  /** Hands-off retries: how many were used and when the next is due
   *  (unix seconds, 0 when none is scheduled). */
  retries: number
  retry_at: number
  publish_attempts: number
  publish_retry_at: number
  delivery_retries: number
  /** 1: the download was deleted once the clips were published. 2: there was
   *  none to delete. 0: kept. */
  source_freed: number
  /** Brought into the Library without making clips. */
  imported: boolean
  /** Into the watch's compilation: 0 not yet, 1 added, 2 could not be (see
   *  compile_note), 3 not asked to. */
  compiled: number
  compile_note: string
}

export interface AutomationStatus {
  enabled: boolean
  /** Delete each watched video's download once its clips are published. */
  delete_sources: boolean
  interval_minutes: number
  watches: number
  watching: number
  presets: { id: string; name: string; description: string }[]
}

/** What the watcher is doing now and did last, for the live panel. */
export interface AutomationActivity {
  now: {
    state: 'off' | 'watching' | 'waiting' | 'busy'
    text: string
    progress?: { percent: number; label: string; eta_seconds: number | null } | null
  }
  /** Channels being watched right now (0 when watching is off). */
  watching: number
  /** Unix seconds of the next channel check, 0 if none is due. */
  next_check_at: number
  /** Newest first. `url` is the video found or the post that went live. */
  events: { at: number; text: string; kind: string; url?: string }[]
}
