// Video Factory: output formats (formats/profiles.py) and AI-clip variants
// (server/formats_api.py).
import { API_BASE } from './api'

export type CanvasKey = '9:16' | '16:9' | '1:1' | '4:5'

export interface PlatformProfile {
  label: string
  canvas: CanvasKey
  max_seconds: number | null
}

export interface FormatsInfo {
  canvases: Record<CanvasKey, { width: number; height: number }>
  profiles: Record<string, PlatformProfile>
}

export interface ClipVariant {
  clip_id: number
  canvas: CanvasKey
  path: string
  stale: number
  created_at: string
  exists: boolean
}

export interface ClipVariants {
  canvas: CanvasKey
  variants: ClipVariant[]
  warnings: Record<CanvasKey, string[]>
}

export const CANVAS_ORDER: CanvasKey[] = ['9:16', '16:9', '4:5', '1:1']

export const canvasTag = (c: string): string => c.replace(':', 'x')

/** "YouTube Shorts, TikTok, …" for one canvas. */
export function platformsFor(info: FormatsInfo | null, canvas: string): string {
  if (!info) return ''
  return Object.values(info.profiles)
    .filter((p) => p.canvas === canvas)
    .map((p) => p.label)
    .join(', ')
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  if (!res.ok) {
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

export const formatsApi = {
  info: () => request<FormatsInfo>('/formats'),
  variants: (clipId: number) => request<ClipVariants>(`/clips/${clipId}/variants`),
  render: (clipId: number, canvases: string[]) =>
    request<{ job_id: number; started: boolean }>(`/clips/${clipId}/variants`, {
      method: 'POST',
      body: JSON.stringify({ canvases })
    }),
  mediaUrl: (clipId: number, canvas: string, stamp: string) =>
    `${API_BASE}/clips/${clipId}/variants/${canvasTag(canvas)}/media?v=${encodeURIComponent(stamp)}`
}
