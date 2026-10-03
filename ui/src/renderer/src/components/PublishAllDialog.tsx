import { scheduleApi, type Duplicate } from '../lib/schedule'
import { COST, limitFor, useQuota } from '../lib/quota'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { publishingApi, slotToIso } from '../lib/publishing'
import type { Clip } from '../lib/types'
import ThumbnailNudge from './ThumbnailNudge'
import PublishThumb, { useThumbStatus } from './PublishThumb'
import YouTubeQuestions, { answered, rememberedAnswers, uploadPostYoutubeOverrides, type YouTubeAnswers } from './YouTubeQuestions'
import {
  PROVIDER_LABEL,
  localZone,
  platformLabel,
  platformsFor,
  type Provider
} from '../lib/uploadpost'

/** Publish a whole batch of clips to several platforms.
 *
 *  A modal with an explicit confirm, deliberately, and deliberately NOT a
 *  twin of the Export button next to it. Export writes files to a folder and
 *  can be undone by deleting them; this posts publicly to every account the
 *  creator owns and cannot be taken back. Two actions with consequences that
 *  far apart should not be one misclick from each other, so this one states
 *  what it is about to do and waits to be told yes.
 *
 *  Spacing defaults to on, and to DAYS: a video's clips are a posting
 *  calendar, not an afternoon. Twelve landing on TikTok in the same second
 *  reads as spam and spends the per-account daily cap in one go. The
 *  provider's own scheduler does the spreading, so a run stretching over
 *  weeks keeps going with Video Factory closed.
 */
/** Which account carries the posts. 'youtube' is a direct upload through the
 *  connected channel, scheduled with YouTube's own publish time, for anyone
 *  who has not set up WoopSocial or Upload-Post. */
export type PublishVia = Provider | 'youtube'

const fmtSlot = (iso: string): string =>
  new Date(iso).toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

export default function PublishAllDialog({
  clips,
  provider: via,
  onClose,
  heading,
  initialWhen = 'schedule'
}: {
  clips: Clip[]
  provider: PublishVia
  onClose: () => void
  /** Which timing the dialog opens on. */
  initialWhen?: 'now' | 'schedule'
  /** Replaces "Publish this clip", e.g. for a compilation. */
  heading?: string
}): JSX.Element {
  const direct = via === 'youtube'
  // The multi-platform code below only ever sees a real provider; direct
  // YouTube takes its own branches.
  const provider: Provider = direct ? 'uploadpost' : via
  const [platforms, setPlatforms] = useState<string[]>([])
  const [connected, setConnected] = useState<string[]>([])
  // A DAILY budget, because that is the shape posting limits take: WoopSocial
  // allows five YouTube posts a day, and sending 37 at once failed 32 of them.
  // One flat interval could not say "five a day, an hour apart" at all.
  const [perDay, setPerDay] = useState(5)
  const [gapHours, setGapHours] = useState(1)
  // "Now" posts everything at once; "schedule" follows the posting schedule
  // (a daily budget, best hours), which is also what the defaults card sets.
  // "slots": the next open slots of the posting schedule, around everything
  // already scheduled (the default). "now": all at once. "custom": pick the
  // spacing here (best hours or a fixed clock) instead.
  const [when, setWhen] = useState<'slots' | 'now' | 'custom'>(initialWhen === 'schedule' ? 'slots' : 'now')
  const spread = when === 'custom'
  const slotMode = when === 'slots'
  const [slotTimes, setSlotTimes] = useState<string[]>([])
  const [slotState, setSlotState] = useState<'loading' | 'ready' | 'error'>('loading')
  // Best times: each post at one of the audience's peak hours rather than on
  // a fixed clock. The default, because it is the one that gets views.
  const [timingMode, setTimingMode] = useState<'best' | 'fixed'>('best')
  const [bestTimes, setBestTimes] = useState<string[]>([])
  const [bestBasis, setBestBasis] = useState('')
  // YouTube holds processing until these are answered.
  const [answers, setAnswers] = useState<YouTubeAnswers>(rememberedAnswers)
  const toYoutube = platforms.includes('youtube')
  const questionsDone = direct || !toYoutube || answered(answers, provider)
  // Clips left out entirely, and clip -> platforms it should skip. Everything
  // starts included, so the common case costs nothing and only the exceptions
  // are work.
  const [dropped, setDropped] = useState<Set<number>>(new Set())
  const [excluded, setExcluded] = useState<Record<number, string[]>>({})
  // Clips already live or scheduled somewhere. They are held back until the
  // person says "post anyway"; "remove" takes them out of the selection.
  const [dupes, setDupes] = useState<Record<number, Duplicate[]>>({})
  const [anyway, setAnyway] = useState<Set<number>>(new Set())
  // Only where this batch is going: live on YouTube is no duplicate of a TikTok post.
  const dupOf = (id: number): Duplicate[] => (dupes[id] ?? []).filter((x) => platforms.includes(x.platform))
  useEffect(() => {
    let alive = true
    scheduleApi
      .duplicates(clips.map((c) => c.id))
      .then((got) => alive && setDupes(got))
      .catch(() => {
        /* the check is a safety net: without it, publishing works as before */
      })
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // How much is already spoken for. A new batch queues behind it, so the
  // estimate has to as well or it promises a date that cannot happen.
  const [queued, setQueued] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState<{ started: number; skipped: { clip_id: number; reason: string }[] } | null>(null)

  const label = direct ? 'YouTube' : PROVIDER_LABEL[provider]
  const backend =
    provider === 'woopsocial'
      ? { connections: api.woopSocialConnections, batch: api.woopSocialBatch }
      : { connections: api.uploadPostConnections, batch: api.uploadPostBatch }
  const usable = direct ? [{ id: 'youtube', label: 'YouTube' }] : platformsFor(provider)

  // The posting schedule set on the Publish page is where a batch starts.
  useEffect(() => {
    publishingApi
      .settings()
      .then((got) => {
        if (got.per_day > 0) setPerDay(got.per_day)
        if (got.min_gap_hours > 0) setGapHours(got.min_gap_hours)
      })
      .catch(() => {
        /* the built-in defaults stand */
      })
  }, [])

  useEffect(() => {
    if (!direct) return
    // Uploads YouTube is already holding for a later time come first.
    api
      .youtubeUploads(100)
      .then((got) =>
        setQueued(got.uploads.filter((u) => u.publish_at && Date.parse(u.publish_at) > Date.now()).length)
      )
      .catch(() => {
        /* nothing scheduled that we can see */
      })
  }, [])

  useEffect(() => {
    if (provider !== 'woopsocial' || direct) return
    api
      .woopSocialSchedule()
      .then((got) =>
        setQueued(
          got.posts.filter(
            (x) => x.state === 'queued' || x.state === 'processing' || x.state === 'sending'
          ).length
        )
      )
      .catch(() => {
        /* no schedule yet is the same as nothing queued */
      })
  }, [])

  useEffect(() => {
    if (direct) {
      setConnected(['youtube'])
      setPlatforms(['youtube'])
      return
    }
    backend
      .connections()
      .then((got) => {
        setConnected(got.connected)
        setPlatforms(got.connected.filter((p) => usable.some((x) => x.id === p)))
      })
      .catch(() => {
        /* not connected yet; the picker still works */
      })
  }, [])

  // Re-plan whenever what is being sent changes. The first platform picked
  // leads: peaks differ by platform, and one schedule has to serve them all.
  const leadPlatform = platforms[0] || 'youtube'
  const wanted = spread && timingMode === 'best' ? clips.length - dropped.size : 0
  useEffect(() => {
    if (!wanted) return
    let alive = true
    publishingApi
      .bestTimes(leadPlatform, wanted, { perDay })
      .then((got) => {
        if (!alive) return
        setBestTimes(got.slots.map((s) => slotToIso(s)))
        setBestBasis(
          got.learned_from
            ? `${t('Peak hours for')} ${platformLabel(leadPlatform)}, ${t('tuned by your last')} ${got.learned_from} ${t('posts')}.`
            : `${t('Peak hours for')} ${platformLabel(leadPlatform)}. ${t('It learns from your own posts as they get views.')}`
        )
      })
      .catch(() => alive && setBestTimes([]))
    return () => {
      alive = false
    }
  }, [leadPlatform, wanted, perDay])
  const useBest = spread && timingMode === 'best' && bestTimes.length > 0

  const toggle = (id: string): void =>
    setPlatforms((c) => (c.includes(id) ? c.filter((p) => p !== id) : [...c, id]))

  const single = clips.length === 1
  const chosen = clips.filter(
    (c) => !dropped.has(c.id) && !(dupOf(c.id).length && !anyway.has(c.id))
  )
  /** Platforms this clip will actually go to. */
  const going = (id: number): string[] =>
    platforms.filter((p) => !(excluded[id] || []).includes(p))
  const posts = chosen.reduce((n, c) => n + going(c.id).length, 0)
  // Only clips with somewhere left to go: one excluded from everything is not
  // a post, and must not take a slot out of the day's budget either.
  const sending = chosen.filter((c) => going(c.id).length > 0)

  // Posts already queued come first, so this batch starts after them. Mirrors
  // schedule.daily_after(), which fills each day to per_day and then rolls
  // over, rather than approximating it — a preview that disagrees with what
  // gets sent is worse than no preview.
  const daysBefore = spread && perDay > 0 ? Math.floor(queued / perDay) : 0
  const totalDays =
    spread && perDay > 0 ? Math.ceil((queued + sending.length) / perDay) : 1
  const days = Math.max(1, totalDays - daysBefore)
  const startAt =
    spread && queued > 0 ? new Date(Date.now() + daysBefore * 86_400_000) : null
  const finishAt =
    spread && sending.length > 1
      ? new Date(Date.now() + Math.max(0, totalDays - 1) * 86_400_000)
      : null

  const haveThumb = useThumbStatus(clips.map((c) => c.id))
  const { quota } = useQuota()

  // What "my schedule" would do right now: one open slot per clip, around
  // everything already scheduled. Asked for again when publishing, so two
  // batches sent close together never land on the same slot.
  const slotProvider = direct ? 'youtube' : provider
  const platformKey = platforms.join(',')
  useEffect(() => {
    if (!slotMode || !sending.length) return
    let alive = true
    setSlotState('loading')
    scheduleApi
      .nextSlots(sending.length, platforms, slotProvider)
      .then((got) => {
        if (!alive) return
        setSlotTimes(got.slots)
        setSlotState('ready')
      })
      .catch(() => alive && setSlotState('error'))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotMode, sending.length, platformKey, slotProvider])

  const freshSlots = async (): Promise<string[]> => {
    const got = await scheduleApi.nextSlots(sending.length, platforms, slotProvider)
    if (got.slots.length < sending.length) {
      throw new Error(
        `${t('Only')} ${got.slots.length} ${t('open slots are left in your posting schedule within the next 60 days, for')} ${sending.length} ${t('clips. Open more days or times in Edit schedule, or pick times yourself.')}`
      )
    }
    return got.slots
  }
  const shortOfSlots = slotState === 'ready' && slotTimes.length < sending.length
  const toggleClip = (id: number): void =>
    setDropped((c) => {
      const next = new Set(c)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const togglePlatformFor = (id: number, platform: string): void =>
    setExcluded((c) => {
      const off = new Set(c[id] || [])
      if (off.has(platform)) off.delete(platform)
      else off.add(platform)
      return { ...c, [id]: [...off] }
    })

  /** `count` publish times for a direct YouTube batch: the best hours when
   *  they are known, else `perDay` a day, `gapHours` apart, after whatever is
   *  already scheduled. Always at least 20 minutes out (YouTube's lead time). */
  const youtubeTimes = (count: number): string[] => {
    if (useBest) return bestTimes.slice(0, count)
    const start = Date.now() + 20 * 60_000
    return Array.from({ length: count }, (_, k) => {
      const slot = queued + k
      const day = Math.floor(slot / Math.max(1, perDay))
      const within = slot % Math.max(1, perDay)
      const at = new Date(start + day * 86_400_000 + within * gapHours * 3_600_000)
      return at.toISOString().replace(/\.\d{3}Z$/, 'Z')
    })
  }

  const run = async (): Promise<void> => {
    if (busy || !platforms.length || !sending.length) return
    setBusy(true)
    setError('')
    try {
      if (direct) {
        // YouTube holds each upload private until its time, so the times are
        // computed here and travel as ordinary per-clip publish times.
        const times = slotMode ? await freshSlots() : spread ? youtubeTimes(sending.length) : []
        const got = await api.executePublishPlan(
          sending.map((c, i) => ({
            clip_id: c.id,
            title: c.title || c.hook || `Clip ${c.id}`,
            description: '',
            privacy: times[i] ? 'private' : 'public',
            publish_at: times[i] ?? null
          }))
        )
        setDone({ started: got.started.length, skipped: got.skipped })
        return
      }
      // Only the exceptions travel, and only for clips being sent.
      const exclude: Record<string, string[]> = {}
      for (const c of sending) {
        const off = excluded[c.id] || []
        if (off.length) exclude[String(c.id)] = off
      }
      // Only WoopSocial understands a daily budget; Upload-Post's batch has
      // its own loop and would accept per_day and then ignore it, firing
      // everything at once. For that provider the budget becomes the nearest
      // flat interval it does honour, so the daily ceiling is still roughly
      // respected instead of silently abandoned.
      const slotNow = slotMode ? await freshSlots() : null
      const daily =
        provider === 'woopsocial'
          ? { per_day: spread ? perDay : 0, gap_hours: gapHours, exclude }
          : { every_hours: spread ? 24 / Math.max(1, perDay) : 0 }
      const got = await backend.batch({
        clip_ids: sending.map((c) => c.id),
        platforms,
        ...daily,
        ...(slotNow ? { times: slotNow } : useBest ? { times: bestTimes.slice(0, sending.length) } : {}),
        ...(toYoutube
          ? {
              overrides:
                provider === 'woopsocial'
                  ? { youtube: { madeForKids: answers.kids } }
                  : { youtube: uploadPostYoutubeOverrides(answers) }
            }
          : {}),
        timezone: spread || slotMode ? localZone() : ''
      })
      setDone({ started: got.started.length, skipped: got.skipped })
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Publish all clips"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-lg space-y-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="font-semibold text-lg">
            {heading ??
              (single ? t('Publish this clip') : `${t('Publish')} ${clips.length} ${t('clips')}`)}
          </h3>
          <p className="text-xs text-muted mt-1">
            {t('Each clip is uploaded once and sent to every platform you pick, through your')}{' '}
            {label} {t('account.')}
          </p>
        </div>

        {done ? (
          <div className="space-y-2 text-sm">
            <p className="text-success">
              ✓ {done.started} {done.started === 1 ? t('clip') : t('clips')} {t('sent to')}{' '}
              {platforms.length} {platforms.length === 1 ? t('platform') : t('platforms')}.
            </p>
            {done.skipped.length > 0 && (
              <div className="text-xs text-warn space-y-0.5">
                <p>{t('Skipped')}:</p>
                {done.skipped.map((s) => (
                  <p key={s.clip_id}>
                    {t('Clip')} {s.clip_id}: {s.reason}
                  </p>
                ))}
              </div>
            )}
            <p className="text-xs text-muted">
              {heading
                ? t('Each platform’s progress shows in the schedule on the Publish page.')
                : t('Open a clip and its Publish tab to watch each platform.')}
            </p>
            <button className="btn-accent w-full !py-2" onClick={onClose}>
              {t('Done')}
            </button>
          </div>
        ) : (
          <>
            {clips.some((c) => dupOf(c.id).length) && (
              <div className="rounded-lg border border-warn/50 bg-warn/10 p-3 space-y-2" role="alert">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-sm font-medium text-warn">
                    {clips.filter((c) => dupOf(c.id).length).length} {t('of')} {clips.length}{' '}
                    {t('already live or scheduled')}
                  </p>
                  {clips.filter((c) => dupOf(c.id).length && !dropped.has(c.id)).length > 1 && (
                    <span className="ml-auto flex gap-2">
                      <button
                        className="btn-ghost !py-0.5 !px-2 text-xs"
                        onClick={() =>
                          setAnyway(new Set([...anyway, ...clips.filter((c) => dupOf(c.id).length).map((c) => c.id)]))
                        }
                      >
                        {t('Post all anyway')}
                      </button>
                      <button
                        className="btn-ghost !py-0.5 !px-2 text-xs"
                        onClick={() =>
                          setDropped(new Set([...dropped, ...clips.filter((c) => dupOf(c.id).length).map((c) => c.id)]))
                        }
                      >
                        {t('Remove all from selection')}
                      </button>
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-muted">
                  {t('Held back so nothing goes out twice. Post it again, or take it out of this selection.')}
                </p>
                <ul className="space-y-1.5">
                  {clips
                    .filter((c) => dupOf(c.id).length && !dropped.has(c.id))
                    .map((c) => (
                      <li key={c.id} className="flex items-start gap-2 text-xs">
                        <PublishThumb id={c.id} has={haveThumb[c.id]} className="h-8 mt-0.5" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-medium" title={c.title || c.hook}>
                            {c.title || c.hook || `${t('Clip')} ${c.id}`}
                          </p>
                          <p className="text-muted break-words">
                            {dupOf(c.id).map((x, i) => (
                              <span key={i}>
                                {i > 0 && ' · '}
                                {x.state === 'live' ? t('Live on') : t('Scheduled on')} {platformLabel(x.platform)}
                                {x.at && x.state === 'scheduled'
                                  ? ` (${new Date(x.at).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })})`
                                  : ''}
                                {x.url && (
                                  <button
                                    className="ml-1 text-accent hover:underline"
                                    onClick={() => void window.studio.openExternal(x.url)}
                                  >
                                    {t('Open')}
                                  </button>
                                )}
                              </span>
                            ))}
                          </p>
                        </div>
                        <div className="flex flex-col sm:flex-row gap-1 shrink-0">
                          <button
                            className={`px-2 py-1 rounded-md border text-[11px] ${
                              anyway.has(c.id) ? 'border-accent bg-accent/15' : 'border-raised hover:border-accent'
                            }`}
                            aria-pressed={anyway.has(c.id)}
                            onClick={() =>
                              setAnyway((cur) => {
                                const next = new Set(cur)
                                if (next.has(c.id)) next.delete(c.id)
                                else next.add(c.id)
                                return next
                              })
                            }
                          >
                            {anyway.has(c.id) ? `✓ ${t('Posting anyway')}` : t('Post anyway')}
                          </button>
                          <button
                            className="px-2 py-1 rounded-md border border-raised text-[11px] hover:border-error hover:text-error"
                            onClick={() => toggleClip(c.id)}
                          >
                            {t('Remove')}
                          </button>
                        </div>
                      </li>
                    ))}
                </ul>
              </div>
            )}
            <ThumbnailNudge ids={clips.map((c) => c.id)} />
            <div>
              <p className="label mb-1.5">{t('Publish to')}</p>
              <div className="flex flex-wrap gap-2">
                {usable.map((p) => {
                  const on = platforms.includes(p.id)
                  const linked = !connected.length || connected.includes(p.id)
                  return (
                    <button
                      key={p.id}
                      onClick={() => toggle(p.id)}
                      aria-pressed={on}
                      className={`px-3 py-1.5 rounded-lg text-xs border transition-colors ${
                        on ? 'border-accent bg-accent/15 text-ink' : 'border-raised text-muted'
                      } ${!linked ? 'opacity-50' : ''}`}
                      title={
                        !linked
                          ? `${t('Not connected to')} ${label} ${t('yet - it will be skipped')}`
                          : ''
                      }
                    >
                      {on ? '✓ ' : !linked ? '○ ' : ''}
                      {p.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="space-y-2">
              <p className="label">{t('When')}</p>
              <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label={t('When to post')}>
                {(
                  [
                    ['slots', t('My schedule'), t('Next open slots'), true],
                    ['now', t('Now'), t('All at once'), false],
                    ['custom', t('Pick times'), t('Best hours or a clock'), false]
                  ] as const
                ).map(([id, title, sub, recommended]) => (
                  <button
                    key={id}
                    type="button"
                    role="radio"
                    aria-checked={when === id}
                    onClick={() => setWhen(id)}
                    className={`relative text-left rounded-lg border px-3 py-2 transition-colors ${
                      when === id ? 'border-accent bg-accent/10' : 'border-raised hover:border-muted'
                    }`}
                  >
                    <span className="block text-sm font-medium">{title}</span>
                    <span className="block text-[11px] text-muted">{sub}</span>
                    {recommended && (
                      <span className="absolute -top-2 right-2 text-[9px] uppercase tracking-wide bg-accent text-black px-1.5 py-px rounded">
                        {t('Recommended')}
                      </span>
                    )}
                  </button>
                ))}
              </div>

              {slotMode && (
                <div className="space-y-1.5">
                  <div className="rounded-lg border border-raised divide-y divide-raised/60 max-h-40 overflow-y-auto text-xs">
                    {sending.map((c, i) => (
                      <div key={c.id} className="flex items-center gap-2 px-2 py-1">
                        <span className={`w-36 shrink-0 tabular-nums ${slotTimes[i] ? '' : 'text-muted'}`}>
                          {slotState === 'loading' ? t('Finding a slot…') : slotTimes[i] ? fmtSlot(slotTimes[i]) : t('No open slot')}
                        </span>
                        <PublishThumb id={c.id} has={haveThumb[c.id]} className="h-6" />
                        <span className="truncate flex-1 min-w-0">{c.title || c.hook || `${t('Clip')} ${c.id}`}</span>
                      </div>
                    ))}
                  </div>
                  <p className={`text-[11px] ${slotState === 'error' || shortOfSlots ? 'text-warn' : 'text-muted'}`}>
                    {slotState === 'error'
                      ? t('Could not read your posting schedule. Pick times yourself instead.')
                      : shortOfSlots
                        ? t('Not enough open slots in the next 60 days. Add days or times in Edit schedule, or pick times yourself.')
                        : t('Fills the next open slots around what is already scheduled, within each platform’s daily limit.')}{' '}
                    <button
                      type="button"
                      className="text-accent hover:underline"
                      onClick={() => {
                        onClose()
                        window.dispatchEvent(new Event('open-publish-schedule'))
                      }}
                    >
                      {t('Edit schedule')}
                    </button>
                  </p>
                </div>
              )}

              {when === 'now' && (
                /* Said plainly, because it is the choice that gets accounts
                   limited rather than a matter of taste. */
                <p className="text-xs text-warn">
                  {t(
                    'All of them go at once. Platforms treat a burst of posts as spam, and each account has a daily limit.'
                  )}
                </p>
              )}

              {spread && (
                <div className="space-y-2">
                  <div className="flex gap-3 text-xs">
                    {(['best', 'fixed'] as const).map((m) => (
                      <label key={m} className="inline-flex items-center gap-1.5">
                        <input
                          type="radio"
                          name="timing-mode"
                          checked={timingMode === m}
                          onChange={() => setTimingMode(m)}
                        />
                        {m === 'best' ? t('At the best times') : t('On a fixed clock')}
                      </label>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 text-sm flex-wrap">
                    <input
                      type="number"
                      min={1}
                      step={1}
                      className="input !py-1 text-sm !w-16"
                      value={perDay}
                      onChange={(e) => setPerDay(Math.max(1, Number(e.target.value) || 1))}
                      aria-label="Posts per day"
                    />
                    <span className="text-muted text-xs">
                      {timingMode === 'best' ? t('a day, at the best hours') : t('a day,')}
                    </span>
                    {timingMode === 'best' && !bestTimes.length && (
                      <span className="text-[11px] text-warn w-full">
                        {t('Could not work out the best times, so these go out evenly spaced instead.')}
                      </span>
                    )}
                    {timingMode === 'fixed' && (
                      <>
                        <input
                          type="number"
                          min={0.5}
                          step={0.5}
                          className="input !py-1 text-sm !w-16"
                          value={gapHours}
                          onChange={(e) => setGapHours(Math.max(0.5, Number(e.target.value) || 1))}
                          aria-label="Hours between posts"
                        />
                        <span className="text-muted text-xs">{t('hours apart')}</span>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Which clip goes where. Pointless for a single clip, and the
                exceptions are the only interesting part, so everything starts
                on and a click is what takes something away. */}
            {!single && platforms.length > 0 && (
              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <p className="label">{t('Clips')}</p>
                  <button
                    className="text-xs text-muted hover:text-accent"
                    onClick={() =>
                      setDropped((c) => (c.size ? new Set() : new Set(clips.map((x) => x.id))))
                    }
                  >
                    {dropped.size ? t('Select all') : t('Select none')}
                  </button>
                </div>
                <div className="max-h-48 overflow-y-auto border border-raised rounded-lg divide-y divide-raised">
                  {clips.map((c) => {
                    const held = Boolean(dupOf(c.id).length) && !anyway.has(c.id)
                    const on = !dropped.has(c.id) && !held
                    return (
                      <div key={c.id} className="flex items-center gap-2 px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => {
                            // Ticking a held-back duplicate is the same as "post anyway".
                            if (held) setAnyway((cur) => new Set(cur).add(c.id))
                            else toggleClip(c.id)
                          }}
                          aria-label={`Include clip ${c.id}`}
                        />
                        <PublishThumb id={c.id} has={haveThumb[c.id]} className="h-7" />
                        <span
                          className={`text-xs flex-1 min-w-0 truncate ${on ? '' : 'text-muted line-through'}`}
                          title={c.title || c.hook || `Clip ${c.id}`}
                        >
                          {c.title || c.hook || `${t('Clip')} ${c.id}`}
                          {held && <span className="ml-1.5 text-warn no-underline">{t('duplicate')}</span>}
                        </span>
                        <span className="flex gap-1 shrink-0">
                          {platforms.map((p) => {
                            const lit = on && going(c.id).includes(p)
                            return (
                              <button
                                key={p}
                                disabled={!on}
                                onClick={() => togglePlatformFor(c.id, p)}
                                aria-pressed={lit}
                                title={`${platformLabel(p)}${lit ? '' : ` - ${t('skipped')}`}`}
                                className={`px-1.5 py-0.5 rounded text-[10px] border ${
                                  lit
                                    ? 'border-accent bg-accent/15 text-ink'
                                    : 'border-raised text-muted line-through'
                                } ${on ? '' : 'opacity-40'}`}
                              >
                                {platformLabel(p)}
                              </button>
                            )
                          })}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {toYoutube && !direct && <YouTubeQuestions value={answers} onChange={setAnswers} via={provider} />}
            {direct && (
              <p className="text-xs text-muted">
                {t('Uses your YouTube defaults (privacy, category, made for kids) from Settings. A scheduled clip stays private on YouTube until its time.')}
              </p>
            )}

            {quota && (
              <div className="text-[11px] space-y-0.5" aria-label={t('Platform limits')}>
                {direct ? (
                  (() => {
                    const n = clips.length - dropped.size
                    const withThumb = clips.filter((c) => !dropped.has(c.id) && haveThumb[c.id]).length
                    const units = withThumb * COST.thumbnail
                    const y = quota.youtube
                    return (
                      <p className={n > y.uploads_remaining || units > y.units_remaining ? 'text-warn' : 'text-muted'}>
                        {t('This spends')} {n} {n === 1 ? t('upload') : t('uploads')} ({y.uploads_remaining} {t('left today of')} {y.uploads_limit})
                        {units > 0 && ` ${t('and about')} ${units} ${t('units for thumbnails')} (${y.units_remaining.toLocaleString()} ${t('left')})`}.
                        {n > y.uploads_remaining && ` ${t('The rest will be refused until it resets at midnight Pacific.')}`}
                      </p>
                    )
                  })()
                ) : (
                  platforms.map((p) => {
                    const cap = limitFor(quota, provider, p)
                    if (cap === undefined) return null
                    const perDayNow = spread ? perDay : clips.length - dropped.size
                    return (
                      <p key={p} className={perDayNow > cap ? 'text-warn' : 'text-muted'}>
                        {platformLabel(p)}: {perDayNow} {t('a day planned; limit')} {cap}.
                        {perDayNow > cap && ` ${t('Lower the posts a day or the extra posts will be refused.')}`}
                      </p>
                    )
                  })
                )}
              </div>
            )}

            {/* The whole point of the confirm: say what is about to happen,
                in the units that matter, before it happens. */}
            <div className="border border-raised rounded-lg p-3 text-xs space-y-1">
              <p className="font-medium text-sm">{t('About to')}</p>
              <p>
                {t('Create')} <span className="text-accent font-medium">{posts}</span>{' '}
                {posts === 1 ? t('post') : t('posts')} {t('from')} {sending.length}{' '}
                {sending.length === 1 ? t('clip') : t('clips')} {t('to')}{' '}
                {platforms.length ? platforms.map(platformLabel).join(', ') : t('nothing yet')}.
              </p>
              {useBest && (
                <p className="text-muted">
                  {t('First')} {new Date(bestTimes[0]).toLocaleString()}
                  {bestTimes.length > 1 &&
                    ` - ${t('last')} ${new Date(bestTimes[Math.min(bestTimes.length, sending.length) - 1]).toLocaleString()}`}
                  . {bestBasis}
                </p>
              )}
              {slotMode && slotState === 'ready' && slotTimes.length > 0 && (
                <p className="text-muted">
                  {t('First')} {fmtSlot(slotTimes[0])}
                  {slotTimes.length > 1 && ` - ${t('last')} ${fmtSlot(slotTimes[Math.min(slotTimes.length, sending.length) - 1])}`}.
                </p>
              )}
              {spread && !useBest && finishAt && (
                <p className="text-muted">
                  {perDay} {t('a day')} - {t('finishes')} {finishAt.toLocaleDateString()} (
                  {days} {days === 1 ? t('day') : t('days')}).
                </p>
              )}
              {spread && !useBest && startAt && (
                <p className="text-muted">
                  {t('Starts')} {startAt.toLocaleDateString()}, {t('after the')} {queued}{' '}
                  {t('already queued.')}
                </p>
              )}
              <p className="text-muted">{t('Uploads cannot be taken back.')}</p>
            </div>

            {error && <p className="text-xs text-error">{error}</p>}

            <div className="flex gap-2">
              <button className="btn-ghost flex-1 !py-2" onClick={onClose} disabled={busy}>
                {t('Cancel')}
              </button>
              <button
                className="btn-accent flex-1 !py-2"
                disabled={busy || !platforms.length || !sending.length || !questionsDone || (slotMode && shortOfSlots)}
                title={questionsDone ? '' : t('Answer YouTube’s questions first')}
                onClick={() => void run()}
              >
                {busy
                  ? t('Publishing…')
                  : `${when !== 'now' ? t('Yes, schedule') : t('Yes, publish')} ${posts} ${posts === 1 ? t('post') : t('posts')}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
