import type { StudioEvent } from './types'

/** Folds WebSocket progress events into one overall job progress value
 *  (0..1) using stage weights, plus a label and timing for the ETA. */

export interface JobProgress {
  active: boolean
  title: string
  label: string
  fraction: number
  startedAt: number
  videoId: string
  /** Where the current step ends (0..1): the bar eases toward it between
   *  events so it keeps moving while a long step runs. 0 = no such hint. */
  ceil: number
  /** When the last event arrived, for that easing. */
  eventAt: number
}

const STAGES: Record<string, { base: number; weight: number; label: string }> = {
  download: { base: 0.0, weight: 0.15, label: 'Downloading video' },
  downloaded: { base: 0.15, weight: 0.0, label: 'Downloaded' },
  transcribe: { base: 0.15, weight: 0.25, label: 'Transcribing speech' },
  signals: { base: 0.4, weight: 0.05, label: 'Analyzing audio & visuals' },
  analyze: { base: 0.45, weight: 0.2, label: 'Finding the best moments' },
  // Ranking used to be inside `analyze`, and reported nothing. The bar
  // reached the end of analyze and sat at 70% through one LLM call per batch
  // of finalists, which on a CPU-only machine looks exactly like a crash.
  ranking: { base: 0.65, weight: 0.05, label: 'Ranking the best moments' },
  reactions: { base: 0.7, weight: 0.08, label: 'Scoring on-screen reactions' },
  render: { base: 0.78, weight: 0.22, label: 'Rendering clips' },
  // Video Factory: a compilation job is one stage (server/jobs.py _STAGES).
  compile: { base: 0.0, weight: 1.0, label: 'Rendering compilation' },
  variants: { base: 0.0, weight: 1.0, label: 'Rendering other formats' },
  // One clip re-rendered: the render names its own steps (core/progress.py).
  rerender: { base: 0.0, weight: 1.0, label: 'Re-rendering clip' }
}

export const emptyProgress: JobProgress = {
  active: false,
  title: '',
  label: '',
  fraction: 0,
  startedAt: 0,
  videoId: '',
  ceil: 0,
  eventAt: 0
}

/** Survives page switches: whichever page is mounted keeps it updated. */
export const progressStore: { current: JobProgress } = { current: { ...emptyProgress } }

export function applyEvent(p: JobProgress, e: StudioEvent): JobProgress {
  if (e.type === 'job') {
    if (e.status === 'running')
      return { ...emptyProgress, active: true, label: 'Starting…', startedAt: Date.now() }
    if (e.status === 'done' || e.status === 'failed' || e.status === 'cancelled')
      return { ...emptyProgress }
  }
  if (e.type !== 'progress') return p
  if (e.stage === 'done') return { ...emptyProgress }
  // A YouTube upload is not the video pipeline. Without this the unknown-stage
  // branch below would set active:true and light up the global processing bar
  // with a stale label for the duration of every publish.
  if (e.stage === 'publish') return p

  const startedAt = p.active && p.startedAt ? p.startedAt : Date.now()
  const title = e.title || p.title
  const videoId = e.video_id || p.videoId
  const stage = STAGES[e.stage ?? '']
  if (!stage) return { ...p, active: true, startedAt, title, videoId }

  let within = 0.5
  if (typeof e.fraction === 'number') within = e.fraction
  else if (typeof e.clip === 'number' && e.total) within = (e.clip - 1) / e.total
  else if (typeof e.current === 'number' && e.total) within = Math.max(0, e.current - 1) / e.total

  const fraction = Math.min(0.99, stage.base + stage.weight * Math.min(1, Math.max(0, within)))
  const label =
    e.stage === 'render' && e.clip && e.total
      ? `Rendering clip ${e.clip}/${e.total}`
      : (e.stage === 'compile' || e.stage === 'variants' || e.stage === 'rerender') && e.message
        ? String(e.message)
        : stage.label

  return {
    active: true,
    title,
    label,
    fraction: Math.max(fraction, p.fraction), // progress never moves backwards
    startedAt,
    videoId,
    ceil:
      typeof e.ceil === 'number'
        ? Math.min(0.99, stage.base + stage.weight * Math.min(1, Math.max(0, e.ceil)))
        : 0,
    eventAt: Date.now()
  }
}

/** The fraction to SHOW. The server reports where a step starts and where it
 *  ends, but a step can take a minute with nothing in between, so between
 *  events this creeps toward (never reaching) the end of the step: slowly at
 *  first, then flattening, so an honest report always lands ahead of it. */
export function displayFraction(p: JobProgress, now: number): number {
  if (!p.active || p.ceil <= p.fraction) return p.fraction
  const waited = Math.max(0, now - p.eventAt) / 1000
  const room = (p.ceil - p.fraction) * 0.9
  return p.fraction + room * (1 - Math.exp(-waited / 10))
}

/** Remaining seconds, extrapolated from elapsed time vs fraction complete.
 *  Recomputed against a live clock, so it counts down between events. */
export function etaSeconds(p: JobProgress, now: number): number | null {
  const f = displayFraction(p, now)
  if (!p.active || f < 0.06) return null // too early to estimate honestly
  const elapsed = (now - p.startedAt) / 1000
  return Math.max(0, (elapsed * (1 - f)) / f)
}

export function formatEta(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return m > 0 ? `${m}m ${s.toString().padStart(2, '0')}s` : `${s}s`
}
