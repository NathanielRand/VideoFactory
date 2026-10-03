import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { t } from '../lib/i18n'
import { publishingApi } from '../lib/publishing'
import {
  cancelItem,
  scheduleApi,
  type CalendarData,
  type ScheduleItem,
  type SlotPolicy
} from '../lib/schedule'
import { COST } from '../lib/quota'
import type { LibraryItem } from '../lib/types'
import type { Compilation } from '../lib/compilations'
import { platformLabel } from '../lib/uploadpost'
import ActionButton from './ActionButton'
import { Ban, CalendarX } from './icons'
import Popover from './Popover'
import PublishThumb, { useThumbStatus } from './PublishThumb'
import ScheduleView from './ScheduleView'

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const iso = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const addDays = (d: Date, n: number): Date => {
  const c = new Date(d)
  c.setDate(c.getDate() + n)
  return c
}
const clock = (at: string): string =>
  new Date(at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

interface Chip {
  key: string
  at: string
  publishId: number
  title: string
  platforms: string[]
  state: 'waiting' | 'published' | 'failed' | 'skipped'
  first: ScheduleItem
  cancellable: boolean
}

const CHIP_STYLE: Record<Chip['state'], string> = {
  waiting: 'border-accent/50 bg-accent/10',
  published: 'border-success/40 bg-success/10 opacity-80',
  failed: 'border-error/50 bg-error/10',
  skipped: 'border-raised bg-raised/40 text-muted'
}

/** One post to several platforms is one chip, not one per platform. */
function chips(items: CalendarData['items']): Chip[] {
  const by = new Map<string, Chip>()
  for (const i of items) {
    if (!i.scheduled_for) continue
    const key = `${i.publish_id}|${i.scheduled_for}`
    const state: Chip['state'] = i.waiting
      ? 'waiting'
      : i.state === 'published'
        ? 'published'
        : i.state === 'failed'
          ? 'failed'
          : 'skipped'
    const got = by.get(key)
    if (got) {
      if (!got.platforms.includes(i.platform)) got.platforms.push(i.platform)
      if (state === 'waiting') got.state = 'waiting'
      if (i.can_cancel) {
        got.cancellable = true
        got.first = i
      }
    } else {
      by.set(key, {
        key,
        at: i.scheduled_for,
        publishId: i.publish_id,
        title: i.title,
        platforms: [i.platform],
        state,
        first: i,
        cancellable: i.can_cancel
      })
    }
  }
  return [...by.values()].sort((a, b) => a.at.localeCompare(b.at))
}

/** The posting schedule as a calendar: every scheduled post (clip or
 *  compilation, any provider) in its day, and the schedule's open slots
 *  between them. New posts are added to the next open slot, so this is also
 *  where "what is coming" and "where does the next one go" are answered. */
export default function ScheduleCalendar({
  ready,
  comps,
  onAddVideo,
  onAddComp
}: {
  /** What could be added; without these there is no "Add to schedule". */
  ready?: LibraryItem[]
  comps?: Compilation[]
  onAddVideo?: (v: LibraryItem) => void
  onAddComp?: (c: Compilation) => void
}): JSX.Element {
  const canAdd = Boolean(onAddVideo && onAddComp)
  const [view, setView] = useState<'calendar' | 'list'>('calendar')
  const [span, setSpan] = useState<7 | 14>(7)
  const [start, setStart] = useState(() => new Date())
  const [data, setData] = useState<CalendarData | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const addBtn = useRef<HTMLButtonElement>(null)

  const load = useCallback(async (): Promise<void> => {
    try {
      setData(await scheduleApi.calendar(iso(start), span))
      setError('')
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    }
  }, [start, span])
  useEffect(() => {
    void load()
  }, [load])

  // "Edit schedule" from a publish dialog lands here.
  useEffect(() => {
    const open = (): void => setEditing(true)
    window.addEventListener('schedule-editor-open', open)
    return () => window.removeEventListener('schedule-editor-open', open)
  }, [])

  const all = useMemo(() => chips(data?.items ?? []), [data])
  const haveThumb = useThumbStatus(useMemo(() => [...new Set(all.map((c) => c.publishId))], [all]))

  const days = useMemo(() => Array.from({ length: span }, (_, i) => addDays(start, i)), [start, span])
  const now = Date.now()

  const act = async (fn: () => Promise<unknown>, done: string): Promise<void> => {
    if (busy) return
    setBusy(true)
    setNote('')
    try {
      await fn()
      setNote(done)
      await load()
    } catch (e) {
      setNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const cancel = (c: Chip): void => {
    const more = c.platforms.length > 1 ? ` ${t('It is cancelled for all')} ${c.platforms.length} ${t('platforms')}.` : ''
    if (!window.confirm(`${t('Cancel')} “${c.title}”?${more} ${t('It will not be posted.')}`)) return
    void act(() => cancelItem(c.first), t('Cancelled.'))
  }

  const openSlots = (data?.slots ?? []).filter((s) => !s.taken && Date.parse(s.at) > now).length

  return (
    <section className="card space-y-3" aria-label={t('Posting schedule')}>
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="font-semibold">{t('Posting schedule')}</h2>
        <span className="text-xs text-muted">
          {data
            ? `${all.filter((c) => c.state === 'waiting').length} ${t('scheduled')} · ${openSlots} ${t('open slots')}`
            : ''}
        </span>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <div className="flex rounded-lg bg-raised p-0.5 text-xs" role="group" aria-label={t('View')}>
            {(['calendar', 'list'] as const).map((v) => (
              <button
                key={v}
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={`px-2.5 py-1 rounded-md ${view === v ? 'bg-base text-ink' : 'text-muted hover:text-ink'}`}
              >
                {v === 'calendar' ? t('Calendar') : t('List')}
              </button>
            ))}
          </div>
          <button className="btn-ghost !py-1 !px-3 text-xs" aria-pressed={editing} onClick={() => setEditing((v) => !v)}>
            ⚙ {t('Edit schedule')}
          </button>
          {canAdd && (
          <button
            ref={addBtn}
            className="btn-accent !py-1 !px-3 text-xs"
            onClick={() => setAddOpen((v) => !v)}
            title={t('Add clips or compilations to the next open slots')}
          >
            ＋ {t('Add to schedule')}
          </button>
          )}
        </div>
      </div>

      <Popover anchor={addBtn} open={addOpen} onClose={() => setAddOpen(false)} width={340} label={t('Add to schedule')}>
        <div className="p-1 max-h-80 overflow-y-auto space-y-0.5">
          <p className="px-2 py-1 text-[11px] text-muted">
            {t('Each goes in the next open slot, after what is already scheduled.')}
          </p>
          {(ready ?? []).length === 0 && (comps ?? []).length === 0 && (
            <p className="px-2 py-2 text-sm text-muted">{t('Nothing is waiting to be scheduled.')}</p>
          )}
          {(ready ?? []).map((v) => (
            <button
              key={v.video_id}
              className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised"
              onClick={() => {
                setAddOpen(false)
                onAddVideo?.(v)
              }}
            >
              <span className="block truncate">{v.title || v.video_id}</span>
              <span className="block text-[11px] text-muted">
                {t('Clips')} · {v.clip_count - v.published_clips} {t('not posted')}
              </span>
            </button>
          ))}
          {(comps ?? []).map((c) => (
            <button
              key={`c${c.id}`}
              className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised"
              onClick={() => {
                setAddOpen(false)
                onAddComp?.(c)
              }}
            >
              <span className="block truncate">{c.title}</span>
              <span className="block text-[11px] text-muted">{t('Compilation')}</span>
            </button>
          ))}
        </div>
      </Popover>

      {editing && data && (
        <PolicyEditor
          policy={data.policy}
          onSaved={() => {
            setEditing(false)
            void load()
          }}
          onCancel={() => setEditing(false)}
        />
      )}

      {error && <p className="text-sm text-error">{error}</p>}
      {note && <p className="text-xs text-muted">{note}</p>}

      {view === 'list' ? (
        <ScheduleView inline embedded />
      ) : (
        <>
          <div className="flex items-center gap-2 text-xs">
            <button className="btn-ghost !py-0.5 !px-2" onClick={() => setStart(addDays(start, -span))} aria-label={t('Earlier')}>
              ‹
            </button>
            <button className="btn-ghost !py-0.5 !px-2" onClick={() => setStart(new Date())}>
              {t('Today')}
            </button>
            <button className="btn-ghost !py-0.5 !px-2" onClick={() => setStart(addDays(start, span))} aria-label={t('Later')}>
              ›
            </button>
            <span className="text-muted">
              {days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} –{' '}
              {days[days.length - 1].toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
            </span>
            <div className="ml-auto flex rounded-lg bg-raised p-0.5" role="group" aria-label={t('Range')}>
              {([7, 14] as const).map((n) => (
                <button
                  key={n}
                  aria-pressed={span === n}
                  onClick={() => setSpan(n)}
                  className={`px-2 py-0.5 rounded-md ${span === n ? 'bg-base text-ink' : 'text-muted hover:text-ink'}`}
                >
                  {n === 7 ? t('1 week') : t('2 weeks')}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 2xl:grid-cols-7 gap-2.5">
            {days.map((day) => {
              const key = iso(day)
              const isToday = key === iso(new Date())
              const dayChips = all.filter((c) => iso(new Date(c.at)) === key)
              const dayOpen = (data?.slots ?? []).filter(
                (s) => !s.taken && iso(new Date(s.at)) === key && Date.parse(s.at) > now
              )
              const entries = [
                ...dayChips.map((c) => ({ at: c.at, chip: c })),
                ...dayOpen.map((s) => ({ at: s.at, chip: null as Chip | null }))
              ].sort((a, b) => a.at.localeCompare(b.at))
              return (
                <div
                  key={key}
                  className={`rounded-lg border p-2 min-h-24 space-y-1.5 ${isToday ? 'border-accent/60' : 'border-raised/60'}`}
                >
                  <p className={`text-xs font-semibold uppercase tracking-wide ${isToday ? 'text-accent' : 'text-muted'}`}>
                    {t(DAY_NAMES[(day.getDay() + 6) % 7])} {day.getDate()}
                  </p>
                  {entries.length === 0 && <p className="text-xs text-muted/60">{t("Nothing scheduled")}</p>}
                  {entries.map((e) =>
                    e.chip ? (
                      <div
                        key={e.chip.key}
                        className={`group relative rounded-md border px-2 py-1.5 text-xs ${CHIP_STYLE[e.chip.state]}`}
                      >
                        <div className="flex items-center gap-1.5">
                          <PublishThumb
                            id={e.chip.publishId}
                            has={haveThumb[e.chip.publishId]}
                            className="h-8"
                          />
                          <div className="min-w-0">
                            <p className="tabular-nums text-sm font-semibold leading-tight">
                              {clock(e.chip.at)}
                              <span className="ml-1.5 text-[11px] font-normal text-muted">
                                {e.chip.publishId < 0 ? t('comp') : t('clip')}
                              </span>
                            </p>
                            <p className="line-clamp-2 leading-snug break-words" title={e.chip.title}>
                              {e.chip.title}
                            </p>
                          </div>
                        </div>
                        <p className="mt-1 text-[11px] text-muted leading-tight break-words">
                          {e.chip.platforms.map(platformLabel).join(' · ')}
                        </p>
                        <div className="absolute top-1 right-1 flex 2xl:hidden 2xl:group-hover:flex 2xl:group-focus-within:flex bg-base/90 rounded-lg">
                          {e.chip.state === 'waiting' && e.chip.cancellable && (
                            <ActionButton
                              icon={<CalendarX size={14} />}
                              tone="danger"
                              label={t('Cancel this post')}
                              cost={e.chip.first.kind === 'upload' ? { units: COST.unschedule } : {}}
                              busy={busy}
                              onClick={() => cancel(e.chip as Chip)}
                            />
                          )}
                          {e.chip.state === 'waiting' && !e.chip.cancellable && (
                            <span
                              className="px-1.5 py-1 text-[11px] text-muted"
                              title={t('This provider holds the post. Cancel it in its own dashboard.')}
                            >
                              {t('At provider')}
                            </span>
                          )}
                          {e.chip.state !== 'waiting' && (
                            <ActionButton
                              icon={<Ban size={14} />}
                              label={t('Remove from the list')}
                              cost={{ note: t('The record is kept') }}
                              busy={busy}
                              onClick={() =>
                                void act(
                                  () =>
                                    scheduleApi.clear(
                                      (e.chip as Chip).platforms.map((p) => ({
                                        clip_id: (e.chip as Chip).publishId,
                                        platform: p
                                      }))
                                    ),
                                  t('Removed from the list.')
                                )
                              }
                            />
                          )}
                        </div>
                      </div>
                    ) : (
                      <button
                        key={`slot-${e.at}`}
                        className="w-full rounded-md border border-dashed border-raised px-2 py-2 text-left text-xs text-muted hover:border-accent hover:text-accent"
                        onClick={() => canAdd && setAddOpen(true)}
                        title={t('Open slot. Add something and it goes in the next open one.')}
                      >
                        <span className="tabular-nums">{clock(e.at)}</span> · {t('open')}
                      </button>
                    )
                  )}
                </div>
              )
            })}
          </div>

          {data && data.unscheduled.length > 0 && (
            <p className="text-xs text-muted">
              {data.unscheduled.length} {t('waiting posts have no fixed time: a provider is queueing them for its own slots. They are in the list view.')}
            </p>
          )}
        </>
      )}
    </section>
  )
}

/** The schedule's rules: which days, and either the strongest hours of each
 *  day or the exact clock times. Every publish reads these. */
function PolicyEditor({
  policy,
  onSaved,
  onCancel
}: {
  policy: SlotPolicy
  onSaved: () => void
  onCancel: () => void
}): JSX.Element {
  const [mode, setMode] = useState(policy.mode)
  const [perDay, setPerDay] = useState(policy.per_day)
  const [gap, setGap] = useState(policy.min_gap_hours)
  const [times, setTimes] = useState<string[]>(policy.fixed_times)
  const [days, setDays] = useState<number[]>(policy.days)
  const [newTime, setNewTime] = useState('12:00')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const toggleDay = (d: number): void =>
    setDays((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort()))

  const save = async (): Promise<void> => {
    if (days.length === 0) return setError(t('Pick at least one day.'))
    if (mode === 'fixed' && times.length === 0) return setError(t('Add at least one time.'))
    setSaving(true)
    try {
      await publishingApi.saveSettings({
        slot_mode: mode,
        fixed_times: times,
        slot_days: days,
        per_day: perDay,
        min_gap_hours: Math.max(1, Math.round(gap))
      })
      onSaved()
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
      setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-raised p-3 space-y-3 text-sm">
      <div className="flex gap-4 flex-wrap items-center">
        <div className="flex rounded-lg bg-raised p-0.5 text-xs" role="group" aria-label={t('Schedule type')}>
          {(['best', 'fixed'] as const).map((m) => (
            <button
              key={m}
              aria-pressed={mode === m}
              onClick={() => setMode(m)}
              className={`px-3 py-1 rounded-md ${mode === m ? 'bg-base text-ink' : 'text-muted hover:text-ink'}`}
            >
              {m === 'best' ? t('Best hours') : t('Fixed times')}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted">
          {mode === 'best'
            ? t('Each day uses its strongest hours for your audience, which sharpen as your posts get views.')
            : t('The same clock times on every day you pick.')}
        </p>
      </div>

      <div className="flex gap-1 flex-wrap" role="group" aria-label={t('Days')}>
        {DAY_NAMES.map((n, d) => (
          <button
            key={n}
            aria-pressed={days.includes(d)}
            onClick={() => toggleDay(d)}
            className={`px-2.5 py-1 rounded-md text-xs border ${
              days.includes(d) ? 'border-accent bg-accent/15 text-ink' : 'border-raised text-muted'
            }`}
          >
            {t(n)}
          </button>
        ))}
      </div>

      {mode === 'best' ? (
        <div className="flex items-center gap-2 text-xs flex-wrap">
          <input
            type="number"
            min={1}
            max={12}
            className="input !py-1 !w-16 text-sm"
            value={perDay}
            onChange={(e) => setPerDay(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
            aria-label={t('Posts per day')}
          />
          <span className="text-muted">{t('posts a day, at least')}</span>
          <input
            type="number"
            min={1}
            max={12}
            className="input !py-1 !w-16 text-sm"
            value={gap}
            onChange={(e) => setGap(Math.max(1, Math.min(12, Number(e.target.value) || 1)))}
            aria-label={t('Hours between posts')}
          />
          <span className="text-muted">{t('hours apart')}</span>
        </div>
      ) : (
        <div className="flex items-center gap-2 flex-wrap text-xs">
          {times.map((tm) => (
            <span key={tm} className="inline-flex items-center gap-1 rounded-md bg-raised px-2 py-1 tabular-nums">
              {tm}
              <button
                className="text-muted hover:text-error"
                aria-label={`${t('Remove')} ${tm}`}
                onClick={() => setTimes((cur) => cur.filter((x) => x !== tm))}
              >
                ✕
              </button>
            </span>
          ))}
          <input
            type="time"
            className="input !py-1 !w-28 text-sm"
            value={newTime}
            onChange={(e) => setNewTime(e.target.value)}
            aria-label={t('New time')}
          />
          <button
            className="btn-ghost !py-1 !px-2"
            onClick={() => newTime && !times.includes(newTime) && setTimes((cur) => [...cur, newTime].sort())}
          >
            ＋ {t('Add time')}
          </button>
        </div>
      )}

      {error && <p className="text-xs text-error">{error}</p>}
      <div className="flex gap-2">
        <button className="btn-accent !py-1 !px-4 text-xs" disabled={saving} onClick={() => void save()}>
          {saving ? t('Saving…') : t('Save schedule')}
        </button>
        <button className="btn-ghost !py-1 !px-3 text-xs" onClick={onCancel}>
          {t('Cancel')}
        </button>
      </div>
    </div>
  )
}
