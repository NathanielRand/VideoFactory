import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import {
  COMPILATIONS_PLAYLIST,
  describeHour,
  publishingApi,
  splitList,
  type BestTimes,
  type PublishingSettings
} from '../lib/publishing'
import type { CreatorSummary } from '../lib/types'
import type { Playlist } from '../lib/youtube'

const PLATFORMS: [string, string][] = [
  ['youtube', 'YouTube'],
  ['tiktok', 'TikTok'],
  ['instagram', 'Instagram'],
  ['facebook', 'Facebook'],
  ['x', 'X'],
  ['linkedin', 'LinkedIn']
]

/** What every post carries whichever publisher sends it, and when posts go.
 *
 *  Per-clip metadata is generated and edited per clip. This is the standing
 *  part: the channel's own keywords and hashtags, the comment under every
 *  post, where videos are filed, and the hours this audience watches. */
export default function PublishingDefaultsCard(): JSX.Element {
  const [s, setS] = useState<PublishingSettings | null>(null)
  const [keywords, setKeywords] = useState('')
  const [hashtags, setHashtags] = useState('')
  const [comment, setComment] = useState('')
  const [titleTag, setTitleTag] = useState('')
  const [applying, setApplying] = useState(false)
  const [applyMsg, setApplyMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [notice, setNotice] = useState('')
  const [platform, setPlatform] = useState('youtube')
  const [best, setBest] = useState<BestTimes | null>(null)
  const [playlists, setPlaylists] = useState<Playlist[] | null>(null)
  const [creators, setCreators] = useState<CreatorSummary[]>([])
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    publishingApi
      .settings()
      .then((got) => {
        setS(got)
        setKeywords(got.channel_keywords.join(', '))
        setHashtags(got.hashtags.join(' '))
        setComment(got.first_comment)
        setTitleTag(got.title_hashtag ?? '')
      })
      .catch(() => setS(null))
    // Playlist rules only mean something with YouTube connected for playlists.
    api
      .youtubeStatus()
      .then((st) => {
        if (!(st.enabled && st.connected && st.playlists_available)) return
        api
          .youtubePlaylists()
          .then((r) => setPlaylists(r.playlists))
          .catch(() => setPlaylists(null))
        api
          .creators()
          .then((r) => setCreators(r.creators))
          .catch(() => setCreators([]))
      })
      .catch(() => {
        /* YouTube off: no playlist section */
      })
  }, [])

  useEffect(() => {
    publishingApi
      .bestTimes(platform, 1)
      .then(setBest)
      .catch(() => setBest(null))
  }, [platform, s?.per_day])

  const save = async (patch: Partial<PublishingSettings>): Promise<void> => {
    try {
      const saved = await publishingApi.saveSettings(patch)
      setS(saved)
      // Show what was actually stored: the server drops duplicates and adds
      // missing #s, and the box should not keep claiming otherwise.
      if (patch.channel_keywords) setKeywords(saved.channel_keywords.join(', '))
      if (patch.hashtags) setHashtags(saved.hashtags.join(' '))
      setNotice(t('Saved'))
      setTimeout(() => setNotice(''), 2000)
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    }
  }

  const applySource = async (): Promise<void> => {
    setApplying(true)
    setApplyMsg(null)
    try {
      const r = await publishingApi.applySourceMetadata()
      setApplyMsg({
        ok: true,
        text: r.updated
          ? `${t('Updated')} ${r.updated} ${t('of')} ${r.total} ${t('clips')}.`
          : `${t('Nothing to change')}: ${r.total} ${t('clips')} ${t('already have it')}.`
      })
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      setApplyMsg({
        ok: false,
        text: /not found|404/i.test(m) ? t('The app needs a restart to pick this up.') : m
      })
    } finally {
      setApplying(false)
    }
  }

  const refreshStats = async (): Promise<void> => {
    setRefreshing(true)
    try {
      const got = await publishingApi.refreshStats()
      setNotice(
        got.updated
          ? `${got.updated} ${t('videos read')}`
          : got.reason || t('Nothing new to learn from yet.')
      )
      setBest(await publishingApi.bestTimes(platform, 1))
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setRefreshing(false)
    }
  }

  if (!s) return <></>

  const rule = (key: string): string => s.playlist_rules[key] ?? ''
  const setRule = (key: string, id: string): void => {
    const next = { ...s.playlist_rules }
    if (id) next[key] = id
    else delete next[key]
    void save({ playlist_rules: next })
  }
  const playlistSelect = (key: string, label: string): JSX.Element => (
    <label key={key} className="flex items-center gap-3 px-3 py-1.5">
      <span className="text-sm flex-1 truncate">{label}</span>
      <select
        className="input !w-56 !py-1 text-sm"
        value={rule(key)}
        onChange={(e) => setRule(key, e.target.value)}
      >
        <option value="">{s.auto_playlists ? t('Make one automatically') : t('None')}</option>
        {(playlists ?? []).map((p) => (
          <option key={p.id} value={p.id}>
            {p.title}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <section className="card space-y-4" aria-label={t('Reach and search')}>
      <div className="flex items-baseline gap-3 flex-wrap">
        <h2 className="font-semibold">{t('Reach & search defaults')}</h2>
        <p className="text-xs text-muted">
          {t('Added to every post on top of what each clip was given.')}
        </p>
        {notice && <span className="text-xs text-accent ml-auto">{notice}</span>}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div>
          <label className="label" htmlFor="pd-keywords">
            {t('Channel keywords')}
          </label>
          <input
            id="pd-keywords"
            className="input"
            value={keywords}
            placeholder={t('your channel name, your game, your niche')}
            onChange={(e) => setKeywords(e.target.value)}
            onBlur={() => void save({ channel_keywords: splitList(keywords) })}
          />
          <p className="text-[11px] text-muted mt-1">
            {t('Search phrases added to every YouTube upload, after the clip’s own.')}
          </p>
        </div>
        <div>
          <label className="label" htmlFor="pd-hashtags">
            {t('Always-on hashtags')}
          </label>
          <input
            id="pd-hashtags"
            className="input"
            value={hashtags}
            placeholder="#yourchannel"
            onChange={(e) => setHashtags(e.target.value)}
            onBlur={() => void save({ hashtags: hashtags.split(/[\s,]+/).filter(Boolean) })}
          />
          <p className="text-[11px] text-muted mt-1">
            {t('Lead every post. Each video’s own hashtags are added after these.')}
          </p>
        </div>
      </div>

      <div>
        <label className="label" htmlFor="pd-comment">
          {t('First comment on every post')}
        </label>
        <textarea
          id="pd-comment"
          className="input min-h-[60px] resize-y"
          maxLength={1000}
          value={comment}
          placeholder={t('Send us your clips on Discord: discord.gg/…')}
          onChange={(e) => setComment(e.target.value)}
          onBlur={() => void save({ first_comment: comment })}
        />
        <p className="text-[11px] text-muted mt-1">
          {t(
            'Posted under every video that has no comment of its own. A video can replace it with its own, or with one the AI writes from what happens in it (clip editor, or the compilation Publish dialog). Upload-Post posts comments; WoopSocial and YouTube direct cannot.'
          )}
        </p>
      </div>

      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          className="mt-0.5"
          checked={s.platform_captions}
          onChange={(e) => void save({ platform_captions: e.target.checked })}
        />
        <span>
          {t('Fit each caption to its platform')}
          <span className="block text-[11px] text-muted">
            {t(
              'TikTok, Instagram, X, Threads and Bluesky show only the caption: the hook leads, and it is cut to each platform’s length and hashtag habits.'
            )}
          </span>
        </span>
      </label>

      {/* When to post */}
      <div className="border border-raised/60 rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <span className="font-medium">{t('Best times')}</span>
          <select
            className="input !w-auto !py-1 text-sm"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            aria-label={t('Platform')}
          >
            {PLATFORMS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          <button
            className="btn-ghost !py-1 text-xs ml-auto"
            disabled={refreshing}
            onClick={() => void refreshStats()}
            title={t('Read view counts for your recent YouTube uploads now')}
          >
            {refreshing ? t('Reading…') : t('Learn from my views now')}
          </button>
        </div>
        {best && (
          <>
            <div className="flex flex-wrap gap-1.5">
              {best.top.map((c) => (
                <span
                  key={`${c.weekday}-${c.hour}`}
                  className="px-2 py-0.5 rounded-md bg-accent/15 text-accent text-xs"
                >
                  {describeHour(c.weekday, c.hour)}
                </span>
              ))}
            </div>
            <p className="text-[11px] text-muted">
              {best.learned_from
                ? `${t('Tuned by')} ${best.learned_from} ${t('of your posts')} (${Math.round(best.confidence * 100)}% ${t('weight')}).`
                : t(
                    'Usual peak hours for this platform, in your timezone. As your YouTube uploads get views, it shifts toward the hours your audience actually watches.'
                  )}
            </p>
          </>
        )}
        <div className="flex items-center gap-2 text-sm flex-wrap">
          <span className="text-xs text-muted">{t('Plan up to')}</span>
          <NumberSetting
            value={s.per_day}
            min={1}
            max={24}
            label={t('Posts per day')}
            onCommit={(v) => void save({ per_day: v })}
          />
          <span className="text-xs text-muted">{t('posts a day, at least')}</span>
          <NumberSetting
            value={s.min_gap_hours}
            min={1}
            max={12}
            label={t('Hours between posts')}
            onCommit={(v) => void save({ min_gap_hours: v })}
          />
          <span className="text-xs text-muted">{t('hours apart')}</span>
        </div>
      </div>

      <div className="space-y-1.5">
        <span className="font-medium text-sm">{t('Titles')}</span>
        <label className="flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.title_channel !== false}
            onChange={(e) => void save({ title_channel: e.target.checked })}
          />
          <span>
            {t('Put the source channel in every clip title')}
            <span className="block text-[11px] text-muted">
              {t('Published as "Title | Channel". Compilations have no single source, so they skip this.')}
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.title_hashtag_on !== false}
            onChange={(e) => void save({ title_hashtag_on: e.target.checked })}
          />
          <span>
            {t('End every title with one hashtag')}
            <span className="block text-[11px] text-muted">
              {t('Added when a video is published; the title is shortened to make room, never the channel or tag.')}
            </span>
          </span>
        </label>
        <div>
          <label className="label" htmlFor="pd-title-tag">
            {t('Title hashtag')}
          </label>
          <input
            id="pd-title-tag"
            className="input"
            value={titleTag}
            disabled={s.title_hashtag_on === false}
            placeholder={s.hashtags[0] ?? '#yourchannel'}
            onChange={(e) => setTitleTag(e.target.value)}
            onBlur={() => void save({ title_hashtag: titleTag.trim() })}
          />
          <p className="text-[11px] text-muted mt-1">
            {t('Left empty, the first always-on hashtag is used.')}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className="btn-ghost !py-1 text-xs"
            disabled={applying}
            onClick={() => void applySource()}
            title={t('Adds the source channel to the title, description, hashtags and keywords saved on clips you already made. Safe to run again.')}
          >
            {applying ? t('Updating…') : t('Add the source channel to existing clips')}
          </button>
          {applyMsg && (
            <span className={`text-xs ${applyMsg.ok ? 'text-accent' : 'text-red-400'}`} role="status">
              {applyMsg.text}
            </span>
          )}
        </div>
      </div>

      <div className="space-y-1.5">
        <span className="font-medium text-sm">{t('Links in descriptions')}</span>
        <label className="flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.link_source !== false}
            onChange={(e) => void save({ link_source: e.target.checked })}
          />
          <span>
            {t('Credit the source of every clip')}
            <span className="block text-[11px] text-muted">
              {t('Adds "Source: channel - link" to the description, linked to the moment the clip starts.')}
            </span>
          </span>
        </label>
        <label className="flex items-start gap-2 text-xs">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={s.link_playlist !== false}
            onChange={(e) => void save({ link_playlist: e.target.checked })}
          />
          <span>
            {t('Link the playlist a video was added to')}
            <span className="block text-[11px] text-muted">
              {t('For direct YouTube uploads that join a playlist.')}
            </span>
          </span>
        </label>
      </div>

      {playlists && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm">{t('YouTube playlists')}</span>
            <label className="flex items-center gap-2 text-xs ml-auto">
              <input
                type="checkbox"
                checked={s.auto_playlists}
                onChange={(e) => void save({ auto_playlists: e.target.checked })}
              />
              {t('Make a playlist per creator when none is set')}
            </label>
          </div>
          <p className="text-[11px] text-muted">
            {t(
              'Uploads with no playlist picked go here. Playlists keep viewers watching from one video into the next, which YouTube rewards.'
            )}
          </p>
          <div className="divide-y divide-raised/60 border border-raised/60 rounded-lg">
            {playlistSelect(COMPILATIONS_PLAYLIST, t('Compilations'))}
            {creators.map((c) => playlistSelect(`creator:${c.creator_id}`, c.display_name))}
          </div>
        </div>
      )}
    </section>
  )
}

/** A number that saves when you are done with it (Enter or leaving the box),
 *  not on every keystroke: typing 12 used to save 1 first, then 12, and
 *  re-plan best times for each. */
function NumberSetting({
  value,
  min,
  max,
  label,
  onCommit
}: {
  value: number
  min: number
  max: number
  label: string
  onCommit: (value: number) => void
}): JSX.Element {
  const [text, setText] = useState(String(value))
  useEffect(() => setText(String(value)), [value])
  const commit = (): void => {
    const next = Math.max(min, Math.min(max, Math.round(Number(text)) || min))
    setText(String(next))
    if (next !== value) onCommit(next)
  }
  return (
    <input
      type="number"
      min={min}
      max={max}
      className="input !w-16 !py-1 text-sm"
      value={text}
      aria-label={label}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
    />
  )
}
