// What the platforms allow, against what has been done and is planned
// (server/quota_api.py). Always an estimate: YouTube's own refusal is the truth.
import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from './api'

export interface ForecastItem {
  provider: string
  platform: string
  count: number
  limit: number | null
  over: boolean
}

export interface Quota {
  estimate: true
  youtube: {
    enabled: boolean
    uploads_used: number
    uploads_limit: number
    uploads_remaining: number
    units_used: number
    units_limit: number
    units_remaining: number
    calls: Record<string, number>
    resets_at: string
  }
  costs: Record<string, number>
  limits: { provider: string; platform: string; per_day: number; source: string }[]
  forecast: {
    days: { date: string; items: ForecastItem[] }[]
    youtube_queued_uploads: number
    youtube_queued_units: number
  }
}

export async function fetchQuota(): Promise<Quota> {
  const res = await fetch(`${API_BASE}/quota`)
  if (!res.ok) throw new Error((await res.text().catch(() => '')) || `HTTP ${res.status}`)
  return res.json() as Promise<Quota>
}

/** The quota, loaded on mount. `reload` after an action that spends some. */
export function useQuota(): { quota: Quota | null; reload: () => void } {
  const [quota, setQuota] = useState<Quota | null>(null)
  const reload = useCallback((): void => {
    fetchQuota()
      .then(setQuota)
      .catch(() => setQuota(null))
  }, [])
  useEffect(() => reload(), [reload])
  return { quota, reload }
}

/** Costs in YouTube's own "units" (quota points), shown on actions. */
export const COST = {
  thumbnail: 50, // thumbnails.set
  playlist: 50, // playlistItems.insert
  channelRead: 3, // channels.list + playlistItems.list + videos.list, per 50 videos
  delete: 50, // videos.delete
  replace: 101, // videos.list + thumbnail + videos.delete, plus one upload
  unschedule: 51, // videos.list + videos.update
  upload: 1 // one of the day's 100 uploads; videos.insert has its own bucket
}

/** Units for reading `videos` of the channel, as publish/quota.read_cost. */
export const readUnits = (videos: number): number => 1 + 2 * Math.max(1, Math.ceil(videos / 50))

/** The documented daily cap for a provider's platform, or undefined when
 *  none is published (never guessed). */
export function limitFor(q: Quota | null, provider: string, platform: string): number | undefined {
  return q?.limits.find((l) => l.provider === provider && l.platform === platform)?.per_day
}
