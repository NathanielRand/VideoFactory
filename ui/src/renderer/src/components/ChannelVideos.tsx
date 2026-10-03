import ActionButton from './ActionButton'
import { CalendarX, Image, Palette, Refresh, Trash } from './icons'
import { Cost } from './QuotaMeter'
import { COST, readUnits } from '../lib/quota'
import ThumbPlaceholder from './ThumbPlaceholder'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { thumbsApi } from '../lib/thumbnails'
import type { ChannelVideo, YouTubeAccount } from '../lib/youtube'
import PublishThumb, { useThumbStatus } from './PublishThumb'
import ThumbnailStudio from './ThumbnailStudio'

/** What is on the YouTube channel and where each video stands:
 *  processing, scheduled, live, rejected... with views, likes and comments.
 *
 *  Read from the channel itself, so it shows everything there however it was
 *  posted (this app, WoopSocial, Upload-Post, or by hand). The ones this app
 *  made are marked. Needs YouTube connected in Settings, as a read: nothing
 *  is changed on the channel from here. */

const STATE: Record<ChannelVideo['state'], { label: string; style: string }> = {
  processing: { label: 'Processing', style: 'bg-amber-500/15 text-amber-400' },
  scheduled: { label: 'Scheduled', style: 'bg-sky-500/15 text-sky-400' },
  live: { label: 'Live', style: 'bg-emerald-500/15 text-emerald-400' },
  unlisted: { label: 'Unlisted', style: 'bg-raised text-muted' },
  private: { label: 'Private', style: 'bg-raised text-muted' },
  rejected: { label: 'Rejected', style: 'bg-red-500/15 text-red-400' },
  failed: { label: 'Failed', style: 'bg-red-500/15 text-red-400' },
  deleted: { label: 'Deleted', style: 'bg-raised text-muted line-through' }
}

type Filter = 'all' | 'scheduled' | 'live' | 'processing' | 'problems'
const FILTERS: [Filter, string][] = [
  ['all', 'All'],
  ['live', 'Live'],
  ['scheduled', 'Scheduled'],
  ['processing', 'Processing'],
  ['problems', 'Problems']
]
const PROBLEMS = new Set(['rejected', 'failed'])

// What the video is to this app: a clip, a compilation, or something on the
// channel that was not made here (posted by hand, or by another tool).
type Kind = 'all' | 'clip' | 'compilation' | 'other'
const KINDS: [Kind, string][] = [
  ['all', 'Everything'],
  ['clip', 'Clips'],
  ['compilation', 'Compilations'],
  ['other', 'Not from this app']
]
const kindOf = (v: ChannelVideo): Exclude<Kind, 'all'> =>
  v.publish_id === null ? 'other' : v.publish_id < 0 ? 'compilation' : 'clip'

export type Sort = 'newest' | 'oldest' | 'views' | 'likes' | 'comments'
const SORTS: [Sort, string][] = [
  ['newest', 'Newest first'],
  ['oldest', 'Oldest first'],
  ['views', 'Most views'],
  ['likes', 'Most likes'],
  ['comments', 'Most comments']
]

/** The moment the "When" column shows: go-live time for a scheduled video,
 *  publish time for the rest. The channel's own list is in upload order, so
 *  a video scheduled for next week sat among today's uploads. */
const moment = (v: ChannelVideo): number => {
  const t = Date.parse((v.state === 'scheduled' ? v.publish_at : v.published_at) || '')
  return Number.isNaN(t) ? 0 : t
}

/** Pure, so the order can be checked without rendering. Videos with no count
 *  (comments off) sort last whichever way the count runs; ties fall back to
 *  the newest. */
export function sortVideos(list: ChannelVideo[], sort: Sort): ChannelVideo[] {
  const out = [...list]
  const count = (v: ChannelVideo): number | null =>
    sort === 'views' ? v.views : sort === 'likes' ? v.likes : v.comments
  out.sort((a, b) => {
    if (sort === 'newest') return moment(b) - moment(a)
    if (sort === 'oldest') return moment(a) - moment(b)
    const x = count(a)
    const y = count(b)
    if (x === null && y === null) return moment(b) - moment(a)
    if (x === null) return 1
    if (y === null) return -1
    return y - x || moment(b) - moment(a)
  })
  return out
}

const compact = (n: number | null): string =>
  n === null
    ? '—'
    : new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n)

function when(v: ChannelVideo): string {
  const iso = v.state === 'scheduled' ? v.publish_at : v.published_at
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

const open = (url: string) => (): void => void window.studio.openExternal(url)

export default function ChannelVideos(): JSX.Element | null {
  const [ready, setReady] = useState<boolean | null>(null)
  const [accounts, setAccounts] = useState<YouTubeAccount[]>([])
  const [channel, setChannel] = useState('')
  const [videos, setVideos] = useState<ChannelVideo[] | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
  const [kind, setKind] = useState<Kind>('all')
  const [sort, setSort] = useState<Sort>('newest')
  const [limit, setLimit] = useState(50)
  const [designing, setDesigning] = useState<number | null>(null)
  const [thumbVersion, setThumbVersion] = useState(0)
  const [pushing, setPushing] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [checked, setChecked] = useState<Date | null>(null)

  useEffect(() => {
    api
      .youtubeStatus()
      .then((s) => {
        const ok = Boolean(s.enabled && s.connected)
        setReady(ok)
        const list = s.accounts ?? []
        setAccounts(list)
        setChannel(list.find((a) => a.default)?.id ?? list[0]?.id ?? '')
      })
      .catch(() => setReady(false))
  }, [])

  const load = useCallback(
    async (fresh = false): Promise<void> => {
      setLoading(true)
      setError('')
      try {
        const got = await api.youtubeChannelVideos({ channelId: channel || undefined, limit, fresh })
        setVideos(got.videos)
        setChecked(new Date())
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setLoading(false)
      }
    },
    [channel, limit]
  )

  useEffect(() => {
    if (ready) void load()
  }, [ready, load])

  // While anything is processing, look again every minute: that is the state
  // people wait on. Otherwise the numbers move slowly and Refresh is enough.
  const processing = videos?.some((v) => v.state === 'processing') ?? false
  useEffect(() => {
    if (!processing) return
    const id = window.setInterval(() => void load(true), 60_000)
    return () => window.clearInterval(id)
  }, [processing, load])

  const ofKind = useMemo(
    () => (videos ?? []).filter((v) => kind === 'all' || kindOf(v) === kind),
    [videos, kind]
  )
  const shown = useMemo(() => {
    const pick =
      filter === 'all'
        ? ofKind
        : filter === 'problems'
          ? ofKind.filter((v) => PROBLEMS.has(v.state))
          : ofKind.filter((v) => v.state === filter)
    return sortVideos(pick, sort)
  }, [ofKind, filter, sort])

  // Each count answers "how many, with the other filter applied".
  const kindCounts = useMemo(() => {
    const c: Record<Kind, number> = { all: 0, clip: 0, compilation: 0, other: 0 }
    for (const v of videos ?? []) {
      if (filter !== 'all' && (filter === 'problems' ? !PROBLEMS.has(v.state) : v.state !== filter)) continue
      c.all += 1
      c[kindOf(v)] += 1
    }
    return c
  }, [videos, filter])

  const ourIds = useMemo(
    () => [...new Set((videos ?? []).flatMap((v) => (v.publish_id === null ? [] : [v.publish_id])))],
    [videos]
  )
  const haveThumb = useThumbStatus(ourIds, thumbVersion)

  const push = async (v: ChannelVideo): Promise<void> => {
    if (v.publish_id === null) return
    if (
      !window.confirm(
        `${t('Replace the thumbnail on YouTube for')} “${v.title}”? ${t('This uses about 50 of your daily YouTube quota units.')}`
      )
    )
      return
    setPushing(v.video_id)
    setNote('')
    setError('')
    try {
      await thumbsApi.pushToYouTube(v.video_id, { publishId: v.publish_id, channelId: channel || undefined })
      setNote(`${t('Thumbnail sent for')} “${v.title}”. ${t('YouTube can take a few minutes to show it.')}`)
      await load(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPushing(null)
    }
  }

  const unschedule = async (v: ChannelVideo): Promise<void> => {
    if (
      !window.confirm(
        `${t('Take')} “${v.title}” ${t('off the schedule?')} ${t('It stays on your channel as a private video and will not go live. You can schedule it again in YouTube Studio.')}`
      )
    )
      return
    setPushing(v.video_id)
    setNote('')
    setError('')
    try {
      await thumbsApi.unscheduleYouTube(v.video_id, channel || undefined)
      setNote(`“${v.title}” ${t('is off the schedule and private.')}`)
      await load(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPushing(null)
    }
  }

  const replace = async (v: ChannelVideo): Promise<void> => {
    if (
      !window.confirm(
        `${t('Re-render and replace')} “${v.title}”?\n\n` +
          `${t('The clip is re-rendered, then uploaded again with the same title, description and settings')}${
            v.state === 'scheduled' ? ` ${t('and the same go-live time')}` : ''
          }. ${t('Only after the new upload succeeds is this video deleted, so nothing is lost if it fails.')}\n\n` +
          `${t('Its views, likes and comments do not carry over, and subscribers are not notified again.')}`
      )
    )
      return
    setPushing(v.video_id)
    setNote('')
    setError('')
    try {
      await thumbsApi.replaceYouTube(v.video_id, channel || undefined)
      setNote(
        `“${v.title}”: ${t('re-rendering now. The new video uploads when the render finishes, and this one is deleted after that. Follow it in the activity feed on Home.')}`
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPushing(null)
    }
  }

  const remove = async (v: ChannelVideo): Promise<void> => {
    const ours = v.publish_id !== null
    if (
      !window.confirm(
        `${t('Delete')} “${v.title}” ${t('from YouTube for good?')} ${t('Its views, likes and comments are lost and it cannot be undone.')}${
          ours ? ` ${t('The clip here stays, so you can re-render it and publish it again.')}` : ''
        }`
      )
    )
      return
    setPushing(v.video_id)
    setNote('')
    setError('')
    try {
      await thumbsApi.deleteYouTube(v.video_id, channel || undefined)
      setNote(`“${v.title}” ${t('was deleted from YouTube.')}`)
      await load(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPushing(null)
    }
  }

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: 0, live: 0, scheduled: 0, processing: 0, problems: 0 }
    for (const v of ofKind) {
      c.all += 1
      if (v.state === 'live') c.live += 1
      if (v.state === 'scheduled') c.scheduled += 1
      if (v.state === 'processing') c.processing += 1
      if (PROBLEMS.has(v.state)) c.problems += 1
    }
    return c
  }, [ofKind])

  if (ready === null) return null

  return (
    <section className="card space-y-3" aria-label={t('On YouTube')}>
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="font-semibold">{t('On YouTube')}</h2>
        {ready && accounts.length > 1 && (
          <select
            className="input !w-auto !py-1 text-xs"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            aria-label={t('Channel')}
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        )}
        {ready && (
          <div className="ml-auto flex items-center gap-2 text-xs text-muted">
            {checked && (
              <span>
                {t('Checked')} {checked.toLocaleTimeString(undefined, { timeStyle: 'short' })}
                {processing && ` · ${t('rechecking every minute while something processes')}`}
              </span>
            )}
            <button className="btn-ghost !py-1 text-xs" disabled={loading} onClick={() => void load(true)}>
              {loading ? t('Checking…') : `↻ ${t('Refresh')}`}
              <Cost units={readUnits(limit)} />
            </button>
          </div>
        )}
      </div>

      {!ready ? (
        <p className="text-sm text-muted">
          {t(
            'Connect YouTube in Settings to see what is on your channel and how each video is doing — including ones posted through WoopSocial or Upload-Post. It only reads; nothing on the channel is changed.'
          )}{' '}
          <button
            className="text-accent hover:underline"
            onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
          >
            {t('Open Settings')}
          </button>
        </p>
      ) : (
        <>
          <div className="flex gap-1 flex-wrap text-xs items-center">
            {KINDS.map(([id, label]) => (
              <button
                key={id}
                onClick={() => setKind(id)}
                aria-pressed={kind === id}
                className={`px-2.5 py-1 rounded-md ${
                  kind === id ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'
                }`}
              >
                {t(label)} <span className="tabular-nums opacity-70">{kindCounts[id]}</span>
              </button>
            ))}
            <select
              className="input !w-auto !py-1 text-xs ml-auto"
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              aria-label={t('Sort by')}
            >
              {SORTS.map(([id, label]) => (
                <option key={id} value={id}>
                  {t(label)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex gap-1 flex-wrap text-xs">
            {FILTERS.map(([id, label]) => (
              <button
                key={id}
                onClick={() => setFilter(id)}
                className={`px-2.5 py-1 rounded-md ${
                  filter === id ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'
                } ${id === 'problems' && counts.problems ? 'text-red-400' : ''}`}
              >
                {t(label)} <span className="tabular-nums opacity-70">{counts[id]}</span>
              </button>
            ))}
          </div>

          {error && <p className="text-sm text-error">{error}</p>}
          {note && <p className="text-sm text-success">{note}</p>}
          {videos === null && !error && <p className="text-sm text-muted">{t('Reading your channel…')}</p>}
          {videos && shown.length === 0 && (
            <p className="text-sm text-muted">
              {filter === 'all' ? t('No videos on this channel yet.') : t('Nothing here right now.')}
            </p>
          )}

          {shown.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wide text-muted border-b border-raised/60">
                    <th className="py-2 pr-2 font-medium">{t('Video')}</th>
                    <th className="py-2 px-2 font-medium">{t('Status')}</th>
                    <th className="py-2 px-2 font-medium">{t('When')}</th>
                    <th className="py-2 px-2 font-medium text-right">{t('Views')}</th>
                    <th className="py-2 px-2 font-medium text-right">{t('Likes')}</th>
                    <th className="py-2 px-2 font-medium text-right">{t('Comments')}</th>
                    <th className="py-2 pl-2 font-medium text-right">{t('Actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-raised/40">
                  {shown.map((v) => {
                    const s = STATE[v.state]
                    const ours = v.publish_id !== null
                    const isClip = ours && (v.publish_id as number) > 0
                    const busyRow = pushing === v.video_id
                    const exists = ['scheduled', 'live', 'private', 'unlisted'].includes(v.state)
                    const saved = ours ? haveThumb[v.publish_id as number] : false
                    return (
                      <tr key={v.video_id} className="align-middle hover:bg-raised/20 transition-colors">
                        <td className="py-2 pr-2">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="relative shrink-0">
                              {v.thumbnail ? (
                                <img
                                  src={v.thumbnail}
                                  alt=""
                                  loading="lazy"
                                  className="h-12 aspect-video object-cover rounded-md bg-raised"
                                />
                              ) : (
                                <ThumbPlaceholder className="h-12" />
                              )}
                              {v.duration > 0 && (
                                <span className="absolute bottom-0.5 right-0.5 bg-black/75 text-white text-[10px] leading-none px-1 py-0.5 rounded tabular-nums">
                                  {Math.floor(v.duration / 60)}:{String(v.duration % 60).padStart(2, '0')}
                                </span>
                              )}
                            </div>
                            <div className="min-w-0 space-y-0.5">
                              <button
                                onClick={open(v.state === 'live' || v.state === 'unlisted' ? v.url : v.studio_url)}
                                className="block truncate max-w-xs font-medium hover:text-accent text-left"
                                title={v.title}
                              >
                                {v.title || v.video_id}
                              </button>
                              <div className="flex items-center gap-1.5 text-[10px]">
                                {v.short && (
                                  <span className="px-1.5 py-px rounded bg-raised text-muted">{t('Short')}</span>
                                )}
                                {ours && (
                                  <span className="px-1.5 py-px rounded bg-accent/15 text-accent">
                                    {isClip ? t('Clip') : t('Compilation')}
                                  </span>
                                )}
                                <button onClick={open(v.studio_url)} className="text-muted hover:text-ink">
                                  {t('Studio')} ↗
                                </button>
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="py-2 px-2">
                          <span
                            className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] whitespace-nowrap ${s.style}`}
                            title={v.detail}
                          >
                            <span className="size-1.5 rounded-full bg-current" aria-hidden />
                            {t(s.label)}
                          </span>
                          {v.detail && <p className="text-[10px] text-red-400 mt-0.5 max-w-[14rem]">{v.detail}</p>}
                        </td>
                        <td className="py-2 px-2 text-xs text-muted whitespace-nowrap">{when(v)}</td>
                        <td className="py-2 px-2 text-right tabular-nums font-medium">{compact(v.views)}</td>
                        <td className="py-2 px-2 text-right tabular-nums text-muted">{compact(v.likes)}</td>
                        <td
                          className="py-2 px-2 text-right tabular-nums text-muted"
                          title={v.comments === null ? t('Comments are off or hidden') : ''}
                        >
                          {compact(v.comments)}
                        </td>
                        <td className="py-2 pl-2">
                          <div className="flex items-center justify-end gap-0.5">
                            {ours && (
                              <PublishThumb
                                id={v.publish_id as number}
                                has={saved}
                                version={thumbVersion}
                                className="h-7 mr-1.5"
                              />
                            )}
                            {ours && (
                              <>
                                <ActionButton
                                  icon={<Palette size={16} />}
                                  label={t('Design a thumbnail')}
                                  cost={{}}
                                  onClick={() => setDesigning(v.publish_id)}
                                />
                                <ActionButton
                                  icon={<Image size={16} />}
                                  tone="accent"
                                  label={t('Send saved thumbnail to YouTube')}
                                  cost={{ units: COST.thumbnail }}
                                  disabled={!saved}
                                  disabledReason={t('Design and save a thumbnail first')}
                                  busy={busyRow}
                                  onClick={() => void push(v)}
                                />
                              </>
                            )}
                            {isClip && exists && (
                              <ActionButton
                                icon={<Refresh size={16} />}
                                tone="accent"
                                label={t('Re-render and replace')}
                                cost={{ units: COST.replace, uploads: 1 }}
                                busy={busyRow}
                                onClick={() => void replace(v)}
                              />
                            )}
                            {(ours || exists) && <span className="w-px h-5 bg-raised mx-1" aria-hidden />}
                            {v.state === 'scheduled' && (
                              <ActionButton
                                icon={<CalendarX size={16} />}
                                label={t('Remove from schedule (keeps it private)')}
                                cost={{ units: COST.unschedule }}
                                busy={busyRow}
                                onClick={() => void unschedule(v)}
                              />
                            )}
                            {exists && (
                              <ActionButton
                                icon={<Trash size={16} />}
                                tone="danger"
                                label={t('Delete from YouTube')}
                                cost={{ units: COST.delete, note: t('Cannot be undone') }}
                                busy={busyRow}
                                onClick={() => void remove(v)}
                              />
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {videos && videos.length >= limit && limit < 200 && (
            <button
              className="btn-ghost !py-1 text-xs"
              disabled={loading}
              onClick={() => setLimit((n) => Math.min(200, n + 50))}
            >
              {t('Load more')}
              <Cost units={readUnits(limit + 50)} />
            </button>
          )}
          {designing !== null && (
            <ThumbnailStudio
              publishId={designing}
              onClose={() => setDesigning(null)}
              onSaved={() => setThumbVersion((n) => n + 1)}
            />
          )}
        </>
      )}
    </section>
  )
}
