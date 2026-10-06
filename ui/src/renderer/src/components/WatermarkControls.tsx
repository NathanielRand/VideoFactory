import { api } from '../lib/api'
import AsyncButton from './AsyncButton'
import { CAPTION_FONTS, assFontSize } from './CaptionStyleControls'
import { Folder, Rotate } from './icons'
import type { CtaConfig, WatermarkConfig } from '../lib/types'

const POSITIONS: {
  id: NonNullable<WatermarkConfig['position']>
  label: JSX.Element | string
  title: string
}[] = [
  { id: 'top_left', label: '↖', title: 'top left' },
  { id: 'top_right', label: '↗', title: 'top right' },
  { id: 'center', label: '⊙', title: 'center' },
  { id: 'bottom_left', label: '↙', title: 'bottom left' },
  { id: 'bottom_right', label: '↘', title: 'bottom right' },
  {
    id: 'moving',
    label: <Rotate />,
    title:
      'Moving — drifts in a small circle at one side edge, then jumps to the other. ' +
      'Hard to crop out, and it never crosses the middle of the shot.'
  }
]

const FRAMES: { id: NonNullable<WatermarkConfig['frame']>; label: string; title: string }[] = [
  { id: 'free', label: 'Free form', title: 'The logo as drawn, no crop' },
  { id: 'square', label: 'Square', title: 'Crop to the centred square of the logo' },
  { id: 'circle', label: 'Circle', title: 'Crop to a circle, like a profile picture' }
]

/** Starting points for a CTA. Picking one fills the text and colours;
 *  everything stays editable after. */
const CTA_PRESETS: { id: CtaConfig['kind']; label: string; text: string; bg: string }[] = [
  { id: 'discord', label: 'Discord', text: 'Upload your clips to our Discord!', bg: '#5865F2' },
  { id: 'subscribe', label: 'Subscribe', text: 'Subscribe for more!', bg: '#E62117' },
  { id: 'vote', label: 'Vote', text: 'Vote in the comments: A or B?', bg: '#F59E0B' },
  { id: 'follow', label: 'Follow', text: 'Follow for part 2', bg: '#111111' },
  { id: 'custom', label: 'Custom', text: '', bg: '#111111' }
]

export const DEFAULT_CTA: CtaConfig = {
  enabled: false,
  kind: 'discord',
  text: CTA_PRESETS[0].text,
  anchor: 'start',
  at: 3,
  duration: 4,
  repeat: 0,
  position: 'top',
  color: '#FFFFFF',
  bg: CTA_PRESETS[0].bg
}

/** How a preview crops the logo to its frame, matching _frame_chain in
 *  video_editor/watermark.py: square and circle take the centred square.
 *  Every preview of the logo uses this, so none can show corners the render
 *  crops away. */
export function logoFrameStyle(frame: WatermarkConfig['frame']): React.CSSProperties {
  if (frame !== 'square' && frame !== 'circle') return {}
  return {
    aspectRatio: '1 / 1',
    objectFit: 'cover',
    borderRadius: frame === 'circle' ? '50%' : undefined
  }
}

// Matches _CTA_Y in video_editor/watermark.py.
const CTA_TOP: Record<CtaConfig['position'], string> = { top: '17%', middle: '50%', bottom: '62%' }

export const DEFAULT_WATERMARK: WatermarkConfig = {
  type: 'text',
  text: '@YourChannel',
  font: 'Arial Black',
  font_size: 42,
  color: '#FFFFFF',
  opacity: 0.85,
  position: 'bottom_right',
  padding: 0.04,
  scale: 0.18,
  rotation: 0,
  shadow: true
}

/** Live 9:16 (or 16:9) preview showing where/how the watermark will burn in. */
function Preview({ config, landscape }: { config: WatermarkConfig; landscape?: boolean }): JSX.Element {
  const pos = config.position ?? 'bottom_right'
  const padPct = (config.padding ?? 0.04) * 100
  const moving = pos === 'moving'
  const place: React.CSSProperties = { position: 'absolute', opacity: config.opacity ?? 0.85 }
  if (moving) {
    // TELEPORTS between the LEFT and RIGHT edge-centres (not top/bottom —
    // the platform UI covers those), orbiting a small circle at each. The
    // side switch is steps(1) so it jumps rather than sweeping through the
    // middle; the orbit runs on the inner element, since both animate
    // transform and would otherwise fight.
    place.top = '50%'
    place.right = `${padPct}%`
    place.transform = 'translateY(-50%)'
    place.animation = 'wm-side 6.8s steps(1) infinite'
  } else if (pos === 'custom') {
    place.left = `${(config.x ?? 0.5) * 100}%`
    place.top = `${(config.y ?? 0.5) * 100}%`
    place.transform = 'translate(-50%, -50%)'
  } else {
    if (pos.includes('top')) place.top = `${padPct}%`
    if (pos.includes('bottom')) place.bottom = `${padPct}%`
    if (pos.includes('left')) place.left = `${padPct}%`
    if (pos.includes('right')) place.right = `${padPct}%`
    if (pos === 'center') {
      place.top = '50%'
      place.left = '50%'
      place.transform = 'translate(-50%, -50%)'
    }
  }
  const showImg = (config.type === 'image' || config.type === 'both') && config.image_asset
  const showTxt = (config.type === 'text' || config.type === 'both') && config.text

  return (
    <div
      className={`relative mx-auto rounded-lg overflow-hidden bg-gradient-to-br from-slate-600 to-slate-800 ${
        landscape ? 'aspect-video w-full max-w-[220px]' : 'aspect-[9/16] max-h-52'
      }`}
      aria-label="Watermark preview"
    >
      {moving && (
        <style>{`@keyframes wm-side {
          0%   { top:50%; bottom:auto; left:auto; right:${padPct}%; transform:translateY(-50%); }
          50%  { top:50%; bottom:auto; left:${padPct}%; right:auto; transform:translateY(-50%); }
        }
        @keyframes wm-orbit {
          0%   { transform: translate(3px, 0); }
          25%  { transform: translate(0, 3px); }
          50%  { transform: translate(-3px, 0); }
          75%  { transform: translate(0, -3px); }
          100% { transform: translate(3px, 0); }
        }`}</style>
      )}
      <div style={place}>
        <div
          className="flex flex-col items-center gap-0.5 leading-none"
          style={moving ? { animation: 'wm-orbit 3.4s linear infinite' } : undefined}
        >
          {showImg && (
            <img
              src={api.brandingAssetUrl(config.image_asset!)}
              alt=""
              style={{
                width: `${(config.scale ?? 0.18) * (landscape ? 220 : 117)}px`,
                ...logoFrameStyle(config.frame)
              }}
              className="max-w-none"
            />
          )}
          {showTxt && (
            <span
              style={{
                color: config.color ?? '#FFFFFF',
                fontFamily: config.font,
                fontSize: `${Math.max(7, (config.font_size ?? 42) * 0.16)}px`,
                textShadow: config.shadow ? '0 1px 2px rgba(0,0,0,0.9)' : 'none',
                transform: config.rotation ? `rotate(${config.rotation}deg)` : undefined,
                fontWeight: 700
              }}
            >
              {config.text}
            </span>
          )}
        </div>
      </div>
      {config.cta?.enabled && config.cta.text && (
        <div
          className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2 px-1.5 py-0.5 text-center font-black leading-tight"
          style={{
            top: CTA_TOP[config.cta.position] ?? '17%',
            background: config.cta.bg,
            color: config.cta.color,
            fontSize: '6.5px',
            maxWidth: '84%'
          }}
        >
          {config.cta.text}
        </div>
      )}
    </div>
  )
}

/** A labelled control: label above, control below, so every field in a
 *  section lines up on the same grid whatever its width. */
function Field({
  label,
  children,
  className = '',
  title
}: {
  label: string
  children: React.ReactNode
  className?: string
  title?: string
}): JSX.Element {
  return (
    <div className={`space-y-1 min-w-0 ${className}`} title={title}>
      <span className="label block">{label}</span>
      {children}
    </div>
  )
}

/** A range with its value beside it. */
function Slider({
  label,
  value,
  min,
  max,
  onChange
}: {
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}): JSX.Element {
  return (
    <label className="space-y-1 min-w-0 block">
      <span className="label flex justify-between gap-2">
        <span>{label}</span>
        <span className="normal-case tracking-normal text-ink tabular-nums">{value}%</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[#38BDF8]"
      />
    </label>
  )
}

const SEGMENT_ON = 'bg-accent/20 text-accent font-medium'
const SEGMENT_OFF = 'bg-raised text-muted hover:text-ink'
const SMALL_FIELD = 'input !py-1.5 text-sm'

export default function WatermarkControls({
  config,
  onChange,
  landscape,
  showCta = true,
  hidePreview = false
}: {
  config: WatermarkConfig
  onChange: (patch: Partial<WatermarkConfig>) => void
  landscape?: boolean
  /** False for compilations: a CTA would pop up again on every segment. */
  showCta?: boolean
  /** When a shared preview above already shows this layer with the others. */
  hidePreview?: boolean
}): JSX.Element {
  const withImage = config.type === 'image' || config.type === 'both'
  const withText = config.type === 'text' || config.type === 'both'
  const shown = config.type !== 'none'

  const upload = async (): Promise<void> => {
    const path = await window.studio.pickImageFile()
    if (!path) return
    try {
      const { asset } = await api.uploadBrandingAsset(path)
      onChange({ image_asset: asset })
    } catch (e) {
      alert(`Couldn't add that image: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="@container">
      <div
        className={`grid grid-cols-1 gap-x-6 gap-y-5 items-start ${
          hidePreview ? '' : '@3xl:grid-cols-[minmax(0,1fr)_auto]'
        }`}
      >
        <div className="space-y-5 min-w-0">
          <div className="grid grid-cols-1 @md:grid-cols-2 gap-x-4 gap-y-4">
            <Field label="Type">
              <div className="flex gap-1.5">
                {(['none', 'text', 'image', 'both'] as const).map((k) => (
                  <button
                    key={k}
                    onClick={() => onChange({ type: k })}
                    className={`px-3 py-1.5 text-sm rounded-md capitalize ${config.type === k ? SEGMENT_ON : SEGMENT_OFF}`}
                  >
                    {k}
                  </button>
                ))}
              </div>
            </Field>

            {withImage && (
              <Field label="Logo">
                <div className="flex items-center gap-2 min-w-0">
                  <AsyncButton className="btn-ghost !py-1.5 !px-3 text-sm shrink-0" busyLabel="Uploading…" onClick={upload}>
                    <Folder className="mr-1.5" />
                    {config.image_asset ? 'Replace logo' : 'Upload logo (PNG)'}
                  </AsyncButton>
                  {config.image_asset && <span className="text-xs text-muted truncate">✓ added</span>}
                </div>
              </Field>
            )}

            {withImage && (
              <Field label="Frame" className="@md:col-span-2">
                <div className="flex flex-wrap gap-1.5">
                  {FRAMES.map((f) => (
                    <button
                      key={f.id}
                      onClick={() => onChange({ frame: f.id })}
                      title={f.title}
                      className={`px-3 py-1.5 text-sm rounded-md ${(config.frame ?? 'free') === f.id ? SEGMENT_ON : SEGMENT_OFF}`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </Field>
            )}
          </div>

          {withText && (
            <div className="grid grid-cols-2 @lg:grid-cols-[minmax(0,1fr)_5rem_auto_auto] gap-x-4 gap-y-4 items-end">
              <Field label="Text" className="col-span-2 @lg:col-span-4">
                <input
                  className={`${SMALL_FIELD} w-full`}
                  value={config.text ?? ''}
                  placeholder="@YourChannel"
                  onChange={(e) => onChange({ text: e.target.value })}
                />
              </Field>
              <Field label="Font" className="col-span-2 @lg:col-span-1">
                <select
                  className={`${SMALL_FIELD} w-full`}
                  value={config.font}
                  onChange={(e) => onChange({ font: e.target.value })}
                >
                  {CAPTION_FONTS.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Size">
                <input
                  type="number"
                  min={12}
                  max={120}
                  value={config.font_size ?? 42}
                  onChange={(e) => onChange({ font_size: Number(e.target.value) })}
                  className={`${SMALL_FIELD} w-full`}
                />
              </Field>
              <Field label="Colour">
                <input
                  type="color"
                  value={config.color ?? '#FFFFFF'}
                  onChange={(e) => onChange({ color: e.target.value })}
                  className="h-9 w-12 bg-raised rounded cursor-pointer border border-raised"
                />
              </Field>
              <label className="flex items-center gap-2 text-sm cursor-pointer h-9 col-span-2 @lg:col-span-1">
                <input
                  type="checkbox"
                  checked={config.shadow ?? true}
                  onChange={(e) => onChange({ shadow: e.target.checked })}
                />
                Shadow
              </label>
            </div>
          )}

          {shown && (
          <div className="grid grid-cols-1 @md:grid-cols-2 gap-x-4 gap-y-4">
            <Field label="Position" className="@md:col-span-2">
              <div className="flex items-center gap-1.5 flex-wrap">
                {POSITIONS.map((p) => (
                  <button
                    key={p.id}
                    onClick={() => onChange({ position: p.id })}
                    className={`w-9 h-9 rounded-md text-base ${config.position === p.id ? 'bg-accent/20 text-accent' : SEGMENT_OFF}`}
                    title={p.title}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              {config.position === 'moving' && (
                <p className="text-[11px] text-muted pt-1">
                  circles at one edge, then jumps to the other — never the middle
                </p>
              )}
            </Field>
            <Slider
              label="Opacity"
              min={1}
              max={100}
              value={Math.round((config.opacity ?? 0.85) * 100)}
              onChange={(v) => onChange({ opacity: v / 100 })}
            />
            <Slider
              label="Edge padding"
              min={0}
              max={12}
              value={Math.round((config.padding ?? 0.04) * 100)}
              onChange={(v) => onChange({ padding: v / 100 })}
            />
            {withImage && (
              <Slider
                label="Logo size"
                min={1}
                max={45}
                value={Math.round((config.scale ?? 0.18) * 100)}
                onChange={(v) => onChange({ scale: v / 100 })}
              />
            )}
          </div>
          )}

          {showCta && (
            <CtaControls
              cta={config.cta ?? DEFAULT_CTA}
              onChange={(patch) => onChange({ cta: { ...DEFAULT_CTA, ...config.cta, ...patch } })}
            />
          )}
        </div>

        {!hidePreview && (
          <div className="@3xl:sticky @3xl:top-4">
            <Preview config={config} landscape={landscape} />
          </div>
        )}
      </div>
    </div>
  )
}

/** The CTA's timing in words, so "at 3, for 4, repeat 0" reads as a plan. */
function describeCtaTiming(cta: CtaConfig): string {
  const len = Math.max(0.5, cta.duration)
  if (cta.anchor === 'end') {
    // Mirrors cta_windows(): timed from the end, a repeat would land past
    // the last frame, so it shows once.
    const base =
      cta.at > 0
        ? `Shows for ${len}s, ending ${cta.at}s before the clip does`
        : `Shows for the clip's final ${len}s`
    return cta.repeat > 0 ? `${base}. Repeats only apply when timed from the start.` : `${base}.`
  }
  const base =
    cta.at > 0 ? `Shows for ${len}s, starting ${cta.at}s in` : `Shows for ${len}s from the first frame`
  return cta.repeat > 0
    ? `${base}, then every ${Math.max(cta.repeat, len + 0.5)}s after that.`
    : `${base}, once.`
}

/** The optional call to action: what it says, and when it shows. */
function CtaControls({
  cta,
  onChange
}: {
  cta: CtaConfig
  onChange: (patch: Partial<CtaConfig>) => void
}): JSX.Element {
  const seconds = (key: 'at' | 'duration' | 'repeat', min: number, label: string, title: string): JSX.Element => (
    <Field label={label} title={title}>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          min={min}
          max={600}
          step={0.5}
          value={cta[key]}
          onChange={(e) => onChange({ [key]: Math.max(min, Number(e.target.value) || 0) })}
          className={`${SMALL_FIELD} w-full`}
        />
        <span className="text-xs text-muted">s</span>
      </div>
    </Field>
  )

  return (
    <div className="border-t border-raised/60 pt-4 space-y-4">
      <label className="flex items-start gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          className="mt-1"
          checked={cta.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
        />
        <span>
          <span className="font-medium">Call to action</span>
          <span className="block text-xs text-muted">a boxed message that pops up while the clip plays</span>
        </span>
      </label>

      {cta.enabled && (
        <>
          <div className="flex items-center gap-1.5 flex-wrap">
            {CTA_PRESETS.map((p) => (
              <button
                key={p.id}
                onClick={() => onChange({ kind: p.id, bg: p.bg, ...(p.text ? { text: p.text } : {}) })}
                className={`px-3 py-1.5 text-sm rounded-md ${cta.kind === p.id ? SEGMENT_ON : SEGMENT_OFF}`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <input
            className={`${SMALL_FIELD} w-full`}
            value={cta.text}
            maxLength={120}
            placeholder="e.g. Upload your clips to discord.gg/yourserver"
            onChange={(e) => onChange({ text: e.target.value })}
          />
          <div className="grid grid-cols-2 @lg:grid-cols-4 gap-x-4 gap-y-4">
            <Field label="Show" className="col-span-2 @lg:col-span-1">
              <select
                className={`${SMALL_FIELD} w-full`}
                value={cta.anchor}
                onChange={(e) => onChange({ anchor: e.target.value as CtaConfig['anchor'] })}
              >
                <option value="start">after the start</option>
                <option value="end">before the end</option>
              </select>
            </Field>
            {seconds('at', 0, 'By', 'How far from the start (or end) of the clip it appears')}
            {seconds('duration', 0.5, 'For', 'How long it stays on screen')}
            {seconds('repeat', 0, 'Repeat every', 'Show it again every N seconds (0 = only once)')}
          </div>
          <p className="text-xs text-muted">{describeCtaTiming(cta)}</p>
          <div className="flex gap-x-6 gap-y-3 flex-wrap items-end">
            <Field label="Place">
              <div className="flex gap-1.5">
                {(['top', 'middle', 'bottom'] as const).map((pos) => (
                  <button
                    key={pos}
                    onClick={() => onChange({ position: pos })}
                    className={`px-3 py-1.5 text-sm rounded-md capitalize ${cta.position === pos ? 'bg-accent/20 text-accent' : SEGMENT_OFF}`}
                  >
                    {pos}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Text colour">
              <input
                type="color"
                value={cta.color}
                onChange={(e) => onChange({ color: e.target.value })}
                className="h-9 w-12 bg-raised rounded cursor-pointer border border-raised"
              />
            </Field>
            <Field label="Box colour">
              <input
                type="color"
                value={cta.bg}
                onChange={(e) => onChange({ bg: e.target.value })}
                className="h-9 w-12 bg-raised rounded cursor-pointer border border-raised"
              />
            </Field>
          </div>
        </>
      )}
    </div>
  )
}

/** A branding profile drawn over a preview frame of `size` (the output
 *  canvas in pixels), shown at `scale` screen pixels per canvas pixel.
 *
 *  Geometry follows video_editor/watermark.py exactly, not the loose
 *  thumbnail in Preview above: the logo is `scale` × frame width and sits
 *  `padding` × the shorter edge in from its corner (apply_image), and the
 *  text is an ASS line sized font_size × height/1920 with the same margins
 *  (_text_style). The two are placed independently, as the render places
 *  them, so "both" at one corner overlaps here exactly as it will there.
 *  "Moving" shows both sides it visits. The CTA is not drawn: compilations
 *  never burn it (it would repeat on every segment). */
export function BrandingLayer({
  config,
  size,
  scale
}: {
  config: WatermarkConfig
  size: [number, number]
  scale: number
}): JSX.Element {
  const [cw, ch] = size
  const pad = (config.padding ?? 0.04) * Math.min(cw, ch) * scale
  const opacity = config.opacity ?? 0.85
  const pos = config.position ?? 'bottom_right'
  const showImg = (config.type === 'image' || config.type === 'both') && config.image_asset
  const showTxt = (config.type === 'text' || config.type === 'both') && config.text?.trim()

  const at = (p: string, side?: 'left' | 'right'): React.CSSProperties => {
    const s: React.CSSProperties = { position: 'absolute' }
    if (p === 'moving') {
      s.top = '50%'
      s[side ?? 'right'] = pad
      s.transform = 'translateY(-50%)'
    } else if (p === 'custom') {
      s.left = `${(config.x ?? 0.5) * 100}%`
      s.top = `${(config.y ?? 0.5) * 100}%`
      s.transform = 'translate(-50%, -50%)'
    } else if (p === 'center') {
      s.left = '50%'
      s.top = '50%'
      s.transform = 'translate(-50%, -50%)'
    } else {
      s[p.includes('top') ? 'top' : 'bottom'] = pad
      s[p.includes('left') ? 'left' : 'right'] = pad
    }
    return s
  }

  const logo = (key: string, style: React.CSSProperties, ghost = false): JSX.Element => (
    <img
      key={key}
      src={api.brandingAssetUrl(config.image_asset!)}
      alt=""
      draggable={false}
      className="max-w-none pointer-events-none"
      style={{
        ...style,
        width: Math.max(8, (config.scale ?? 0.18) * cw) * scale,
        opacity: ghost ? opacity * 0.45 : opacity,
        rotate: config.rotation ? `${config.rotation}deg` : undefined,
        ...logoFrameStyle(config.frame)
      }}
    />
  )
  const text = (key: string, style: React.CSSProperties, ghost = false): JSX.Element => (
    <span
      key={key}
      className="pointer-events-none"
      style={{
        ...style,
        color: config.color ?? '#FFFFFF',
        opacity: ghost ? opacity * 0.45 : opacity,
        fontFamily: config.font,
        fontSize: assFontSize(config.font, Math.max(12, Math.round((config.font_size ?? 42) * (ch / 1920)))) * scale,
        whiteSpace: 'nowrap',
        lineHeight: 1.1,
        textShadow: config.shadow ?? true ? '0 0 2px #000, 1px 1px 2px rgba(0,0,0,.6)' : '0 0 1px #000',
        rotate: config.rotation ? `${config.rotation}deg` : undefined
      }}
    >
      {config.text}
    </span>
  )

  const sides: ('right' | 'left')[] = pos === 'moving' ? ['right', 'left'] : ['right']
  return (
    <>
      {sides.map((side, i) => (
        <span key={side}>
          {showImg && logo(`img-${side}`, at(pos, side), i > 0)}
          {showTxt &&
            text(
              `txt-${side}`,
              // Moving text circles at 18% / 82% of the width (_text_events),
              // not at the padded edge the logo uses.
              pos === 'moving'
                ? {
                    position: 'absolute',
                    top: '50%',
                    left: side === 'right' ? '82%' : '18%',
                    transform: 'translate(-50%, -50%)'
                  }
                : at(pos, side),
              i > 0
            )}
        </span>
      ))}
    </>
  )
}
