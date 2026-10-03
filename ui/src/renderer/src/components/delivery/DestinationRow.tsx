import { useState } from 'react'
import { exportsApi, type Destination } from '../../lib/exports'
import Switch from '../Switch'
import { Folder } from '../icons'
import { t } from '../../lib/i18n'

export const PROVIDER_ICON: Record<string, string> = {
  onedrive: '☁',
  google_drive: '▲',
  dropbox: '◆',
  icloud: '☁',
  box: '▣'
}

/** One export destination: where it is, whether new work goes there on its
 *  own, and the checks and actions that belong to it. */
export default function DestinationRow({
  dest,
  onChanged
}: {
  dest: Destination
  onChanged: () => void
}): JSX.Element {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [confirm, setConfirm] = useState(false)

  const act = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setNote(null)
    try {
      await fn()
      onChanged()
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const check = async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await exportsApi.checkDestination(dest.id)
      setNote(r.ok ? { ok: true, text: t('Ready: files can be written there.') } : { ok: false, text: r.problem })
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const icon =
    dest.kind === 'rclone' ? '⇅' : dest.kind === 'cloud_folder' ? (PROVIDER_ICON[dest.provider] ?? '☁') : null
  const small = 'btn-ghost !px-2.5 !py-1 text-xs'

  return (
    <div className="rounded-lg bg-raised/40 px-3 py-2.5 space-y-2">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-raised text-accent">
          {icon ?? <Folder size={15} />}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-medium text-sm truncate">
            {dest.name}
            {dest.kind === 'rclone' && (
              <span className="ml-2 text-[10px] uppercase tracking-wide text-muted">
                rclone{dest.provider ? ` · ${dest.provider}` : ''}
              </span>
            )}
          </p>
          <p className="text-xs text-muted truncate" title={dest.target}>
            {dest.target}
          </p>
        </div>
        <div className="flex items-center gap-4 flex-wrap text-xs">
          <label className="flex items-center gap-2" title={t('Every new clip goes here as soon as it is made.')}>
            <Switch
              size="sm"
              checked={dest.auto_clips}
              disabled={busy}
              label={t('Send new clips automatically')}
              onChange={(on) => void act(() => exportsApi.patchDestination(dest.id, { auto_clips: on }))}
            />
            {t('New clips')}
          </label>
          <label
            className="flex items-center gap-2"
            title={t('Every new compilation render goes here as soon as it is done.')}
          >
            <Switch
              size="sm"
              checked={dest.auto_compilations}
              disabled={busy}
              label={t('Send new compilations automatically')}
              onChange={(on) =>
                void act(() => exportsApi.patchDestination(dest.id, { auto_compilations: on }))
              }
            />
            {t('New compilations')}
          </label>
        </div>
        <div className="flex items-center gap-1.5">
          {dest.kind !== 'rclone' && window.studio?.openFolder && (
            <button className={small} onClick={() => void window.studio?.openFolder?.(dest.target)}>
              {t('Open')}
            </button>
          )}
          <button className={small} disabled={busy} onClick={() => void check()}>
            {t('Check')}
          </button>
          {confirm ? (
            <>
              <button
                className={`${small} !text-error`}
                disabled={busy}
                onClick={() => void act(() => exportsApi.deleteDestination(dest.id))}
              >
                {t('Remove')}
              </button>
              <button className={small} onClick={() => setConfirm(false)}>
                {t('Cancel')}
              </button>
            </>
          ) : (
            <button
              className={`${small} hover:!text-error`}
              title={t('Stop using this destination. Files already there stay.')}
              onClick={() => setConfirm(true)}
            >
              ✕
            </button>
          )}
        </div>
      </div>
      {confirm && (
        <p className="text-xs text-muted">
          {t('Remove this destination? Files already sent there stay where they are.')}
        </p>
      )}
      {note && <p className={`text-xs ${note.ok ? 'text-success' : 'text-error'}`}>{note.text}</p>}
    </div>
  )
}
