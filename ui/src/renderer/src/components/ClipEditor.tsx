import { useEffect, useState } from 'react'
import { useClipRendering } from '../lib/clipWork'
import { api } from '../lib/api'
import { getExportFolder, pickExportFolder, setExportFolder } from '../lib/exportFolder'
import { Folder, Scissors } from './icons'
import FormatVariants from './FormatVariants'
import type { CreditStyle } from '../lib/compilations'
import {
  CLIP_SIZES,
  CREDIT_POSITIONS,
  CREDIT_SAMPLES,
  clipProfiles,
  useBrandingProfiles
} from '../lib/branding'
import type { CaptionStyle, Clip, WatermarkConfig } from '../lib/types'
import BrandingSection, { type ProfileChoice } from './BrandingSection'
import { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import { DEFAULT_WATERMARK } from './WatermarkControls'
import { FirstCommentField, VideoHashtagsField } from './PublishExtras'
import ThumbnailCard from './ThumbnailCard'
import PlaylistSelect, { useChannelPlaylists } from './PlaylistSelect'
import ClipBrandingOverlay from './ClipBrandingOverlay'
import VideoPlayer from './VideoPlayer'
import { thumbsApi } from '../lib/thumbnails'

const CHANNELS = ['text', 'audio', 'visual', 'reaction', 'engagement'] as const

/** The clip's branding link, as the editor's state: which profile it follows
 *  and the parts it sets for itself. A clip with no link is "as processed". */
function brandingOf(clip: Clip): {
  profile: ProfileChoice
  cw: boolean
  cc: boolean
  ck: boolean
  wm: WatermarkConfig
  credit: CreditStyle
  captions: CaptionStyle
} {
  const link = clip.render_opts?.branding
  const { credit, captions, ...wm } = clip.render_opts?.watermark ?? { ...DEFAULT_WATERMARK, type: 'none' as const }
  return {
    profile: link ? link.profile_id : 'processed',
    cw: !!link?.custom_watermark,
    cc: !!link?.custom_credit,
    ck: !!link?.custom_captions,
    wm: wm as WatermarkConfig,
    credit: credit ?? { enabled: false },
    captions: { ...DEFAULT_CAPTION_STYLE, ...(captions ?? clip.render_opts?.caption_style) }
  }
}

export default function ClipEditor({
  clip,
  onChanged,
  onOpenEditor,
  onPublish
}: {
  clip: Clip
  onChanged: () => void
  onOpenEditor: () => void
  /** Publish this one clip. Absent when no provider is set up, which hides
   *  the button rather than showing one that can only apologise. */
  onPublish?: () => void
}): JSX.Element {
  const [title, setTitle] = useState(clip.title)
  const [description, setDescription] = useState(clip.description)
  const [hashtags, setHashtags] = useState(clip.hashtags.join(' '))
  const [keywords, setKeywords] = useState((clip.keywords ?? []).join(', '))
  const [firstComment, setFirstComment] = useState(clip.first_comment ?? '')
  const [suggestedComment, setSuggestedComment] = useState(clip.suggested_comment ?? '')
  const [playlistId, setPlaylistId] = useState(clip.playlist_id ?? '')
  const { playlists, available: playlistsAvailable } = useChannelPlaylists()
  const [start, setStart] = useState(clip.start_s)
  const [end, setEnd] = useState(clip.end_s)
  const [folder, setFolder] = useState('')

  // Default the export destination to the remembered folder / OS Downloads.
  useEffect(() => {
    getExportFolder().then(setFolder)
  }, [])
  const [busy, setBusy] = useState<string | null>(null)
  const rendering = useClipRendering(clip.id)
  const [notice, setNotice] = useState<string | null>(null)

  // ---- branding: a profile, with any part this clip sets for itself ----
  const profiles = clipProfiles(useBrandingProfiles().profiles)
  const brand = brandingOf(clip)
  const [bProfile, setBProfile] = useState<ProfileChoice>(brand.profile)
  const [cw, setCw] = useState(brand.cw)
  const [cc, setCc] = useState(brand.cc)
  const [ck, setCk] = useState(brand.ck)
  const [wm, setWm] = useState<WatermarkConfig>(brand.wm)
  const [credit, setCredit] = useState<CreditStyle>(brand.credit)
  const [captions, setCaptions] = useState<CaptionStyle>(brand.captions)
  const [captionsTouched, setCaptionsTouched] = useState(false)
  const chosen = typeof bProfile === 'number' ? profiles.find((x) => x.id === bProfile) : undefined
  const landscape = !!clip.render_opts?.profile

  // The layers the next render will burn, as BrandingSection resolves them:
  // the profile's, except for any part this clip sets for itself.
  const followed = bProfile !== 'processed'
  const forced = followed && !chosen
  const effWatermark: WatermarkConfig | null = !followed ? null : cw || forced ? wm : (chosen?.config ?? null)
  const effCredit: CreditStyle | null = !followed
    ? null
    : cc || forced
      ? credit
      : (chosen?.config.credit ?? { enabled: false })
  const effCaptions: CaptionStyle | null = !followed
    ? null
    : ck || forced
      ? captions
      : (chosen?.config.captions ?? DEFAULT_CAPTION_STYLE)
  const [showBranding, setShowBranding] = useState(false)
  // Once a thumbnail exists it stands in for the video before play.
  const [hasThumb, setHasThumb] = useState(false)
  const [thumbVersion, setThumbVersion] = useState(0)

  useEffect(() => {
    setTitle(clip.title)
    setDescription(clip.description)
    setHashtags(clip.hashtags.join(' '))
    setKeywords((clip.keywords ?? []).join(', '))
    setFirstComment(clip.first_comment ?? '')
    setSuggestedComment(clip.suggested_comment ?? '')
    setPlaylistId(clip.playlist_id ?? '')
    setStart(clip.start_s)
    setEnd(clip.end_s)
    setNotice(null)
    const b = brandingOf(clip)
    setBProfile(b.profile)
    setCw(b.cw)
    setCc(b.cc)
    setCk(b.ck)
    setWm(b.wm)
    setCredit(b.credit)
    setCaptions(b.captions)
    setCaptionsTouched(false)
    setShowBranding(false)
  }, [clip.id])

  const flash = (msg: string): void => {
    setNotice(msg)
    setTimeout(() => setNotice(null), 4000)
  }

  const run = async (label: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(label)
    try {
      await fn()
    } catch (e) {
      flash(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
    }
  }

  const saveMetadata = (): Promise<void> =>
    run('save', async () => {
      await api.patchClip(clip.id, {
        title,
        description,
        hashtags: hashtags.split(/\s+/).filter(Boolean),
        keywords: keywords
          .split(',')
          .map((k) => k.trim())
          .filter(Boolean),
        first_comment: firstComment,
        playlist_id: playlistId
      })
      flash('Saved')
      onChanged()
    })

  const rerender = (): Promise<void> =>
    run('render', async () => {
      const range = start !== clip.start_s || end !== clip.end_s ? { start, end } : undefined
      await api.rerenderClip(clip.id, range)
      flash('Re-render queued — watch the activity feed on Home')
    })

  // What each custom part starts from when it is switched on: the profile's.
  const profileWatermark = (): WatermarkConfig => {
    if (!chosen) return wm
    const { credit: _c, captions: _k, ...rest } = chosen.config
    return rest
  }

  const applyBranding = (): Promise<void> =>
    run('branding', async () => {
      if (bProfile === 'processed') return
      const forced = bProfile === null // no profile: every part is this clip's own
      const flags = {
        profile_id: bProfile,
        custom_watermark: forced || cw,
        custom_credit: forced || cc,
        custom_captions: forced ? captionsTouched : ck
      }
      const own: WatermarkConfig = {
        ...wm,
        ...(flags.custom_credit ? { credit: credit } : {}),
        ...(flags.custom_captions ? { captions } : {})
      }
      await api.rerenderClip(clip.id, undefined, { branding: flags, watermark: own })
      flash('Re-render queued with this branding — watch the activity feed on Home')
    })

  const exportOne = (): Promise<void> =>
    run('export', async () => {
      const res = await api.exportClip(clip.id, folder)
      flash(res.exported.length ? `Exported: ${res.exported[0]}` : 'Nothing exported')
      onChanged() // exporting stars the clip; show it on its card
    })

  return (
    <div className="card space-y-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(16rem,22rem)_minmax(0,1fr)] items-start">
        <div className="space-y-3 min-w-0">
      <VideoPlayer
        src={api.mediaUrl(clip.id)}
        poster={hasThumb ? thumbsApi.imageUrl(clip.id, thumbVersion) : undefined}
        label={`Preview of clip: ${clip.title || clip.hook || 'untitled'}. Captions are burned into the video.`}
        className="mx-auto"
      >
        {showBranding && followed && (
          <ClipBrandingOverlay
            watermark={effWatermark}
            credit={effCredit}
            captions={effCaptions}
            cta={effWatermark?.cta}
            landscape={landscape}
          />
        )}
      </VideoPlayer>
      <button
        type="button"
        className="btn-ghost !py-1 text-xs w-full"
        aria-pressed={showBranding}
        onClick={() => setShowBranding((v) => !v)}
        title="Draw the branding this clip will be re-rendered with over the video"
      >
        {showBranding ? 'Hide branding preview' : 'Preview branding on this clip'}
      </button>
      {showBranding && (
        <p className="text-[11px] text-muted">
          {followed
            ? 'Drawn over the current render, which still shows any branding already burned in until you apply.'
            : 'This clip keeps the branding it was made with, and it is already in the video above. Pick a branding profile below to preview a change over it.'}
        </p>
      )}

      <button
        onClick={onOpenEditor}
        className="btn-accent w-full !py-3 text-base font-semibold"
        title="Open the editor: trim, cut, mute words, censor, color, captions, AI edit"
      >
        <span className="inline-flex items-center gap-2 justify-center">
          <Scissors size={16} /> Edit this clip
        </span>
      </button>

      <div className="flex gap-2 flex-wrap text-xs items-center">
        {CHANNELS.map((ch) => (
          <span key={ch} className="bg-raised px-2 py-1 rounded-md text-muted">
            {ch} <span className="text-ink font-semibold">{clip.scores[ch] ?? '–'}</span>
          </span>
        ))}
      </div>

        </div>
        <div className="space-y-4 min-w-0">
      <div className="space-y-3">
        <div>
          <label className="label">Title</label>
          <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} />
          {(clip.alt_titles ?? []).filter((a) => a !== title).length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-1.5 text-[11px]">
              <span className="text-muted">Try instead:</span>
              {(clip.alt_titles ?? [])
                .filter((a) => a !== title)
                .map((alt) => (
                  <button
                    key={alt}
                    className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink text-left"
                    onClick={() => setTitle(alt)}
                  >
                    {alt}
                  </button>
                ))}
            </div>
          )}
        </div>
        <div>
          <label className="label">Description</label>
          <textarea
            className="input mt-1 h-20 resize-none"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <VideoHashtagsField value={hashtags} onChange={setHashtags} id={`clip-tags-${clip.id}`} />
        <div>
          <label className="label">Search keywords (comma-separated)</label>
          <input
            className="input mt-1"
            value={keywords}
            placeholder="phrases people would search for"
            onChange={(e) => setKeywords(e.target.value)}
          />
          <p className="text-[11px] text-muted mt-1">Sent as YouTube tags; never shown to viewers.</p>
        </div>
        <FirstCommentField
          publishId={clip.id}
          value={firstComment}
          suggestion={suggestedComment}
          onChange={setFirstComment}
          onSuggestion={setSuggestedComment}
        />
        {/* Only with YouTube connected for playlists: it is applied when the
            clip is published there, and beats the creator's playlist rule. */}
        {playlistsAvailable && (
          <div>
            <label className="label" htmlFor={`clip-playlist-${clip.id}`}>
              YouTube playlist
            </label>
            <PlaylistSelect
              id={`clip-playlist-${clip.id}`}
              value={playlistId}
              playlists={playlists}
              onChange={setPlaylistId}
            />
            <p className="text-[11px] text-muted mt-1">
              Joined when this clip is published to YouTube, and linked in its description. Save metadata to keep it.
            </p>
          </div>
        )}
        <ThumbnailCard
          publishId={clip.id}
          onState={setHasThumb}
          onSaved={() => setThumbVersion((v) => v + 1)}
        />
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="label">Start (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={start}
              step={0.5}
              onChange={(e) => setStart(Number(e.target.value))}
            />
          </div>
          <div className="flex-1">
            <label className="label">End (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={end}
              step={0.5}
              onChange={(e) => setEnd(Number(e.target.value))}
            />
          </div>
        </div>
      </div>

        </div>
      </div>

      <div className="flex gap-2 flex-wrap items-center">
        <button className="btn-accent" onClick={saveMetadata} disabled={busy !== null}>
          {busy === 'save' ? 'Saving…' : 'Save metadata'}
        </button>
        <button className="btn-ghost" onClick={rerender} disabled={busy !== null || rendering}>
          {busy === 'render' ? 'Queueing…' : rendering ? 'Rendering…' : 'Re-render'}
        </button>
        <input
          className="input !w-44"
          value={folder}
          onChange={(e) => {
            setFolder(e.target.value)
            setExportFolder(e.target.value)
          }}
          placeholder="export folder"
          title={folder}
        />
        <button
          className="btn-ghost"
          onClick={async () => {
            const chosen = await pickExportFolder()
            if (chosen) setFolder(chosen)
          }}
          title="Choose where exported clips are saved"
          aria-label="Choose export folder"
        >
          <Folder />
        </button>
        <button className="btn-ghost" onClick={exportOne} disabled={busy !== null}>
          {busy === 'export' ? 'Exporting…' : 'Export'}
        </button>
        {/* Beside Export rather than only on the card in the grid: Export
            writes this one clip to a folder, and Publish is the same scope
            for the other destination. Only shown once a provider is set up,
            so the row does not carry a button that can only explain itself.
            Posting is not undoable, so it opens the confirm dialog rather
            than acting on the click, exactly like Publish all. */}
        {onPublish && (
          <button className="btn-ghost" onClick={onPublish} disabled={busy !== null}>
            Publish ↗
          </button>
        )}
      </div>
      {notice && <p className="text-sm text-accent">{notice}</p>}

      <BrandingSection
        kind="clip"
        profiles={profiles}
        profile={bProfile}
        onProfile={setBProfile}
        allowProcessed
        disabled={busy !== null}
        customWatermark={cw}
        onCustomWatermark={(on) => {
          if (on) setWm(profileWatermark())
          setCw(on)
        }}
        watermark={wm}
        onWatermark={(patch) => setWm((w) => ({ ...w, ...patch }))}
        customCredit={cc}
        onCustomCredit={(on) => {
          if (on) setCredit({ ...(chosen?.config.credit ?? { enabled: false }) })
          setCc(on)
        }}
        credit={credit}
        onCredit={(patch) => setCredit((c) => ({ ...c, ...patch }))}
        customCaptions={ck}
        onCustomCaptions={(on) => {
          if (on) setCaptions({ ...DEFAULT_CAPTION_STYLE, ...(chosen?.config.captions ?? captions) })
          setCk(on)
        }}
        captions={captions}
        onCaptions={(key, value) => {
          setCaptionsTouched(true)
          setCaptions((c) => ({ ...c, [key]: value }))
        }}
        positions={CREDIT_POSITIONS}
        formats={[landscape ? '16:9' : '9:16']}
        sizes={CLIP_SIZES}
        samples={CREDIT_SAMPLES}
      />
      {bProfile !== 'processed' && (
        <div className="flex items-center gap-3 -mt-2">
          <button className="btn-accent" onClick={applyBranding} disabled={busy !== null || rendering}>
            {busy === 'branding' ? 'Queueing…' : rendering ? 'Rendering…' : 'Apply branding & re-render'}
          </button>
          <span className="text-xs text-muted">
            Burned into the clip, so it takes a re-render. The profile is re-read each time.
          </span>
        </div>
      )}
      <FormatVariants clipId={clip.id} />
    </div>
  )
}
