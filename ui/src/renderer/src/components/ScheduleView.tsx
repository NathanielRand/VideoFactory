import { useEffect, useState } from 'react'
import PublishThumb, { useThumbStatus } from './PublishThumb'
import { api } from '../lib/api'
import { cancelItem, scheduleApi, type ScheduleItem } from '../lib/schedule'
import { t } from '../lib/i18n'
import { platformLabel } from '../lib/uploadpost'

/** What is due, and what actually happened.
 *
 *  This exists because of a real run: 37 clips went out at once, five posted
 *  and thirty-two failed, and the app could say nothing about any of it. The
 *  rows sat at "processing" forever because nothing ever asked again, so a
 *  queue moving slowly looked exactly like a batch that had died. The answer
 *  was counting Shorts on YouTube by hand.
 *
 *  Refresh asks the provider what became of everything still in the air. It
 *  is a button rather than a poll because these runs stretch over days: a
 *  timer would spend its life asking about posts due on Thursday.
 */
const STATE_STYLE: Record<string, string> = {
  published: 'text-success',
  failed: 'text-error',
  skipped: 'text-muted',
  processing: 'text-warn',
  queued: 'text-warn',
  scheduled: 'text-warn'
}

/** `inline` renders it as a card on a page (Publish) rather than a modal. */
export default function ScheduleView({
  onClose,
  inline = false,
  embedded = false
}: {
  onClose?: () => void
  inline?: boolean
  /** Inside another card (the calendar's list view): no card, no title. */
  embedded?: boolean
}): JSX.Element {
  const [posts, setPosts] = useState<ScheduleItem[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  const load = async (): Promise<void> => {
    try {
      setPosts((await scheduleApi.list()).items)
    } catch (e) {
      setNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const refresh = async (): Promise<void> => {
    if (busy) return
    setBusy(true)
    setNote('')
    try {
      const got = await api.woopSocialRefresh()
      setNote(
        got.checked === 0
          ? t('Nothing is waiting.')
          : `${t('Checked')} ${got.checked}, ${t('updated')} ${got.updated}, ${got.still_waiting} ${t('still waiting')}.`
      )
      await load()
    } catch (e) {
      setNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const act = async (fn: () => Promise<string>): Promise<void> => {
    if (busy) return
    setBusy(true)
    setNote('')
    try {
      setNote(await fn())
      await load()
    } catch (e) {
      setNote(String(e).replace(/^Error:\s*/, ''))
    } finally {
      setBusy(false)
    }
  }

  const cancel = (p: ScheduleItem): void => {
    const also =
      p.cancel_group > 1
        ? ` ${t('The provider holds this post as one, so it is cancelled for all')} ${p.cancel_group} ${t('platforms')}.`
        : ''
    if (!window.confirm(`${t('Cancel')} “${p.title}”?${also} ${t('It will not be posted.')}`)) return
    void act(async () => {
      await cancelItem(p)
      return t('Cancelled.')
    })
  }

  const finished = posts.filter((p) => !p.waiting).length
  const haveThumb = useThumbStatus([...new Set(posts.map((p) => p.clip_id))])
  const done = posts.filter((p) => p.state === 'published').length
  const failed = posts.filter((p) => p.state === 'failed').length
  const waiting = posts.filter(
    (p) => p.state === 'queued' || p.state === 'processing' || p.state === 'sending'
  ).length

  const body = (
    <div
      className={`${embedded ? '' : 'card'} w-full space-y-3 overflow-hidden flex flex-col ${
        inline ? 'max-h-[70vh]' : 'max-w-2xl max-h-[85vh]'
      }`}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          {!embedded && <h3 className="font-semibold text-lg">{t('Posting schedule')}</h3>}
          <p className="text-xs text-muted mt-1">
            {posts.length
              ? `${done} ${t('posted')}, ${waiting} ${t('waiting')}, ${failed} ${t('failed')}.`
              : t('Nothing scheduled yet.')}
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          {finished > 0 && (
            <button
              className="btn-ghost !py-1 !px-3 text-sm"
              disabled={busy}
              title={t('Takes finished posts off this list. Their records are kept.')}
              onClick={() =>
                void act(async () => {
                  const got = await scheduleApi.clear()
                  return `${t('Cleared')} ${got.cleared}.`
                })
              }
            >
              {t('Clear finished')} ({finished})
            </button>
          )}
          <button className="btn-ghost !py-1 !px-3 text-sm" onClick={() => void refresh()}>
            {busy ? t('Checking…') : t('Refresh')}
          </button>
        </div>
      </div>

      {note && <p className="text-xs text-muted">{note}</p>}

      <div className="flex-1 overflow-y-auto border border-raised rounded-lg divide-y divide-raised">
        {loading && <p className="p-3 text-sm text-muted">{t('Loading…')}</p>}
        {!loading && posts.length === 0 && (
          <p className="p-3 text-sm text-muted">
            {t('Nothing is waiting to go out. Clips you publish on a schedule (WoopSocial, Upload-Post or straight to YouTube) appear here until they post.')}
          </p>
        )}
        {posts.map((p) => (
          <div
            key={`${p.kind}-${p.job_id ?? p.youtube_id ?? ''}-${p.clip_id}-${p.platform}`}
            className="flex items-center gap-3 px-3 py-2 text-sm"
          >
            <PublishThumb id={p.clip_id} has={haveThumb[p.clip_id]} className="h-8" />
            <span className="tabular-nums text-xs text-muted w-32 shrink-0">
              {p.scheduled_for ? new Date(p.scheduled_for).toLocaleString() : '-'}
            </span>
            <span className="flex-1 min-w-0 truncate" title={p.title}>
              {p.title}
            </span>
            <span className="text-xs text-muted shrink-0">{platformLabel(p.platform)}</span>
            <span className={`text-xs shrink-0 w-20 ${STATE_STYLE[p.state] || 'text-muted'}`}>
              {p.state}
            </span>
            {p.post_url ? (
              <button
                className="text-xs text-accent shrink-0"
                onClick={() => void window.studio.openExternal(p.post_url)}
              >
                {t('Open')}
              </button>
            ) : (
              <span className="w-10 shrink-0" />
            )}
            {p.can_cancel ? (
              <button
                className="text-xs text-error hover:underline shrink-0 w-14 text-right"
                disabled={busy}
                onClick={() => cancel(p)}
                title={t('Stop this post before it goes out')}
              >
                {t('Cancel')}
              </button>
            ) : p.waiting ? (
              <span
                className="text-[10px] text-muted shrink-0 w-14 text-right leading-tight"
                title={t('This provider holds the post. Cancel it in its own dashboard; removing it here would not stop it.')}
              >
                {t('Cancel at provider')}
              </span>
            ) : (
              <button
                className="text-xs text-muted hover:text-ink shrink-0 w-14 text-right"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await scheduleApi.clear([{ clip_id: p.clip_id, platform: p.platform }])
                    return t('Removed from the list.')
                  })
                }
                title={t('Take this off the list. The record is kept.')}
              >
                {t('Remove')}
              </button>
            )}
          </div>
        ))}
      </div>

      {failed > 0 && (
        <p className="text-xs text-warn">
          {t(
            'Failed posts are usually the daily limit: WoopSocial allows five YouTube posts a day. Publish the rest with a daily budget rather than all at once.'
          )}
        </p>
      )}

      {!inline && (
        <button className="btn-accent w-full !py-2" onClick={onClose}>
          {t('Done')}
        </button>
      )}
    </div>
  )
  if (inline) return body
  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="Posting schedule"
      onClick={onClose}
    >
      {body}
    </div>
  )
}
