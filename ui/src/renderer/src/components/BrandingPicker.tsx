import { clipProfiles, defaultBrandingId, useBrandingProfiles, type BrandingChoice } from '../lib/branding'
import { t } from '../lib/i18n'

/** Which branding ONE video gets: a profile, none, or the creator's default.
 *  Profiles themselves are made and edited on the Branding page; this only
 *  picks between them, with a link there. */
export default function BrandingPicker({
  value,
  onChange,
  disabled
}: {
  value: BrandingChoice
  onChange: (choice: BrandingChoice) => void
  disabled?: boolean
}): JSX.Element {
  const profiles = clipProfiles(useBrandingProfiles().profiles)
  const fallback = defaultBrandingId()
  // A profile deleted since this video was set up reads as "creator default",
  // which is what the pipeline will do with an id it cannot find.
  const current =
    typeof value === 'number' && !profiles.some((p) => p.id === value) ? 'auto' : value

  return (
    <label
      className="flex items-center gap-2 text-sm shrink-0 whitespace-nowrap"
      title={t(
        'Logo / channel handle burned into every clip of this video. Creator default uses the profile set for that creator on the Branding page, or none.'
      )}
    >
      {t('Branding')}
      <select
        className="input !w-44 !py-1 text-sm"
        value={String(current)}
        disabled={disabled}
        onChange={(e) => {
          const v = e.target.value
          onChange(v === 'auto' || v === 'none' ? v : Number(v))
        }}
      >
        <option value="auto">{t('Creator default')}</option>
        <option value="none">{t('No branding')}</option>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
            {p.id === fallback ? ` ★` : ''}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="text-xs text-accent hover:underline"
        onClick={(e) => {
          e.preventDefault()
          window.dispatchEvent(new Event('open-branding'))
        }}
      >
        {profiles.length === 0 ? t('Set up') : t('Manage')}
      </button>
    </label>
  )
}
