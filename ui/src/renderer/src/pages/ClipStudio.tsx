import { useEffect, useMemo, useRef, useState } from 'react'
import AddToCompilation from '../components/AddToCompilation'
import NoClipsExplanation from '../components/NoClipsExplanation'
import ClipBulkBar from '../components/ClipBulkBar'
import ClipCard from '../components/ClipCard'
import ClipEditor from '../components/ClipEditor'
import EditorView from '../components/EditorModal'
import ProcessingBar from '../components/ProcessingBar'
import PublishAllDialog from '../components/PublishAllDialog'
import ScheduleCalendar from '../components/ScheduleCalendar'
import { api } from '../lib/api'
import type { PublishVia } from '../components/PublishAllDialog'
import FlagClipDialog from '../components/FlagClipDialog'
import { useEvents } from '../lib/useEvents'
import { useJobWatch } from '../lib/useJobWatch'
import { useClipWork } from '../lib/clipWork'
import { useAutoThumbnails } from '../lib/autoThumbnails'
import { usePublishStates, useVideoStates, type ItemStateName } from '../lib/publishState'
import { ItemBadge, VideoBadge, VideoProgress } from '../components/PublishBadge'
import type { StudioTarget } from '../App'
import type { Clip, StudioEvent, Video } from '../lib/types'

/** Browse and edit the clips of processed videos — the Clips tab of the
 *  Editor. New videos are started from Home or the Library; clicking a clip
 *  there navigates here with it selected. */
export default function ClipStudio({
  target,
  onTargetConsumed
}: {
  target: StudioTarget | null
  onTargetConsumed: () => void
}): JSX.Element {
  const [videos, setVideos] = useState<Video[]>([])
  const [activeVideo, setActiveVideo] = useState<string | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [selectedClip, setSelectedClip] = useState<number | null>(null)
  // One clip published on its own. The same dialog as Publish all,
  // handed a list of one, so the platform picker and the daily budget
  // do not need a second implementation.
  const [publishOne, setPublishOne] = useState<Clip | null>(null)
  const [publishMany, setPublishMany] = useState<{ clips: Clip[]; when: 'now' | 'schedule' } | null>(null)
  // Ticked for a bulk action — not the same as the one clip open above.
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [flagging, setFlagging] = useState<Clip | null>(null)
  const [showSchedule, setShowSchedule] = useState(false)
  const [editingClipId, setEditingClipId] = useState<number | null>(null)
  const [videoSearch, setVideoSearch] = useState('')
  // Publishing is only offered once Upload-Post is switched on AND a key
  // is stored — the same rule the editor tab follows.
  const [publishReady, setPublishReady] = useState(false)
  // Which provider a batch goes through. WoopSocial when it is set up.
  const [publishProvider, setPublishProvider] = useState<PublishVia>('woopsocial')
  const [publishing, setPublishing] = useState(false)
  const [clipType, setClipType] = useState<'all' | 'shorts' | 'longform'>('all')
  // Filter by where a clip stands: the quick way to find what still needs posting.
  const [stateFilter, setStateFilter] = useState<'all' | 'todo' | 'scheduled' | 'published' | 'failed'>('all')
  const pendingClip = useRef<number | null>(null)
  const editorRef = useRef<HTMLDivElement>(null)
  const lastEventAt = useRef(Date.now())
  // Only while something is in flight, so an idle page never polls.
  // 'imported' is at rest too: in the library, never clipped.
  const busy = videos.some((v) => v.status !== 'done' && v.status !== 'failed' && v.status !== 'imported')

  const refreshVideos = async (): Promise<void> => {
    try {
      // Uploads kept for compilations have no clips to show; the Library
      // lists them, with Make clips, until they do.
      const v = (await api.videos()).filter((x) => x.status !== 'imported' || x.clip_count > 0)
      setVideos(v)
      if (!activeVideo && !target && v.length > 0) setActiveVideo(v[0].video_id)
    } catch {
      /* backend starting up */
    }
  }

  const deleteClip = async (clipId: number): Promise<void> => {
    try {
      await api.deleteClip(clipId)
      setSelectedClip((cur) => (cur === clipId ? null : cur)) // a card may hold an older render's closure
      setClips((cur) => cur.filter((c) => c.id !== clipId)) // drop it immediately
    } catch (e) {
      window.alert(`Could not delete: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  // The star on a card: mark or unmark a clip as exported by hand.
  const toggleExported = async (clip: Clip): Promise<void> => {
    try {
      const updated = await api.patchClip(clip.id, { exported: !clip.exported_at })
      setClips((cur) => cur.map((c) => (c.id === updated.id ? updated : c)))
    } catch (e) {
      window.alert(`Could not update: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const activeRef = useRef(activeVideo)
  activeRef.current = activeVideo
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshNow = (videoId: string): void => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
    refreshTimer.current = null
    refreshVideos()
    // The timer may fire after the user has moved to another video.
    if (activeRef.current === videoId || !activeRef.current) void refreshClips(videoId)
  }
  const refreshSoon = (videoId: string): void => {
    if (refreshTimer.current) return // one is already waiting; it reads the latest
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null
      refreshNow(videoId)
    }, 1000)
  }
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
    },
    []
  )

  const refreshClips = async (videoId: string): Promise<void> => {
    try {
      const c = await api.clips(videoId)
      // Keep the objects that did not change: a card redraws only when its own
      // clip object is a new one, so this is what lets a refresh be cheap.
      setClips((prev) => {
        const old = new Map(prev.map((x) => [x.id, x]))
        return c.map((n) => {
          const o = old.get(n.id)
          return o && JSON.stringify(o) === JSON.stringify(n) ? o : n
        })
      })
      if (pendingClip.current !== null) {
        if (c.some((x) => x.id === pendingClip.current)) setSelectedClip(pendingClip.current)
        pendingClip.current = null
      }
    } catch {
      setClips([])
    }
  }

  useEffect(() => {
    // Either provider makes batch publishing available.
    // Any connected account can publish: WoopSocial, Upload-Post, or a
    // YouTube channel posting on its own.
    Promise.allSettled([api.woopSocialStatus(), api.uploadPostStatus(), api.youtubeStatus()]).then(
      ([woop, up, yt]) => {
        const wsReady =
          woop.status === 'fulfilled' && Boolean(woop.value.enabled && woop.value.has_key)
        const upReady =
          up.status === 'fulfilled' && Boolean(up.value.enabled && up.value.has_key)
        const ytReady =
          yt.status === 'fulfilled' && Boolean(yt.value.enabled && yt.value.connected)
        setPublishReady(wsReady || upReady || ytReady)
        setPublishProvider(wsReady ? 'woopsocial' : upReady ? 'uploadpost' : 'youtube')
      }
    )
  }, [])

  useEffect(() => {
    refreshVideos()
  }, [])

  // Navigated here from a Dashboard clip link: jump to that video + clip.
  useEffect(() => {
    if (!target) return
    pendingClip.current = target.clipId ?? null
    setActiveVideo(target.videoId)
    onTargetConsumed()
  }, [target])

  useEffect(() => {
    setChecked(new Set())
    if (activeVideo) refreshClips(activeVideo)
    if (pendingClip.current === null) setSelectedClip(null)
  }, [activeVideo])

  useEvents((e: StudioEvent) => {
    lastEventAt.current = Date.now()
    if (e.type === 'progress' && (e.stage === 'render' || e.stage === 'done') && e.video_id) {
      // A render reports every clip it finishes; re-reading the list for each
      // one, while the machine is busy rendering, is what made the page crawl.
      // The end of the run reads at once, the rest at most about once a second.
      if (e.stage === 'done') refreshNow(e.video_id)
      else refreshSoon(e.video_id)
      if (!activeVideo) setActiveVideo(e.video_id)
    }
    if (e.type === 'job' && e.status === 'done' && activeVideo) refreshClips(activeVideo)
  })

  // Same reason as the Dashboard: the terminal event can be dropped for a
  // client that falls behind, and this page would then keep showing the clip
  // list from before the render.
  useJobWatch({
    active: busy,
    lastEventAt,
    onSettled: () => {
      refreshVideos()
      if (activeVideo) refreshClips(activeVideo)
    }
  })

  const current = useMemo(() => clips.find((c) => c.id === selectedClip) ?? null, [clips, selectedClip])
  const editingClip = useMemo(
    () => clips.find((c) => c.id === editingClipId) ?? null,
    [clips, editingClipId]
  )

  const shownVideos = useMemo(() => {
    const q = videoSearch.trim().toLowerCase()
    if (!q) return videos
    return videos.filter(
      (v) =>
        (v.title || '').toLowerCase().includes(q) || (v.channel_name || '').toLowerCase().includes(q)
    )
  }, [videos, videoSearch])

  const clipIds = useMemo(() => clips.map((c) => c.id), [clips])
  const states = usePublishStates(clipIds)
  const work = useClipWork(clipIds)
  // A new clip gets its first thumbnail on its own; a re-render never does.
  useAutoThumbnails(clipIds)
  const videoStates = useVideoStates()
  const inFilter = (s: ItemStateName | undefined): boolean => {
    if (stateFilter === 'all' || !s) return true
    if (stateFilter === 'todo') return s === 'ready' || s === 'exported'
    if (stateFilter === 'scheduled') return s === 'scheduled' || s === 'publishing' || s === 'partial'
    return s === stateFilter
  }
  const shownClips = useMemo(
    () =>
      clips
        .filter((c) =>
          clipType === 'all'
            ? true
            : clipType === 'longform'
              ? !!c.render_opts?.profile
              : !c.render_opts?.profile
        )
        .filter((c) => inFilter(states[c.id]?.state)),
    [clips, clipType, stateFilter, states]
  )

  if (editingClip) {
    return (
      <div className="p-6">
        <EditorView
          clip={editingClip}
          onClose={() => setEditingClipId(null)}
          onChanged={() => activeVideo && refreshClips(activeVideo)}
        />
      </div>
    )
  }

  return (
    <div className="p-6 space-y-5">
      <ProcessingBar />

      {videos.length === 0 ? (
        <div className="card text-muted text-sm">
          No clips yet — add a video on <span className="text-accent">Home</span> or in the{' '}
          <span className="text-accent">Library</span> and choose Make clips.
        </div>
      ) : (
        <input
          type="search"
          className="input !w-72"
          placeholder="Search your videos or channels…"
          aria-label="Search processed videos by title or channel"
          value={videoSearch}
          onChange={(e) => setVideoSearch(e.target.value)}
        />
      )}

      {shownVideos.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {shownVideos.map((v) => (
            <button
              key={v.video_id}
              onClick={() => setActiveVideo(v.video_id)}
              className={`px-3 py-1.5 rounded-lg text-sm max-w-64 truncate ${
                activeVideo === v.video_id
                  ? 'bg-accent/15 text-accent'
                  : 'bg-raised text-muted hover:text-ink'
              }`}
            >
              {v.channel_name ? `${v.channel_name} — ` : ''}
              {v.title || v.video_id}
              {/* Where the whole video has got, on its tab. */}
              {videoStates[v.video_id] && (
                <span className="ml-2 align-middle">
                  <VideoBadge state={videoStates[v.video_id]} />
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {videos.length > 0 && (
        <div className="space-y-5">
          {/* The selected clip sits above the grid, so picking one never
              moves it out from under the cursor and the grid can use the
              full width for smaller cards. */}
          <div ref={editorRef}>
            {current ? (
              <div className="space-y-3">
                <ClipEditor
                  clip={current}
                  onChanged={() => activeVideo && refreshClips(activeVideo)}
                  onOpenEditor={() => setEditingClipId(current.id)}
                  onPublish={publishReady ? () => setPublishOne(current) : undefined}
                />
                {/* This clip's range of the SOURCE, not its rendered file: a
                    compilation re-frames and re-credits every segment itself. */}
                <AddToCompilation
                  videoId={current.video_id}
                  start={current.start_s}
                  end={current.end_s}
                  label="Use in a compilation"
                />
              </div>
            ) : (
              <div className="card text-muted text-sm">Select a clip to preview and edit it.</div>
            )}
          </div>
          <div className="space-y-3">
            {activeVideo && videoStates[activeVideo] && (
              <div className="card !p-3 space-y-2">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  <VideoBadge state={videoStates[activeVideo]} detail />
                  {videoStates[activeVideo].scheduled > 0 && (
                    <span className="text-xs text-muted">
                      {videoStates[activeVideo].scheduled} scheduled
                    </span>
                  )}
                  {videoStates[activeVideo].ready > 0 && (
                    <span className="text-xs text-muted">{videoStates[activeVideo].ready} not posted</span>
                  )}
                  {videoStates[activeVideo].failed > 0 && (
                    <span className="text-xs text-red-300">{videoStates[activeVideo].failed} failed</span>
                  )}
                  <span className="ml-auto flex gap-1" role="group" aria-label="Filter clips by posting state">
                    {(
                      [
                        ['all', 'All'],
                        ['todo', 'Not posted'],
                        ['scheduled', 'Scheduled'],
                        ['published', 'Posted'],
                        ['failed', 'Failed']
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        onClick={() => setStateFilter(value)}
                        className={`px-2 py-0.5 rounded-md text-xs ${
                          stateFilter === value
                            ? 'bg-accent/20 text-accent font-medium'
                            : 'bg-raised text-muted hover:text-ink'
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </span>
                </div>
                <VideoProgress state={videoStates[activeVideo]} />
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex gap-1.5" role="group" aria-label="Filter clips by format">
                {(
                  [
                    ['all', 'All'],
                    ['shorts', '📱 Shorts'],
                    ['longform', '▭ Longform']
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setClipType(value)}
                    className={`px-2.5 py-1 rounded-md text-xs ${
                      clipType === value
                        ? 'bg-accent/20 text-accent font-medium'
                        : 'bg-raised text-muted hover:text-ink'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {/* Deliberately not styled as a twin of Export beside it.
                  Export writes files you can delete; this posts publicly and
                  cannot be undone, so it is quieter to look at and opens a
                  confirm rather than acting on the click. */}
              {publishReady && (
                <>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  onClick={() => setPublishing(true)}
                  disabled={shownClips.length === 0}
                  title={`Publish the ${shownClips.length} clip${shownClips.length === 1 ? '' : 's'} shown here to your social accounts`}
                >
                  {`Publish all (${shownClips.length}) ↗`}
                </button>
                <button
                  className="btn-ghost !py-1 !px-3 text-xs"
                  onClick={() => setShowSchedule(true)}
                  title="When each scheduled post is due, and what actually posted"
                >
                  Schedule
                </button>
                </>
              )}
            </div>
            <ClipBulkBar
              picked={shownClips.filter((c) => checked.has(c.id))}
              total={shownClips.length}
              onSelectAll={() => setChecked(new Set(shownClips.map((c) => c.id)))}
              onClear={() => setChecked(new Set())}
              onChanged={(deleted) => {
                if (deleted?.length) {
                  setClips((cur) => cur.filter((c) => !deleted.includes(c.id)))
                  if (selectedClip !== null && deleted.includes(selectedClip)) setSelectedClip(null)
                }
                if (activeVideo) void refreshClips(activeVideo)
              }}
              onPublish={(clips, when) => setPublishMany({ clips, when })}
              publishReady={publishReady}
            />
            <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-3">
              {shownClips.map((clip) => (
                <ClipCard
                  key={clip.id}
                  clip={clip}
                  selected={clip.id === selectedClip}
                  onClick={() => {
                    setSelectedClip(clip.id)
                    editorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                  onDelete={() => deleteClip(clip.id)}
                  onToggleExported={() => toggleExported(clip)}
                  onPublish={() => setPublishOne(clip)}
                  onFlag={() => setFlagging(clip)}
                  publishState={states[clip.id]}
                  work={work[clip.id]}
                  checked={checked.has(clip.id)}
                  selecting={checked.size > 0}
                  onCheck={() =>
                    setChecked((cur) => {
                      const next = new Set(cur)
                      if (!next.delete(clip.id)) next.add(clip.id)
                      return next
                    })
                  }
                />
              ))}
              {clips.length === 0 && (
                <div className="col-span-full">
                  <NoClipsExplanation
                    outcome={videos.find((v) => v.video_id === activeVideo)?.outcome}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showSchedule && (
        <div
          className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
          role="dialog"
          aria-modal="true"
          aria-label="Posting schedule"
          onClick={() => setShowSchedule(false)}
        >
          <div className="w-full max-w-6xl max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
            <ScheduleCalendar />
            <button className="btn-ghost w-full !py-2 mt-2" onClick={() => setShowSchedule(false)}>
              Close
            </button>
          </div>
        </div>
      )}

      {flagging && <FlagClipDialog clip={flagging} onClose={() => setFlagging(null)} />}

      {publishOne && (
        <PublishAllDialog
          clips={[publishOne]}
          provider={publishProvider}
          onClose={() => {
            setPublishOne(null)
            if (activeVideo) refreshClips(activeVideo)
          }}
        />
      )}

      {publishMany && (
        <PublishAllDialog
          clips={publishMany.clips}
          initialWhen={publishMany.when}
          provider={publishProvider}
          onClose={() => {
            setPublishMany(null)
            if (activeVideo) refreshClips(activeVideo)
          }}
        />
      )}

      {publishing && (
        <PublishAllDialog
          clips={shownClips}
          provider={publishProvider}
          onClose={() => {
            setPublishing(false)
            if (activeVideo) refreshClips(activeVideo)
          }}
        />
      )}
    </div>
  )
}
