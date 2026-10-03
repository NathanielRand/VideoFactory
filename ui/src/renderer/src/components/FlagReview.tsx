import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { LearningProposal } from '../lib/types'

const KIND: Record<LearningProposal['kind'], (value: string) => string> = {
  pad_lead: (v) => `Start every clip ${v}s earlier`,
  pad_tail: (v) => `End every clip ${v}s later`,
  crop: (v) => `Default to ${v} framing`,
  min_score_delta: (v) => `${Number(v) > 0 ? 'Raise' : 'Lower'} the quality bar by ${Math.abs(Number(v))}`,
  guidance: (v) => `Tell the model: “${v}”`
}

/** What the model suggests from this creator's flags and notes. Nothing here
 *  changes a run until it is approved, and an approved one can be taken back
 *  (server/review_api.py, creator/reviewer.py). */
export default function FlagReview({
  creatorId,
  flagCount,
  onChange
}: {
  creatorId: number
  flagCount: number
  onChange: () => void
}): JSX.Element {
  const [items, setItems] = useState<LearningProposal[]>([])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    try {
      setItems((await api.proposals(creatorId)).proposals.filter((p) => p.status !== 'rejected'))
    } catch {
      setItems([])
    }
  }, [creatorId])

  useEffect(() => {
    setNote('')
    void load()
  }, [load])

  const run = async (fn: () => Promise<unknown>): Promise<void> => {
    setBusy(true)
    setNote('')
    try {
      await fn()
      await load()
      onChange()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const review = (): Promise<void> =>
    run(async () => {
      const r = await api.reviewFlags(creatorId)
      if (!r.created.length) setNote(r.reason || 'Nothing new to suggest.')
    })

  const pending = items.filter((p) => p.status === 'pending')
  const approved = items.filter((p) => p.status === 'approved')

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <h4 className="text-sm font-semibold">Suggestions from your flags</h4>
        <button
          className="btn-ghost !py-0.5 !px-2 text-xs"
          disabled={busy || flagCount < 2}
          onClick={() => void review()}
          title={flagCount < 2 ? 'Flag at least 2 clips from this creator first' : undefined}
        >
          {busy ? 'Reading…' : 'Review my flags'}
        </button>
      </div>
      <p className="text-xs text-muted mb-1">
        {flagCount
          ? `${flagCount} flagged clip(s). The AI reads your flags and notes and suggests changes; nothing applies until you approve it.`
          : 'Flag clips that come out wrong and they show up here as suggestions.'}
      </p>
      {note && <p className="text-xs text-amber-400 mb-1">{note}</p>}
      {pending.map((p) => (
        <div key={p.id} className="flex items-start gap-2 py-1 text-xs">
          <div className="flex-1">
            <div className="font-medium">{KIND[p.kind]?.(p.value) ?? `${p.kind}: ${p.value}`}</div>
            {p.rationale && <div className="text-muted">{p.rationale}</div>}
          </div>
          <button
            className="btn-accent !py-0.5 !px-2"
            disabled={busy}
            onClick={() => void run(() => api.approveProposal(p.id))}
          >
            Approve
          </button>
          <button
            className="btn-ghost !py-0.5 !px-2"
            disabled={busy}
            onClick={() => void run(() => api.rejectProposal(p.id))}
          >
            Dismiss
          </button>
        </div>
      ))}
      {approved.length > 0 && (
        <div className="mt-1">
          <div className="text-xs text-muted">In effect for the next videos:</div>
          {approved.map((p) => (
            <div key={p.id} className="flex items-center gap-2 py-0.5 text-xs">
              <span className="flex-1">{KIND[p.kind]?.(p.value) ?? `${p.kind}: ${p.value}`}</span>
              <button
                className="text-muted hover:text-red-400"
                disabled={busy}
                onClick={() => void run(() => api.rejectProposal(p.id))}
                title="Take this back"
              >
                Undo
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
