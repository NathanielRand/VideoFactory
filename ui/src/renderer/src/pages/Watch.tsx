import { useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText } from '../lib/api'
import type {
  AutomationStatus,
  StudioEvent,
  Watch as WatchRow,
  WatchActions,
  WatchPlatform,
  WatchPublish
} from '../lib/types'
import { useEvents } from '../lib/useEvents'
import WatchCard from '../components/watch/WatchCard'
import WatchLive from '../components/watch/WatchLive'
import { ModePicker, useWoopAccounts } from '../components/watch/WatchPublishSettings'
import WatchSchedule, { type ScheduleValue } from '../components/watch/WatchSchedule'
import WatchActionsPicker, { DEFAULT_ACTIONS } from '../components/watch/WatchActionsPicker'
import Switch from '../components/Switch'
import WatchBackfill, {
  backfillBody,
  DEFAULT_BACKFILL,
  type BackfillValue
} from '../components/watch/WatchBackfill'
import { seedOptions } from '../components/queue/AddVideos'
import type { JobOptions } from '../lib/types'
import { platformLabel, WOOPSOCIAL_PLATFORMS } from '../lib/uploadpost'
import { t } from '../lib/i18n'

type AddMode = WatchPublish['mode']

const PLATFORMS: { id: WatchPlatform; label: string }[] = [
  { id: 'youtube', label: 'YouTube' },
  { id: 'twitch', label: 'Twitch' },
  { id: 'kick', label: 'Kick' }
]

const PLACEHOLDER: Record<WatchPlatform, string> = {
  youtube: 'Channel link, @handle, or playlist link',
  twitch: 'https://www.twitch.tv/channel or channel name',
  kick: 'https://kick.com/channel or channel name'
}

/** What the pasted text will be watched as, before the server looks it up.
 *  The server decides for real (sources/channel_feed.playlist_id); this is
 *  only so the form can say "Playlist" as you paste. */
function kindOf(platform: WatchPlatform, text: string): 'channel' | 'playlist' | null {
  const raw = text.trim()
  if (!raw) return null
  if (platform === 'youtube' && (/[?&]list=/.test(raw) || /^(PL|OLAK5uy_)[\w-]{10,}$/.test(raw)))
    return 'playlist'
  return 'channel'
}

/** Watched channels and playlists: a creator posts, and Video Factory clips
 *  it, adds it to a compilation, or both, with nobody pasting a link.
 *
 *  Everything here is off until switched on, and each watch is its own
 *  go-ahead. The page only shows and edits; the watching itself happens in
 *  the backend (server/automation.py), which keeps going with this page
 *  closed, and, when the tray option is on, with the window closed too. */
export default function Watch({
  onOpenInStudio,
  onOpenCreator
}: {
  onOpenInStudio?: (videoId: string) => void
  onOpenCreator?: (creatorId: number) => void
}): JSX.Element {
  const [status, setStatus] = useState<AutomationStatus | null>(null)
  const [watches, setWatches] = useState<WatchRow[] | null>(null)
  const [version, setVersion] = useState(0)
  const [platform, setPlatform] = useState<WatchPlatform>('youtube')
  const [channel, setChannel] = useState('')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tray, setTray] = useState<boolean | null>(null)
  const woop = useWoopAccounts()
  const [actions, setActions] = useState<WatchActions>(DEFAULT_ACTIONS)
  // Catching up on what is already there. One channel's choice, so it goes
  // back to "leave them" after each add.
  const [backfill, setBackfill] = useState<BackfillValue>(DEFAULT_BACKFILL)
  const [addMode, setAddMode] = useState<AddMode | null>(null)
  const [addPlatforms, setAddPlatforms] = useState<string[] | null>(null)
  // The watch just added opens its whole setup.
  const [justAdded, setJustAdded] = useState<number | null>(null)
  // How its clips go out, chosen before Add like in the Publish dialog.
  const [addSchedule, setAddSchedule] = useState<ScheduleValue>({
    max_posts: 0,
    spread: true,
    per_day: 5,
    gap_hours: 1,
    day_start: ''
  })
  // How its clips are made: the Generate bar's own settings to start with,
  // which is what "my settings" means everywhere else in the app.
  const [addClip, setAddClip] = useState<JobOptions>(() => seedOptions())
  const [addHashtags, setAddHashtags] = useState('')
  const [addOnlyMine, setAddOnlyMine] = useState(false)
  const inFlight = useRef(false)
  // Until touched, the add form follows what WoopSocial can do: hands-off with
  // every connected account when it is set up, asking first when it is not.
  const mode: AddMode = addMode ?? (woop?.ready ? 'auto' : 'ask')
  const connected = WOOPSOCIAL_PLATFORMS.filter((id) => woop?.connected.includes(id))
  const platforms = addPlatforms ?? connected
  const kind = kindOf(platform, channel)

  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const [s, w] = await Promise.all([api.automation(), api.watches()])
      setStatus(s)
      setWatches(w)
      setVersion((v) => v + 1)
      setError(null)
    } catch (e) {
      setError(errorText(e))
    } finally {
      inFlight.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (e.type === 'automation' || e.type === 'queue' || e.type === 'job') void refresh()
  })

  // Belt and braces against a dropped WebSocket, like the Queue page: a
  // watch's "checked 3 min ago" must not freeze because a socket died.
  useEffect(() => {
    const id = setInterval(() => void refresh(), 20000)
    return () => clearInterval(id)
  }, [refresh])

  // The tray option lives in the main process, which decides what closing
  // the window does. An older preload has no tray at all, so ask carefully.
  useEffect(() => {
    const bridge = window.studio?.tray
    if (!bridge) return
    bridge
      .get()
      .then((got) => setTray(got.keepInTray))
      .catch(() => setTray(null))
  }, [])

  const setKeepInTray = async (on: boolean): Promise<void> => {
    const bridge = window.studio?.tray
    if (!bridge) return
    const got = await bridge.set(on)
    setTray(got.keepInTray)
  }

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  const add = async (): Promise<void> => {
    if (!channel.trim()) return
    setAdding(true)
    setError(null)
    try {
      const added = await api.addWatch(
        platform,
        channel.trim(),
        {
          // Nothing to publish from a watch that makes no clips.
          mode: actions.clips ? mode : 'off',
          platforms,
          ...addSchedule,
          hashtags: addHashtags
            .split(/[\s,]+/)
            .map((h) => h.replace(/^#/, '').trim())
            .filter(Boolean),
          ai_hashtags: !addOnlyMine
        },
        addClip,
        actions,
        backfillBody(backfill)
      )
      setJustAdded(added.id)
      setBackfill({ ...DEFAULT_BACKFILL, newest_at: backfill.newest_at })
      setChannel('')
      // A new compilation is made once; the next watch picks it from the list.
      if (actions.compile && added.actions.compilation.compilation_id != null) {
        setActions({
          ...actions,
          compilation: {
            what: actions.compilation.what,
            max_clips: actions.compilation.max_clips,
            compilation_id: added.actions.compilation.compilation_id
          }
        })
      }
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setAdding(false)
    }
  }

  const clipsPanel = (
    <div className="space-y-4">
      <div className="flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
        <span className="label">{t('Clip options')}</span>
        {(
          [
            ['captions', 'Captions', addClip.captions !== false],
            ['long_clips', '60s+', Boolean(addClip.long_clips)],
            ['podcast', 'Podcast', Boolean(addClip.podcast)],
            ['longform', 'Longform', Boolean(addClip.longform)]
          ] as const
        ).map(([key, label, on]) => (
          <label key={key} className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={on}
              onChange={(e) => {
                const next = { ...addClip }
                if (key === 'captions') next.captions = e.target.checked
                else if (key === 'longform')
                  next.longform = e.target.checked ? { mode: 'short_clips' } : null
                else next[key] = e.target.checked
                setAddClip(next)
              }}
            />
            {t(label)}
          </label>
        ))}
      </div>
      <p className="text-xs text-muted -mt-2">
        {t('Starts from your Generate settings. Caption style and more are in its Settings after you add it.')}
      </p>

      <ModePicker value={mode} onChange={setAddMode} />

      {mode !== 'off' && woop && !woop.ready && (
        <div className="text-sm text-warn flex items-center gap-3 flex-wrap">
          {t('Publishing goes through WoopSocial. Add your WoopSocial API key in Settings first.')}
          <button
            className="btn-ghost !px-2 !py-1 text-xs"
            onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
          >
            {t('Open Settings')}
          </button>
        </div>
      )}
      {mode !== 'off' && woop?.ready && (
        <div className="space-y-4">
          <div className="flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
            <span className="label">{t('Publish to')}</span>
            {connected.length === 0 ? (
              <span className="text-muted">
                {t('No accounts are connected to WoopSocial yet. Connect them in Settings.')}
              </span>
            ) : (
              connected.map((id) => (
                <label key={id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    className="size-4 accent-[#38BDF8]"
                    checked={platforms.includes(id)}
                    onChange={(e) =>
                      setAddPlatforms(
                        e.target.checked ? [...platforms, id] : platforms.filter((x) => x !== id)
                      )
                    }
                  />
                  {platformLabel(id)}
                </label>
              ))
            )}
          </div>
          <WatchSchedule value={addSchedule} onChange={setAddSchedule} />
          <div className="text-sm space-y-1">
            <label className="label block" htmlFor="add-watch-tags">
              {t('Hashtags on every post')}
            </label>
            <input
              id="add-watch-tags"
              className="input"
              value={addHashtags}
              placeholder="#creatorname #twitch"
              onChange={(e) => setAddHashtags(e.target.value)}
            />
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="checkbox"
                className="size-4 accent-[#38BDF8]"
                checked={addOnlyMine}
                onChange={(e) => setAddOnlyMine(e.target.checked)}
              />
              {t('Only use my hashtags')}
              <span className="text-muted text-xs">{t('(leave out the ones the AI picks)')}</span>
            </label>
          </div>
        </div>
      )}
    </div>
  )

  const on = Boolean(status?.enabled)
  const channels = (watches ?? []).filter((w) => w.kind !== 'playlist').length
  const lists = (watches ?? []).filter((w) => w.kind === 'playlist').length
  const handsOff = actions.clips && mode === 'auto'

  return (
    <div className="p-6 space-y-6 w-full max-w-5xl">
      {/* ---- title and the one switch everything hangs on ---- */}
      <header className="flex items-start gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">{t('Watching')}</h1>
          <p className="text-sm text-muted mt-1 max-w-2xl">
            {t('Follow channels and playlists. When something new is posted, Video Factory clips it, adds it to a compilation, or both, with nobody pasting a link.')}
          </p>
        </div>
        <div
          className={`card !py-3 flex items-center gap-4 border ${on ? '!border-success/40' : ''}`}
        >
          <div className="text-right">
            <p className={`font-semibold text-sm ${on ? 'text-success' : ''}`}>
              {on ? t('Watching is on') : t('Watching is off')}
            </p>
            <p className="text-xs text-muted">
              {on && status
                ? `${t('Checks every')} ${status.interval_minutes} ${t('min while Video Factory runs')}`
                : t('Nothing is checked, queued or published')}
            </p>
          </div>
          <Switch
            checked={on}
            disabled={busy || status === null}
            label={t('Watching')}
            onChange={(next) => void act(() => api.setAutomation({ enabled: next }))}
          />
        </div>
      </header>

      <WatchLive />

      {/* ---- add a channel or playlist ---- */}
      <section className="card !p-0 overflow-hidden" aria-label={t('Watch something new')}>
        <div className="p-5 space-y-4">
          <div>
            <h2 className="font-semibold text-lg">{t('Watch something new')}</h2>
            <p className="text-sm text-muted">
              {t('A channel, an @handle, or a YouTube playlist.')}
            </p>
          </div>
          <div className="flex gap-2 flex-wrap items-stretch">
            <div className="inline-flex rounded-lg bg-raised/60 p-1 gap-1" role="radiogroup" aria-label={t('Platform')}>
              {PLATFORMS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  role="radio"
                  aria-checked={platform === p.id}
                  onClick={() => setPlatform(p.id)}
                  className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                    platform === p.id ? 'bg-accent text-base font-semibold' : 'text-muted hover:text-ink'
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="relative flex-1 min-w-64">
              <input
                className="input !pr-24 h-full"
                value={channel}
                placeholder={t(PLACEHOLDER[platform])}
                onChange={(e) => setChannel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void add()
                }}
                aria-label={t('Channel or playlist')}
              />
              {kind && (
                <span
                  className={`absolute right-2 top-1/2 -translate-y-1/2 text-[10px] font-semibold uppercase tracking-wide rounded px-2 py-0.5 ${
                    kind === 'playlist' ? 'bg-accent/15 text-accent' : 'bg-raised text-muted'
                  }`}
                >
                  {kind === 'playlist' ? t('Playlist') : t('Channel')}
                </span>
              )}
            </div>
          </div>
          {platform === 'kick' && (
            <p className="text-xs text-warn">
              {t(
                'Kick has no official way to list a channel’s videos. Video Factory uses the same unofficial one its Kick downloads rely on, so it can stop working without notice.'
              )}
            </p>
          )}

          <div className="space-y-3 pt-1">
            <p className="label">{t('What happens to each new video')}</p>
            <WatchActionsPicker
              value={actions}
              onChange={setActions}
              name=""
              clipsPanel={clipsPanel}
            />
          </div>

          <div className="space-y-3 pt-1">
            <p className="label">
              {kind === 'playlist' ? t('Videos already in the playlist') : t('Videos already posted')}
            </p>
            <WatchBackfill
              value={backfill}
              onChange={setBackfill}
              playlist={kind === 'playlist'}
              platform={platform}
            />
          </div>
        </div>
        <div className="flex items-center gap-4 flex-wrap bg-raised/30 border-t border-raised/60 px-5 py-3">
          <p className="text-xs text-muted flex-1 min-w-64">
            {backfill.mode === 'count'
              ? `${t('Catches up on the latest')} ${backfill.count} ${t('videos first, then takes each new one')}${handsOff ? ` ${t('and publishes its clips on the schedule above')}` : ''}.`
              : backfill.mode === 'since' && backfill.since
                ? `${t('Catches up on everything posted since')} ${new Date(backfill.since + 'T00:00').toLocaleDateString()}, ${t('then takes each new one')}${handsOff ? ` ${t('and publishes its clips on the schedule above')}` : ''}.`
                : handsOff
                  ? t('From now on each new video is taken and its clips published on the schedule above, with nobody at the PC. What is already there is listed, not taken.')
                  : t('Only what is posted from now on is taken. What is already there is listed, so you can take any of it with one click.')}
          </p>
          <button
            className="btn-accent"
            onClick={() => void add()}
            disabled={adding || !channel.trim()}
          >
            {adding
              ? kind === 'playlist'
                ? t('Finding the playlist…')
                : t('Finding the channel…')
              : t('Start watching')}
          </button>
        </div>
      </section>

      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}

      {/* ---- what is being watched ---- */}
      <section className="space-y-3" aria-label={t('Your watches')}>
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="font-semibold text-lg">{t('Your watches')}</h2>
          {watches && watches.length > 0 && (
            <span className="text-sm text-muted">
              {channels > 0 && `${channels} ${t(channels === 1 ? 'channel' : 'channels')}`}
              {channels > 0 && lists > 0 && ' · '}
              {lists > 0 && `${lists} ${t(lists === 1 ? 'playlist' : 'playlists')}`}
            </span>
          )}
        </div>
        {watches && status && watches.length === 0 && (
          <div className="card border-dashed text-center py-8 space-y-1">
            <p className="font-medium">{t('Nothing watched yet')}</p>
            <p className="text-sm text-muted">
              {t('Paste a channel or playlist above to start.')}
            </p>
          </div>
        )}
        {watches &&
          status &&
          watches.map((w) => (
            <WatchCard
              key={w.id}
              watch={w}
              automation={status}
              version={version}
              onChanged={() => void refresh()}
              onOpenInStudio={onOpenInStudio}
              onOpenCreator={onOpenCreator}
              openSetup={w.id === justAdded}
            />
          ))}
        {!watches && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}
      </section>

      {/* ---- running unattended ---- */}
      <section className="card space-y-3" aria-label={t('Running unattended')}>
        <h2 className="font-semibold">{t('Running unattended')}</h2>
        <Toggle
          checked={Boolean(status?.delete_sources)}
          disabled={busy || status === null}
          onChange={(next) => void act(() => api.setAutomation({ delete_sources: next }))}
          title={t('Delete each watched video’s download once its clips are published')}
          text={t(
            'Keeps an always-on PC from filling its disk. The clips stay. Re-rendering one of those clips later needs the video downloaded again.'
          )}
        />
        {tray !== null && (
          <Toggle
            checked={tray}
            onChange={(next) => void setKeepInTray(next)}
            title={t('Keep watching when the window is closed')}
            text={t(
              'Closing the window leaves Video Factory running in the system tray. Quit it from the tray icon. Windows going to sleep still stops it.'
            )}
          />
        )}
      </section>
    </div>
  )
}

function Toggle({
  checked,
  disabled = false,
  onChange,
  title,
  text
}: {
  checked: boolean
  disabled?: boolean
  onChange: (on: boolean) => void
  title: string
  text: string
}): JSX.Element {
  return (
    <div className="flex items-start gap-3">
      <div className="pt-0.5">
        <Switch size="sm" checked={checked} disabled={disabled} label={title} onChange={onChange} />
      </div>
      <div className="text-sm">
        <p>{title}</p>
        <p className="text-xs text-muted">{text}</p>
      </div>
    </div>
  )
}
