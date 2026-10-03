import { compact, useAnalytics, type Analytics } from '../lib/analytics'
import { t } from '../lib/i18n'

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }): JSX.Element {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-muted truncate">{label}</p>
      <p className="text-lg font-semibold tabular-nums leading-tight">{value}</p>
      {sub && <p className="text-[10px] text-muted truncate">{sub}</p>}
    </div>
  )
}

/** Thirty days of "views of what went live that day" as thin bars. */
export function Bars({
  data,
  tall
}: {
  data: NonNullable<Analytics['timeline']>
  tall?: boolean
}): JSX.Element {
  const max = Math.max(1, ...data.map((d) => d.views))
  return (
    <div className={`flex items-end gap-px w-full ${tall ? 'h-32' : 'h-10'}`}>
      {data.map((d) => (
        <div
          key={d.date}
          title={`${d.date}: ${compact(d.views)} ${t('views')} · ${d.videos} ${t('videos')}`}
          className="flex-1 bg-accent/60 rounded-sm min-h-px"
          style={{ height: `${Math.max(2, (d.views / max) * 100)}%`, opacity: d.videos ? 1 : 0.2 }}
        />
      ))}
    </div>
  )
}

/** A quick look at how things are doing, with a way into the full page.
 *  Home leads with performance; Publish leads with what is going out and
 *  whether it is ready (thumbnails, failures), since that is what someone on
 *  that page is deciding. */
export default function AnalyticsSnapshot({
  variant,
  onOpen
}: {
  variant: 'home' | 'publish'
  onOpen: () => void
}): JSX.Element {
  const { data, error, loading } = useAnalytics()
  if (!data) {
    return (
      <section className="card !p-3 text-xs text-muted" aria-label={t('Analytics')}>
        {error ? `${t('Analytics unavailable')}: ${error}` : loading ? t('Loading…') : ''}
      </section>
    )
  }
  const yt = data.youtube
  const posts = Object.values(data.posts)
  const scheduled = posts.reduce((n, p) => n + p.scheduled, 0)
  const failed = posts.reduce((n, p) => n + p.failed, 0)
  const sent = posts.reduce((n, p) => n + p.published, 0)
  const tot = data.totals
  const netSubs = (data.daily?.days ?? []).reduce((n, d) => n + d.subscribers - d.subscribers_lost, 0)
  const best = data.by_type
    ? (Object.entries(data.by_type) as [string, { videos: number; views: number }][])
        .filter(([, v]) => v.videos > 0)
        .map(([k, v]) => ({ k, avg: v.views / v.videos }))
        .sort((a, b) => b.avg - a.avg)[0]
    : undefined
  const kindLabel: Record<string, string> = {
    clip: t('Clips'),
    compilation: t('Compilations'),
    other: t('Other videos')
  }

  return (
    <section className="card !p-3 space-y-2" aria-label={t('Analytics snapshot')}>
      <div className="flex items-center gap-3">
        <h2 className="font-semibold text-sm">{variant === 'home' ? t('Snapshot') : t('Going out')}</h2>
        <button className="ml-auto text-xs text-accent hover:underline" onClick={onOpen}>
          {t('Full analytics')} →
        </button>
      </div>
      {variant === 'home' ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
          {tot ? (
            <>
              {data.subscribers?.total != null && (
                <Stat
                  label={t('Subscribers')}
                  value={compact(data.subscribers.total)}
                  sub={
                    data.daily?.days.length
                      ? `${netSubs >= 0 ? '+' : ''}${netSubs} ${t('in 30 days')}`
                      : undefined
                  }
                />
              )}
              <Stat label={t('Views')} value={compact(tot.views)} sub={`${tot.videos} ${t('live videos')}`} />
              <Stat label={t('Likes')} value={compact(tot.likes)} />
              <Stat label={t('Average views')} value={compact(tot.average_views)} />
              <Stat
                label={t('Best performing type')}
                value={best ? kindLabel[best.k] : '—'}
                sub={best ? `${compact(Math.round(best.avg))} ${t('views each')}` : undefined}
              />
              <div className="col-span-2 sm:col-span-1">{data.timeline && <Bars data={data.timeline} />}</div>
            </>
          ) : (
            <p className="col-span-full text-xs text-muted">
              {yt.error || t('Connect YouTube in Settings to see views and likes here.')}
            </p>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <Stat label={t('Scheduled')} value={String(scheduled)} sub={t('posts waiting')} />
          <Stat label={t('Sent')} value={String(sent)} sub={t('posts published')} />
          <Stat label={t('Failed')} value={String(failed)} sub={failed ? t('need a look') : t('none')} />
          <Stat
            label={t('With a thumbnail')}
            value={`${data.thumbnails.with}/${data.thumbnails.total}`}
            sub={data.thumbnails.without ? `${data.thumbnails.without} ${t('without')}` : t('all covered')}
          />
          <Stat
            label={t('Views so far')}
            value={tot ? compact(tot.views) : '—'}
            sub={tot ? `${compact(tot.average_views)} ${t('per video')}` : undefined}
          />
        </div>
      )}
    </section>
  )
}
