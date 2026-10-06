// What is happening to a clip right now: waiting for a re-render, being
// rendered, translating, making other formats, or a job that just failed.
// The clip row says nothing about any of this until the job is done (the
// server reads it from the jobs table: StateDB.clip_work), so without it a card
// reads "Ready" while its video is being replaced underneath it.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from './api'
import type { StudioEvent } from './types'
import { sameStates } from './publishState'
import { useEvents } from './useEvents'

export interface ClipWork {
  state: 'queued' | 'running' | 'failed'
  kind: 'render' | 'formats' | 'translate'
  job_id: number
  error: string
}

const QUICK_MS = 4_000 // while something is in flight: watch it
const IDLE_MS = 20_000 // otherwise: only catch what an event missed

/** Is this one clip waiting for or in the middle of a render (a re-render, a
 *  caption or colour change)? Editors lock their Apply buttons on it: the
 *  request that queued the work returns at once, so without this the same
 *  button could be pressed again while the first render is still going. */
export function useClipRendering(clipId: number): boolean {
  const ids = useMemo(() => [clipId], [clipId])
  const w = useClipWork(ids)[clipId]
  return !!w && w.kind === 'render' && w.state !== 'failed'
}

/** Work in flight per clip id; clips with nothing going on are absent. */
export function useClipWork(ids: number[]): Record<number, ClipWork> {
  const [work, setWork] = useState<Record<number, ClipWork>>({})
  const key = useMemo(() => [...new Set(ids)].sort((a, b) => a - b).join(','), [ids])
  const busy = useRef(false)

  const load = useCallback(() => {
    if (!key) {
      busy.current = false
      setWork({})
      return
    }
    fetch(`${API_BASE}/clip-work?ids=${key}`)
      .then((res) => (res.ok ? (res.json() as Promise<Record<string, ClipWork>>) : Promise.reject()))
      .then((got) => {
        busy.current = Object.values(got).some((w) => w.state !== 'failed')
        setWork((prev) => sameStates(prev, Object.fromEntries(Object.entries(got).map(([k, v]) => [Number(k), v]))))
      })
      .catch(() => undefined) // engine not up yet: no badges, not an error
  }, [key])

  useEffect(() => {
    load()
    let timer: ReturnType<typeof setTimeout>
    const tick = (): void => {
      load()
      timer = setTimeout(tick, busy.current ? QUICK_MS : IDLE_MS)
    }
    timer = setTimeout(tick, IDLE_MS)
    return () => clearTimeout(timer)
  }, [load])

  // A job starting, moving or ending: look now instead of waiting for the timer.
  useEvents((e: StudioEvent) => {
    if (e.type === 'job' || e.type === 'queue') load()
  })
  return work
}
