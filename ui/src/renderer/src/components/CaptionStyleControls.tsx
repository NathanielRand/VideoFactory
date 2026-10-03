import { useEffect, useState } from 'react'
import type { CaptionStyle } from '../lib/types'

export const DEFAULT_CAPTION_STYLE: Required<CaptionStyle> = {
  font: 'Arial',
  font_size: 84,
  color: '#FFFFFF',
  position: 'bottom',
  words_per_caption: 3,
  uppercase: true,
  highlight: false,
  highlight_color: '#FFE600'
}

/** Fonts on every stock Windows install — matches video/captions.py FONTS. */
export const CAPTION_FONTS = [
  'Arial',
  'Arial Black',
  'Impact',
  'Verdana',
  'Tahoma',
  'Trebuchet MS',
  'Segoe UI',
  'Georgia',
  'Comic Sans MS',
  'Courier New'
]

/** ASS's Fontsize is the height of the whole line (ascent + descent), not the
 *  em that CSS's font-size means. libass sizes the glyphs so that line is
 *  Fontsize tall, so the glyphs it draws are Fontsize × unitsPerEm /
 *  (winAscent + winDescent). Measured from the bold Windows faces the render
 *  uses (video/captions.py FONTS); a preview that used CSS font-size directly
 *  drew every font 10–40% larger than the video. */
const ASS_EM: Record<string, number> = {
  Arial: 0.8951,
  'Arial Black': 0.7091,
  Impact: 0.8199,
  Verdana: 0.8228,
  Tahoma: 0.8285,
  'Trebuchet MS': 0.8612,
  'Segoe UI': 0.7518,
  Georgia: 0.8801,
  'Comic Sans MS': 0.7176,
  'Courier New': 0.8828
}

/** CSS font-size, in the same units as `assSize`, that draws glyphs the way
 *  libass draws an ASS style of Fontsize `assSize`. */
export const assFontSize = (font: string | undefined, assSize: number): number =>
  assSize * (ASS_EM[font ?? 'Arial'] ?? 0.85)

/** The captions drawn over a preview frame of `size` canvas pixels, at
 *  `scale` screen pixels per canvas pixel. Sizes follow video/captions.py:
 *  font_size is against a 1920-tall canvas. */
export function CaptionLayer({
  style,
  size,
  scale
}: {
  style: Required<CaptionStyle>
  size: [number, number]
  scale: number
}): JSX.Element {
  const words = ['your', 'captions', 'look', 'like', 'this', 'onscreen'].slice(
    0,
    Math.max(1, Math.min(6, style.words_per_caption))
  )
  const [hot, setHot] = useState(0)
  useEffect(() => {
    if (!style.highlight) return
    const id = setInterval(() => setHot((h) => (h + 1) % words.length), 550)
    return () => clearInterval(id)
  }, [style.highlight, words.length])
  const ch = size[1]
  // The render's own numbers (video/captions.py _header): style values are for
  // a 1920-tall canvas and scale with height; margins 440 (bottom) / 140 (top)
  // from the edge, 60 px at the sides, an outline 7 px thick, a 2 px shadow.
  const s = ch / 1920
  const assSize = Math.max(24, Math.round(Math.max(40, Math.min(140, style.font_size)) * s))
  const place: React.CSSProperties =
    style.position === 'top'
      ? { top: 140 * s * scale }
      : style.position === 'middle'
        ? { top: '50%', transform: 'translateY(-50%)' }
        : { bottom: 440 * s * scale }
  return (
    <p
      className="absolute inset-x-0 text-center pointer-events-none"
      style={{
        ...place,
        paddingInline: 60 * scale,
        fontFamily: `'${style.font}', sans-serif`,
        color: style.color,
        fontSize: assFontSize(style.font, assSize) * scale,
        lineHeight: `${assSize * scale}px`,
        fontWeight: 700,
        WebkitTextStroke: `${Math.max(0.5, 14 * scale)}px black`, // centred on the edge: 7 px shows outside
        paintOrder: 'stroke fill',
        textShadow: `${2 * scale}px ${2 * scale}px 0 rgba(0,0,0,0.5)`
      }}
    >
      {words.map((w, i) => (
        <span key={i} style={{ color: style.highlight && i === hot ? style.highlight_color : style.color }}>
          {(style.uppercase ? w.toUpperCase() : w) + (i < words.length - 1 ? ' ' : '')}
        </span>
      ))}
    </p>
  )
}

/** Live example of how the burned-in captions will look (9:16 mock). */
function CaptionExample({ style }: { style: Required<CaptionStyle> }): JSX.Element {
  // Show one caption group of exactly words_per_caption words — the same
  // grouping the burn-in uses, so changing the setting changes the example.
  const sample = ['your', 'captions', 'look', 'like', 'this', 'onscreen']
  const words = sample.slice(0, Math.max(1, Math.min(6, style.words_per_caption)))
  const text = style.uppercase ? words.join(' ').toUpperCase() : words.join(' ')
  // Cycle the highlight through the words so the example shows the effect
  // moving, which is the whole point of it.
  const [hot, setHot] = useState(0)
  useEffect(() => {
    if (!style.highlight) return
    const id = setInterval(() => setHot((h) => (h + 1) % words.length), 550)
    return () => clearInterval(id)
  }, [style.highlight, words.length])
  // A 9:16 frame 176 px tall, so 176/1920 screen px per canvas px; the same
  // maths as CaptionLayer, which is what the render matches.
  const k = 176 / 1920
  return (
    <div
      className="relative rounded-lg bg-gradient-to-br from-slate-700 via-slate-800 to-slate-900 aspect-[9/16] max-h-44 mx-auto w-auto overflow-hidden"
      aria-label="Caption style example"
    >
      <p
        className="absolute inset-x-0 text-center"
        style={{
          ...(style.position === 'top'
            ? { top: 140 * k }
            : style.position === 'middle'
              ? { top: '50%', transform: 'translateY(-50%)' }
              : { bottom: 440 * k }),
          paddingInline: 60 * k,
          fontFamily: `'${style.font}', sans-serif`,
          color: style.color,
          fontSize: assFontSize(style.font, style.font_size) * k,
          lineHeight: `${style.font_size * k}px`,
          fontWeight: 700,
          WebkitTextStroke: `${Math.max(0.5, 14 * k)}px black`,
          paintOrder: 'stroke fill'
        }}
      >
        {style.highlight
          ? words.map((w, i) => (
              <span key={i} style={{ color: i === hot ? style.highlight_color : style.color }}>
                {(style.uppercase ? w.toUpperCase() : w) + (i < words.length - 1 ? ' ' : '')}
              </span>
            ))
          : text}
      </p>
    </div>
  )
}

/** The caption style controls (colour, size, position, words, casing),
 *  shared between the Generate bar (style for all new clips) and the
 *  per-clip caption editor. */
export default function CaptionStyleControls({
  idPrefix,
  style,
  onChange,
  hideWordsPerCaption = false,
  hideExample = false
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** Translated subtitles inherit their grouping from the lines they
   *  replace, so regrouping does nothing there — hide it rather than offer
   *  a control that silently has no effect. */
  hideWordsPerCaption?: boolean
  /** When a larger preview elsewhere already shows the captions. */
  hideExample?: boolean
}): JSX.Element {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label htmlFor={`${idPrefix}-font`} className="label">
            Font
          </label>
          <select
            id={`${idPrefix}-font`}
            className="input mt-1"
            value={style.font}
            style={{ fontFamily: `'${style.font}', sans-serif` }}
            onChange={(e) => onChange('font', e.target.value)}
          >
            {CAPTION_FONTS.map((f) => (
              <option key={f} value={f} style={{ fontFamily: `'${f}', sans-serif` }}>
                {f}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-color`} className="label">
            Text colour
          </label>
          <input
            id={`${idPrefix}-color`}
            type="color"
            className="mt-1 h-9 w-full rounded-lg bg-raised cursor-pointer"
            value={style.color}
            onChange={(e) => onChange('color', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-size`} className="label">
            Size ({style.font_size})
          </label>
          <input
            id={`${idPrefix}-size`}
            type="range"
            min={40}
            max={140}
            className="mt-3 w-full accent-[#38BDF8]"
            value={style.font_size}
            onChange={(e) => onChange('font_size', Number(e.target.value))}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-pos`} className="label">
            Position
          </label>
          <select
            id={`${idPrefix}-pos`}
            className="input mt-1"
            value={style.position}
            onChange={(e) => onChange('position', e.target.value as CaptionStyle['position'])}
          >
            <option value="bottom">Bottom</option>
            <option value="middle">Middle</option>
            <option value="top">Top</option>
          </select>
        </div>
        {!hideWordsPerCaption && (
          <div>
            <label htmlFor={`${idPrefix}-words`} className="label">
              Words per caption
            </label>
            <select
              id={`${idPrefix}-words`}
              className="input mt-1"
              value={style.words_per_caption}
              onChange={(e) => onChange('words_per_caption', Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[#38BDF8]"
          checked={style.uppercase}
          onChange={(e) => onChange('uppercase', e.target.checked)}
        />
        UPPERCASE captions
      </label>

      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={style.highlight}
            onChange={(e) => onChange('highlight', e.target.checked)}
          />
          Highlight each word as it&apos;s said
        </label>
        {style.highlight && (
          <input
            type="color"
            aria-label="Highlight colour"
            className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
            value={style.highlight_color}
            onChange={(e) => onChange('highlight_color', e.target.value.toUpperCase())}
          />
        )}
      </div>

      {!hideExample && (
        <div>
          <p className="label mb-1">Example</p>
          <CaptionExample style={style} />
        </div>
      )}
    </>
  )
}
