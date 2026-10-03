import { useRef, useState } from 'react'
import { exportsApi, type Destination, type SendItem } from '../../lib/exports'
import { Folder } from '../icons'
import Popover from '../Popover'
import { PROVIDER_ICON } from './DestinationRow'
import { t } from '../../lib/i18n'

/** "Send to…": every saved destination, then any folder, chosen now. */
export default function SendMenu({
  items,
  destinations,
  onSent,
  label = 'Send to…',
  className = 'btn-ghost !px-2.5 !py-1 text-xs'
}: {
  items: SendItem[]
  destinations: Destination[]
  onSent: (message: string) => void
  label?: string
  className?: string
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)

  const send = async (to: { destination_id: number } | { folder: string }, where: string): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      const r = await exportsApi.send(items, to)
      setOpen(false)
      onSent(`${r.queued.length} ${t(r.queued.length === 1 ? 'file' : 'files')} ${t('on the way to')} ${where}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const chooseFolder = async (): Promise<void> => {
    const folder = await window.studio?.pickFolder()
    if (folder) await send({ folder }, folder)
  }

  const row = 'w-full flex items-center gap-2 text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised disabled:opacity-50'
  return (
    <div className="relative inline-block">
      <button
        ref={trigger}
        className={className}
        disabled={busy || items.length === 0}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {t(label)}
      </button>
      <Popover anchor={trigger} open={open} onClose={() => setOpen(false)} width={288} align="end" label={t(label)}>
          {destinations.map((d) => (
            <button
              key={d.id}
              className={row}
              disabled={busy}
              onClick={() => void send({ destination_id: d.id }, d.name)}
            >
              <span className="w-4 text-center text-accent">
                {d.kind === 'folder' ? <Folder size={13} /> : d.kind === 'rclone' ? '⇅' : (PROVIDER_ICON[d.provider] ?? '☁')}
              </span>
              <span className="truncate">{d.name}</span>
            </button>
          ))}
          {destinations.length > 0 && <div className="border-t border-raised/60 my-1" />}
          <button className={row} disabled={busy} onClick={() => void chooseFolder()}>
            <span className="w-4 text-center text-muted">＋</span>
            {t('Choose a folder…')}
          </button>
          {error && <p className="text-xs text-error px-2 py-1">{error}</p>}
      </Popover>
    </div>
  )
}
