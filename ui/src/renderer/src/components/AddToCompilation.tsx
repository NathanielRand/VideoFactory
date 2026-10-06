import { useEffect, useRef, useState } from 'react'
import { compilationsApi, type Compilation } from '../lib/compilations'
import { t } from '../lib/i18n'
import Popover from './Popover'

/** Send a clip — or a whole upload — into a compilation, from wherever it is.
 *
 *  The bridge between the two halves of the Editor: a moment the AI found in
 *  the Clips tab can become a compilation segment without re-scrubbing the
 *  source for it in the Compilations tab. No range means the whole video,
 *  which is what an upload of a ready-made clip wants. */
export default function AddToCompilation({
  videoId,
  start,
  end,
  label,
  className = 'btn-ghost !py-1 !px-3 text-xs',
  segments
}: {
  videoId: string
  start?: number
  end?: number
  /** Several ranges at once, added in order; replaces the one above. */
  segments?: { video_id: string; start?: number; end?: number }[]
  label?: string
  className?: string
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const [list, setList] = useState<Compilation[]>([])
  const [newTitle, setNewTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Compilation | null>(null)
  const [error, setError] = useState('')
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    compilationsApi
      .list()
      .then(setList)
      .catch(() => setList([]))
  }, [open])

  // A different clip selected: the last confirmation was about another one.
  useEffect(() => {
    setDone(null)
    setError('')
  }, [videoId, start, end, segments?.length])

  const add = async (comp: Compilation | null): Promise<void> => {
    if (busy) return // Enter in the title box skips the button's own lock
    setBusy(true)
    setError('')
    try {
      const target =
        comp ?? (await compilationsApi.create(newTitle.trim() || t('Untitled compilation')))
      const segs = segments ?? [start === undefined ? { video_id: videoId } : { video_id: videoId, start, end }]
      let last = target
      for (const seg of segs) last = await compilationsApi.appendSegment(target.id, seg)
      setDone(last)
      setNewTitle('')
      setOpen(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const editable = list.filter((c) => c.status !== 'queued' && c.status !== 'rendering')

  return (
    <div className="relative inline-flex items-center gap-2 flex-wrap">
      <button
        ref={trigger}
        className={className}
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        disabled={busy}
      >
        ▦ {label ? t(label) : t('Add to compilation')}
      </button>
      {done && (
        <span className="text-xs text-accent">
          {t('Added to')} {done.title} ·{' '}
          <button
            className="underline hover:text-ink"
            onClick={() =>
              window.dispatchEvent(new CustomEvent('open-compilation', { detail: done.id }))
            }
          >
            {t('Open')}
          </button>
        </span>
      )}
      {error && <span className="text-xs text-error">{error}</span>}
      <Popover
        anchor={trigger}
        open={open}
        onClose={() => setOpen(false)}
        width={288}
        className="card !p-2 space-y-1 shadow-xl"
        label={t('Add to compilation')}
      >
          {editable.length === 0 && (
            <p className="text-xs text-muted px-2 py-1">{t('No compilations yet.')}</p>
          )}
          <div className="max-h-56 overflow-y-auto space-y-0.5">
            {editable.map((c) => (
              <button
                key={c.id}
                className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised truncate"
                disabled={busy}
                onClick={() => void add(c)}
              >
                {c.title}{' '}
                <span className="text-xs text-muted">
                  ({(c.recipe.segments ?? []).length} {t('segments')})
                </span>
              </button>
            ))}
          </div>
          <div className="flex gap-1.5 pt-1 border-t border-raised/60">
            <input
              className="input !py-1 text-sm flex-1"
              placeholder={t('New compilation title')}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void add(null)}
            />
            <button
              className="btn-accent !py-1 !px-2.5 text-xs"
              disabled={busy}
              onClick={() => void add(null)}
            >
              {t('New')}
            </button>
          </div>
      </Popover>
    </div>
  )
}
