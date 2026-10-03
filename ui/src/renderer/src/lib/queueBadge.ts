import { useEffect, useState } from 'react'
import { api } from './api'
import { applyEvent, displayFraction, emptyProgress, type JobProgress } from './jobProgress'
import { useEvents } from './useEvents'

/** What the sidebar's Queue link shows: how far the running job is, and how
 *  many jobs are still to come after it. Mounted at the app shell, so it is
 *  right whichever page is open. */
export interface QueueBadge {
  /** A job is running. */
  active: boolean
  /** 0..100, the running job. */
  percent: number
  /** Jobs still waiting behind the running one. */
  waiting: number
  /** The running job's step, for the tooltip. */
  label: string
}

export function useQueueBadge(): QueueBadge {
  const [progress, setProgress] = useState<JobProgress>({ ...emptyProgress })
  const [running, setRunning] = useState(0)
  const [waiting, setWaiting] = useState(0)
  const [now, setNow] = useState(Date.now())

  const refresh = (): void => {
    api
      .queue()
      .then((q) => {
        setRunning(q.processing.length)
        setWaiting(q.queued.length)
        // A terminal event can be dropped; the queue itself is the truth.
        if (q.processing.length === 0) setProgress({ ...emptyProgress })
      })
      .catch(() => undefined) // engine not up yet
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 10000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [])

  useEvents((e) => {
    setProgress((p) => applyEvent(p, e))
    if (e.type === 'queue' || e.type === 'job') refresh()
  })

  const active = running > 0 || progress.active
  return {
    active,
    percent: active ? Math.round(displayFraction(progress, now) * 100) : 0,
    waiting,
    label: progress.label
  }
}
