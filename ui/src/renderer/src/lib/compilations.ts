// Video Factory: compilations (many sources -> one video). Types mirror
// compilation/recipe.py; routes are server/compilations_api.py.
import { API_BASE } from './api'

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

export interface CreditStyle {
  enabled?: boolean
  template?: string
  seconds?: number
  position?: string
  font_size?: number
}

export interface Recipe {
  canvas?: Canvas
  /** Every format to render; [0] is the primary (canvas). */
  outputs?: Canvas[]
  fit?: Fit
  segments?: SegmentSpec[]
  transition?: { type: string; duration?: number }
  credits?: CreditStyle
  banner?: { profile_id: number } | null
  intro?: { path: string } | null
  outro?: { path: string } | null
  normalize_audio?: boolean
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

export interface CompilationTemplate {
  id: number
  name: string
  config: Recipe
  created_at: string
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

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const compilationsApi = {
  options: () => request<CompilationOptions>('/compilations/options'),
  library: () => request<LibraryVideo[]>('/compilations/library'),
  list: () => request<Compilation[]>('/compilations'),
  get: (id: number) => request<Compilation>(`/compilations/${id}`),
  create: (title: string, templateId?: number | null) =>
    request<Compilation>('/compilations', json('POST', { title, template_id: templateId ?? null })),
  update: (id: number, patch: { title?: string; recipe?: Recipe }) =>
    request<Compilation>(`/compilations/${id}`, json('PATCH', patch)),
  remove: (id: number) => request<{ ok: boolean }>(`/compilations/${id}`, { method: 'DELETE' }),
  render: (id: number) =>
    request<{ job_id: number; started: boolean }>(`/compilations/${id}/render`, { method: 'POST' }),
  mediaUrl: (id: number, updatedAt: string, canvas = '') =>
    `${API_BASE}/compilations/${id}/media?canvas=${encodeURIComponent(canvas.replace(':', 'x'))}&v=${encodeURIComponent(updatedAt)}`,
  sourceUrl: (videoId: string) => `${API_BASE}/compilations/source/${encodeURIComponent(videoId)}`,
  templates: () => request<CompilationTemplate[]>('/compilation-templates'),
  saveTemplate: (name: string, config: Recipe) =>
    request<CompilationTemplate>('/compilation-templates', json('POST', { name, config })),
  deleteTemplate: (id: number) =>
    request<{ ok: boolean }>(`/compilation-templates/${id}`, { method: 'DELETE' }),
  setCredit: (videoId: string, patch: { channel_name?: string; channel_url?: string; rights?: Rights }) =>
    request<LibraryVideo>(`/videos/${encodeURIComponent(videoId)}/credit`, json('PATCH', patch))
}
