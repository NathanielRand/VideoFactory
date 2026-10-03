import { useCallback, useEffect, useMemo, useState } from 'react'
import AddToCompilation from '../components/AddToCompilation'
import AddVideos, { seedOptions } from '../components/queue/AddVideos'
import { Trash } from '../components/icons'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { LibraryItem, StudioEvent } from '../lib/types'
import { useEvents } from '../lib/useEvents'

type Filter = 'all' | 'unused' | 'clips' | 'compilations' | 'imported'

const FILTERS: [Filter, string, string][] = [
  ['all', 'All', 'Everything you have uploaded or downloaded'],
  ['unused', 'Not used yet', 'No clips made and in no compilation'],
  ['clips', 'Clipped', 'Clips have been made from it'],
  ['compilations', 'In compilations', 'At least one compilation uses it'],
  ['imported', 'Never clipped', 'Imported for a compilation or to decide later']
]

const fmtDuration = (s: number): string => {
  if (!s) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.round(s % 60)
  return h
    ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
    : `${m}:${String(sec).padStart(2, '0')}`
}

const isUnused = (v: LibraryItem): boolean => v.clip_count === 0 && v.compilations.length === 0

/** Every upload, and everywhere it has been used.
 *
 *  Step 1 of the flow ends here: a video comes in (a link, a file, a watched
 *  channel) and lands in the Library whatever it was brought in for. From
 *  here it can go either way — clips, a compilation, or both — and each row
 *  says what it has already been used for, so nothing gets clipped twice or
 *  forgotten in a folder. */
export default function Library({
  onOpenClips,
  onOpenCompilation
}: {
  onOpenClips: (videoId: string) => void
  onOpenCompilation: (id: number) => void
}): JSX.Element {
  const [items, setItems] = useState<LibraryItem[] | null>(null)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    try {
      setItems(await api.library())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (
      e.type === 'library' ||
      e.type === 'compilation' ||
      e.type === 'queue' ||
      (e.type === 'progress' && (e.stage === 'downloaded' || e.stage === 'done'))
    ) {
      void refresh()
    }
  })

  const counts = useMemo(() => {
    const all = items ?? []
    return {
      all: all.length,
      unused: all.filter(isUnused).length,
      clips: all.filter((v) => v.clip_count > 0).length,
      compilations: all.filter((v) => v.compilations.length > 0).length,
      imported: all.filter((v) => v.status === 'imported').length
    } as Record<Filter, number>
  }, [items])

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (items ?? []).filter((v) => {
      if (filter === 'unused' && !isUnused(v)) return false
      if (filter === 'clips' && v.clip_count === 0) return false
      if (filter === 'compilations' && v.compilations.length === 0) return false
      if (filter === 'imported' && v.status !== 'imported') return false
      if (!q) return true
      return (
        (v.title || '').toLowerCase().includes(q) ||
        (v.channel_name || '').toLowerCase().includes(q) ||
        (v.creator_name || '').toLowerCase().includes(q)
      )
    })
  }, [items, filter, search])

  /** Queue the clip pipeline for an upload that is already in the library.
   *  Uses the Generate bar's current settings, the same as a fresh link. */
  const makeClips = async (v: LibraryItem): Promise<void> => {
    const url = v.local ? `local:${v.video_id}` : v.source_url
    if (!url) return
    setBusy(v.video_id)
    setNotice('')
    try {
      const again = v.clip_count > 0 || v.status === 'done'
      const res = await api.createJobsBatch([
        { url, ...seedOptions(), ...(again ? { force: true } : {}) }
      ])
      if (res.created.length > 0) {
        const q = await api.queue()
        if (q.paused) await api.resumeQueue()
        setNotice(
          `${t('Making clips from')} “${v.title || v.video_id}”. ${t('Follow it in the Queue.')}`
        )
      } else {
        const why = res.skipped[0]
        setNotice(
          why
            ? `${t('Not queued')}: ${why.reason.replace(/_/g, ' ')}${why.detail ? ` - ${why.detail}` : ''}`
            : ''
        )
      }
      await refresh()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const remove = async (v: LibraryItem): Promise<void> => {
    setConfirming(null)
    setBusy(v.video_id)
    try {
      await api.deleteVideo(v.video_id)
      await refresh()
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const status = (v: LibraryItem): JSX.Element => {
    if (v.in_queue === 'running') return <span className="text-accent">{t('Working…')}</span>
    if (v.in_queue === 'queued') return <span className="text-warn">{t('In queue')}</span>
    if (v.status === 'failed') return <span className="text-error">{t('Failed')}</span>
    if (!v.has_source)
      return (
        <span
          className="text-muted"
          title={t(
            'The source file was deleted to save space. Clips and rendered compilations are kept.'
          )}
        >
          {t('Source removed')}
        </span>
      )
    if (v.status === 'imported') return <span className="text-muted">{t('In library')}</span>
    if (v.status === 'done') return <span className="text-success">{t('Clipped')}</span>
    return <span className="text-warn">{t(v.status)}</span>
  }

  return (
    <div className="p-6 space-y-5 w-full">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-xl font-bold">{t('Library')}</h1>
        <p className="text-sm text-muted">
          {t(
            'Everything you have brought in, and where each video has been used. Any video can go into clips, compilations, or both.'
          )}
        </p>
        <button
          className="btn-ghost ml-auto"
          onClick={() => setAdding(!adding)}
          aria-expanded={adding}
        >
          {adding ? t('Close') : `+ ${t('Add videos')}`}
        </button>
      </div>

      {adding && <AddVideos onAdded={refresh} libraryOnly />}

      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1.5 flex-wrap" role="group" aria-label={t('Filter the library')}>
          {FILTERS.map(([id, label, hint]) => (
            <button
              key={id}
              title={t(hint)}
              onClick={() => setFilter(id)}
              aria-pressed={filter === id}
              className={`px-2.5 py-1 rounded-md text-xs ${
                filter === id
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'bg-raised text-muted hover:text-ink'
              }`}
            >
              {t(label)} <span className="tabular-nums opacity-70">{counts[id]}</span>
            </button>
          ))}
        </div>
        <input
          type="search"
          className="input !w-64 !py-1 text-sm ml-auto"
          placeholder={t('Search title, channel or creator…')}
          aria-label={t('Search the library')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {notice && <p className="text-sm text-accent">{notice}</p>}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
      {!items && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}

      {items && shown.length === 0 && (
        <div className="card text-sm text-muted">
          {items.length === 0
            ? t(
                'Nothing here yet. Press + Add videos to bring in a link or a file, then choose Make clips or Add to compilation on its row.'
              )
            : t('Nothing matches this filter.')}
        </div>
      )}

      {shown.length > 0 && (
        <div className="card !p-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="label text-left">
                <th className="px-4 py-2.5 font-normal">{t('Video')}</th>
                <th className="px-2 py-2.5 font-normal">{t('Status')}</th>
                <th className="px-2 py-2.5 font-normal">{t('Used in')}</th>
                <th className="px-4 py-2.5 font-normal text-right">{t('Actions')}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((v) => (
                <tr key={v.video_id} className="border-t border-raised/50 align-top">
                  <td className="px-4 py-3 max-w-80">
                    <div className="font-medium truncate" title={v.title}>
                      {v.title || v.video_id}
                    </div>
                    <div className="text-xs text-muted flex gap-2 flex-wrap mt-0.5">
                      <span>{v.creator_name || v.channel_name || t('No creator')}</span>
                      <span>·</span>
                      <span className="tabular-nums">{fmtDuration(v.duration)}</span>
                      <span>·</span>
                      <span>{v.local ? t('File') : t('Link')}</span>
                      <span>·</span>
                      <span>{new Date(v.created_at).toLocaleDateString()}</span>
                    </div>
                  </td>
                  <td className="px-2 py-3 whitespace-nowrap text-xs">{status(v)}</td>
                  <td className="px-2 py-3">
                    <div className="flex gap-1.5 flex-wrap text-xs">
                      {isUnused(v) && <span className="text-muted">{t('Not used yet')}</span>}
                      {v.clip_count > 0 && (
                        <button
                          className="px-2 py-0.5 rounded bg-raised hover:text-accent"
                          onClick={() => onOpenClips(v.video_id)}
                          title={t('Open these clips in the Editor')}
                        >
                          ✂ {v.clip_count} {v.clip_count === 1 ? t('clip') : t('clips')}
                          {v.published_clips > 0 && (
                            <span className="text-success">
                              {' '}
                              · {v.published_clips} {t('posted')}
                            </span>
                          )}
                        </button>
                      )}
                      {v.compilations.map((c) => (
                        <button
                          key={c.id}
                          className="px-2 py-0.5 rounded bg-raised hover:text-accent max-w-56 truncate"
                          onClick={() => onOpenCompilation(c.id)}
                          title={`${t('Open this compilation')} (${c.segments} ${t('segments')}, ${t(c.status)})`}
                        >
                          ▦ {c.title}
                          {c.segments > 1 && <span className="text-muted"> ×{c.segments}</span>}
                        </button>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2 justify-end items-start flex-wrap">
                      <button
                        className="btn-ghost !py-1 !px-3 text-xs"
                        disabled={busy === v.video_id || !!v.in_queue || !v.has_source}
                        onClick={() => void makeClips(v)}
                        title={
                          !v.has_source
                            ? t('The source file is gone - add the link again to clip it')
                            : v.clip_count > 0
                              ? t(
                                  'Run the AI again with the Generate bar settings. Existing clips are kept.'
                                )
                              : t('Find the best moments with AI, using the Generate bar settings')
                        }
                      >
                        ✂ {v.clip_count > 0 ? t('Clip again') : t('Make clips')}
                      </button>
                      {v.has_source && v.duration > 0 && <AddToCompilation videoId={v.video_id} />}
                      {confirming === v.video_id ? (
                        <span className="flex items-center gap-1.5 text-xs">
                          {t('Delete with its clips?')}
                          <button
                            className="btn-ghost !px-2 !py-1 text-xs text-error"
                            onClick={() => void remove(v)}
                          >
                            {t('Delete')}
                          </button>
                          <button
                            className="btn-ghost !px-2 !py-1 text-xs"
                            onClick={() => setConfirming(null)}
                          >
                            {t('Cancel')}
                          </button>
                        </span>
                      ) : (
                        <button
                          className="text-muted hover:text-error px-1 py-1"
                          onClick={() => setConfirming(v.video_id)}
                          disabled={!!v.in_queue}
                          aria-label={`${t('Delete')} ${v.title || v.video_id}`}
                          title={
                            v.compilations.length > 0
                              ? t(
                                  'Delete this video and its clips. Compilations that use it will need a replacement before they render again.'
                                )
                              : t('Delete this video and its clips')
                          }
                        >
                          <Trash />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
