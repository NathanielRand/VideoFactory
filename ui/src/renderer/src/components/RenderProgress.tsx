import type { RenderProgress as Progress } from '../lib/compilations'
import { t } from '../lib/i18n'

/** "1:05", "12:40", "1:02:07". */
function clock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

function left(seconds: number): string {
  if (seconds < 60) return t('less than a minute left')
  const minutes = Math.round(seconds / 60)
  return minutes < 90
    ? `${t('about')} ${minutes} ${t('min left')}`
    : `${t('about')} ${(minutes / 60).toFixed(1)} ${t('h left')}`
}

/** The bar itself: an animated stripe while it waits, the fill once running. */
function Bar({ percent, waiting }: { percent: number; waiting: boolean }): JSX.Element {
  return (
    <div
      className="h-2 rounded-full bg-raised overflow-hidden"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={waiting ? undefined : percent}
    >
      {waiting ? (
        <div className="h-full w-1/3 rounded-full bg-yellow-400/50 animate-pulse" />
      ) : (
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-700 ease-out"
          style={{ width: `${Math.max(2, percent)}%` }}
        />
      )}
    </div>
  )
}

/** A compilation's render: how far along, what it is on, how long to go.
 *
 *  `compact` is the one-line version for the compilations list. The full one
 *  sits in the editor with the Cancel button. Nothing is estimated here: the
 *  percent and the time left come from the engine, which reads them from
 *  FFmpeg as each part encodes. */
export default function RenderProgress({
  progress,
  compact = false,
  onCancel,
  cancelling = false
}: {
  progress: Progress | undefined
  compact?: boolean
  onCancel?: () => void
  cancelling?: boolean
}): JSX.Element {
  const running = progress?.state === 'running'
  const percent = running ? Math.min(100, progress?.percent ?? 0) : 0
  const waiting = !running

  if (compact) {
    // The percent is on the row's status chip; this is the bar, and why a
    // waiting one is waiting.
    return (
      <div className="mt-1.5 space-y-0.5">
        <Bar percent={percent} waiting={waiting} />
        {waiting && (
          <div className="text-[11px] text-muted">
            {progress?.queue_paused ? t('Queue stopped') : t('Waiting…')}
          </div>
        )}
      </div>
    )
  }

  let detail: string
  if (running) {
    const parts = [progress?.label || t('Rendering')]
    if (progress?.eta_seconds != null) parts.push(left(progress.eta_seconds))
    detail = parts.join(' · ')
  } else if (progress?.queue_paused) {
    detail = t('The queue is stopped. Start it on the Queue page to render.')
  } else if (progress?.ahead) {
    detail = `${t('Waiting in the queue')} · ${progress.ahead} ${t(progress.ahead === 1 ? 'job ahead' : 'jobs ahead')}`
  } else {
    detail = t('Starting…')
  }

  return (
    <div
      className={`card space-y-2 border ${running ? '!border-accent/40' : '!border-yellow-400/30'}`}
      aria-live="polite"
    >
      <div className="flex items-baseline gap-3">
        <span className="text-2xl font-bold tabular-nums">{running ? `${percent}%` : '—'}</span>
        <span className="text-sm font-medium">
          {running ? t('Rendering') : t('Queued to render')}
        </span>
        {running && progress?.elapsed_seconds != null && (
          <span className="text-xs text-muted tabular-nums">
            {clock(progress.elapsed_seconds)} {t('elapsed')}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {progress?.queue_paused && !running && (
            <button
              className="btn-ghost !px-2.5 !py-1 text-xs"
              onClick={() => window.dispatchEvent(new CustomEvent('open-queue'))}
            >
              {t('Open queue')}
            </button>
          )}
          {onCancel && (
            <button
              className="btn-ghost !px-2.5 !py-1 text-xs hover:!text-error"
              disabled={cancelling}
              onClick={onCancel}
            >
              {cancelling ? t('Stopping…') : running ? t('Stop render') : t('Take off the queue')}
            </button>
          )}
        </span>
      </div>
      <Bar percent={percent} waiting={waiting} />
      <p className="text-xs text-muted truncate" title={detail}>
        {detail}
      </p>
    </div>
  )
}
