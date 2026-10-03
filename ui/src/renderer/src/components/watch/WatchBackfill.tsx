import { useState, type ReactNode } from 'react'
import type { WatchBackfill as Backfill, WatchPlatform } from '../../lib/types'
import { t } from '../../lib/i18n'

export type BackfillMode = 'none' | 'count' | 'since'

export interface BackfillValue {
  mode: BackfillMode
  count: number
  since: string
  /** With `since`: the most videos to look through. */
  cap: number
  newest_at: Backfill['newest_at']
}

export const DEFAULT_BACKFILL: BackfillValue = {
  mode: 'none',
  count: 10,
  since: '',
  cap: 100,
  newest_at: 'bottom'
}

const COUNTS = [5, 10, 25, 50, 100]
const CAPS = [25, 50, 100, 250, 500, 1000]

/** What the server takes, or undefined for "leave earlier videos alone". */
export function backfillBody(v: BackfillValue): Backfill | undefined {
  if (v.mode === 'count') return { count: v.count, since: '', newest_at: v.newest_at }
  if (v.mode === 'since' && v.since) return { count: v.cap, since: v.since, newest_at: v.newest_at }
  return undefined
}

/** "2026-06-27", for the date input's default: three months back. */
function monthsAgo(n: number): string {
  const d = new Date()
  d.setMonth(d.getMonth() - n)
  return d.toISOString().slice(0, 10)
}

function Segment({
  on,
  onClick,
  children
}: {
  on: boolean
  onClick: () => void
  children: ReactNode
}): JSX.Element {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
        on ? 'bg-accent text-base font-semibold' : 'text-muted hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

/** Catching up when a channel or playlist is first watched: leave what is
 *  already there alone (the default), take the latest few, or everything back
 *  to a date. Only the choice that is on shows its settings. */
export default function WatchBackfill({
  value,
  onChange,
  playlist,
  platform
}: {
  value: BackfillValue
  onChange: (next: BackfillValue) => void
  /** A playlist: its listing has no dates, so which end is new is asked. */
  playlist: boolean
  platform: WatchPlatform
}): JSX.Element {
  const set = (patch: Partial<BackfillValue>): void => onChange({ ...value, ...patch })
  const [custom, setCustom] = useState(!COUNTS.includes(value.count))
  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-3">
      <div className="inline-flex flex-wrap rounded-lg bg-raised/60 p-1 gap-1" role="radiogroup">
        <Segment on={value.mode === 'none'} onClick={() => set({ mode: 'none' })}>
          {t('Leave them')}
        </Segment>
        <Segment on={value.mode === 'count'} onClick={() => set({ mode: 'count' })}>
          {t('Take the latest…')}
        </Segment>
        <Segment
          on={value.mode === 'since'}
          onClick={() => set({ mode: 'since', since: value.since || monthsAgo(3) })}
        >
          {t('Go back to a date…')}
        </Segment>
      </div>

      {value.mode === 'none' && (
        <p className="text-xs text-muted">
          {t('Videos already there are listed, not taken. You can still take any of them with one click.')}
        </p>
      )}

      {value.mode === 'count' && (
        <div className="flex items-center gap-2 flex-wrap text-sm">
          {COUNTS.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => {
                setCustom(false)
                set({ count: n })
              }}
              className={`rounded-full border px-3 py-1 tabular-nums transition-colors ${
                !custom && value.count === n
                  ? 'border-accent bg-accent/15 text-accent font-semibold'
                  : 'border-raised text-muted hover:text-ink hover:border-accent/40'
              }`}
            >
              {n}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setCustom(true)}
            className={`rounded-full border px-3 py-1 transition-colors ${
              custom ? 'border-accent bg-accent/15 text-accent font-semibold' : 'border-raised text-muted hover:text-ink'
            }`}
          >
            {t('Custom')}
          </button>
          {custom && (
            <input
              type="number"
              min={1}
              max={1000}
              className="input !w-24 !py-1"
              value={value.count}
              aria-label={t('How many earlier videos')}
              onChange={(e) => set({ count: Math.max(1, Math.min(1000, Number(e.target.value) || 1)) })}
            />
          )}
          <span className="text-muted">{t('videos')}</span>
        </div>
      )}

      {value.mode === 'since' && (
        <div className="flex items-center gap-x-3 gap-y-2 flex-wrap text-sm">
          <span className="text-muted">{t('Posted on or after')}</span>
          <input
            type="date"
            className="input !w-44 !py-1"
            value={value.since}
            max={today}
            onChange={(e) => set({ since: e.target.value })}
          />
          <span className="text-muted">{t('looking through at most')}</span>
          <select
            className="input !w-24 !py-1"
            value={value.cap}
            onChange={(e) => set({ cap: Number(e.target.value) })}
          >
            {CAPS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <span className="text-muted">{t('videos')}</span>
        </div>
      )}

      {value.mode !== 'none' && playlist && (
        <div className="flex items-center gap-3 flex-wrap text-sm">
          <span className="text-muted">{t('New videos are added to the')}</span>
          <div className="inline-flex rounded-lg bg-raised/60 p-0.5 gap-0.5" role="radiogroup">
            <Segment on={value.newest_at === 'bottom'} onClick={() => set({ newest_at: 'bottom' })}>
              {t('Bottom')}
            </Segment>
            <Segment on={value.newest_at === 'top'} onClick={() => set({ newest_at: 'top' })}>
              {t('Top')}
            </Segment>
          </div>
          <span className="text-muted">{t('of this playlist')}</span>
          <span className="text-xs text-muted basis-full">
            {t('YouTube adds to the bottom unless the playlist’s owner changed it. Playlists list no dates, so this says which end is newest.')}
          </span>
        </div>
      )}

      {value.mode !== 'none' && (
        <p className="text-xs text-muted">
          {t('Each one is checked first: anything already in your Library, already clipped, or already watched elsewhere is not downloaded again.')}
          {value.mode === 'since' && playlist
            ? ` ${t('A playlist’s videos are dated as they are checked, so older ones are passed over then.')}`
            : ''}
          {platform === 'twitch'
            ? ` ${t('Twitch lists its most recent page of past broadcasts, so a large number may find fewer.')}`
            : ''}
        </p>
      )}
    </div>
  )
}
