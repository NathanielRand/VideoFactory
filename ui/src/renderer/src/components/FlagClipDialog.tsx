import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { Clip } from '../lib/types'

type Reason = { id: string; label: string; group: 'moment' | 'framing' | 'other' }

const GROUPS: { id: Reason['group']; title: string }[] = [
  { id: 'moment', title: 'The moment' },
  { id: 'framing', title: 'The framing' },
  { id: 'other', title: 'Other' }
]

/** Report a clip that came out wrong. The ticked reasons and a snapshot of what
 *  the pipeline decided are kept for review (server/flags_api.py). */
export default function FlagClipDialog({
  clip,
  onClose
}: {
  clip: Clip
  onClose: () => void
}): JSX.Element {
  const [reasons, setReasons] = useState<Reason[]>([])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [sent, setSent] = useState<Awaited<ReturnType<typeof api.flagClip>> | null>(null)
  const [fixed, setFixed] = useState<string[] | null>(null)

  useEffect(() => {
    api
      .flagReasons()
      .then((d) => setReasons(d.reasons))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const toggle = (id: string): void =>
    setPicked((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  const send = async (): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      setSent(await api.flagClip(clip.id, [...picked], note))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const fixNow = async (): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      setFixed((await api.recutClip(clip.id)).changes)
    } catch (e) {
      // The engine's own sentence, not "409 /clips/255/recut: {"detail": ...}".
      const raw = e instanceof Error ? e.message : String(e)
      setError(/"detail":\s*"([^"]+)"/.exec(raw)?.[1] ?? raw)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-6"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={t('Flag this clip')}
    >
      <div
        className="card w-full max-w-lg space-y-4 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <h3 className="font-semibold text-lg">{t('Flag this clip')}</h3>
          <p className="text-xs text-muted truncate">{clip.title || clip.hook || t('Untitled clip')}</p>
        </div>

        {sent ? (
          <>
            <p className="text-sm text-accent">
              {t('Thanks. Saved with what was decided for this clip, so it can be reviewed.')}
            </p>
            {/* This clip is not changed by flagging; future clips are, past a few flags. */}
            {sent.learning.applied.map((line) => (
              <p key={line} className="text-xs">
                {t('Learned')}: {line}
              </p>
            ))}
            {sent.learning.pending.map((line) => (
              <p key={line} className="text-xs text-muted">
                {line}
              </p>
            ))}
            {sent.learning.recorded.map((line) => (
              <p key={line} className="text-xs text-muted">
                {line}
              </p>
            ))}
            {fixed ? (
              <p className="text-sm text-accent">
                {fixed.length ? `${t('Re-rendering this clip')}: ${fixed.join(', ')}.` : ''}
              </p>
            ) : sent.recut.available ? (
              <p className="text-xs text-muted">
                {t('Flagging does not change this clip. Re-cutting it now would')}: {sent.recut.changes.join(', ')}.
              </p>
            ) : (
              <p className="text-xs text-muted">
                {t('Nothing you ticked can be re-cut automatically. Open the editor to adjust this clip by hand.')}
              </p>
            )}
            {error && <p className="text-sm text-red-400">{error}</p>}
            <div className="flex items-center gap-2">
              {!fixed && sent.recut.available && (
                <button className="btn-accent !py-1.5" disabled={busy} onClick={() => void fixNow()}>
                  {busy ? t('Working…') : t('Re-cut this clip now')}
                </button>
              )}
              <button className="btn-ghost !py-1.5" onClick={onClose} autoFocus>
                {t('Close')}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-xs text-muted">
              {t('What was wrong? Tick everything that applies.')}
            </p>
            {GROUPS.map((g) => (
              <fieldset key={g.id} className="space-y-1">
                <legend className="label">{t(g.title)}</legend>
                {reasons
                  .filter((r) => r.group === g.id)
                  .map((r) => (
                    <label key={r.id} className="flex items-center gap-2 text-sm py-0.5">
                      <input
                        type="checkbox"
                        checked={picked.has(r.id)}
                        onChange={() => toggle(r.id)}
                      />
                      {t(r.label)}
                    </label>
                  ))}
              </fieldset>
            ))}
            <label className="space-y-1 block">
              <span className="label">{t('Anything else? (optional)')}</span>
              <textarea
                className="input w-full"
                rows={3}
                maxLength={2000}
                value={note}
                placeholder={t('e.g. it missed the question that started the story')}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            {error && <p className="text-sm text-red-400">{error}</p>}
            <div className="flex items-center gap-2">
              <button
                className="btn-accent !py-1.5"
                disabled={busy || picked.size === 0}
                onClick={() => void send()}
              >
                {busy ? t('Saving…') : t('Flag clip')}
              </button>
              <button className="btn-ghost !py-1.5" onClick={onClose}>
                {t('Cancel')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
