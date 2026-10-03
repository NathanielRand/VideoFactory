import { useEffect, useState } from 'react'
import BrandingPreview from '../components/BrandingPreview'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../components/CaptionStyleControls'
import CreditControls from '../components/CreditControls'
import WatermarkControls, { DEFAULT_WATERMARK } from '../components/WatermarkControls'
import { api } from '../lib/api'
import { compilationsApi, type CompilationOptions } from '../lib/compilations'
import {
  CLIP_FORMATS,
  CLIP_SIZES,
  CREDIT_POSITIONS,
  CREDIT_SAMPLES,
  brandingChanged,
  defaultBrandingId,
  setDefaultBrandingId,
  useBrandingProfiles
} from '../lib/branding'
import { t } from '../lib/i18n'
import type { BrandingKind, BrandingProfile, CreatorSummary, WatermarkConfig } from '../lib/types'

/** One line saying what a profile puts on screen, for the list. */
function summary(c: WatermarkConfig): string {
  const parts: string[] = []
  if ((c.type === 'text' || c.type === 'both') && c.text) parts.push(c.text)
  if ((c.type === 'image' || c.type === 'both') && c.image_asset) parts.push(t('logo'))
  if (c.cta?.enabled && c.cta.text) parts.push(t('CTA'))
  if (c.credit?.enabled) parts.push(t('Credit'))
  return parts.join(' + ') || t('empty')
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)

const KINDS: { id: BrandingKind; label: string; hint: string }[] = [
  { id: 'clip', label: 'Clips', hint: 'Burned into each clip cut from a video.' },
  {
    id: 'compilation',
    label: 'Compilations',
    hint: 'Burned into compilations, in each format they render. No call to action: it would repeat on every segment.'
  }
]

/** Keeps the profile list collapsed or open between visits. */
const RAIL_KEY = 'branding-rail-collapsed'
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(RAIL_KEY) === 'true'
  } catch {
    return false
  }
}
const initials = (name: string): string =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join('') || '?'

/** Every branding setting in one place.
 *
 *  Profiles are made and edited here and nowhere else. The places a video is
 *  set up (Add videos, a queued item, a watched channel) only PICK a profile,
 *  so there is one editor to learn instead of three half-editors.
 *
 *  What a video ends up with, in order: the profile chosen for that video,
 *  else its creator's default, else nothing. "Default for new videos" is only
 *  what a new row in Add videos starts on. */
export default function Branding(): JSX.Element {
  const { profiles: allProfiles, loaded } = useBrandingProfiles()
  const [kind, setKind] = useState<BrandingKind>('clip')
  const profiles = allProfiles.filter((p) => (p.kind ?? 'clip') === kind)
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const [compOptions, setCompOptions] = useState<CompilationOptions | null>(null)
  const [selected, setSelected] = useState<number | 'new' | null>(null)
  const [name, setName] = useState('')
  const [config, setConfig] = useState<WatermarkConfig>(DEFAULT_WATERMARK)
  const [notice, setNotice] = useState('')
  const [tab, setTab] = useState<'watermark' | 'credit' | 'captions'>('watermark')
  const [clipDefault, setClipDefault] = useState(defaultBrandingId('clip'))
  const [compDefault, setCompDefault] = useState(defaultBrandingId('compilation'))
  const defaultId = kind === 'compilation' ? compDefault : clipDefault

  const current = typeof selected === 'number' ? profiles.find((p) => p.id === selected) : undefined
  const dirty =
    selected === 'new' ||
    (current !== undefined && (name !== current.name || !same(config, current.config)))

  const load = (p: BrandingProfile | undefined): void => {
    setSelected(p ? p.id : null)
    setName(p?.name ?? '')
    setConfig(p?.config ?? DEFAULT_WATERMARK)
  }

  // Open on the first profile, and move off one that was just deleted.
  useEffect(() => {
    if (!loaded || selected === 'new') return
    if (selected === null || !profiles.some((p) => p.id === selected)) load(profiles[0])
  }, [loaded, allProfiles, kind])

  useEffect(() => {
    compilationsApi.options().then(setCompOptions).catch(() => undefined)
  }, [])

  const toggleRail = (): void => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(RAIL_KEY, String(!c))
      } catch {
        // Blocked storage: it just opens expanded next time.
      }
      return !c
    })
  }

  const switchKind = (k: BrandingKind): void => {
    if (k === kind || !leave()) return
    setKind(k)
    setSelected(null) // the effect above opens the first profile of the new kind
  }

  useEffect(() => {
    const sync = (): void => {
      setClipDefault(defaultBrandingId('clip'))
      setCompDefault(defaultBrandingId('compilation'))
    }
    window.addEventListener('branding-changed', sync)
    return () => window.removeEventListener('branding-changed', sync)
  }, [])

  const flash = (m: string): void => {
    setNotice(m)
    setTimeout(() => setNotice(''), 3000)
  }

  const leave = (): boolean =>
    !dirty || window.confirm(t('Discard unsaved changes to this profile?'))

  const startNew = (from?: BrandingProfile): void => {
    if (!leave()) return
    setSelected('new')
    setName(from ? `${from.name} ${t('copy')}` : '')
    setConfig(from ? { ...from.config } : { ...DEFAULT_WATERMARK })
  }

  const save = async (): Promise<void> => {
    try {
      const label = name.trim() || t('Branding')
      if (selected === 'new') {
        const { id } = await api.createBranding(label, config, kind)
        // The first profile anyone makes is almost always the one they want
        // on everything, so it starts as the default.
        if (profiles.length === 0) setDefaultBrandingId(id, kind)
        setSelected(id)
        flash(t('Profile created'))
      } else if (typeof selected === 'number') {
        await api.updateBranding(selected, label, config)
        flash(t('Saved'))
      }
      setName(label)
      brandingChanged()
    } catch (e) {
      flash(e instanceof Error ? e.message : String(e))
    }
  }

  const remove = async (): Promise<void> => {
    if (!current || !window.confirm(`${t('Delete branding profile')} "${current.name}"?`)) return
    try {
      await api.deleteBranding(current.id)
      if (defaultId === current.id) setDefaultBrandingId(null, kind)
      setSelected(null)
      brandingChanged()
    } catch (e) {
      flash(e instanceof Error ? e.message : String(e))
    }
  }

  const kindInfo = KINDS.find((k) => k.id === kind)!
  const isCompilation = kind === 'compilation'
  // Compilations burn no captions, so a tab left on Captions falls back.
  const activeTab = isCompilation && tab === 'captions' ? 'watermark' : tab
  const previewFormats = isCompilation ? Object.keys(compOptions?.canvases ?? { '16:9': 0, '9:16': 0 }) : CLIP_FORMATS
  const previewSizes = isCompilation ? (compOptions?.canvases ?? CLIP_SIZES) : CLIP_SIZES

  return (
    <div className="p-6 space-y-5 max-w-[88rem]">
      <div>
        <h2 className="text-2xl font-bold">{t('Branding')}</h2>
        <p className="text-sm text-muted mt-1 max-w-3xl">
          {t(
            'Profiles hold a logo and/or channel handle burned into your videos. Clips and compilations have their own profiles, since they render in different shapes.'
          )}
        </p>
      </div>

      <div className="flex gap-1 border-b border-raised/60" role="tablist" aria-label={t('Profile type')}>
        {KINDS.map((k) => (
          <button
            key={k.id}
            role="tab"
            aria-selected={kind === k.id}
            className={`px-4 py-2 text-sm font-medium -mb-px border-b-2 ${
              kind === k.id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-ink'
            }`}
            onClick={() => switchKind(k.id)}
          >
            {t(k.label)}
            <span className="ml-2 text-xs text-muted tabular-nums">
              {allProfiles.filter((p) => (p.kind ?? 'clip') === k.id).length}
            </span>
          </button>
        ))}
      </div>

      <div
        className={`card !p-0 grid ${
          collapsed ? 'md:grid-cols-[3.5rem_minmax(0,1fr)]' : 'md:grid-cols-[13rem_minmax(0,1fr)]'
        }`}
        aria-label={t('Profiles')}
      >
        <div className="p-2 space-y-2 md:border-r border-raised/60 min-w-0">
          <div className={`flex items-center gap-1 ${collapsed ? 'flex-col' : 'justify-between'}`}>
            {!collapsed && <h3 className="text-sm font-semibold pl-1">{t('Profiles')}</h3>}
            <div className={`flex items-center gap-0.5 ${collapsed ? 'flex-col' : ''}`}>
              <button
                className="btn-ghost !py-0.5 !px-2 text-xs"
                onClick={() => startNew()}
                title={t('New profile')}
                aria-label={t('New profile')}
              >
                +{collapsed ? '' : ` ${t('New')}`}
              </button>
              <button
                className="text-muted hover:text-ink px-1.5 py-0.5 text-sm"
                onClick={toggleRail}
                aria-expanded={!collapsed}
                aria-label={collapsed ? t('Expand profile list') : t('Collapse profile list')}
                title={collapsed ? t('Expand profile list') : t('Collapse profile list')}
              >
                {collapsed ? '»' : '«'}
              </button>
            </div>
          </div>
          {!collapsed && loaded && profiles.length === 0 && selected !== 'new' && (
            <p className="text-xs text-muted px-1">
              {t('No profiles yet. Press New to make your first one.')}
            </p>
          )}
          <ul className="space-y-0.5">
            {profiles.map((p) => (
              <li key={p.id}>
                <button
                  className={`w-full text-left rounded-md transition-colors ${
                    collapsed ? 'h-9 text-xs font-semibold text-center' : 'px-2 py-1.5'
                  } ${selected === p.id ? 'bg-accent/15 text-accent' : 'hover:bg-raised'}`}
                  aria-current={selected === p.id ? 'true' : undefined}
                  title={collapsed ? `${p.name} — ${summary(p.config)}` : undefined}
                  onClick={() => selected !== p.id && leave() && load(p)}
                >
                  {collapsed ? (
                    initials(p.name)
                  ) : (
                    <>
                      <span className="flex items-center gap-1.5 text-sm">
                        <span className="truncate font-medium">{p.name}</span>
                        {p.id === defaultId && (
                          <span className="ml-auto text-accent shrink-0" title={t('Default')}>
                            ★
                          </span>
                        )}
                      </span>
                      <span className="block text-[11px] text-muted truncate">{summary(p.config)}</span>
                    </>
                  )}
                </button>
              </li>
            ))}
            {selected === 'new' && (
              <li
                className={`rounded-md bg-accent/15 text-accent text-sm font-medium ${
                  collapsed ? 'h-9 flex items-center justify-center' : 'px-2 py-1.5'
                }`}
                title={name.trim() || t('New profile')}
              >
                {collapsed ? '+' : name.trim() || t('New profile')}
              </li>
            )}
          </ul>
        </div>

        {selected === null ? (
          <div className="text-sm text-muted self-center p-6">
            {t('Pick a profile to edit it, or make a new one.')}
          </div>
        ) : (
          <div className="space-y-5 min-w-0 p-5">
            <div className="flex items-center gap-2 flex-wrap">
              <input
                className="input !py-1.5 text-sm flex-1 min-w-48"
                value={name}
                placeholder={
                  isCompilation ? t('Profile name (e.g. Weekly Highlights)') : t('Profile name (e.g. YouTube Channel)')
                }
                aria-label={t('Profile name')}
                onChange={(e) => setName(e.target.value)}
              />
              {current && (
                <>
                  {(
                    <button
                      className="btn-ghost !py-1 text-xs"
                      disabled={current.id === defaultId}
                      onClick={() => setDefaultBrandingId(current.id, kind)}
                      title={
                        isCompilation
                          ? t('New compilations start with this profile')
                          : t('New videos in Add videos start with this profile')
                      }
                    >
                      {current.id === defaultId ? `★ ${t('Default')}` : t('Make default')}
                    </button>
                  )}
                  <button className="btn-ghost !py-1 text-xs" onClick={() => startNew(current)}>
                    {t('Duplicate')}
                  </button>
                  <button className="text-xs text-muted hover:text-red-400 px-1" onClick={remove}>
                    {t('Delete')}
                  </button>
                </>
              )}
            </div>
            <p className="text-xs text-muted -mt-3">{t(kindInfo.hint)}</p>

            <div className="flex gap-1 border-b border-raised/60" role="tablist" aria-label={t('Branding sections')}>
              {(
                [
                  ['watermark', isCompilation ? t('Watermark') : t('Watermark & CTA')],
                  ['credit', t('Credit')],
                  ...(isCompilation ? [] : [['captions', t('Captions')]])
                ] as [string, string][]
              ).map(([id, label]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={activeTab === id}
                  className={`px-3 py-1.5 text-sm -mb-px border-b-2 ${
                    activeTab === id ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-ink'
                  }`}
                  onClick={() => setTab(id as typeof tab)}
                >
                  {label}
                  {id === 'credit' && config.credit?.enabled && (
                    <span className="ml-1.5 text-[10px] text-accent">●</span>
                  )}
                  {id === 'captions' && config.captions && (
                    <span className="ml-1.5 text-[10px] text-accent">●</span>
                  )}
                </button>
              ))}
            </div>

            {/* The same preview, in the same place, on every tab: only the
                controls below it change. It shows every active layer. */}
            <BrandingPreview
              watermark={config}
              credit={config.credit ?? { enabled: false }}
              captions={isCompilation ? null : (config.captions ?? DEFAULT_CAPTION_STYLE)}
              cta={isCompilation ? null : config.cta}
              formats={previewFormats}
              sizes={previewSizes}
              samples={CREDIT_SAMPLES}
              note={
                isCompilation
                  ? t('Compilations do not burn captions or a call to action.')
                  : config.captions
                    ? undefined
                    : t('Captions shown in the default style; set one on the Captions tab.')
              }
            />

            {activeTab === 'watermark' && (
              <WatermarkControls
                config={config}
                landscape={isCompilation}
                showCta={!isCompilation}
                hidePreview
                onChange={(patch) => setConfig((c) => ({ ...c, ...patch }))}
              />
            )}
            {activeTab === 'credit' && (
              <div className="space-y-3">
                <p className="text-xs text-muted">
                  {isCompilation
                    ? t('Credits the channel each segment came from. Compilations that use this profile follow it unless they set their own.')
                    : t(
                        'Credits the channel each clip came from, filled in automatically from the source video. The banner image is part of this profile.'
                      )}
                </p>
                <CreditControls
                  credits={config.credit ?? { enabled: false }}
                  positions={isCompilation ? (compOptions?.credit_positions ?? CREDIT_POSITIONS) : CREDIT_POSITIONS}
                  formats={previewFormats}
                  sizes={previewSizes}
                  samples={CREDIT_SAMPLES}
                  banner={config}
                  hidePreview
                  onChange={(patch) =>
                    setConfig((c) => ({ ...c, credit: { ...(c.credit ?? {}), ...patch } }))
                  }
                />
              </div>
            )}
            {activeTab === 'captions' && (
              <div className="space-y-4">
                <label className="flex items-start gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={!!config.captions}
                    onChange={(e) =>
                      setConfig((c) => {
                        const { captions: _drop, ...rest } = c
                        return e.target.checked ? { ...rest, captions: { ...DEFAULT_CAPTION_STYLE } } : rest
                      })
                    }
                  />
                  <span>
                    <span className="font-medium">{t('Set the caption look in this profile')}</span>
                    <span className="block text-xs text-muted">
                      {t('Clips using this profile burn their captions this way. Off: clips keep the caption style they were set up with.')}
                    </span>
                  </span>
                </label>
                {config.captions && (
                  <div className="max-w-xl space-y-3">
                    <CaptionStyleControls
                      idPrefix="branding"
                      style={{ ...DEFAULT_CAPTION_STYLE, ...config.captions }}
                      hideExample
                      onChange={(key, value) =>
                        setConfig((c) => ({ ...c, captions: { ...DEFAULT_CAPTION_STYLE, ...c.captions, [key]: value } }))
                      }
                    />
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center gap-3 pt-1">
              <button className="btn-accent !py-1.5" onClick={save} disabled={!dirty}>
                {selected === 'new' ? t('Create profile') : t('Save profile')}
              </button>
              {dirty && (
                <button
                  className="text-xs text-muted hover:text-ink"
                  onClick={() => (selected === 'new' ? load(profiles[0]) : load(current))}
                >
                  {selected === 'new' ? t('Cancel') : t('Revert')}
                </button>
              )}
              {notice && <span className="text-xs text-accent">{notice}</span>}
            </div>
          </div>
        )}
      </div>

      {kind === 'clip' && <DefaultsCard profiles={profiles} defaultId={defaultId} />}
    </div>
  )
}

/** Which profile applies when a video doesn't pick one. */
function DefaultsCard({
  profiles,
  defaultId
}: {
  profiles: BrandingProfile[]
  defaultId: number | null
}): JSX.Element {
  const [creators, setCreators] = useState<CreatorSummary[]>([])
  const [error, setError] = useState('')

  const loadCreators = (): void => {
    api
      .creators()
      .then((d) => setCreators(d.creators))
      .catch(() => setCreators([]))
  }
  // Re-read after a profile is deleted: the server clears creators that used it.
  useEffect(() => {
    loadCreators()
    window.addEventListener('branding-changed', loadCreators)
    return () => window.removeEventListener('branding-changed', loadCreators)
  }, [])

  const options = (
    <>
      {profiles.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </>
  )

  return (
    <div className="card space-y-4" aria-label={t('Defaults')}>
      <h3 className="font-semibold">{t('Defaults')}</h3>

      <label className="flex items-center gap-3 flex-wrap">
        <span className="text-sm w-44 shrink-0">{t('New videos start with')}</span>
        <select
          className="input !w-56 !py-1.5 text-sm"
          value={defaultId ?? ''}
          onChange={(e) => setDefaultBrandingId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">{t('Creator default')}</option>
          {options}
        </select>
        <span className="text-xs text-muted">
          {t('Each video can still be changed in Add videos.')}
        </span>
      </label>

      <div className="space-y-2">
        <p className="text-sm">{t('Creator defaults')}</p>
        <p className="text-xs text-muted">
          {t(
            'Used for a creator’s videos when the video is set to Creator default — including every video from a watched channel.'
          )}
        </p>
        {creators.length === 0 ? (
          <p className="text-sm text-muted">
            {t('No creators yet. They appear once a video has been processed.')}
          </p>
        ) : (
          <div className="divide-y divide-raised/60 border border-raised/60 rounded-lg">
            {creators.map((c) => (
              <label key={c.creator_id} className="flex items-center gap-3 px-3 py-2">
                <span className="text-sm flex-1 truncate">{c.display_name}</span>
                <select
                  className="input !w-56 !py-1 text-sm"
                  value={c.default_branding_id ?? ''}
                  onChange={async (e) => {
                    const v = e.target.value ? Number(e.target.value) : null
                    setError('')
                    try {
                      await api.setCreatorBranding(c.creator_id, v)
                      setCreators((all) =>
                        all.map((x) =>
                          x.creator_id === c.creator_id ? { ...x, default_branding_id: v } : x
                        )
                      )
                    } catch (err) {
                      setError(err instanceof Error ? err.message : String(err))
                    }
                  }}
                >
                  <option value="">{t('None')}</option>
                  {options}
                </select>
              </label>
            ))}
          </div>
        )}
        {error && <p className="text-sm text-error">{error}</p>}
      </div>
    </div>
  )
}
