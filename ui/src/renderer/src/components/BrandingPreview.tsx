// One preview for every branding layer, kept in the same place on every tab
// so switching between Watermark, Credit and Captions changes only the
// controls beneath it. Every active layer is drawn together — the credit
// plate, the watermark, the call to action and the captions — so an edit to
// one shows how it sits with the rest.
import { useEffect, useMemo, useState } from 'react'
import { t } from '../lib/i18n'
import type { CreditStyle } from '../lib/compilations'
import { api } from '../lib/api'
import type { CaptionStyle, CtaConfig, WatermarkConfig } from '../lib/types'
import { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import { CreditPreview, creditText, useImageAspect, type CreditSample } from './CreditControls'

/** The stage is always this tall, so the controls below never move when the
 *  frame inside changes shape. */
const STAGE_H = 400
const MAX_LANDSCAPE_W = 560

export default function BrandingPreview({
  watermark,
  credit,
  captions,
  cta,
  formats,
  sizes,
  samples,
  note
}: {
  /** The watermark/logo layer. */
  watermark: WatermarkConfig | null
  /** Null or disabled: no credit is drawn. */
  credit: CreditStyle | null
  /** Null: no captions layer (compilations do not burn captions). */
  captions: CaptionStyle | null
  /** Clips only: compilations never burn a call to action. */
  cta?: CtaConfig | null
  formats: string[]
  sizes: Record<string, { width: number; height: number }>
  samples: CreditSample[]
  note?: string
}): JSX.Element {
  const [format, setFormat] = useState(formats[0])
  const [sample, setSample] = useState(0)
  const [expanded, setExpanded] = useState(false)
  useEffect(() => {
    if (!formats.includes(format)) setFormat(formats[0])
  }, [formats, format])
  useEffect(() => {
    if (!expanded) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setExpanded(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [expanded])

  const c: CreditStyle = credit ?? { enabled: false }
  const on = c.enabled ?? true
  const template = c.template ?? 'Clip: {channel}'
  const texts = useMemo(() => {
    const all = samples.map((s) => creditText(template, s)).filter(Boolean)
    const unique = [...new Set(all)].sort((a, b) => b.length - a.length)
    return unique.length ? unique : [creditText(template, { channel: 'Creator Name', title: 'Video title', url: 'youtu.be/…' })]
  }, [samples, template])
  const text = texts[Math.min(sample, texts.length - 1)]

  const imageUrl = c.bg_image ? api.brandingAssetUrl(c.bg_image) : null
  const [aspect] = useImageAspect(imageUrl)
  const size: [number, number] = sizes[format] ? [sizes[format].width, sizes[format].height] : [1920, 1080]
  const [cw, ch] = size
  const width = cw >= ch ? Math.min(MAX_LANDSCAPE_W, (STAGE_H * cw) / ch) : (STAGE_H * cw) / ch
  const bigW = Math.min(window.innerWidth * 0.95, ((window.innerHeight * 0.86 - 60) * cw) / ch)
  const captionStyle = captions ? { ...DEFAULT_CAPTION_STYLE, ...captions } : null

  const frame = (w: number): JSX.Element => (
    <CreditPreview
      c={c}
      size={size}
      text={text}
      aspect={aspect}
      width={w}
      banner={watermark}
      showCredit={on}
      captions={captionStyle}
      cta={cta}
    />
  )

  const pickers = (
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

  return (
    <section className="rounded-xl bg-base/60 border border-raised/60 p-3 space-y-2" aria-label={t('Preview')}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {pickers}
        <button
          type="button"
          className="btn-ghost !py-1 !px-3 text-xs"
          onClick={() => setExpanded(true)}
          title={t('Expand preview')}
        >
          ⤢ {t('Full screen')}
        </button>
      </div>
      <div className="flex items-center justify-center" style={{ height: STAGE_H }}>
        {frame(width)}
      </div>
      {note && <p className="text-[11px] text-muted text-center">{note}</p>}

      {expanded && (
        <div
          className="fixed inset-0 z-50 bg-black/85 flex items-center justify-center p-4"
          onClick={() => setExpanded(false)}
          role="dialog"
          aria-modal="true"
          aria-label={t('Preview')}
        >
          <div className="space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-4">
              {pickers}
              <button type="button" className="btn-ghost !py-1" onClick={() => setExpanded(false)} autoFocus>
                {t('Close')} (Esc)
              </button>
            </div>
            {frame(bigW)}
            <p className="text-xs text-muted">
              {cw}×{ch} · {t('shown at')} {Math.round((bigW / cw) * 100)}%
            </p>
          </div>
        </div>
      )}
    </section>
  )
}
