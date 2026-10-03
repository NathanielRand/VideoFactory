import { useState } from 'react'
import { bytes, exportsApi, type Transfer } from '../../lib/exports'
import { t } from '../../lib/i18n'

const STATE: Record<Transfer['state'], { text: string; tone: string }> = {
  queued: { text: 'Waiting', tone: 'text-muted' },
  sending: { text: 'Sending', tone: 'text-accent' },
  done: { text: 'Done', tone: 'text-success' },
  failed: { text: 'Failed', tone: 'text-error' }
}

/** Files on their way somewhere, with a bar for the one moving, and a retry
 *  for any that failed. Shared by the Local and Cloud pages, each passing
 *  the transfers that belong to it. */
export default function TransferList({
  transfers,
  onChanged,
  empty
}: {
  transfers: Transfer[]
  onChanged: () => void
  empty: string
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    try {
      await fn()
    } catch {
      /* the list re-reads either way */
    } finally {
      setBusy(false)
      onChanged()
    }
  }
  const finished = transfers.some((x) => x.state === 'done' || x.state === 'failed')
  const small = 'btn-ghost !px-2 !py-0.5 text-xs'

  if (transfers.length === 0) return <p className="text-sm text-muted">{t(empty)}</p>
  return (
    <div className="space-y-1.5">
      {transfers.map((x) => {
        const s = STATE[x.state]
        return (
          <div key={x.id} className="rounded-lg bg-raised/40 px-3 py-2 space-y-1">
            <div className="flex items-center gap-3 text-sm">
              <span className="min-w-0 flex-1 truncate" title={x.result || x.name}>
                {x.name}
                <span className="text-muted"> → {x.destination_name ?? x.target}</span>
              </span>
              <span className={`text-xs ${s.tone}`}>
                {t(s.text)}
                {x.state === 'sending' && ` ${x.percent}%`}
              </span>
              <span className="text-xs text-muted tabular-nums w-24 text-right">
                {x.state === 'sending' && x.bytes ? `${bytes(x.sent)} / ${bytes(x.bytes)}` : bytes(x.bytes)}
              </span>
              {x.state === 'failed' && (
                <button className={small} disabled={busy} onClick={() => void act(() => exportsApi.retry(x.id))}>
                  {t('Retry')}
                </button>
              )}
              {x.state === 'queued' && (
                <button
                  className={small}
                  disabled={busy}
                  title={t('Do not send this one')}
                  onClick={() => void act(() => exportsApi.drop(x.id))}
                >
                  ✕
                </button>
              )}
              {x.state === 'done' && x.via === 'copy' && window.studio?.showInFolder && (
                <button className={small} onClick={() => void window.studio?.showInFolder?.(x.result)}>
                  {t('Show')}
                </button>
              )}
            </div>
            {x.state === 'sending' && (
              <div className="h-1.5 rounded-full bg-raised overflow-hidden">
                <div
                  className="h-full bg-accent transition-[width] duration-500"
                  style={{ width: `${Math.max(2, x.percent)}%` }}
                />
              </div>
            )}
            {x.state === 'failed' && x.error && <p className="text-xs text-error">{x.error}</p>}
          </div>
        )
      })}
      {finished && (
        <button className={small} disabled={busy} onClick={() => void act(() => exportsApi.clearTransfers())}>
          {t('Clear finished')}
        </button>
      )}
    </div>
  )
}
