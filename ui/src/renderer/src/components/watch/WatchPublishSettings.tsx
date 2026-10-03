import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../../lib/api'
import type { Watch, WatchPublish } from '../../lib/types'
import { platformLabel, WOOPSOCIAL_PLATFORMS } from '../../lib/uploadpost'
import { t } from '../../lib/i18n'
import WatchPlatformOptions, { type PlatformOverrides } from './WatchPlatformOptions'
import WatchSchedule, { type ScheduleValue } from './WatchSchedule'

/** Whether WoopSocial can publish, and which accounts it has. Null while
 *  loading. Shared by the add form and each channel's settings, so both offer
 *  the same platforms. */
export function useWoopAccounts(): { ready: boolean; connected: string[] } | null {
  const [woop, setWoop] = useState<{ ready: boolean; connected: string[] } | null>(null)
  useEffect(() => {
    let live = true
    const load = async (): Promise<void> => {
      try {
        const status = await api.woopSocialStatus()
        const ready = status.enabled && status.has_key
        const connected = ready ? (await api.woopSocialConnections()).connected : []
        if (live) setWoop({ ready, connected })
      } catch {
        if (live) setWoop({ ready: false, connected: [] })
      }
    }
    void load()
    return () => {
      live = false
    }
  }, [])
  return woop
}

/** What happens once a watch's clips are made. Shared with the add form. */
export const PUBLISH_MODES: { id: WatchPublish['mode']; label: string; hint: string }[] = [
  { id: 'off', label: 'Keep them', hint: 'Only make the clips. Publish any of them yourself, when you like.' },
  { id: 'ask', label: 'Ask me first', hint: 'Make the clips, then wait for you to press Publish.' },
  {
    id: 'auto',
    label: 'Publish automatically',
    hint: 'Publish as soon as the clips are made. Anything that fails is tried again, so it keeps going with nobody at the PC.'
  }
]

/** Radio buttons drawn as one segmented control. */
export function ModePicker({
  value,
  onChange
}: {
  value: WatchPublish['mode']
  onChange: (mode: WatchPublish['mode']) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      <p className="label">{t('When the clips are made')}</p>
      <div className="inline-flex flex-wrap rounded-lg bg-raised/60 p-1 gap-1" role="radiogroup">
        {PUBLISH_MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={value === m.id}
            onClick={() => onChange(m.id)}
            className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
              value === m.id ? 'bg-accent text-base font-semibold' : 'text-muted hover:text-ink'
            }`}
          >
            {t(m.label)}
          </button>
        ))}
      </div>
      <p className="text-xs text-muted">{t(PUBLISH_MODES.find((m) => m.id === value)?.hint ?? '')}</p>
    </div>
  )
}

/** What happens to a watch's clips once they are made.
 *
 *  Configured once per channel, so nobody fills in a publish form for every
 *  video. The platforms offered are the ones actually connected to
 *  WoopSocial, not a fixed list: a box for an account that is not there would
 *  only produce a "skipped" row later. */
export default function WatchPublishSettings({
  watch,
  onSaved
}: {
  watch: Watch
  onSaved: () => void
}): JSX.Element {
  const p = watch.publish
  const [mode, setMode] = useState(p.mode)
  const [platforms, setPlatforms] = useState<string[]>(p.platforms)
  const [schedule, setSchedule] = useState<ScheduleValue>({
    max_posts: p.max_posts ?? 0,
    spread: p.spread ?? true,
    per_day: p.per_day,
    gap_hours: p.gap_hours,
    day_start: p.day_start
  })
  const [hashtags, setHashtags] = useState(p.hashtags.map((h) => `#${h.replace(/^#/, '')}`).join(' '))
  const [aiHashtags, setAiHashtags] = useState(p.ai_hashtags)
  const [footer, setFooter] = useState(p.footer)
  const [overrides, setOverrides] = useState<PlatformOverrides>(
    (p.overrides ?? {}) as PlatformOverrides
  )
  const woop = useWoopAccounts()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // Connected accounts, plus anything already chosen that has since been
  // disconnected, so a stale choice stays visible and can be unticked.
  const offered = WOOPSOCIAL_PLATFORMS.filter(
    (id) => woop?.connected.includes(id) || platforms.includes(id)
  )

  const save = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      await api.patchWatch(watch.id, {
        publish: {
          mode,
          platforms,
          ...schedule,
          hashtags: hashtags
            .split(/[\s,]+/)
            .map((h) => h.replace(/^#/, '').trim())
            .filter(Boolean),
          ai_hashtags: aiHashtags,
          footer,
          overrides
        }
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  // Every change saves itself a moment later: one panel, no button to forget.
  // Only real changes, compared with what was last saved.
  const current = JSON.stringify([
    mode, platforms, schedule, hashtags, aiHashtags, footer, overrides
  ])
  const lastSaved = useRef(current)
  useEffect(() => {
    if (current === lastSaved.current) return
    const id = setTimeout(() => {
      lastSaved.current = current
      void save()
    }, 600)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current])

  return (
    <div className="space-y-4">
      <ModePicker value={mode} onChange={setMode} />

      {mode !== 'off' && (
        <>
          {woop && !woop.ready ? (
            <div className="text-sm text-warn flex items-center gap-3 flex-wrap">
              {t('Publishing goes through WoopSocial, which is not set up yet.')}
              <button
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}
              >
                {t('Open Settings')}
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="label">{t('Publish to')}</p>
              {woop === null ? (
                <p className="text-sm text-muted">{t('Loading…')}</p>
              ) : offered.length === 0 ? (
                <p className="text-sm text-muted">
                  {t('No accounts are connected to WoopSocial yet. Connect them in Settings.')}
                </p>
              ) : (
                <div className="flex gap-x-5 gap-y-2 flex-wrap">
                  {offered.map((id) => (
                    <label key={id} className="flex items-center gap-2 cursor-pointer text-sm">
                      <input
                        type="checkbox"
                        className="size-4 accent-[#38BDF8]"
                        checked={platforms.includes(id)}
                        onChange={(e) =>
                          setPlatforms((prev) =>
                            e.target.checked ? [...prev, id] : prev.filter((x) => x !== id)
                          )
                        }
                      />
                      {platformLabel(id)}
                      {!woop.connected.includes(id) && (
                        <span className="text-xs text-warn">{t('(not connected)')}</span>
                      )}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          <WatchSchedule value={schedule} onChange={setSchedule} />

          <WatchPlatformOptions platforms={platforms} value={overrides} onChange={setOverrides} />

          <div className="text-sm space-y-1">
            <label className="label block" htmlFor={`watch-tags-${watch.id}`}>
              {t('Hashtags on every post')}
            </label>
            <input
              id={`watch-tags-${watch.id}`}
              className="input"
              value={hashtags}
              placeholder="#creatorname #twitch"
              onChange={(e) => setHashtags(e.target.value)}
            />
            <label className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="checkbox"
                className="size-4 accent-[#38BDF8]"
                checked={!aiHashtags}
                onChange={(e) => setAiHashtags(!e.target.checked)}
              />
              {t('Only use my hashtags')}
              <span className="text-muted text-xs">{t('(leave out the ones the AI picks)')}</span>
            </label>
            <span className="text-xs text-muted block">
              {t('These go first on every caption, so they are always kept.')}
            </span>
          </div>

          <label className="text-sm space-y-1 block">
            <span className="label block">{t('Text under every caption (optional)')}</span>
            <textarea
              className="input min-h-16"
              value={footer}
              maxLength={1000}
              onChange={(e) => setFooter(e.target.value)}
            />
            <span className="text-xs text-muted block">
              {t(
                "Leave out links and 'clipped from' wording: TikTok can flag those posts as unoriginal content."
              )}
            </span>
          </label>
        </>
      )}

      <div className="flex items-center gap-3">
        <span className="text-xs text-muted">
          {busy ? t('Saving…') : t('Changes save as you make them.')}
        </span>
        {saved && <span className="text-sm text-accent">{t('Saved')}</span>}
        {error && <span className="text-sm text-error">{error}</span>}
      </div>
    </div>
  )
}
