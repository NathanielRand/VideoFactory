// What the connected sources report (server/analytics_api.py). Only YouTube
// reports numbers; the other providers report posts, which is all this shows
// for them.
import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from './api'

export interface Tally {
  videos: number
  views: number
  likes: number
  comments: number
}

export interface TopVideo {
  video_id: string
  title: string
  thumbnail: string
  url: string
  views: number | null
  likes: number | null
  comments: number | null
  kind: 'clip' | 'compilation' | 'other'
  short: boolean
  publish_id: number | null
}

export interface DailyStat {
  date: string
  views: number
  minutes: number
  likes: number
  comments: number
  subscribers: number
  subscribers_lost: number
  average_view_seconds: number
}

/** A video YouTube credits with new subscribers over `days`. */
export interface SubscriberVideo {
  video_id: string
  title: string
  thumbnail: string
  url: string
  gained: number
  lost: number
  views: number
  kind: 'clip' | 'compilation' | 'other'
  publish_id: number | null
}

export interface Subscribers {
  /** Rounded by YouTube; null when hidden or unreadable. */
  total: number | null
  hidden: boolean
  days: number
  top: SubscriberVideo[]
  error: string
}

export interface PostCounts {
  published: number
  scheduled: number
  failed: number
  providers: string[]
}

export interface Analytics {
  youtube: { connected: boolean; channels: { id: string; title: string }[]; error: string }
  posts: Record<string, PostCounts>
  thumbnails: { with: number; without: number; total: number }
  generated_at: string
  // Present only when YouTube is connected and answered.
  totals?: Tally & { average_views: number }
  states?: Record<string, number>
  by_type?: Record<'clip' | 'compilation' | 'other', Tally>
  by_format?: Record<'short' | 'long', Tally>
  by_via?: Record<string, Tally>
  by_channel?: Record<string, Tally>
  timeline?: ({ date: string } & Tally)[]
  /** Real per-day numbers from YouTube Analytics; `error` explains an empty list. */
  daily?: { days: DailyStat[]; error: string }
  subscribers?: Subscribers
  top?: TopVideo[]
}

export async function fetchAnalytics(fresh = false): Promise<Analytics> {
  const res = await fetch(`${API_BASE}/analytics/summary${fresh ? '?fresh=true' : ''}`)
  if (!res.ok) throw new Error((await res.text().catch(() => '')) || `HTTP ${res.status}`)
  return res.json() as Promise<Analytics>
}

/** The summary, loaded once on mount; `reload(true)` asks YouTube again. */
export function useAnalytics(): {
  data: Analytics | null
  error: string
  loading: boolean
  reload: (fresh?: boolean) => void
} {
  const [data, setData] = useState<Analytics | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const reload = useCallback((fresh = false): void => {
    setLoading(true)
    fetchAnalytics(fresh)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => reload(), [reload])
  return { data, error, loading, reload }
}

export const compact = (n: number | null | undefined): string =>
  n === null || n === undefined
    ? '—'
    : new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)

/** Where a YouTube video came from, for people. */
export const VIA_LABEL: Record<string, string> = {
  direct: 'Uploaded from this app',
  youtube: 'Uploaded from this app',
  woopsocial: 'WoopSocial',
  uploadpost: 'Upload-Post',
  manual: 'Posted outside this app',
  app: 'This app'
}
