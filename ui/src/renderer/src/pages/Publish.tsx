import QuotaMeter from '../components/QuotaMeter'
import { useCallback, useEffect, useMemo, useState } from 'react'
import AnalyticsSnapshot from '../components/AnalyticsSnapshot'
import ChannelVideos from '../components/ChannelVideos'
import CompilationPublishDialog from '../components/CompilationPublishDialog'
import PublishAllDialog from '../components/PublishAllDialog'
import PublishingDefaultsCard from '../components/PublishingDefaultsCard'
import ScheduleCalendar from '../components/ScheduleCalendar'
import { api } from '../lib/api'
import { compilationsApi, type Compilation } from '../lib/compilations'
import { t } from '../lib/i18n'
import type { Clip, LibraryItem, StudioEvent } from '../lib/types'
import type { PublishVia } from '../components/PublishAllDialog'
import { ItemBadge, VideoBadge, VideoProgress } from '../components/PublishBadge'
import { usePublishStates, useVideoStates } from '../lib/publishState'
import { useEvents } from '../lib/useEvents'
import PublishThumb, { useThumbStatus } from '../components/PublishThumb'

/** The first few clips of a video, each with its thumbnail (or a quiet blank
 *  where it has none), so a row shows what is about to go out. */
function VideoThumbStrip({ videoId }: { videoId: string }): JSX.Element | null {
  const [ids, setIds] = useState<number[]>([])
  useEffect(() => {
    let alive = true
    api
      .clips(videoId)
      .then((c) => alive && setIds(c.filter((x) => x.status !== 'uploaded').slice(0, 4).map((x) => x.id)))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [videoId])
  const have = useThumbStatus(ids)
  if (ids.length === 0) return null
  return (
    <div className="flex gap-1 shrink-0">
      {ids.map((id) => (
        <PublishThumb key={id} id={id} has={have[id]} className="h-9" />
      ))}
    </div>
  )
}

interface Account {
  name: string
  ready: boolean
  detail: string
}

/** Step 3: getting finished work out.
 *
 *  Everything to do with posting used to live wherever it was first built —
 *  Publish all and the schedule on the clip grid, accounts in Settings,
 *  auto-posting on each watched channel. None of that moves; this page
 *  gathers it so "what is ready, what is going out, what went out" is one
 *  place, and each card links to where its settings live. */
export default function Publish({
  onOpenClips,
  onOpenCompilation,
  onOpenWatch,
  onOpenAnalytics
}: {
  onOpenClips: (videoId: string) => void
  onOpenCompilation: (id: number) => void
  onOpenWatch: () => void
  onOpenAnalytics: () => void
}): JSX.Element {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [provider, setProvider] = useState<PublishVia>('woopsocial')
  const [publishReady, setPublishReady] = useState(false)
  const [youtubeReady, setYoutubeReady] = useState(false)
  // Any account that can post: WoopSocial, Upload-Post or YouTube.
  const [anyReady, setAnyReady] = useState(false)
  const [publishingComp, setPublishingComp] = useState<Compilation | null>(null)
  const [library, setLibrary] = useState<LibraryItem[]>([])
  const [compilations, setCompilations] = useState<Compilation[]>([])
  const [autoPosting, setAutoPosting] = useState<{ enabled: boolean; watching: number } | null>(
    null
  )
  const [publishing, setPublishing] = useState<Clip[] | null>(null)
  const [loadingClips, setLoadingClips] = useState<string | null>(null)
  const [error, setError] = useState('')

  const refresh = useCallback(async (): Promise<void> => {
    const [lib, comps] = await Promise.allSettled([api.library(), compilationsApi.list()])
    if (lib.status === 'fulfilled') setLibrary(lib.value)
    if (comps.status === 'fulfilled') setCompilations(comps.value)
  }, [])

  useEffect(() => {
    void refresh()
    // Same readiness rule as the clip grid: a provider is usable once it is
    // switched on AND holds a key. YouTube direct is listed for completeness.
    void Promise.allSettled([
      api.woopSocialStatus(),
      api.uploadPostStatus(),
      api.youtubeStatus()
    ]).then(([woop, up, yt]) => {
      const ws = woop.status === 'fulfilled' && Boolean(woop.value.enabled && woop.value.has_key)
      const upr = up.status === 'fulfilled' && Boolean(up.value.enabled && up.value.has_key)
      const ytr = yt.status === 'fulfilled' && Boolean(yt.value.enabled && yt.value.connected)
      // Multi-platform accounts first; a connected YouTube channel is enough
      // on its own, posting straight to YouTube on its own schedule.
      setPublishReady(ws || upr)
      setYoutubeReady(ytr)
      setAnyReady(ws || upr || ytr)
      setProvider(ws ? 'woopsocial' : upr ? 'uploadpost' : 'youtube')
      setAccounts([
        {
          name: 'WoopSocial',
          ready: ws,
          detail: ws
            ? woop.status === 'fulfilled'
              ? woop.value.platforms.join(', ')
              : ''
            : t('Not connected')
        },
        {
          name: 'Upload-Post',
          ready: upr,
          detail: upr
            ? up.status === 'fulfilled'
              ? up.value.platforms.join(', ')
              : ''
            : t('Not connected')
        },
        {
          name: 'YouTube',
          ready: ytr,
          detail: ytr
            ? (yt.status === 'fulfilled' && yt.value.channel?.title) || t('Connected')
            : t('Not connected')
        }
      ])
    })
    api
      .automation()
      .then((a) => setAutoPosting({ enabled: a.enabled, watching: a.watching }))
      .catch(() => setAutoPosting(null))
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (
      e.type === 'publish' ||
      e.type === 'compilation' ||
      (e.type === 'job' && e.status === 'done')
    ) {
      void refresh()
    }
  })

  const videoStates = useVideoStates()
  // Not "clip_count > published_clips": that only counted multi-platform
  // posts, so a video posted straight to YouTube still read as unposted.
  const ready = useMemo(
    () =>
      library.filter((v) => {
        const s = videoStates[v.video_id]
        return s ? s.clips > 0 && s.stage !== 'complete' : v.clip_count > v.published_clips
      }),
    [library, videoStates]
  )
  const done = useMemo(
    () => library.filter((v) => videoStates[v.video_id]?.stage === 'complete'),
    [library, videoStates]
  )
  const rendered = useMemo(() => compilations.filter((c) => c.status === 'done'), [compilations])
  const compStates = usePublishStates(useMemo(() => rendered.map((c) => -c.id), [rendered]))
  const compThumbs = useThumbStatus(useMemo(() => rendered.map((c) => -c.id), [rendered]))

  /** Open the same Publish dialog the clip grid uses, with this video's
   *  clips that have not gone out yet. */
  const publishVideo = async (v: LibraryItem): Promise<void> => {
    setLoadingClips(v.video_id)
    setError('')
    try {
      const clips = await api.clips(v.video_id)
      setPublishing(clips.filter((c) => c.status !== 'uploaded'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoadingClips(null)
    }
  }

  return (
    <div className="p-6 space-y-5 w-full">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-xl font-bold">{t('Publish')}</h1>
        <p className="text-sm text-muted">
          {t(
            'Post finished clips and compilations now, on a schedule, or automatically as new videos come in.'
          )}
        </p>
      </div>

      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}

      {/* First: what is coming, and where the next post goes. */}
      <ScheduleCalendar
        ready={ready}
        comps={rendered}
        onAddVideo={(v) => void publishVideo(v)}
        onAddComp={setPublishingComp}
      />

      <AnalyticsSnapshot variant="publish" onOpen={onOpenAnalytics} />

      {/* Limits and accounts share a row: both are "what can I post with". */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 items-start">
        <QuotaMeter />
        <section className="card space-y-3" aria-label={t('Accounts')}>
          <div className="flex items-center justify-between gap-2">
            <h2 className="font-semibold">{t('Accounts')}</h2>
            <button
              className="text-xs text-accent hover:underline"
              onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
            >
              {t('Manage in Settings')} →
            </button>
          </div>
          {!accounts && <p className="text-sm text-muted">{t('Loading…')}</p>}
          <ul className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-x-4 gap-y-2 text-sm">
            {accounts?.map((a) => (
              <li key={a.name} className="flex items-start gap-2 min-w-0">
                <span
                  className={`mt-1.5 size-2 rounded-full shrink-0 ${a.ready ? 'bg-success' : 'bg-raised'}`}
                  aria-hidden
                />
                <span className="min-w-0">
                  <span className="block font-medium">{a.name}</span>
                  <span className="block text-xs text-muted break-words">{a.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
        <section className="card space-y-3" aria-label={t('Automatic posting')}>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{t('Automatic posting')}</h2>
            <button className="text-xs text-accent hover:underline" onClick={onOpenWatch}>
              {t('Watching')} →
            </button>
          </div>
          <p className="text-sm text-muted">
            {autoPosting?.enabled && autoPosting.watching > 0
              ? `${t('Watching')} ${autoPosting.watching} ${autoPosting.watching === 1 ? t('channel') : t('channels')}. ${t('Each one posts its new clips the way you set it: off, ask first, or automatically.')}`
              : t(
                  'Watch a channel or playlist and its new videos are clipped and posted without you pasting a link. Set it up per channel.'
                )}
          </p>
        </section>

        <section className="card space-y-3" aria-label={t('Compilations')}>
          <h2 className="font-semibold">{t('Rendered compilations')}</h2>
          {rendered.length === 0 ? (
            <p className="text-sm text-muted">
              {t('Render a compilation in the Editor and it shows up here.')}
            </p>
          ) : (
            <ul className="space-y-1 text-sm max-h-40 overflow-y-auto">
              {rendered.map((c) => (
                <li key={c.id} className="flex items-center gap-2">
                  <PublishThumb id={-c.id} has={compThumbs[-c.id]} className="h-8" />
                  <span className="truncate flex-1">{c.title}</span>
                  <ItemBadge state={compStates[-c.id]} next />
                  <span className="text-xs text-muted">
                    {Object.keys(c.outputs ?? {}).join(' · ')}
                  </span>
                  <button
                    className="text-xs text-accent hover:underline"
                    onClick={() => onOpenCompilation(c.id)}
                  >
                    {t('Open')}
                  </button>
                  <button
                    className="btn-accent !py-0.5 !px-2 text-xs"
                    onClick={() => setPublishingComp(c)}
                    title={t('Title, description with chapters and credits, keywords, then platforms and timing')}
                  >
                    {t('Publish')} ↗
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="card space-y-3" aria-label={t('Ready to post')}>
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="font-semibold">{t('Ready to post')}</h2>
          <p className="text-xs text-muted">{t('Videos with clips that have not gone out yet.')}</p>
        </div>
        {!anyReady && (
          <p className="text-sm text-warn">
            {t(
              'Connect YouTube, WoopSocial or Upload-Post in Settings to publish from here. You can still export clips from the Editor.'
            )}
          </p>
        )}
        {anyReady && !publishReady && youtubeReady && (
          <p className="text-xs text-muted">
            {t('Posting to YouTube. Connect WoopSocial or Upload-Post to post to other platforms too.')}
          </p>
        )}
        {ready.length === 0 ? (
          <p className="text-sm text-muted">
            {t('Nothing waiting - every clip has been posted, or none have been made yet.')}
          </p>
        ) : (
          <ul className="divide-y divide-raised/60">
            {ready.map((v) => (
              <li key={v.video_id} className="flex items-center gap-3 py-2 text-sm flex-wrap">
                <VideoThumbStrip videoId={v.video_id} />
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="truncate font-medium">{v.title || v.video_id}</span>
                    <VideoBadge state={videoStates[v.video_id]} detail />
                  </div>
                  <div className="text-xs text-muted">
                    {v.creator_name || v.channel_name || '—'}
                    {videoStates[v.video_id]
                      ? ` · ${videoStates[v.video_id].ready} ${t('not posted')} · ${videoStates[v.video_id].scheduled + videoStates[v.video_id].publishing} ${t('scheduled')} · ${videoStates[v.video_id].published} ${t('posted')}`
                      : ` · ${v.clip_count - v.published_clips} ${t('not posted')}`}
                  </div>
                  <VideoProgress state={videoStates[v.video_id]} />
                </div>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  onClick={() => onOpenClips(v.video_id)}
                >
                  {t('Review in Editor')}
                </button>
                <button
                  className="btn-accent !py-1 !px-3 text-xs"
                  disabled={!anyReady || loadingClips === v.video_id}
                  onClick={() => void publishVideo(v)}
                >
                  {loadingClips === v.video_id ? t('Loading…') : `${t('Publish')} ↗`}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {done.length > 0 && (
        <section className="card space-y-2" aria-label={t('Fully posted')}>
          <h2 className="font-semibold">
            {t('Fully posted')} <span className="text-xs text-muted font-normal">({done.length})</span>
          </h2>
          <ul className="divide-y divide-raised/60 text-sm">
            {done.map((v) => (
              <li key={v.video_id} className="flex items-center gap-3 py-1.5">
                <span className="truncate flex-1">{v.title || v.video_id}</span>
                <VideoBadge state={videoStates[v.video_id]} detail />
                <button className="text-xs text-accent hover:underline" onClick={() => onOpenClips(v.video_id)}>
                  {t('Open')}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <ChannelVideos />

      <PublishingDefaultsCard />

      {publishingComp && (
        <CompilationPublishDialog
          compilationId={publishingComp.id}
          title={publishingComp.title}
          provider={provider === 'youtube' ? 'uploadpost' : provider}
          multiReady={publishReady}
          youtubeReady={youtubeReady}
          onClose={() => {
            setPublishingComp(null)
            void refresh()
          }}
        />
      )}

      {publishing && (
        <PublishAllDialog
          clips={publishing}
          provider={provider}
          onClose={() => {
            setPublishing(null)
            void refresh()
          }}
        />
      )}
    </div>
  )
}
