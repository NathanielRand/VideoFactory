// Branding for ONE clip or ONE compilation: pick a profile, and each layer
// (watermark & CTA, credit, captions) follows it until this item sets its
// own. The preview on top shows every layer as it will burn in, and the
// custom controls open beneath it, so the layout is the same whichever layer
// is being edited.
import type { ReactNode } from 'react'
import { t } from '../lib/i18n'
import type { CreditStyle } from '../lib/compilations'
import type { BrandingKind, BrandingProfile, CaptionStyle, WatermarkConfig } from '../lib/types'
import BrandingPreview from './BrandingPreview'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import CreditControls, { type CreditSample } from './CreditControls'
import WatermarkControls from './WatermarkControls'

/** 'processed': a clip keeps the branding it was made with. */
export type ProfileChoice = number | null | 'processed'

export interface BrandingSectionProps {
  kind: BrandingKind
  profiles: BrandingProfile[]
  profile: ProfileChoice
  onProfile: (p: ProfileChoice) => void
  /** Clips only: the choice that leaves a clip's branding untouched. */
  allowProcessed?: boolean
  disabled?: boolean

  customWatermark: boolean
  onCustomWatermark: (on: boolean) => void
  watermark: WatermarkConfig
  onWatermark: (patch: Partial<WatermarkConfig>) => void

  customCredit: boolean
  onCustomCredit: (on: boolean) => void
  credit: CreditStyle
  onCredit: (patch: Partial<CreditStyle>) => void

  /** Clips only. */
  customCaptions?: boolean
  onCustomCaptions?: (on: boolean) => void
  captions?: CaptionStyle
  onCaptions?: (key: keyof CaptionStyle, value: CaptionStyle[keyof CaptionStyle]) => void

  positions: string[]
  formats: string[]
  sizes: Record<string, { width: number; height: number }>
  samples: CreditSample[]
}

/** One layer: what it currently follows, with the switch to set its own. */
function Layer({
  title,
  following,
  custom,
  onCustom,
  forced,
  disabled,
  noun,
  children
}: {
  title: string
  following: string
  custom: boolean
  onCustom: (on: boolean) => void
  /** No profile to follow: the layer is always its own. */
  forced: boolean
  disabled?: boolean
  noun: string
  children: ReactNode
}): JSX.Element {
  const open = custom || forced
  return (
    <div className="border-t border-raised/60 pt-3 space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <h4 className="text-sm font-semibold">{title}</h4>
        {!open && <span className="text-xs text-muted">{following}</span>}
        {!forced && (
          <label className="flex items-center gap-2 text-xs cursor-pointer ml-auto">
            <input
              type="checkbox"
              checked={custom}
              disabled={disabled}
              onChange={(e) => onCustom(e.target.checked)}
            />
            {t('Custom for this')} {noun}
          </label>
        )}
      </div>
      {open && children}
    </div>
  )
}

export default function BrandingSection(p: BrandingSectionProps): JSX.Element {
  const manage = (): void => {
    window.dispatchEvent(new Event('open-branding'))
  }
  const isComp = p.kind === 'compilation'
  const noun = isComp ? t('compilation') : t('clip')
  const chosen = typeof p.profile === 'number' ? p.profiles.find((x) => x.id === p.profile) : undefined
  const followed = p.profile !== 'processed'
  const forced = followed && !chosen // "None": nothing to follow, every layer is its own

  const effWatermark = !followed ? null : p.customWatermark || forced ? p.watermark : (chosen?.config ?? null)
  const effCredit: CreditStyle | null = !followed
    ? null
    : p.customCredit || forced
      ? p.credit
      : (chosen?.config.credit ?? { enabled: false })
  const effCaptions: CaptionStyle | null = isComp
    ? null
    : p.customCaptions || forced
      ? (p.captions ?? DEFAULT_CAPTION_STYLE)
      : (chosen?.config.captions ?? DEFAULT_CAPTION_STYLE)

  const nameOf = chosen?.name ?? ''
  const follow = (what: string): string => `${t('Following')} “${nameOf}”: ${what}`
  const wmWhat = (c: WatermarkConfig | undefined): string =>
    !c || c.type === 'none' ? t('none') : [c.type, c.cta?.enabled ? 'CTA' : ''].filter(Boolean).join(' + ')

  return (
    <div className="col-span-2 lg:col-span-4 space-y-4 border-y border-raised/60 py-4">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-semibold">{t('Branding')}</h3>
        <select
          className="input !w-64 !py-1.5 text-sm"
          value={String(p.profile)}
          disabled={p.disabled}
          aria-label={t('Branding profile')}
          onChange={(e) => {
            const v = e.target.value
            p.onProfile(v === 'processed' ? 'processed' : v === 'none' ? null : Number(v))
          }}
        >
          {p.allowProcessed && <option value="processed">{t('As processed')}</option>}
          <option value="none">{t('No profile')}</option>
          {p.profiles.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
        <button type="button" className="text-xs text-accent hover:underline" onClick={manage}>
          {p.profiles.length === 0 ? t('Set up') : t('Manage')}
        </button>
      </div>

      {!followed ? (
        <p className="text-xs text-muted">
          {t('This clip keeps the branding it was made with. Pick a profile to change it; each part then follows the profile unless you set your own.')}
        </p>
      ) : (
        <>
          <BrandingPreview
            watermark={effWatermark}
            credit={effCredit}
            captions={effCaptions}
            cta={isComp ? null : effWatermark?.cta}
            formats={p.formats}
            sizes={p.sizes}
            samples={p.samples}
          />

          <Layer
            title={isComp ? t('Watermark') : t('Watermark & CTA')}
            following={follow(wmWhat(chosen?.config))}
            custom={p.customWatermark}
            onCustom={p.onCustomWatermark}
            forced={forced}
            disabled={p.disabled}
            noun={noun}
          >
            <WatermarkControls
              config={p.watermark}
              landscape={isComp}
              showCta={!isComp}
              hidePreview
              onChange={p.onWatermark}
            />
          </Layer>

          <Layer
            title={t('Credit')}
            following={follow(chosen?.config.credit?.enabled ? t('credits on') : t('credits off'))}
            custom={p.customCredit}
            onCustom={p.onCustomCredit}
            forced={forced}
            disabled={p.disabled}
            noun={noun}
          >
            <CreditControls
              credits={p.credit}
              positions={p.positions}
              formats={p.formats}
              sizes={p.sizes}
              samples={p.samples}
              banner={effWatermark}
              hidePreview
              onChange={p.onCredit}
            />
          </Layer>

          {!isComp && p.onCustomCaptions && p.onCaptions && (
            <Layer
              title={t('Captions')}
              following={follow(chosen?.config.captions ? t('profile look') : t('default look'))}
              custom={!!p.customCaptions}
              onCustom={p.onCustomCaptions}
              forced={forced}
              disabled={p.disabled}
              noun={noun}
            >
              <div className="max-w-xl space-y-3">
                <CaptionStyleControls
                  idPrefix="branding-item"
                  style={{ ...DEFAULT_CAPTION_STYLE, ...p.captions }}
                  hideExample
                  onChange={p.onCaptions}
                />
              </div>
            </Layer>
          )}
        </>
      )}
    </div>
  )
}
