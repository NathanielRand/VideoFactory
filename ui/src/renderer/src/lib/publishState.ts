// Where a clip, compilation or video stands on the way out. The server folds
// every place posting state lives into one summary (publish/rollup.py); this
// reads it and keeps it current, so every screen shows the same badge.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE } from './api'
import type { StudioEvent } from './types'
import { useEvents } from './useEvents'

export type ItemStateName =
  | 'ready' // made, nothing posted
  | 'exported' // saved or sent out by hand, not posted
  | 'scheduled' // queued for a later time
  | 'publishing' // being sent now
  | 'partial' // on some platforms, others still to come
  | 'published'
  | 'failed'

export interface ItemState {
  state: ItemStateName
  published: number
  scheduled: number
  publishing: number
  failed: number
  platforms: string[]
  /** The soonest scheduled post, ISO. */
  next_at: string
  exported: boolean
  errors: string[]
}

export type VideoStage =
  | 'processing' // still being clipped
  | 'ready' // clips made, none posted
  | 'partial' // some posted or queued, some still waiting
  | 'queued' // every clip is posted or scheduled
  | 'complete' // every clip has been posted
  | 'attention' // something failed

export interface VideoState {
  stage: VideoStage
  clips: number
  published: number
  scheduled: number
  publishing: number
  failed: number
  ready: number
  exported: number
  next_at: string
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) throw new Error(String(res.status))
  return res.json() as Promise<T>
}

/** Re-read on anything that can change a post's state, and now and then in
 *  case an event was missed: a scheduled post turns published with no event
 *  from us at all. */
function useRefreshing(load: () => void): void {
  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [load])
  useEvents((e: StudioEvent) => {
    if (e.type === 'publish' || e.type === 'exports' || e.type === 'compilation') load()
    if (e.type === 'job' && (e.status === 'done' || e.status === 'failed')) load()
  })
}

/** The new states, but with each entry that did not change kept as the same
 *  object, and the whole map kept when nothing changed at all. This is polled
 *  every 30 seconds and on every job event; without it each poll handed every
 *  clip card a new object and redrew all of them for nothing. */
export function sameStates<T>(prev: Record<number, T>, next: Record<number, T>): Record<number, T> {
  const keys = Object.keys(next)
  let same = keys.length === Object.keys(prev).length
  const merged: Record<number, T> = {}
  for (const k of keys) {
    const id = Number(k)
    const old = prev[id]
    if (old !== undefined && JSON.stringify(old) === JSON.stringify(next[id])) merged[id] = old
    else {
      merged[id] = next[id]
      same = false
    }
  }
  return same ? prev : merged
}

/** States for clips (positive ids) and compilations (negative), by id. */
export function usePublishStates(ids: number[]): Record<number, ItemState> {
  const [states, setStates] = useState<Record<number, ItemState>>({})
  const key = useMemo(() => [...new Set(ids)].sort((a, b) => a - b).join(','), [ids])
  const load = useCallback(() => {
    if (!key) {
      setStates({})
      return
    }
    get<Record<string, ItemState>>(`/publish/states?ids=${key}`)
      .then((got) => setStates((prev) => sameStates(prev, Object.fromEntries(Object.entries(got).map(([k, v]) => [Number(k), v])))))
      .catch(() => undefined) // engine not up yet: no badges, not an error
  }, [key])
  useRefreshing(load)
  return states
}

/** Every video's clips gathered into one status, by video id. */
export function useVideoStates(): Record<string, VideoState> {
  const [states, setStates] = useState<Record<string, VideoState>>({})
  const load = useCallback(() => {
    get<Record<string, VideoState>>('/publish/video-states')
      .then(setStates)
      .catch(() => undefined)
  }, [])
  useRefreshing(load)
  return states
}

/** "Sep 30, 7:00 PM" — short enough for a badge. */
export function shortWhen(iso: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  return at.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
