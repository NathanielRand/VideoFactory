// The compilation credit: what it says, where and when it shows, and how it
// looks, with a live preview. The burned-in version is drawn by
// compilation/credits.py; `layoutCredit` below mirrors its sizing and
// fitting rules, so keep the two in step.
import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { CreditStyle } from '../lib/compilations'
import { CAPTION_FONTS } from './CaptionStyleControls'

// Same constants as compilation/credits.py.
const MARGIN = 0.045
const PLATE_TEXT_WIDTH = 0.84
const PREVIEW_W = 320
/** Below this share of the chosen size, a fitted name gets a warning. */
const SHRINK_WARN = 0.75

/** The look, as opposed to what it says and when: what "Reset look" resets. */
const LOOK_DEFAULTS: Partial<CreditStyle> = {
  font: 'Arial',
  font_size: 44,
  bold: true,
  italic: false,
  color: '#FFFFFF',
  backing: 'box',
  bg_image: null,
  bg_scale: 2.4,
  bg_text_x: 0.5,
  bg_text_y: 0.5
}

export interface CreditSample {
  channel: string
  title: string
  url: string
}

/** The credit's text for one source, as credits.credit_text builds it. */
function creditText(template: string, s: CreditSample): string {
  const name = s.channel.trim() || s.title.trim()
  if (!name) return ''
  return template.replaceAll('{channel}', name).replaceAll('{title}', s.title).replaceAll('{url}', s.url)
}

let measureCtx: CanvasRenderingContext2D | null = null
function measure(text: string, font: string): number {
  measureCtx ??= document.createElement('canvas').getContext('2d')
  if (!measureCtx) return text.length * 0.6
  measureCtx.font = font
  return measureCtx.measureText(text).width
}

function cssFont(c: CreditStyle): { family: string; weight: number; style: string } {
  return {
    family: `'${c.font ?? 'Arial'}', sans-serif`,
    weight: (c.bold ?? true) ? 700 : 400,
    style: (c.italic ?? false) ? 'italic' : 'normal'
  }
}

interface Layout {
  basePx: number
  margin: number
  /** The image in canvas pixels, and the text size fitted onto it. */
  plate: { w: number; h: number; px: number } | null
}

/** Canvas-pixel layout: credits.font_px / plate_for / fit_px. */
function layoutCredit(c: CreditStyle, [cw, ch]: [number, number], aspect: number | null, text: string): Layout {
  const basePx = Math.max(14, Math.round(((c.font_size ?? 44) * Math.min(cw, ch)) / 1080))
  const margin = Math.round(MARGIN * Math.min(cw, ch))
  if (!c.bg_image || !aspect) return { basePx, margin, plate: null }
  let h = Math.round(basePx * (c.bg_scale ?? 2.4))
  let w = Math.round(h * aspect)
  if (w > cw - 2 * margin) {
    w = cw - 2 * margin
    h = Math.round(w / aspect)
  }
  w = Math.max(2, Math.floor(w / 2) * 2)
  h = Math.max(2, Math.floor(h / 2) * 2)
  let px = Math.min(basePx, Math.round(h * 0.8))
  const f = cssFont(c)
  const width = measure(text, `${f.style} ${f.weight} ${px}px ${f.family}`)
  // Plate.room(): narrower when the text is off-centre.
  const tx = c.bg_text_x ?? 0.5
  const room = w * PLATE_TEXT_WIDTH * 2 * Math.min(tx, 1 - tx)
  if (width > room) px = Math.floor((px * room) / width)
  return { basePx, margin, plate: { w, h, px: Math.max(10, px) } }
}

/** [width / height once loaded, whether it failed to load]. */
function useImageAspect(url: string | null): [number | null, boolean] {
  const [aspect, setAspect] = useState<number | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    setAspect(null)
    setFailed(false)
    if (!url) return
    const img = new Image()
    img.onload = () => setAspect(img.naturalWidth / Math.max(1, img.naturalHeight))
    img.onerror = () => setFailed(true)
    img.src = url
  }, [url])
  return [aspect, failed]
}

function CreditPreview({
  c,
  size,
  text,
  aspect,
  width
}: {
  c: CreditStyle
  size: [number, number]
  text: string
  aspect: number | null
  width: number
}): JSX.Element {
  const [cw, ch] = size
  const k = width / cw
  const image = c.bg_image ? api.brandingAssetUrl(c.bg_image) : null
  const { basePx, margin, plate } = layoutCredit(c, size, aspect, text)
  const [vert, horiz] = (c.position ?? 'bottom_left').split('_')
  const f = cssFont(c)

  const place: React.CSSProperties = {
    position: 'absolute',
    [vert === 'top' ? 'top' : 'bottom']: margin * k,
    ...(horiz === 'left'
      ? { left: margin * k }
      : horiz === 'right'
        ? { right: margin * k }
        : { left: '50%', transform: 'translateX(-50%)' })
  }
  const textStyle: React.CSSProperties = {
    fontFamily: f.family,
    fontWeight: f.weight,
    fontStyle: f.style,
    color: c.color ?? '#FFFFFF',
    whiteSpace: 'nowrap',
    lineHeight: 1.15
  }
  const backing = c.backing ?? 'box'

  return (
    <div
      className="relative overflow-hidden rounded-lg bg-gradient-to-br from-slate-600 via-slate-800 to-slate-900 shrink-0"
      style={{ width, height: (width * ch) / cw }}
      aria-label={t('Credit preview')}
    >
      {plate ? (
        <div
          style={{
            ...place,
            width: plate.w * k,
            height: plate.h * k,
            backgroundImage: `url("${image}")`,
            backgroundSize: '100% 100%'
          }}
        >
          <span
            style={{
              ...textStyle,
              position: 'absolute',
              left: `${(c.bg_text_x ?? 0.5) * 100}%`,
              top: `${(c.bg_text_y ?? 0.5) * 100}%`,
              transform: 'translate(-50%, -50%)',
              fontSize: plate.px * k,
              textShadow: '0 1px 2px rgba(0,0,0,.5)'
            }}
          >
            {text}
          </span>
        </div>
      ) : (
        !image && (
          <span
            style={{
              ...place,
              ...textStyle,
              fontSize: basePx * k,
              ...(backing === 'box'
                ? { background: 'rgba(10,26,40,.65)', padding: `${basePx * 0.35 * k}px` }
                : backing === 'outline'
                  ? { WebkitTextStroke: `${Math.max(0.5, basePx * 0.08 * k)}px black`, paintOrder: 'stroke fill' }
                  : {})
            }}
          >
            {text}
          </span>
        )
      )}
    </div>
  )
}

/** Which format and which creator the preview shows: shared by the inline
 *  preview and the expanded one. */
function PreviewPickers({
  formats,
  format,
  setFormat,
  texts,
  sample,
  setSample
}: {
  formats: string[]
  format: string
  setFormat: (f: string) => void
  texts: string[]
  sample: number
  setSample: (i: number) => void
}): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      {formats.length > 1 && (
        <div className="flex gap-1" role="group" aria-label={t('Preview format')}>
          {formats.map((f) => (
            <button
              key={f}
              type="button"
              aria-pressed={f === format}
              onClick={() => setFormat(f)}
              className={`px-2 py-1 rounded ${f === format ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
            >
              {f}
            </button>
          ))}
        </div>
      )}
      {texts.length > 1 && (
        <select
          className="input !w-auto !py-1 text-xs max-w-56"
          value={sample}
          onChange={(e) => setSample(Number(e.target.value))}
          aria-label={t('Preview with')}
          title={t('Preview with')}
        >
          {texts.map((s, i) => (
            <option key={i} value={i}>
              {s}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

export default function CreditControls({
  credits: c,
  positions,
  formats,
  sizes,
  samples,
  onChange
}: {
  credits: CreditStyle
  positions: string[]
  /** The formats this compilation renders; [0] is the main one. */
  formats: string[]
  sizes: Record<string, { width: number; height: number }>
  /** The sources in this compilation, to preview their real credits. */
  samples: CreditSample[]
  onChange: (patch: Partial<CreditStyle>) => void
}): JSX.Element {
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [format, setFormat] = useState(formats[0])
  const [sample, setSample] = useState(0)
  const on = c.enabled ?? true
  const template = c.template ?? 'Clip: {channel}'

  useEffect(() => {
    if (!formats.includes(format)) setFormat(formats[0])
  }, [formats, format])

  // Distinct credits, longest first: the longest is the hardest to fit, so
  // it is the one to design against.
  const texts = useMemo(() => {
    const all = samples.map((s) => creditText(template, s)).filter(Boolean)
    const unique = [...new Set(all)].sort((a, b) => b.length - a.length)
    return unique.length ? unique : [creditText(template, { channel: 'Creator Name', title: 'Video title', url: 'youtu.be/…' })]
  }, [samples, template])
  const text = texts[Math.min(sample, texts.length - 1)]

  const imageUrl = c.bg_image ? api.brandingAssetUrl(c.bg_image) : null
  const [aspect, imageFailed] = useImageAspect(imageUrl)
  const sizeOf = (f: string): [number, number] => {
    const s = sizes[f]
    return s ? [s.width, s.height] : [1920, 1080]
  }
  const layout = layoutCredit(c, sizeOf(format), aspect, text)
  // The worst fit across every name and format: what the warning is about.
  const worstFit = useMemo(() => {
    if (!c.bg_image || !aspect) return null
    let worst: { share: number; text: string; px: number } | null = null
    for (const f of formats) {
      for (const s of texts) {
        const l = layoutCredit(c, sizeOf(f), aspect, s)
        if (!l.plate) continue
        const share = l.plate.px / Math.min(l.basePx, Math.round(l.plate.h * 0.8))
        if (!worst || share < worst.share) worst = { share, text: s, px: l.plate.px }
      }
    }
    return worst
  }, [c, aspect, formats, texts]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!expanded) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setExpanded(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expanded])

  const pickImage = async (): Promise<void> => {
    setError('')
    const path = await window.studio.pickImageFile()
    if (!path) return
    try {
      const { asset } = await api.uploadBrandingAsset(path)
      onChange({ bg_image: asset })
    } catch (e) {
      setError(`${t("Couldn't add that image")}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const insert = (field: string): void => onChange({ template: `${template}${template.endsWith(' ') || !template ? '' : ' '}${field}` })

  const [cw, ch] = sizeOf(format)
  const bigW = Math.min(window.innerWidth * 0.9, ((window.innerHeight * 0.8 - 80) * cw) / ch)
  const pickers = (
    <PreviewPickers
      formats={formats}
      format={format}
      setFormat={setFormat}
      texts={texts}
      sample={sample}
      setSample={setSample}
    />
  )

  return (
    <div className="col-span-2 lg:col-span-4 space-y-3 border-y border-raised/60 py-3">
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={on} onChange={(e) => onChange({ enabled: e.target.checked })} />
          <span className="text-sm font-medium">{t('Credit each creator')}</span>
        </label>
        <button
          type="button"
          className="text-xs text-muted hover:text-ink disabled:opacity-40"
          disabled={!on}
          onClick={() => onChange(LOOK_DEFAULTS)}
          title={t('Font, size, colour, backing and image back to the defaults. The text and timing stay.')}
        >
          {t('Reset look')}
        </button>
      </div>

      <fieldset disabled={!on} className="flex flex-col md:flex-row gap-4 items-start disabled:opacity-50">
        <div className="flex-1 min-w-0 grid grid-cols-2 lg:grid-cols-3 gap-3 w-full">
          <div className="space-y-1 col-span-2 lg:col-span-3">
            <label htmlFor="credit-template" className="label">
              {t('Credit text')}
            </label>
            <div className="flex gap-1">
              <input
                id="credit-template"
                className="input flex-1"
                value={template}
                onChange={(e) => onChange({ template: e.target.value })}
              />
              {['{channel}', '{title}', '{url}'].map((f) => (
                <button
                  key={f}
                  type="button"
                  className="btn-ghost !px-2 text-xs font-mono"
                  onClick={() => insert(f)}
                  title={t('Insert') + ` ${f}`}
                >
                  {f}
                </button>
              ))}
            </div>
          </div>

          <label className="space-y-1">
            <span className="label">{t('Font')}</span>
            <select
              className="input w-full"
              value={c.font ?? 'Arial'}
              style={{ fontFamily: `'${c.font ?? 'Arial'}', sans-serif` }}
              onChange={(e) => onChange({ font: e.target.value })}
            >
              {CAPTION_FONTS.map((f) => (
                <option key={f} value={f} style={{ fontFamily: `'${f}', sans-serif` }}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="label">{t('Size')}</span>
            <input
              type="number"
              min={16}
              max={120}
              className="input w-full"
              value={c.font_size ?? 44}
              onChange={(e) => onChange({ font_size: Number(e.target.value) })}
            />
          </label>
          <div className="space-y-1">
            <span className="label">{t('Style')}</span>
            <div className="flex items-center gap-2 h-[42px]">
              <input
                type="color"
                className="h-9 w-10 rounded bg-transparent cursor-pointer"
                value={c.color ?? '#FFFFFF'}
                onChange={(e) => onChange({ color: e.target.value.toUpperCase() })}
                aria-label={t('Text colour')}
                title={t('Text colour')}
              />
              <button
                type="button"
                className={`px-2.5 py-1.5 rounded-lg font-bold ${(c.bold ?? true) ? 'bg-accent/20 text-accent' : 'bg-raised text-muted'}`}
                aria-pressed={c.bold ?? true}
                title={t('Bold')}
                onClick={() => onChange({ bold: !(c.bold ?? true) })}
              >
                B
              </button>
              <button
                type="button"
                className={`px-2.5 py-1.5 rounded-lg italic ${(c.italic ?? false) ? 'bg-accent/20 text-accent' : 'bg-raised text-muted'}`}
                aria-pressed={c.italic ?? false}
                title={t('Italic')}
                onClick={() => onChange({ italic: !(c.italic ?? false) })}
              >
                I
              </button>
            </div>
          </div>

          <label className="space-y-1">
            <span className="label">{t('Credit position')}</span>
            <select
              className="input w-full"
              value={c.position ?? 'bottom_left'}
              onChange={(e) => onChange({ position: e.target.value })}
            >
              {positions.map((p) => (
                <option key={p} value={p}>
                  {p.replace('_', ' ')}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="label">{t('Credit seconds')}</span>
            <input
              type="number"
              min={1}
              max={15}
              step={0.5}
              className="input w-full"
              disabled={c.whole_clip ?? false}
              value={c.seconds ?? 4}
              onChange={(e) => onChange({ seconds: Number(e.target.value) })}
            />
          </label>
          <label className="flex items-center gap-2 self-end pb-2.5">
            <input
              type="checkbox"
              checked={c.whole_clip ?? false}
              onChange={(e) => onChange({ whole_clip: e.target.checked })}
            />
            <span className="text-sm">{t('Keep up for the whole clip')}</span>
          </label>

          <div className="space-y-1 col-span-2">
            <span className="label">{t('Background image')}</span>
            <div className="flex gap-1">
              {imageUrl && (
                <img
                  src={imageUrl}
                  alt=""
                  className="h-[42px] max-w-24 object-contain rounded bg-raised/60 p-1 shrink-0"
                />
              )}
              <button type="button" className="btn-ghost flex-1 truncate text-left" onClick={() => void pickImage()}>
                {c.bg_image ? t('Change image…') : t('Upload image…')}
              </button>
              {c.bg_image && (
                <button
                  type="button"
                  className="btn-ghost px-3"
                  onClick={() => onChange({ bg_image: null })}
                  aria-label={t('Remove background image')}
                  title={t('Remove background image')}
                >
                  ✕
                </button>
              )}
            </div>
          </div>
          {c.bg_image ? (
            <label className="space-y-1">
              <span className="label flex justify-between gap-2">
                <span>{t('Image size')}</span>
                <span className="normal-case tracking-normal text-ink tabular-nums">
                  {(c.bg_scale ?? 2.4).toFixed(1)}×
                </span>
              </span>
              <input
                type="range"
                min={1.2}
                max={6}
                step={0.1}
                className="w-full mt-2"
                value={c.bg_scale ?? 2.4}
                onChange={(e) => onChange({ bg_scale: Number(e.target.value) })}
                aria-describedby="credit-image-px"
              />
              <span id="credit-image-px" className="block text-[11px] text-muted tabular-nums">
                {layout.plate
                  ? `${layout.plate.w} × ${layout.plate.h} px ${t('on')} ${cw}×${ch} · ${t('text')} ${layout.plate.px} px`
                  : imageFailed
                    ? t("Couldn't load this image; it may have been removed. Upload it again.")
                    : t('Loading image…')}
              </span>
            </label>
          ) : (
            <label className="space-y-1">
              <span className="label">{t('Behind the text')}</span>
              <select
                className="input w-full"
                value={c.backing ?? 'box'}
                onChange={(e) => onChange({ backing: e.target.value as CreditStyle['backing'] })}
              >
                <option value="box">{t('Box')}</option>
                <option value="outline">{t('Outline')}</option>
                <option value="none">{t('Nothing')}</option>
              </select>
            </label>
          )}
          {c.bg_image && (
            <div className="col-span-2 lg:col-span-3 grid grid-cols-2 gap-3">
              {(
                [
                  ['bg_text_x', t('Text across the image'), '←', '→'],
                  ['bg_text_y', t('Text up / down the image'), '↑', '↓']
                ] as const
              ).map(([key, label, lo, hi]) => (
                <label key={key} className="space-y-1">
                  <span className="label flex justify-between gap-2">
                    <span>{label}</span>
                    <button
                      type="button"
                      className="normal-case tracking-normal text-muted hover:text-ink"
                      onClick={() => onChange({ [key]: 0.5 })}
                      title={t('Centre it')}
                    >
                      {Math.round((c[key] ?? 0.5) * 100)}%
                    </button>
                  </span>
                  <span className="flex items-center gap-2 text-muted text-xs">
                    {lo}
                    <input
                      type="range"
                      min={0.1}
                      max={0.9}
                      step={0.01}
                      className="flex-1"
                      value={c[key] ?? 0.5}
                      onChange={(e) => onChange({ [key]: Number(e.target.value) })}
                    />
                    {hi}
                  </span>
                </label>
              ))}
            </div>
          )}
          {worstFit && worstFit.share < SHRINK_WARN && (
            <p className="col-span-2 lg:col-span-3 text-xs text-amber-400">
              ⚠ {t('Long names are shrunk to fit the image')}: “{worstFit.text}” →{' '}
              {worstFit.px} px. {t('A larger or wider image gives them more room.')}
            </p>
          )}
          {error && <p className="col-span-2 lg:col-span-3 text-xs text-red-400">{error}</p>}
        </div>

        <div className="space-y-2 shrink-0" style={{ width: PREVIEW_W }}>
          <div className="relative">
            <CreditPreview c={c} size={sizeOf(format)} text={text} aspect={aspect} width={PREVIEW_W} />
            <button
              type="button"
              className="absolute top-1.5 right-1.5 rounded bg-black/50 hover:bg-black/70 text-white text-xs px-2 py-1"
              onClick={() => setExpanded(true)}
              aria-label={t('Expand preview')}
              title={t('Expand preview')}
            >
              ⤢
            </button>
          </div>
          {pickers}
        </div>
      </fieldset>

      {expanded && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
          onClick={() => setExpanded(false)}
          role="dialog"
          aria-modal="true"
          aria-label={t('Credit preview')}
        >
          <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-4">
              {pickers}
              <button type="button" className="btn-ghost !py-1" onClick={() => setExpanded(false)} autoFocus>
                {t('Close')} (Esc)
              </button>
            </div>
            <CreditPreview c={c} size={sizeOf(format)} text={text} aspect={aspect} width={bigW} />
            <p className="text-xs text-muted">
              {cw}×{ch} · {t('shown at')} {Math.round((bigW / cw) * 100)}%
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
