import { t } from '../lib/i18n'
import { useQuota } from '../lib/quota'
import { platformLabel } from '../lib/uploadpost'

function Meter({ label, used, limit }: { label: string; used: number; limit: number }): JSX.Element {
  const pct = Math.min(100, (used / Math.max(1, limit)) * 100)
  return (
    <div className="space-y-1 min-w-0">
      <div className="flex justify-between gap-2 text-xs">
        <span className="text-muted truncate">{label}</span>
        <span className="tabular-nums">
          {used.toLocaleString()} / {limit.toLocaleString()}
        </span>
      </div>
      <div className="h-1.5 rounded bg-raised" role="progressbar" aria-valuenow={used} aria-valuemax={limit} aria-label={label}>
        <div
          className={`h-1.5 rounded ${pct >= 90 ? 'bg-error' : pct >= 70 ? 'bg-warn' : 'bg-accent'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** Today's YouTube quota and what is planned against each platform's daily
 *  cap. An estimate: another install on the same Google project spends from
 *  the same pool and is not seen here. */
export default function QuotaMeter(): JSX.Element | null {
  const { quota } = useQuota()
  if (!quota) return null
  const y = quota.youtube
  const over = quota.forecast.days.flatMap((d) => d.items.filter((i) => i.over).map((i) => ({ ...i, date: d.date })))
  const reset = new Date(y.resets_at)
  return (
    <section className="card !p-3 space-y-2" aria-label={t('Platform limits')}>
      <div className="flex items-baseline gap-2 flex-wrap">
        <h2 className="font-semibold text-sm">{t('Platform limits')}</h2>
        <span className="text-[11px] text-muted">
          {t('Estimate. Other installs on the same YouTube project spend from the same pool.')}
        </span>
      </div>
      {y.enabled && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Meter label={t('YouTube uploads today')} used={y.uploads_used} limit={y.uploads_limit} />
            <Meter label={t('YouTube units (quota points) today')} used={y.units_used} limit={y.units_limit} />
          </div>
          <p className="text-[11px] text-muted">
            {t('Resets')} {reset.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })} (
            {t('midnight Pacific')}).{' '}
            {quota.forecast.youtube_queued_uploads > 0 &&
              `${quota.forecast.youtube_queued_uploads} ${t('uploads still queued here will spend')} ${quota.forecast.youtube_queued_uploads} ${t('uploads and about')} ${quota.forecast.youtube_queued_units} ${t('units when they run.')}`}
          </p>
        </>
      )}
      {over.map((o) => (
        <p key={`${o.date}-${o.provider}-${o.platform}`} className="text-xs text-warn">
          {o.date}: {o.count} {t('posts to')} {platformLabel(o.platform)} {t('through')} {o.provider} {t('but the documented limit is')} {o.limit} {t('a day.')}
        </p>
      ))}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted hover:text-ink">{t('Limits and costs')}</summary>
        <ul className="mt-1 space-y-0.5 text-muted">
          <li>{t('Uploading a video: 1 of 100 uploads a day (its own bucket, no units).')}</li>
          {Object.entries(quota.costs)
            .filter(([, n]) => n >= 1)
            .map(([m, n]) => (
              <li key={m}>
                {m}: {n} {n === 1 ? t('unit') : t('units')}
              </li>
            ))}
          {quota.limits.map((l) => (
            <li key={`${l.provider}-${l.platform}`}>
              {platformLabel(l.platform)} {t('via')} {l.provider}: {l.per_day} {t('a day.')} {l.source}
            </li>
          ))}
          <li>{t('Other platforms: no published limit, so none is checked.')}</li>
        </ul>
      </details>
    </section>
  )
}

/** "≈50 units" on the button of an action that spends some. */
export function Cost({ units, uploads }: { units?: number; uploads?: number }): JSX.Element {
  const parts = [
    uploads ? `${uploads} ${uploads === 1 ? t('upload') : t('uploads')}` : '',
    units ? `≈${units} ${t('units')}` : ''
  ].filter(Boolean)
  return (
    <span className="opacity-70 font-normal ml-1" title={t('YouTube quota points, an estimate')}>
      · {parts.join(' + ')}
    </span>
  )
}
