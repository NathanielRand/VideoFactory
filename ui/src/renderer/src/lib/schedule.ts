// The posting schedule across providers (server/schedule_api.py).
import { thumbsApi } from './thumbnails'
import { API_BASE } from './api'

export interface ScheduleItem {
  /** 'post' is a row with a provider; 'job' is a direct-YouTube upload still
   *  waiting in this app's own queue; 'upload' is one already on YouTube. */
  kind: 'post' | 'job' | 'upload'
  job_id?: number
  /** For 'upload': the video already on YouTube, waiting for its time. */
  youtube_id?: string
  clip_id: number
  platform: string
  provider: string
  state: string
  scheduled_for: string
  post_url: string
  error: string
  title: string
  waiting: boolean
  can_cancel: boolean
  /** How many platforms are cancelled together with this one. */
  cancel_group: number
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text
    try {
      detail = JSON.parse(text).detail ?? text
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

/** Stop a scheduled item wherever it is held: this app's own queue, a
 *  WoopSocial post (every platform it shares), or a video already on YouTube. */
export async function cancelItem(p: ScheduleItem): Promise<void> {
  if (p.kind === 'job' && p.job_id !== undefined) await scheduleApi.cancelJob(p.job_id)
  else if (p.kind === 'upload' && p.youtube_id) await thumbsApi.unscheduleYouTube(p.youtube_id)
  else await scheduleApi.cancel(p.clip_id, p.platform)
}

/** An instant as the value of a datetime-local input, in this computer's time. */
export function isoToLocalInput(iso: string): string {
  const d = new Date(iso)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/** Where a clip is already live or waiting to go out. */
export interface Duplicate {
  platform: string
  provider: string
  state: 'live' | 'scheduled'
  /** When it goes (or went) live, when known. */
  at: string
  url: string
}

export const scheduleApi = {
  /** Which of these clips are already live or scheduled, and where. */
  duplicates: async (clipIds: number[]): Promise<Record<number, Duplicate[]>> => {
    if (!clipIds.length) return {}
    const got = await call<{ duplicates: Record<string, Duplicate[]> }>('/publishing/duplicates', {
      clip_ids: clipIds
    })
    return Object.fromEntries(Object.entries(got.duplicates).map(([k, v]) => [Number(k), v]))
  },
  list: () => call<{ items: ScheduleItem[] }>('/publishing/schedule'),
  /** Stop a scheduled post at the provider (every platform it shares). */
  cancel: (clipId: number, platform: string) =>
    call<{ cancelled: number }>('/publishing/schedule/cancel', { clip_id: clipId, platform }),
  cancelJob: (jobId: number) =>
    call<{ cancelled: boolean }>(`/publishing/schedule/job/${jobId}/cancel`, {}),
  /** Take finished rows off the list (all of them, or the ones given). */
  clear: (items?: { clip_id: number; platform: string }[]) =>
    call<{ cleared: number; refused: { clip_id: number; platform: string; reason: string }[] }>(
      '/publishing/schedule/clear',
      { items: items ?? null }
    ),
  /** The next free slots for a new post, around everything already scheduled. */
  nextSlots: (count: number, platforms: string[], provider: string) =>
    call<{ slots: string[]; short: boolean }>('/publishing/slots/next', { count, platforms, provider }),
  /** The policy's slots over a range, and everything committed or recently handled. */
  calendar: (start: string, days: number) =>
    call<CalendarData>(`/publishing/calendar?start=${start}&days=${days}`)
}

export interface SlotPolicy {
  mode: 'best' | 'fixed'
  per_day: number
  min_gap_hours: number
  fixed_times: string[]
  days: number[]
}

export interface CalendarData {
  policy: SlotPolicy
  start: string
  days: number
  items: (ScheduleItem & { publish_id: number })[]
  /** Waiting posts a provider is queueing itself: no time to place them at. */
  unscheduled: (ScheduleItem & { publish_id: number })[]
  slots: { at: string; taken: boolean }[]
}
