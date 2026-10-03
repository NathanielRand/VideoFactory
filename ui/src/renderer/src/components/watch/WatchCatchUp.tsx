import { useEffect, useRef, useState } from 'react'
import { api, errorText } from '../../lib/api'
import type { Watch } from '../../lib/types'
import { t } from '../../lib/i18n'

const BACKLOG: { id: Watch['backlog']; label: string }[] = [
  { id: 'newest', label: 'Only the newest one' },
  { id: 'all', label: 'All of them' },
  { id: 'day', label: 'The ones from the last 24 hours' },
  { id: 'none', label: 'None, I will pick' }
]

/** What a watch does with videos it finds late, and which it passes over as
 *  too short. Whatever the watch does with its videos, so kept apart from
 *  the clip and publish settings. Saves itself a moment after each change. */
export default function WatchCatchUp({
  watch,
  onSaved
}: {
  watch: Watch
  onSaved: () => void
}): JSX.Element {
  const [backlog, setBacklog] = useState(watch.backlog)
  const [minMinutes, setMinMinutes] = useState(watch.min_minutes)
  const [error, setError] = useState<string | null>(null)
  // Only clip-only watches skip short videos; one feeding a compilation or
  // the Library takes them, Shorts included.
  const skipsShort = watch.actions.clips && !watch.actions.compile

  const current = JSON.stringify([backlog, minMinutes])
  const lastSaved = useRef(current)
  useEffect(() => {
    if (current === lastSaved.current) return
    const id = setTimeout(() => {
      lastSaved.current = current
      api
        .patchWatch(watch.id, { backlog, min_minutes: minMinutes })
        .then(() => {
          setError(null)
          onSaved()
        })
        .catch((e) => setError(errorText(e)))
    }, 600)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current])

  return (
    <div className="flex gap-x-6 gap-y-3 flex-wrap items-end">
      <label className="text-sm space-y-1">
        <span className="label block">{t('Several posted while Video Factory was closed? Take')}</span>
        <select
          className="input !w-72"
          value={backlog}
          onChange={(e) => setBacklog(e.target.value as Watch['backlog'])}
        >
          {BACKLOG.map((b) => (
            <option key={b.id} value={b.id}>
              {t(b.label)}
            </option>
          ))}
        </select>
      </label>
      {skipsShort && (
        <label className="text-sm space-y-1">
          <span className="label block">{t('Skip videos shorter than (minutes)')}</span>
          <input
            type="number"
            min={0}
            max={600}
            className="input !w-24"
            value={minMinutes}
            onChange={(e) => setMinMinutes(Math.max(0, Number(e.target.value) || 0))}
          />
        </label>
      )}
      {error && <span className="text-sm text-error">{error}</span>}
    </div>
  )
}
