// Reach and search for every publisher: SEO defaults, the pre-publish check,
// per-platform captions, best times and compilation publish metadata.
// Routes are server/publishing_api.py.
import { API_BASE } from './api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
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

const json = (method: string, body: unknown): RequestInit => ({
  method,
  body: JSON.stringify(body)
})

export interface PublishingSettings {
  channel_keywords: string[]
  hashtags: string[]
  first_comment: string
  platform_captions: boolean
  /** Creator name -> playlist id; "__compilations__" for compilations. */
  playlist_rules: Record<string, string>
  auto_playlists: boolean
  /** "Source: <channel> - <link>" in every description. */
  link_source: boolean
  /** A link to the playlist in the description of a YouTube upload that joined one. */
  link_playlist: boolean
  /** "<title> | <source channel> #Tag" on every title as it is published. */
  title_channel: boolean
  title_hashtag_on: boolean
  /** The tag on the title; empty means the first always-on hashtag. */
  title_hashtag: string
  /** The posting schedule: best hours of the day, or the clock times below. */
  slot_mode: 'best' | 'fixed'
  fixed_times: string[]
  /** Days posts may go out, Monday = 0. */
  slot_days: number[]
  per_day: number
  min_gap_hours: number
}

export const COMPILATIONS_PLAYLIST = '__compilations__'

export interface SeoTip {
  level: 'good' | 'warn' | 'bad'
  message: string
}

export interface SeoReport {
  score: number
  tips: SeoTip[]
}

export interface SeoDraft {
  title: string
  description: string
  keywords: string[]
  hashtags: string[]
  long_form?: boolean
  has_thumbnail?: boolean
  has_playlist?: boolean
}

export interface BestTimes {
  slots: { day_offset: number; hour: number }[]
  top: { weekday: number; hour: number; weight: number }[]
  confidence: number
  learned_from: number
}

export interface CompilationPublishMeta {
  title: string
  description: string
  hashtags: string[]
  keywords: string[]
  first_comment: string
  /** The AI's comment for this compilation; used only when picked. */
  suggested_comment?: string
  alt_titles: string[]
  canvas: string
  summary?: string
  chapters?: string
  credits?: string
  long_form?: boolean
}

export const publishingApi = {
  settings: () => request<PublishingSettings>('/publishing/settings'),
  saveSettings: (patch: Partial<PublishingSettings>) =>
    request<PublishingSettings>('/publishing/settings', json('PATCH', patch)),
  check: (draft: SeoDraft) => request<SeoReport>('/publishing/check', json('POST', draft)),
  captions: (body: {
    title: string
    description: string
    hashtags: string[]
    platforms: string[]
    footer?: string
  }) =>
    request<{
      captions: Record<string, string>
      limits: Record<string, { caption_max: number; visible: number; hashtags: number }>
    }>('/publishing/captions', json('POST', body)),
  standingComment: () => request<{ first_comment: string }>('/publishing/standing-comment'),
  /** An AI first comment for a clip (id > 0) or compilation (id < 0). */
  suggestComment: (publishId: number) =>
    request<{ suggested_comment: string }>(
      `/publishing/suggest-comment/${publishId}`,
      json('POST', {})
    ),
  /** Add the source channel to the stored metadata of clips that lack it. */
  applySourceMetadata: () =>
    request<{ updated: number; total: number }>('/publishing/apply-source-metadata', json('POST', {})),
  refreshStats: () =>
    request<{ updated: number; reason?: string }>('/publishing/stats/refresh', json('POST', {})),

  /** Best posting times from now, in this computer's timezone. */
  bestTimes: (platform: string, count: number, opts?: { perDay?: number; gap?: number }) => {
    const now = new Date()
    const params = new URLSearchParams({
      platform,
      count: String(count),
      // getTimezoneOffset is minutes WEST of UTC; the server wants east.
      offset: String(-now.getTimezoneOffset()),
      weekday: String((now.getDay() + 6) % 7), // Monday = 0, as Python counts
      hour: String(now.getHours())
    })
    if (opts?.perDay) params.set('per_day', String(opts.perDay))
    if (opts?.gap) params.set('gap', String(opts.gap))
    return request<BestTimes>(`/publishing/best-times?${params}`)
  },

  compilationMeta: (id: number) =>
    request<{
      meta: Partial<CompilationPublishMeta>
      publish_id: number
      outputs: string[]
      status: string
    }>(`/compilations/${id}/publish-meta`),
  saveCompilationMeta: (id: number, meta: Partial<CompilationPublishMeta>) =>
    request<{ meta: CompilationPublishMeta }>(
      `/compilations/${id}/publish-meta`,
      json('PUT', meta)
    ),
  /** A draft, NOT saved: saving is always the person's Save. ai=false
   *  returns at once with chapters, credits and plain text. */
  generateCompilationMeta: (id: number, ai = true) =>
    request<{ meta: CompilationPublishMeta }>(
      `/compilations/${id}/publish-meta/generate?save=false${ai ? '' : '&ai=false'}`,
      json('POST', {})
    )
}

/** A best-times slot as a real instant. Built with the LOCAL Date
 *  constructor, so the offset in force on that day is applied and a slot
 *  across a daylight-saving change still lands at the intended wall-clock hour. */
export function slotToIso(slot: { day_offset: number; hour: number }, minute = 0): string {
  const now = new Date()
  const at = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate() + slot.day_offset,
    slot.hour,
    minute
  )
  return at.toISOString().replace(/\.\d{3}Z$/, 'Z')
}

/** The same instant as a `datetime-local` input value. */
export function slotToLocalInput(slot: { day_offset: number; hour: number }): string {
  const now = new Date()
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate() + slot.day_offset, slot.hour)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:00`
  )
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export function describeHour(weekday: number, hour: number): string {
  const h12 = hour % 12 === 0 ? 12 : hour % 12
  return `${WEEKDAYS[weekday]} ${h12}${hour < 12 ? 'am' : 'pm'}`
}

/** Comma-separated text to a clean list. */
export function splitList(raw: string): string[] {
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}
