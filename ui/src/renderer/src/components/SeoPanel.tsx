import { useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import { publishingApi, type SeoDraft, type SeoReport } from '../lib/publishing'

/** A live grade of the draft against what search and the feed reward.
 *
 *  Every point lost comes with the sentence that says how to earn it back, so
 *  this is a checklist, not a mystery number. Graded on the server
 *  (publish/seo.py) so the rules live in one place. */
export default function SeoPanel({ draft }: { draft: SeoDraft }): JSX.Element | null {
  const [report, setReport] = useState<SeoReport | null>(null)
  const key = JSON.stringify(draft)

  useEffect(() => {
    let alive = true
    // Debounced: grading on every keystroke would be one request per letter.
    const timer = setTimeout(() => {
      publishingApi
        .check(draft)
        .then((r) => alive && setReport(r))
        .catch(() => alive && setReport(null))
    }, 400)
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [key])

  if (!report) return null
  const colour =
    report.score >= 85 ? 'text-success' : report.score >= 60 ? 'text-warn' : 'text-error'
  const todo = report.tips.filter((tip) => tip.level !== 'good')
  const done = report.tips.filter((tip) => tip.level === 'good')

  return (
    <details className="border border-raised/60 rounded-lg" open={todo.length > 0}>
      <summary className="px-3 py-2 text-xs cursor-pointer hover:bg-raised/40 rounded-lg flex items-center gap-2">
        <span className="font-medium">{t('Search & reach')}</span>
        <span className={`tabular-nums font-semibold ${colour}`}>{report.score}/100</span>
        {todo.length > 0 && (
          <span className="text-muted">
            · {todo.length} {todo.length === 1 ? t('suggestion') : t('suggestions')}
          </span>
        )}
      </summary>
      <ul className="px-3 pb-3 space-y-1 text-xs">
        {todo.map((tip) => (
          <li key={tip.message} className="flex gap-2">
            <span className={tip.level === 'bad' ? 'text-error' : 'text-warn'} aria-hidden>
              {tip.level === 'bad' ? '✕' : '!'}
            </span>
            <span>{tip.message}</span>
          </li>
        ))}
        {done.map((tip) => (
          <li key={tip.message} className="flex gap-2 text-muted">
            <span className="text-success" aria-hidden>
              ✓
            </span>
            <span>{tip.message}</span>
          </li>
        ))}
      </ul>
    </details>
  )
}
