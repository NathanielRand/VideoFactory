import { t } from '../lib/i18n'
import { shortWhen, type ItemState, type ItemStateName, type VideoStage, type VideoState } from '../lib/publishState'

// One look for "where is this?" wherever it appears. Colour carries the
// meaning (green done, amber waiting, red needs you), and the icon and words
// carry it too, so it never depends on colour alone.
const ITEM: Record<ItemStateName, { icon: string; label: string; tone: string }> = {
  ready: { icon: '○', label: 'Ready', tone: 'bg-raised text-muted' },
  exported: { icon: '⇩', label: 'Exported', tone: 'bg-sky-500/15 text-sky-300' },
  scheduled: { icon: '◷', label: 'Scheduled', tone: 'bg-amber-500/15 text-amber-300' },
  publishing: { icon: '↻', label: 'Publishing', tone: 'bg-accent/20 text-accent animate-pulse' },
  partial: { icon: '◐', label: 'Partly posted', tone: 'bg-emerald-500/10 text-amber-300' },
  published: { icon: '✓', label: 'Posted', tone: 'bg-emerald-500/15 text-emerald-300' },
  failed: { icon: '!', label: 'Failed', tone: 'bg-red-500/15 text-red-300' }
}

const STAGE: Record<VideoStage, { icon: string; label: string; tone: string }> = {
  processing: { icon: '↻', label: 'Processing', tone: 'bg-accent/20 text-accent animate-pulse' },
  ready: { icon: '○', label: 'Ready to post', tone: 'bg-raised text-muted' },
  partial: { icon: '◐', label: 'In progress', tone: 'bg-amber-500/15 text-amber-300' },
  queued: { icon: '◷', label: 'All queued', tone: 'bg-amber-500/15 text-amber-300' },
  complete: { icon: '✓', label: 'All posted', tone: 'bg-emerald-500/15 text-emerald-300' },
  attention: { icon: '!', label: 'Needs attention', tone: 'bg-red-500/15 text-red-300' }
}

const pill = 'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none whitespace-nowrap'

/** The tooltip: every fact the badge is short for. */
function describe(s: ItemState): string {
  const bits: string[] = []
  if (s.published) bits.push(`${s.published} ${t('posted')}`)
  if (s.scheduled) bits.push(`${s.scheduled} ${t('scheduled')}${s.next_at ? ` (${t('next')} ${shortWhen(s.next_at)})` : ''}`)
  if (s.publishing) bits.push(`${s.publishing} ${t('sending now')}`)
  if (s.failed) bits.push(`${s.failed} ${t('failed')}`)
  if (s.platforms.length) bits.push(s.platforms.join(', '))
  if (s.exported) bits.push(t('exported'))
  return [...bits, ...s.errors].join(' · ') || t('Not posted yet')
}

/** One clip or compilation. With `next`, a scheduled item names its time. */
export function ItemBadge({
  state,
  next = false,
  className = ''
}: {
  state: ItemState | undefined
  next?: boolean
  className?: string
}): JSX.Element | null {
  if (!state) return null
  const v = ITEM[state.state]
  const when = next && state.next_at && (state.state === 'scheduled' || state.state === 'partial')
  return (
    <span className={`${pill} ${v.tone} ${className}`} title={describe(state)}>
      <span aria-hidden>{v.icon}</span>
      {t(v.label)}
      {when ? ` · ${shortWhen(state.next_at)}` : ''}
    </span>
  )
}

/** A whole video: its stage, and how many of its clips have got how far. */
export function VideoBadge({
  state,
  detail = false
}: {
  state: VideoState | undefined
  /** Add "5/12 posted" beside the stage. */
  detail?: boolean
}): JSX.Element | null {
  if (!state) return null
  const v = STAGE[state.stage]
  const tip = [
    `${state.clips} ${t('clips')}`,
    state.published && `${state.published} ${t('posted')}`,
    state.scheduled && `${state.scheduled} ${t('scheduled')}${state.next_at ? ` (${t('next')} ${shortWhen(state.next_at)})` : ''}`,
    state.publishing && `${state.publishing} ${t('sending now')}`,
    state.ready && `${state.ready} ${t('not posted')}`,
    state.failed && `${state.failed} ${t('failed')}`
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`${pill} ${v.tone}`} title={tip}>
        <span aria-hidden>{v.icon}</span>
        {t(v.label)}
      </span>
      {detail && state.clips > 0 && (
        <span className="text-[11px] text-muted tabular-nums" title={tip}>
          {state.published}/{state.clips} {t('posted')}
        </span>
      )}
    </span>
  )
}

/** A segmented bar of a video's clips: posted, scheduled, sending, failed,
 *  waiting. Shows at a glance how far through the pipeline the video is. */
export function VideoProgress({ state }: { state: VideoState | undefined }): JSX.Element | null {
  if (!state || state.clips === 0) return null
  const parts: [number, string, string][] = [
    [state.published, 'bg-emerald-400', 'posted'],
    [state.publishing, 'bg-accent', 'sending now'],
    [state.scheduled, 'bg-amber-400', 'scheduled'],
    [state.failed, 'bg-red-400', 'failed'],
    [state.ready, 'bg-raised', 'not posted']
  ]
  return (
    <div
      className="flex h-1.5 w-full rounded-full overflow-hidden bg-raised"
      role="img"
      aria-label={parts.filter(([n]) => n).map(([n, , l]) => `${n} ${t(l)}`).join(', ')}
    >
      {parts.map(([n, tone, l]) =>
        n ? <span key={l} className={tone} style={{ width: `${(n / state.clips) * 100}%` }} /> : null
      )}
    </div>
  )
}
