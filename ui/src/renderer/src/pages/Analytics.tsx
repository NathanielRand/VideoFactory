import { Cost } from '../components/QuotaMeter'
import { readUnits } from '../lib/quota'
import ThumbPlaceholder from '../components/ThumbPlaceholder'
import { Bars } from '../components/AnalyticsSnapshot'
import { compact, useAnalytics, VIA_LABEL, type Tally } from '../lib/analytics'
import { t } from '../lib/i18n'
import { platformLabel } from '../lib/uploadpost'

const open = (url: string) => (): void => void window.studio.openExternal(url)

function Card({ title, children, hint }: { title: string; children: React.ReactNode; hint?: string }): JSX.Element {
  return (
    <section className="card space-y-3" aria-label={title}>
      <div>
        <h2 className="font-semibold">{title}</h2>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

/** Rows of the same four numbers, one per group, with a bar for views. */
function Breakdown({ rows }: { rows: { label: string; tally: Tally }[] }): JSX.Element {
  const max = Math.max(1, ...rows.map((r) => r.tally.views))
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-xs text-muted border-b border-raised/60">
          <th className="py-1.5 pr-2 font-normal" />
          <th className="py-1.5 px-2 font-normal text-right">{t('Videos')}</th>
          <th className="py-1.5 px-2 font-normal text-right">{t('Views')}</th>
          <th className="py-1.5 px-2 font-normal text-right">{t('Avg views')}</th>
          <th className="py-1.5 px-2 font-normal text-right">{t('Likes')}</th>
          <th className="py-1.5 pl-2 font-normal text-right">{t('Comments')}</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-raised/40">
        {rows.map((r) => (
          <tr key={r.label}>
            <td className="py-1.5 pr-2">
              <p className="truncate max-w-xs">{r.label}</p>
              <div className="h-1 bg-raised rounded mt-1 max-w-xs">
                <div className="h-1 bg-accent rounded" style={{ width: `${(r.tally.views / max) * 100}%` }} />
              </div>
            </td>
            <td className="py-1.5 px-2 text-right tabular-nums">{r.tally.videos}</td>
            <td className="py-1.5 px-2 text-right tabular-nums">{compact(r.tally.views)}</td>
            <td className="py-1.5 px-2 text-right tabular-nums">
              {r.tally.videos ? compact(Math.round(r.tally.views / r.tally.videos)) : '—'}
            </td>
            <td className="py-1.5 px-2 text-right tabular-nums">{compact(r.tally.likes)}</td>
            <td className="py-1.5 pl-2 text-right tabular-nums">{compact(r.tally.comments)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Everything the connected sources tell us, in one place, split by what the
 *  content is. YouTube is the only source that reports viewing numbers, so
 *  the other providers appear as post counts, never as invented views. */
export default function Analytics(): JSX.Element {
  const { data, error, loading, reload } = useAnalytics()
  const tot = data?.totals
  const subsBy: Record<string, number> = Object.fromEntries(
    (data?.subscribers?.top ?? []).map((v) => [v.video_id, v.gained])
  )

  return (
    <div className="p-6 space-y-5 w-full">
      <div className="flex items-baseline gap-3 flex-wrap">
        <h1 className="text-xl font-bold">{t('Analytics')}</h1>
        <p className="text-sm text-muted">
          {t('Views, likes and comments from every connected source, by kind of content.')}
        </p>
        <button className="btn-ghost !py-1 text-xs ml-auto" disabled={loading} onClick={() => reload(true)}>
          {loading ? t('Checking…') : `↻ ${t('Refresh')}`}
          <Cost units={readUnits(200)} />
        </button>
      </div>

      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
      {!data && !error && <p className="text-sm text-muted">{t('Loading…')}</p>}

      {data && (
        <>
          {!data.youtube.connected && (
            <div className="card text-sm text-muted">
              {data.youtube.error ||
                t('Connect YouTube in Settings to see views, likes and comments. WoopSocial and Upload-Post only report whether a post went out, so those appear below as post counts.')}
            </div>
          )}
          {data.youtube.error && data.youtube.connected && (
            <div className="card border-warn/40 text-warn text-sm">{data.youtube.error}</div>
          )}

          {tot && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                {[
                  [t('Views'), compact(tot.views)],
                  [t('Likes'), compact(tot.likes)],
                  [t('Comments'), compact(tot.comments)],
                  [t('Live videos'), String(tot.videos)],
                  [t('Average views'), compact(tot.average_views)]
                ].map(([label, value]) => (
                  <div key={label} className="card !p-3">
                    <p className="text-xs text-muted">{label}</p>
                    <p className="text-2xl font-bold tabular-nums">{value}</p>
                  </div>
                ))}
              </div>

              {data.daily && (
                <Card
                  title={t('Watch time, last 30 days')}
                  hint={t('From YouTube Analytics: what happened each day, whichever video was watched.')}
                >
                  {data.daily.days.length === 0 ? (
                    <p className="text-sm text-warn">
                      {data.daily.error || t('YouTube has no daily numbers yet.')}
                      {' '}
                      {t('Reconnect YouTube in Settings if this is about a missing permission.')}
                    </p>
                  ) : (
                    <>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                        {[
                          [t('Views'), compact(data.daily.days.reduce((n, d) => n + d.views, 0))],
                          [t('Hours watched'), compact(Math.round(data.daily.days.reduce((n, d) => n + d.minutes, 0) / 60))],
                          [t('Subscribers gained'), compact(data.daily.days.reduce((n, d) => n + d.subscribers, 0))],
                          [t('Average view'), `${Math.round(data.daily.days.reduce((n, d) => n + d.average_view_seconds * d.views, 0) / Math.max(1, data.daily.days.reduce((n, d) => n + d.views, 0)))}s`]
                        ].map(([label, value]) => (
                          <div key={label}>
                            <p className="text-xs text-muted">{label}</p>
                            <p className="text-lg font-semibold tabular-nums">{value}</p>
                          </div>
                        ))}
                      </div>
                      <Bars
                        tall
                        data={data.daily.days.map((d) => ({ date: d.date, videos: 1, views: d.views, likes: d.likes, comments: d.comments }))}
                      />
                    </>
                  )}
                </Card>
              )}

              {data.subscribers && (
                <Card
                  title={t('Subscribers')}
                  hint={t('YouTube credits a new subscriber to a video when they subscribe while watching it or from its page. The total is rounded by YouTube.')}
                >
                  {(() => {
                    const sd = data.daily?.days ?? []
                    const gained = sd.reduce((n, d) => n + d.subscribers, 0)
                    const lost = sd.reduce((n, d) => n + d.subscribers_lost, 0)
                    return (
                      <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                          {[
                            [t('Subscribers'), data.subscribers.hidden ? t('Hidden') : compact(data.subscribers.total)],
                            [t('Gained, 30 days'), sd.length ? `+${compact(gained)}` : '—'],
                            [t('Lost, 30 days'), sd.length ? `-${compact(lost)}` : '—'],
                            [t('Net, 30 days'), sd.length ? `${gained - lost >= 0 ? '+' : ''}${compact(gained - lost)}` : '—']
                          ].map(([label, value]) => (
                            <div key={label}>
                              <p className="text-xs text-muted">{label}</p>
                              <p className="text-lg font-semibold tabular-nums">{value}</p>
                            </div>
                          ))}
                        </div>
                        {data.subscribers.error && data.subscribers.top.length === 0 && (
                          <p className="text-sm text-warn">
                            {data.subscribers.error} {t('Reconnect YouTube in Settings if this is about a missing permission.')}
                          </p>
                        )}
                        {data.subscribers.top.length > 0 && (
                          <div>
                            <p className="text-xs text-muted mb-1">
                              {t('Videos that brought the most subscribers, last')} {data.subscribers.days} {t('days')}
                            </p>
                            <ul className="divide-y divide-raised/40 text-sm">
                              {data.subscribers.top.map((v) => (
                                <li key={v.video_id} className="flex items-center gap-3 py-1.5 flex-wrap">
                                  {v.thumbnail ? (
                                    <img src={v.thumbnail} alt="" loading="lazy" className="h-10 aspect-video object-cover rounded bg-raised" />
                                  ) : (
                                    <ThumbPlaceholder className="h-10" />
                                  )}
                                  <button onClick={open(v.url)} className="flex-1 min-w-40 truncate text-left hover:text-accent" title={v.title}>
                                    {v.title}
                                  </button>
                                  <span className="text-xs text-muted">
                                    {v.kind === 'clip' ? t('Clip') : v.kind === 'compilation' ? t('Compilation') : t('Other')}
                                  </span>
                                  <span className="tabular-nums w-16 text-right text-success font-medium">+{v.gained}</span>
                                  <span className="tabular-nums w-14 text-right text-muted" title={t('Subscribers lost on this video')}>
                                    {v.lost ? `-${v.lost}` : ''}
                                  </span>
                                  <span
                                    className="tabular-nums w-24 text-right text-xs text-muted"
                                    title={t('Subscribers gained per 1,000 views')}
                                  >
                                    {v.views ? `${((v.gained / v.views) * 1000).toFixed(1)} /1K ${t('views')}` : ''}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {!data.subscribers.error && data.subscribers.top.length === 0 && (
                          <p className="text-sm text-muted">{t('No video has brought a subscriber in this period yet.')}</p>
                        )}
                      </>
                    )
                  })()}
                </Card>
              )}

              {data.timeline && (
                <Card
                  title={t('Last 30 days')}
                  hint={t('Views so far of the videos that went live each day, by publish date. Hover a bar for the day.')}
                >
                  <Bars data={data.timeline} tall />
                </Card>
              )}

              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                {data.by_type && (
                  <Card title={t('By content type')} hint={t('What you made here, against everything else on the channel.')}>
                    <Breakdown
                      rows={[
                        { label: t('Clips'), tally: data.by_type.clip },
                        { label: t('Compilations'), tally: data.by_type.compilation },
                        { label: t('Not made in this app'), tally: data.by_type.other }
                      ]}
                    />
                  </Card>
                )}
                {data.by_format && (
                  <Card title={t('By format')} hint={t('Shorts are videos of three minutes or less.')}>
                    <Breakdown
                      rows={[
                        { label: t('Shorts'), tally: data.by_format.short },
                        { label: t('Longer videos'), tally: data.by_format.long }
                      ]}
                    />
                  </Card>
                )}
                {data.by_via && (
                  <Card title={t('By how it was posted')}>
                    <Breakdown
                      rows={Object.entries(data.by_via).map(([k, v]) => ({ label: t(VIA_LABEL[k] ?? k), tally: v }))}
                    />
                  </Card>
                )}
                {data.by_channel && Object.keys(data.by_channel).length > 1 && (
                  <Card title={t('By channel')}>
                    <Breakdown
                      rows={Object.entries(data.by_channel).map(([k, v]) => ({ label: k || t('Channel'), tally: v }))}
                    />
                  </Card>
                )}
              </div>

              {data.top && data.top.length > 0 && (
                <Card title={t('Top videos')}>
                  <ul className="divide-y divide-raised/40 text-sm">
                    {data.top.map((v) => (
                      <li key={v.video_id} className="flex items-center gap-3 py-1.5">
                        {v.thumbnail ? (
                          <img src={v.thumbnail} alt="" loading="lazy" className="h-10 aspect-video object-cover rounded bg-raised" />
                        ) : (
                          <ThumbPlaceholder className="h-10" />
                        )}
                        <button onClick={open(v.url)} className="flex-1 min-w-0 truncate text-left hover:text-accent" title={v.title}>
                          {v.title}
                        </button>
                        <span className="text-xs text-muted">
                          {v.kind === 'clip' ? t('Clip') : v.kind === 'compilation' ? t('Compilation') : t('Other')}
                          {v.short ? ` · ${t('Short')}` : ''}
                        </span>
                        <span className="tabular-nums w-16 text-right">{compact(v.views)}</span>
                        <span className="tabular-nums w-14 text-right text-muted">{compact(v.likes)} ♥</span>
                        <span className="tabular-nums w-14 text-right text-muted">{compact(v.comments)} 💬</span>
                        {subsBy[v.video_id] > 0 && (
                          <span className="tabular-nums w-16 text-right text-success text-xs" title={t('Subscribers gained (last 90 days)')}>
                            +{subsBy[v.video_id]} {t('subs')}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}

              {data.states && (
                <Card title={t('Status on YouTube')}>
                  <div className="flex gap-2 flex-wrap text-xs">
                    {Object.entries(data.states).map(([k, n]) => (
                      <span key={k} className="px-2.5 py-1 rounded-md bg-raised">
                        {k} <span className="tabular-nums opacity-70">{n}</span>
                      </span>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}

          <Card
            title={t('Posts by platform')}
            hint={t('From this app’s own record of what each provider took. These sources report posting, not viewing.')}
          >
            {Object.keys(data.posts).length === 0 ? (
              <p className="text-sm text-muted">{t('Nothing has been published from here yet.')}</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-muted border-b border-raised/60">
                    <th className="py-1.5 pr-2 font-normal">{t('Platform')}</th>
                    <th className="py-1.5 px-2 font-normal">{t('Through')}</th>
                    <th className="py-1.5 px-2 font-normal text-right">{t('Published')}</th>
                    <th className="py-1.5 px-2 font-normal text-right">{t('Scheduled')}</th>
                    <th className="py-1.5 pl-2 font-normal text-right">{t('Failed')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-raised/40">
                  {Object.entries(data.posts).map(([platform, p]) => (
                    <tr key={platform}>
                      <td className="py-1.5 pr-2">{platformLabel(platform)}</td>
                      <td className="py-1.5 px-2 text-xs text-muted">
                        {p.providers.map((x) => t(VIA_LABEL[x] ?? x)).join(', ') || '—'}
                      </td>
                      <td className="py-1.5 px-2 text-right tabular-nums">{p.published}</td>
                      <td className="py-1.5 px-2 text-right tabular-nums">{p.scheduled}</td>
                      <td className={`py-1.5 pl-2 text-right tabular-nums ${p.failed ? 'text-red-400' : ''}`}>{p.failed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title={t('Thumbnails')}>
            <p className="text-sm">
              {data.thumbnails.with} {t('of')} {data.thumbnails.total} {t('rendered clips and compilations have a thumbnail.')}
            </p>
          </Card>
        </>
      )}
    </div>
  )
}
