// Video Factory: compilations (many sources -> one video). Types mirror
// compilation/recipe.py; routes are server/compilations_api.py.
import { API_BASE } from './api'
import { defaultBrandingId } from './branding'
import type { WatermarkConfig } from './types'

export type Canvas = '16:9' | '9:16' | '1:1' | '4:5'
export type Fit = 'blur' | 'pad' | 'crop'
export type Rights = 'own' | 'licensed' | 'permission' | 'fair_use' | 'unknown'

/** x, y, w, h as fractions of the source frame. */
export type BlurRegion = [number, number, number, number]

export interface SegmentSpec {
  video_id: string
  start: number
  end: number
  credit?: boolean
  volume?: number
  blur_regions?: BlurRegion[]
}

/** Two segments show the same footage: same video, overlapping by at least
 *  half of the shorter one. Mirrors compilation/store.py's _repeats. */
function repeats(a: SegmentSpec, b: SegmentSpec): boolean {
  if (!a.video_id || a.video_id !== b.video_id) return false
  const shorter = Math.min(a.end - a.start, b.end - b.start)
  if (shorter <= 0) return a.start === b.start && a.end === b.end
  return Math.min(a.end, b.end) - Math.max(a.start, b.start) >= shorter / 2
}

/** The index of the first of `segments` that `seg` repeats, or -1. */
export function duplicateOf(segments: SegmentSpec[], seg: SegmentSpec): number {
  return segments.findIndex((s) => repeats(s, seg))
}

/** index → index of the earlier segment it repeats, for every repeat. */
export function duplicates(segments: SegmentSpec[]): Map<number, number> {
  const out = new Map<number, number>()
  segments.forEach((seg, i) => {
    const j = duplicateOf(segments.slice(0, i), seg)
    if (j >= 0) out.set(i, j)
  })
  return out
}

export interface CreditStyle {
  enabled?: boolean
  template?: string
  seconds?: number
  /** Keep the credit up for the whole segment; `seconds` is ignored. */
  whole_clip?: boolean
  position?: string
  /** Distance in from the frame edges, as a fraction of the width / height.
   *  Unset is the original margin. Not used by the 'custom' position. */
  inset_x?: number | null
  inset_y?: number | null
  /** 'custom' position: where the credit's centre sits, as fractions of the frame. */
  x?: number
  y?: number
  font_size?: number
  font?: string
  bold?: boolean
  italic?: boolean
  /** #RRGGBB */
  color?: string
  /** What is drawn behind the text when there is no background image. */
  backing?: 'box' | 'outline' | 'none'
  /** A branding asset filename: an image the text sits centred on. */
  bg_image?: string | null
  /** The image's height as a multiple of the font size. */
  bg_scale?: number
  /** Where the text's centre sits on the image, as fractions of it. */
  bg_text_x?: number
  bg_text_y?: number
}

export interface Recipe {
  canvas?: Canvas
  /** Every format to render; [0] is the primary (canvas). */
  outputs?: Canvas[]
  fit?: Fit
  segments?: SegmentSpec[]
  transition?: { type: string; duration?: number }
  credits?: CreditStyle
  /** false: the banner profile's credit is used; true: `credits` above. */
  credits_custom?: boolean
  /** A profile, this compilation's own settings (`custom`), or both: the
   *  profile it started from, with its watermark overridden. */
  banner?: { profile_id?: number; custom?: WatermarkConfig } | null
  intro?: { path: string } | null
  outro?: { path: string } | null
  normalize_audio?: boolean
  /** LUFS every part is matched to when normalize_audio is on. */
  loudness_target?: number
}

/** Loudness targets offered, as compilation/recipe.py LOUDNESS_TARGETS. */
export const LOUDNESS_TARGETS: { value: number; label: string }[] = [
  { value: -14, label: '−14 LUFS · YouTube, TikTok, Instagram' },
  { value: -16, label: '−16 LUFS · quieter, podcast-style' },
  { value: -12, label: '−12 LUFS · louder' }
]

/** A segment's volume is stored as a multiplier; the editor speaks dB. */
export const volumeToDb = (v: number): number | null => (v <= 0 ? null : 20 * Math.log10(v))
export const dbToVolume = (db: number | null): number => (db === null ? 0 : Math.pow(10, db / 20))

/** One part's measured loudness (compilation/loudness.py report). */
export interface LoudnessPart {
  kind: 'segment' | 'intro' | 'outro'
  index: number | null
  lufs: number | null
  peak: number | null
  silent: boolean
  /** The gain evening-out gives it to reach the target. */
  match_db: number | null
  trim_db: number | null
  muted: boolean
}

export interface LoudnessReport {
  target: number
  parts: LoudnessPart[]
  /** How far apart the creators were, in LU. */
  spread: number
}

export interface Compilation {
  id: number
  title: string
  recipe: Recipe
  status: 'draft' | 'queued' | 'rendering' | 'done' | 'failed'
  output_path: string
  /** {canvas: path} for every rendered format. */
  outputs: Record<string, string>
  error: string
  created_at: string
  updated_at: string
  problem?: string
  /** Per format: the platforms this is too long for. */
  warnings?: Record<string, string[]>
}

/** One render of a compilation, kept on disk as a version. */
export interface CompilationRender {
  id: number
  compilation_id: number
  version: number
  /** The recipe it was rendered from; null for a render from before versions. */
  recipe: Recipe | null
  /** {canvas: path} */
  outputs: Record<string, string>
  created_at: string
  bytes: number
  /** Formats whose file is no longer on disk. */
  missing: string[]
}

export interface CompilationSettings {
  /** How many versions each compilation keeps; 0 keeps all. */
  keep_versions: number
}

export interface CompilationTemplate {
  id: number
  name: string
  config: Recipe
  created_at: string
}

/** A template is a compilation's look: everything but its segments.
 *  Mirrors TEMPLATE_KEYS in compilation/recipe.py; keep the two in step. */
export const TEMPLATE_KEYS = [
  'canvas',
  'outputs',
  'fit',
  'transition',
  'credits',
  'banner',
  'intro',
  'outro',
  'normalize_audio',
  'loudness_target'
] as const

/** The recipe with a template's look laid over it, its segments kept.
 *  Mirrors apply_template: a key the template has replaces the recipe's, a
 *  key it lacks is left as the compilation had it. */
export function applyTemplate(template: Recipe, recipe: Recipe): Recipe {
  const next: Recipe = { ...recipe }
  for (const key of TEMPLATE_KEYS) {
    if (key in template) (next as Record<string, unknown>)[key] = structuredClone(template[key])
  }
  return next
}

/** Just the look of a recipe, for comparing. */
export function lookOf(recipe: Recipe): Partial<Recipe> {
  const out: Record<string, unknown> = {}
  for (const key of TEMPLATE_KEYS) if (key in recipe) out[key] = recipe[key]
  return out
}

/** Whether a compilation currently looks exactly like a template. */
export function usesTemplate(recipe: Recipe, template: CompilationTemplate): boolean {
  const stable = (v: unknown): string =>
    JSON.stringify(v, (_k, x) =>
      x && typeof x === 'object' && !Array.isArray(x)
        ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)))
        : x
    )
  const mine = lookOf(recipe)
  const theirs = lookOf(template.config)
  // Only what the template sets has to match; a key it leaves out is free.
  return Object.keys(theirs).every(
    (k) => stable((mine as Record<string, unknown>)[k]) === stable((theirs as Record<string, unknown>)[k])
  )
}

export interface LibraryVideo {
  video_id: string
  title: string
  channel_name: string
  channel_url: string
  source_url: string
  rights: Rights
  duration: number
  created_at: string
  has_source: boolean
}

export interface CompilationOptions {
  canvases: Record<Canvas, { width: number; height: number }>
  fits: Fit[]
  transitions: string[]
  credit_positions: string[]
  rights: Rights[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
  if (!res.ok) {
    // FastAPI puts the readable reason in `detail`; show that, not the JSON.
    const body = await res.text().catch(() => '')
    let detail = body
    try {
      detail = JSON.parse(body).detail ?? body
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

/** A compilation waiting to render or rendering (GET /compilations/progress). */
export interface RenderProgress {
  job_id: number
  state: 'queued' | 'running'
  /** Running: how far through, what it is doing, and time left (null until
   *  there is enough to estimate from). */
  percent?: number
  label?: string
  eta_seconds?: number | null
  elapsed_seconds?: number
  /** Queued: jobs that will run first, and whether the queue is stopped. */
  ahead?: number
  queue_paused?: boolean
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const compilationsApi = {
  options: () => request<CompilationOptions>('/compilations/options'),
  library: () => request<LibraryVideo[]>('/compilations/library'),
  list: () => request<Compilation[]>('/compilations'),
  get: (id: number) => request<Compilation>(`/compilations/${id}`),
  create: (title: string, templateId?: number | null) => {
    // A new compilation starts on the default compilation profile, unless a
    // template is supplying its look.
    const profile = defaultBrandingId('compilation')
    return request<Compilation>(
      '/compilations',
      json('POST', {
        title,
        template_id: templateId ?? null,
        ...(!templateId && profile ? { recipe: { banner: { profile_id: profile } } } : {})
      })
    )
  },
  update: (id: number, patch: { title?: string; recipe?: Recipe }) =>
    request<Compilation>(`/compilations/${id}`, json('PATCH', patch)),
  remove: (id: number) => request<{ ok: boolean }>(`/compilations/${id}`, { method: 'DELETE' }),
  /** Append a segment from outside the editor. No range: the whole video. */
  appendSegment: (id: number, seg: { video_id: string; start?: number; end?: number }) =>
    request<Compilation>(`/compilations/${id}/segments`, json('POST', seg)),
  render: (id: number) =>
    request<{ job_id: number; started: boolean }>(`/compilations/${id}/render`, { method: 'POST' }),
  /** Every compilation queued or rendering, keyed by id. */
  progress: () => request<Record<string, RenderProgress>>('/compilations/progress'),
  /** Take a waiting render off the queue, or stop a running one. */
  cancelRender: (id: number) =>
    request<{ state: 'removed' | 'cancelling' | 'idle' }>(`/compilations/${id}/cancel`, {
      method: 'POST'
    }),
  mediaUrl: (id: number, updatedAt: string, canvas = '') =>
    `${API_BASE}/compilations/${id}/media?canvas=${encodeURIComponent(canvas.replace(':', 'x'))}&v=${encodeURIComponent(updatedAt)}`,
  renders: (id: number) => request<CompilationRender[]>(`/compilations/${id}/renders`),
  renderMediaUrl: (id: number, renderId: number, canvas = '') =>
    `${API_BASE}/compilations/${id}/renders/${renderId}/media?canvas=${encodeURIComponent(canvas.replace(':', 'x'))}`,
  restoreRender: (id: number, renderId: number) =>
    request<Compilation>(`/compilations/${id}/renders/${renderId}/restore`, { method: 'POST' }),
  deleteRender: (id: number, renderId: number) =>
    request<{ deleted: boolean; compilation: Compilation }>(`/compilations/${id}/renders/${renderId}`, {
      method: 'DELETE'
    }),
  settings: () => request<CompilationSettings>('/compilation-settings'),
  saveSettings: (s: CompilationSettings) =>
    request<CompilationSettings & { removed: number }>('/compilation-settings', json('PUT', s)),
  unusedFiles: () => request<{ name: string; bytes: number }[]>('/compilation-files/unused'),
  cleanUnused: () =>
    request<{ deleted: string[]; kept: string[] }>('/compilation-files/unused', { method: 'DELETE' }),
  sourceUrl: (videoId: string) => `${API_BASE}/compilations/source/${encodeURIComponent(videoId)}`,
  templates: () => request<CompilationTemplate[]>('/compilation-templates'),
  saveTemplate: (name: string, config: Recipe) =>
    request<CompilationTemplate>('/compilation-templates', json('POST', { name, config })),
  updateTemplate: (id: number, name: string, config: Recipe) =>
    request<CompilationTemplate>(`/compilation-templates/${id}`, json('PUT', { name, config })),
  /** Measure each part's loudness (cached, and shared with the render). */
  measureLoudness: (id: number) =>
    request<LoudnessReport>(`/compilations/${id}/loudness`, { method: 'POST' }),
  /** Rename only; the look is left exactly as stored. */
  renameTemplate: (id: number, name: string) =>
    request<CompilationTemplate>(`/compilation-templates/${id}`, json('PATCH', { name })),
  deleteTemplate: (id: number) =>
    request<{ ok: boolean }>(`/compilation-templates/${id}`, { method: 'DELETE' }),
  setCredit: (videoId: string, patch: { channel_name?: string; channel_url?: string; rights?: Rights }) =>
    request<LibraryVideo>(`/videos/${encodeURIComponent(videoId)}/credit`, json('PATCH', patch))
}
