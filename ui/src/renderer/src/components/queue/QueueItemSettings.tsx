import { useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions } from '../../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import BrandingPicker from '../BrandingPicker'
import { brandingChoice, type BrandingChoice } from '../../lib/branding'
import { t } from '../../lib/i18n'
import { compilationsApi, type Compilation } from '../../lib/compilations'

/** Settings for ONE queued video, or for every video a watched channel posts.
 *
 *  Every queued job carries its own snapshot of these options, so changing
 *  them here cannot reach any other video in the queue — that isolation is
 *  the whole reason the settings live on the job row rather than in the
 *  app-wide preferences the Generate bar writes to.
 *
 *  Editable only while a video is still waiting. Once the worker has claimed
 *  it, changing the configuration halfway would render some of its clips one
 *  way and the rest another, so the running item shows its settings read-only.
 *
 *  One Save posts the whole panel. Caption style alone has eight fields, and
 *  a request per keystroke would be absurd. */
export default function QueueItemSettings({
  job,
  onSaved,
  save: saveTo,
  heading = 'Settings for this video only',
  autoSave = false
}: {
  job: { id: number; settings: JobOptions }
  onSaved: () => void
  /** Where the options go. A queued job by default; a watched channel passes
   *  its own, so both edit the same options through the same controls. */
  save?: (patch: Partial<JobOptions> & { clear?: string[] }) => Promise<unknown>
  heading?: string
  /** Save each change as it is made, with no Save button. For a watched
   *  channel, where a second Save button was one too many: captions were
   *  unticked, the other panel was saved, and clips came out with captions. */
  autoSave?: boolean
}): JSX.Element {
  const s = job.settings ?? {}
  const [captions, setCaptions] = useState(s.captions !== false)
  const [longClips, setLongClips] = useState(Boolean(s.long_clips))
  const [podcast, setPodcast] = useState(Boolean(s.podcast))
  const [longform, setLongform] = useState(Boolean(s.longform))
  const [longformMode, setLongformMode] = useState(s.longform?.mode ?? 'short_clips')
  const [branding, setBranding] = useState<BrandingChoice>(brandingChoice(s))
  const [style, setStyle] = useState<Required<CaptionStyle>>({
    ...DEFAULT_CAPTION_STYLE,
    ...(s.caption_style ?? {})
  })
  // What the video is FOR: clips (the default), or the Library only —
  // optionally straight into a compilation. For a watched channel this is
  // how a channel of ready-made clips feeds a compilation on its own.
  const [importOnly, setImportOnly] = useState(Boolean(s.import_only))
  const [compilationId, setCompilationId] = useState<number | ''>(s.add_to_compilation ?? '')
  const [compilations, setCompilations] = useState<Compilation[]>([])
  useEffect(() => {
    compilationsApi
      .list()
      .then((all) =>
        setCompilations(all.filter((c) => c.status !== 'queued' && c.status !== 'rendering'))
      )
      .catch(() => undefined)
  }, [])
  // The same three answers as the Add videos bar.
  const intent = !importOnly ? 'clips' : compilationId === '' ? 'library' : 'compilation'
  const pickIntent = (next: 'library' | 'clips' | 'compilation'): void => {
    setImportOnly(next !== 'clips')
    if (next !== 'compilation') setCompilationId('')
    else if (compilationId === '' && compilations[0]) setCompilationId(compilations[0].id)
  }
  const [styleOpen, setStyleOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void =>
    setStyle((prev) => ({ ...prev, [key]: value }))

  const save = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      // `clear` is how an option goes back OFF: an absent field means
      // "unchanged" on the server, so switching a toggle off has to say so.
      const clear: string[] = []
      const patch: Partial<JobOptions> & { clear?: string[] } = { captions }
      if (longClips) patch.long_clips = true
      else clear.push('long_clips')
      if (podcast) patch.podcast = true
      else clear.push('podcast')
      if (longform) patch.longform = { mode: longformMode }
      else clear.push('longform')
      // Branding: one of the two keys, or neither (the creator's default).
      if (typeof branding === 'number') patch.watermark_profile_id = branding
      else if (branding === 'none') patch.no_watermark = true
      else clear.push('watermark_profile_id', 'no_watermark')
      patch.caption_style = style
      if (importOnly) {
        patch.import_only = true
        if (compilationId !== '') patch.add_to_compilation = compilationId
        else clear.push('add_to_compilation')
      } else clear.push('import_only', 'add_to_compilation')
      patch.clear = clear
      await (saveTo ? saveTo(patch) : api.patchJob(job.id, patch))
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  // Saves a moment after the last change, so a burst of clicks is one save,
  // and only when something actually differs from what was last saved.
  const current = JSON.stringify([
    captions,
    longClips,
    podcast,
    longform,
    longformMode,
    branding,
    style,
    importOnly,
    compilationId
  ])
  const lastSaved = useRef(current)
  useEffect(() => {
    if (!autoSave || current === lastSaved.current) return
    const id = setTimeout(() => {
      lastSaved.current = current
      void save()
    }, 500)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSave, current])

  const toggle = (
    label: string,
    hint: string,
    checked: boolean,
    onChange: (v: boolean) => void,
    title: string
  ): JSX.Element => (
    <label className="flex items-center gap-2 cursor-pointer text-sm" title={title}>
      <input
        type="checkbox"
        className="size-4 accent-[#38BDF8]"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {t(label)} {hint && <span className="text-muted">{t(hint)}</span>}
    </label>
  )

  return (
    <div className="mt-3 pt-3 border-t border-raised/60 space-y-3">
      <p className="label">{t(heading)}</p>
      <div className="flex items-center gap-3 flex-wrap">
        <div
          className="flex rounded-lg bg-raised p-0.5"
          role="radiogroup"
          aria-label={t('What to do with it')}
        >
          {(
            [
              [
                'library',
                '▤ ' + t('Library only'),
                'Into your Library; decide what it is for from there'
              ],
              [
                'clips',
                '✂ ' + t('Make clips'),
                'Find the best moments with AI and cut them into clips'
              ],
              [
                'compilation',
                '▦ ' + t('Compilation'),
                'Into your Library and straight into a compilation, whole'
              ]
            ] as const
          ).map(([value, label, hint]) => (
            <button
              key={value}
              role="radio"
              aria-checked={intent === value}
              title={
                value === 'compilation' && compilations.length === 0
                  ? t('Make a compilation in the Editor first')
                  : t(hint)
              }
              disabled={value === 'compilation' && compilations.length === 0}
              onClick={() => pickIntent(value)}
              className={`px-2.5 py-1 rounded-md text-xs whitespace-nowrap disabled:opacity-40 ${
                intent === value
                  ? 'bg-accent/20 text-accent font-medium'
                  : 'text-muted hover:text-ink'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {intent === 'compilation' && (
          <select
            className="input !w-64"
            aria-label={t('Compilation')}
            value={compilationId}
            onChange={(e) => setCompilationId(Number(e.target.value))}
          >
            {compilations.map((c) => (
              <option key={c.id} value={c.id}>
                {t('Add to')}: {c.title}
              </option>
            ))}
          </select>
        )}
      </div>
      {!importOnly && (
        <>
          <div className="flex gap-x-5 gap-y-2 flex-wrap">
            {toggle('Captions', '', captions, setCaptions, 'Burn captions into this video’s clips')}
            {toggle(
              '60s+',
              '(TikTok monetization)',
              longClips,
              setLongClips,
              'TikTok monetization requires videos over 1 minute. On: clips run 61-180s.'
            )}
            {toggle(
              'Podcast',
              '(multi-cam)',
              podcast,
              setPodcast,
              'For multi-camera podcasts: each shot gets one steady crop on whoever is talking.'
            )}
            {toggle(
              'Longform',
              '(16:9)',
              longform,
              setLongform,
              'Horizontal 1920x1080 outputs using the same AI.'
            )}
            <BrandingPicker value={branding} onChange={setBranding} />
          </div>

          {longform && (
            <div className="flex items-center gap-3 flex-wrap">
              <p className="label shrink-0">{t('Longform output')}</p>
              <select
                className="input !w-64"
                value={longformMode}
                onChange={(e) => setLongformMode(e.target.value)}
                aria-label="Longform output type"
              >
                <option value="short_clips">Short Clips (up to 60s, horizontal)</option>
                <option value="clips_140">Clips (up to 140s — X/Twitter)</option>
                <option value="highlights">Highlights (best-of, 8-20 min by quality)</option>
                <option value="edited_stream">Edited Stream (downtime removed)</option>
              </select>
            </div>
          )}

          <button
            className="btn-ghost"
            onClick={() => setStyleOpen(!styleOpen)}
            aria-expanded={styleOpen}
            disabled={!captions}
          >
            {t('Caption style')} {styleOpen ? '▾' : '▸'}
          </button>
          {styleOpen && captions && (
            <CaptionStyleControls idPrefix={`q${job.id}`} style={style} onChange={setStyleField} />
          )}
        </>
      )}

      <div className="flex items-center gap-3">
        {autoSave ? (
          <span className="text-xs text-muted">
            {busy ? t('Saving…') : t('Changes save as you make them.')}
          </span>
        ) : (
          <button className="btn-accent" onClick={save} disabled={busy}>
            {busy ? t('Saving…') : t('Save settings')}
          </button>
        )}
        {saved && <span className="text-sm text-accent">{t('Saved')}</span>}
        {error && <span className="text-sm text-error">{error}</span>}
      </div>
    </div>
  )
}
