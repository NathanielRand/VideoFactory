import { useState } from 'react'
import { t } from '../lib/i18n'
import { autoThumbnail } from '../lib/thumbnails'
import { useThumbStatus } from './PublishThumb'

/** "These have no thumbnail": recommended, never required. Sits above a
 *  publish button and does not touch it; a post without one still goes out
 *  with the platform's own frame. Offers to make the missing ones with the AI
 *  designer. `ids` are clip ids (negative: compilations). */
export default function ThumbnailNudge({
  ids,
  onChanged
}: {
  ids: number[]
  onChanged?: () => void
}): JSX.Element | null {
  const [version, setVersion] = useState(0)
  const have = useThumbStatus(ids, version)
  const [busy, setBusy] = useState<{ done: number; total: number } | null>(null)
  const [error, setError] = useState('')

  const missing = ids.filter((id) => have[id] === false)
  if (missing.length === 0 && !busy) return null

  const run = async (): Promise<void> => {
    setError('')
    const failures: string[] = []
    for (let i = 0; i < missing.length; i++) {
      setBusy({ done: i, total: missing.length })
      try {
        await autoThumbnail(missing[i])
      } catch (e) {
        failures.push(e instanceof Error ? e.message : String(e))
      }
    }
    setBusy(null)
    if (failures.length) setError(`${failures.length} ${t('could not be made')}: ${failures[0]}`)
    setVersion((v) => v + 1)
    onChanged?.()
  }

  return (
    <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs space-y-1.5" role="status">
      <p className="text-warn">
        {ids.length === 1
          ? t('No thumbnail yet. Recommended: a good one is the biggest single lever on clicks.')
          : `${missing.length} ${t('of')} ${ids.length} ${t('have no thumbnail. Recommended, not required.')}`}
      </p>
      <div className="flex items-center gap-2">
        <button className="btn-ghost !py-1 text-xs" disabled={!!busy} onClick={() => void run()}>
          ✨{' '}
          {busy
            ? `${t('Generating…')} ${busy.done + 1}/${busy.total}`
            : ids.length === 1
              ? t('Generate one')
              : t('Generate missing')}
        </button>
        <span className="text-muted">{t('You can still publish without.')}</span>
      </div>
      {error && <p className="text-error">{error}</p>}
    </div>
  )
}
