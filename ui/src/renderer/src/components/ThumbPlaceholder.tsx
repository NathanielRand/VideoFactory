import { t } from '../lib/i18n'

/** Stands in for a thumbnail that does not exist: a 16:9 frame with a
 *  mountain-and-sun picture glyph. Inline SVG in the theme's own colours (no
 *  image request, nothing for the app's Content-Security-Policy to refuse).
 *  Pass `loading` while it is still unknown whether one exists, so a row does
 *  not flash a "missing" picture on its way to showing the real one. */
export default function ThumbPlaceholder({
  className = 'h-10',
  loading = false
}: {
  className?: string
  loading?: boolean
}): JSX.Element {
  if (loading) {
    return <div className={`${className} aspect-video rounded shrink-0 bg-raised/60 animate-pulse`} aria-hidden />
  }
  return (
    <svg
      viewBox="0 0 160 90"
      role="img"
      aria-label={t('No thumbnail')}
      className={`${className} aspect-video rounded shrink-0 text-muted bg-raised/60 border border-raised`}
      preserveAspectRatio="xMidYMid slice"
    >
      <title>{t('No thumbnail')}</title>
      <circle cx="112" cy="30" r="9" fill="currentColor" opacity="0.35" />
      <path d="M20 72 L58 34 L82 58 L100 42 L140 72 Z" fill="currentColor" opacity="0.28" />
      <path d="M58 34 L72 48 L64 52 L58 46 L48 56 Z" fill="currentColor" opacity="0.18" />
    </svg>
  )
}
