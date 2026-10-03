import { useEffect, useState } from 'react'
import { api } from './api'
import type { BrandingKind, BrandingProfile, JobOptions } from './types'

/** Which branding a video gets. Three answers, not two: a profile, explicitly
 *  none, or 'auto' — the creator's default profile if they have one, and
 *  nothing if they don't. 'auto' is the absence of both job keys. */
export type BrandingChoice = number | 'none' | 'auto'

// The same two keys the old Generate-bar editor wrote, so an existing setup
// ("watermark on, profile 2") carries over as "profile 2 is the default".
const ENABLED_KEY = 'watermark-enabled'
const PROFILE_KEY = 'watermark-profile-id'

/** Fired after profiles are created, changed or deleted, or the default
 *  moves, so every picker on screen re-reads the list. */
const CHANGED = 'branding-changed'

// Compilations keep their own default, under their own key.
const COMPILATION_KEY = 'compilation-branding-profile-id'

/** The profile new videos (clips) start with, or null for "creator default";
 *  for compilations, the profile a new compilation starts with, or null. */
export function defaultBrandingId(kind: BrandingKind = 'clip'): number | null {
  try {
    if (kind === 'compilation') {
      const id = Number(localStorage.getItem(COMPILATION_KEY))
      return id > 0 ? id : null
    }
    if (localStorage.getItem(ENABLED_KEY) !== 'true') return null
    const id = Number(localStorage.getItem(PROFILE_KEY))
    return id > 0 ? id : null
  } catch {
    return null
  }
}

export function setDefaultBrandingId(id: number | null, kind: BrandingKind = 'clip'): void {
  try {
    if (kind === 'compilation') {
      if (id !== null) localStorage.setItem(COMPILATION_KEY, String(id))
      else localStorage.removeItem(COMPILATION_KEY)
    } else {
      localStorage.setItem(ENABLED_KEY, String(id !== null))
      if (id !== null) localStorage.setItem(PROFILE_KEY, String(id))
      else localStorage.removeItem(PROFILE_KEY)
    }
  } catch {
    // Blocked storage: the default just won't be remembered.
  }
  brandingChanged()
}

/** What a clip credit is previewed on: the two shapes a clip renders in. */
export const CLIP_FORMATS = ['9:16', '16:9']
export const CLIP_SIZES = { '9:16': { width: 1080, height: 1920 }, '16:9': { width: 1920, height: 1080 } }
export const CREDIT_POSITIONS = [
  'bottom_left',
  'bottom_center',
  'bottom_right',
  'middle_left',
  'middle_center',
  'middle_right',
  'top_left',
  'top_center',
  'top_right',
  'custom'
]
/** Stand-in names for the preview: the real one comes from each clip's source video. */
export const CREDIT_SAMPLES = [
  { channel: 'Creator Name', title: 'Video title', url: 'youtube.com/@creator' },
  { channel: 'A Much Longer Channel Name Here', title: '', url: '' }
]

/** Profiles that brand single clips: what every clip picker offers. */
export const clipProfiles = (all: BrandingProfile[]): BrandingProfile[] =>
  all.filter((p) => (p.kind ?? 'clip') === 'clip')

export function brandingChanged(): void {
  window.dispatchEvent(new Event(CHANGED))
}

export function brandingChoice(o: JobOptions | undefined): BrandingChoice {
  if (o?.no_watermark) return 'none'
  if (o?.watermark_profile_id) return o.watermark_profile_id
  return 'auto'
}

/** A copy of `o` carrying `choice`. Replaces rather than merges: the two keys
 *  are one setting, and a job must never hold both. */
export function withBranding(o: JobOptions, choice: BrandingChoice): JobOptions {
  const next = { ...o }
  delete next.watermark_profile_id
  delete next.no_watermark
  if (choice === 'none') next.no_watermark = true
  else if (typeof choice === 'number') next.watermark_profile_id = choice
  return next
}

/** The saved profiles, kept current across the app. A default that points at
 *  a deleted profile is dropped here, the one place that sees both. */
export function useBrandingProfiles(): {
  profiles: BrandingProfile[]
  loaded: boolean
  reload: () => void
} {
  const [profiles, setProfiles] = useState<BrandingProfile[]>([])
  const [loaded, setLoaded] = useState(false)
  const reload = (): void => {
    api
      .branding()
      .then((list) => {
        setProfiles(list)
        setLoaded(true)
        for (const kind of ['clip', 'compilation'] as const) {
          const d = defaultBrandingId(kind)
          if (d !== null && !list.some((p) => p.id === d && (p.kind ?? 'clip') === kind)) {
            setDefaultBrandingId(null, kind)
          }
        }
      })
      .catch(() => setLoaded(true)) // engine not up yet: an empty list, not a spinner forever
  }
  useEffect(() => {
    reload()
    window.addEventListener(CHANGED, reload)
    return () => window.removeEventListener(CHANGED, reload)
  }, [])
  return { profiles, loaded, reload }
}
