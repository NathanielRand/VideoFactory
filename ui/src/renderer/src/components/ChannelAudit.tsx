import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { AuditReport, AuditResult, AuditVideo, YouTubeAccount } from '../lib/youtube'
import AsyncButton from './AsyncButton'
import { Cost } from './QuotaMeter'
import { readUnits } from '../lib/quota'

/** Metadata audit: checks every video on the channel against the monetization
 *  rules (hashtag spam, keyword stuffing, machine-sounding wording), shows what
 *  would change, and only changes a video when asked.
 *
 *  Three separate steps on purpose. Checking and proposing rewrites change
 *  nothing on YouTube. Applying edits PUBLIC videos, so it names the videos and
 *  the quota first, only sends what the report shows, skips a video edited
 *  since, and keeps the original so Undo can put it back. Applying needs the
 *  full YouTube permission (Settings → Update permissions). */

const UNITS_PER_VIDEO = 51
const BAD_TONE = new Set(['machine-tone', 'keyword-repeat', 'keyword-share', 'keyword-list'])

const scoreStyle = (n: number): string =>
  n >= 90 ? 'text-emerald-400' : n >= 60 ? 'text-amber-400' : 'text-red-400'

function Diff({ label, before, after }: { label: string; before: string; after: string }): JSX.Element | null {
  if (before === after) return null
  return (
    <div className="text-xs space-y-1">
      <div className="text-muted">{label}</div>
      <pre className="whitespace-pre-wrap rounded bg-red-500/10 px-2 py-1 text-red-300/90">{before || '—'}</pre>
      <pre className="whitespace-pre-wrap rounded bg-emerald-500/10 px-2 py-1 text-emerald-300/90">
        {after || '—'}
      </pre>
    </div>
  )
}

export default function ChannelAudit(): JSX.Element | null {
  const [ready, setReady] = useState<boolean | null>(null)
  const [accounts, setAccounts] = useState<YouTubeAccount[]>([])
  const [channel, setChannel] = useState('')
  const [report, setReport] = useState<AuditReport | null>(null)
  const [applied, setApplied] = useState<string[]>([])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(null)
  const [showClean, setShowClean] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .youtubeStatus()
      .then((s) => {
        setReady(Boolean(s.enabled && s.connected))
        const list = s.accounts ?? []
        setAccounts(list)
        setChannel(list.find((a) => a.default)?.id ?? list[0]?.id ?? '')
      })
      .catch(() => setReady(false))
  }, [])

  const take = useCallback((view: { report: AuditReport | null; applied: string[] }) => {
    setReport(view.report)
    setApplied(view.applied)
  }, [])

  useEffect(() => {
    if (!ready) return
    api
      .youtubeAuditLast(channel || undefined)
      .then(take)
      .catch(() => undefined)
  }, [ready, channel, take])

  const fail = (e: unknown): void => setError(e instanceof Error ? e.message : String(e))
  const summarize = (results: AuditResult[], verb: string): string => {
    const n = (s: AuditResult['status']): number => results.filter((r) => r.status === s).length
    const problems = results.filter((r) => ['failed', 'deferred', 'skipped'].includes(r.status))
    const head = `${n(verb === 'restored' ? 'restored' : 'updated')} ${verb}`
    const tail = problems.length ? ` · ${problems.map((r) => r.message).filter(Boolean)[0] ?? ''}` : ''
    return `${head}${problems.length ? `, ${problems.length} left alone` : ''}${tail}`
  }

  const flagged = useMemo(() => (report?.videos ?? []).filter((v) => v.findings.length > 0), [report])
  const fixable = flagged.filter((v) => v.proposed && !applied.includes(v.video_id))
  const rewritable = flagged.filter((v) => v.manual.length > 0 && !v.rewritten)
  const chosen = [...picked]
  const chosenFixable = chosen.filter((id) => fixable.some((v) => v.video_id === id))

  const toggle = (id: string): void =>
    setPicked((p) => {
      const next = new Set(p)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const run = async (): Promise<void> => {
    setError('')
    setNote('')
    take(await api.youtubeAuditRun(channel || undefined))
    setPicked(new Set())
  }

  const rewrite = async (): Promise<void> => {
    setError('')
    const ids = chosen.filter((id) => rewritable.some((v) => v.video_id === id))
    const got = await api.youtubeAuditRewrite(ids, channel || undefined)
    take(got)
    setNote(`${got.rewritten} ${t('rewrite(s) proposed. Nothing has been changed on YouTube yet.')}`)
  }

  const apply = async (): Promise<void> => {
    setError('')
    const units = chosenFixable.length * UNITS_PER_VIDEO
    if (
      !window.confirm(
        `${t('Change')} ${chosenFixable.length} ${t('public video(s) on YouTube?')}\n\n` +
          `${t('Only the changes shown here are sent. A video edited on YouTube since the check is skipped. You can undo from this panel. Costs about')} ${units} ${t('quota units.')}`
      )
    )
      return
    const got = await api.youtubeAuditApply(chosenFixable, channel || undefined)
    take(got)
    setPicked(new Set())
    setNote(summarize(got.results, t('updated')))
  }

  const undo = async (ids: string[]): Promise<void> => {
    setError('')
    if (!window.confirm(`${t('Put the original text back on')} ${ids.length} ${t('video(s)?')}`)) return
    const got = await api.youtubeAuditUndo(ids, channel || undefined)
    take(got)
    setNote(summarize(got.results, t('restored')))
  }

  if (ready === null) return null

  return (
    <section className="card space-y-3" aria-label={t('Metadata audit')}>
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="font-semibold">{t('Metadata audit')}</h2>
        {ready && accounts.length > 1 && (
          <select
            className="input !w-auto !py-1 text-xs"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            aria-label={t('Channel')}
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        )}
        {ready && (
          <AsyncButton className="btn-ghost !py-1 text-xs ml-auto" onClick={run} onError={fail} busyLabel={t('Checking…')}>
            {report ? `↻ ${t('Check again')}` : t('Check my channel')}
            <Cost units={readUnits(200)} />
          </AsyncButton>
        )}
      </div>
      <p className="text-xs text-muted">
        {t(
          'YouTube’s 2027 Partner Program rules disqualify hashtag spam, keyword stuffing and machine-sounding text. This checks the title, description and tags of your videos against limits set in config (compliance). The limits are conservative defaults, not numbers YouTube publishes. Checking changes nothing.'
        )}
      </p>

      {!ready && <p className="text-sm text-muted">{t('Connect YouTube in Settings to check your videos.')}</p>}
      {error && (
        <p className="text-sm text-error">
          {error}{' '}
          {/permission/i.test(error) && (
            <button className="text-accent hover:underline" onClick={() => window.dispatchEvent(new CustomEvent('open-settings'))}>
              {t('Open Settings')}
            </button>
          )}
        </p>
      )}
      {note && <p className="text-sm text-success">{note}</p>}

      {report && (
        <>
          <div className="flex gap-2 flex-wrap text-xs items-center">
            <span className="px-2 py-1 rounded-md bg-raised">
              {report.summary.checked} {t('checked')}
            </span>
            <span className="px-2 py-1 rounded-md bg-emerald-500/15 text-emerald-400">
              {report.summary.clean} {t('clean')}
            </span>
            <span className="px-2 py-1 rounded-md bg-amber-500/15 text-amber-400">
              {report.summary.fixable} {t('can be fixed automatically')}
            </span>
            <span className="px-2 py-1 rounded-md bg-red-500/15 text-red-400">
              {report.summary.needs_rewrite} {t('need rewording')}
            </span>
            <span className="text-muted ml-auto">{new Date(report.at).toLocaleString()}</span>
          </div>

          <div className="flex gap-2 flex-wrap items-center text-xs">
            <button className="btn-ghost !py-1" onClick={() => setPicked(new Set(fixable.map((v) => v.video_id)))}>
              {t('Select all fixable')}
            </button>
            <button className="btn-ghost !py-1" onClick={() => setPicked(new Set())}>
              {t('Clear')}
            </button>
            <AsyncButton
              className="btn-ghost !py-1"
              disabled={!chosen.some((id) => rewritable.some((v) => v.video_id === id))}
              onClick={rewrite}
              onError={fail}
              busyLabel={t('Asking the model…')}
            >
              {t('Propose rewording')}
            </AsyncButton>
            <AsyncButton
              className="btn-primary !py-1"
              disabled={chosenFixable.length === 0}
              onClick={apply}
              onError={fail}
              busyLabel={t('Updating…')}
            >
              {t('Apply to')} {chosenFixable.length} <Cost units={chosenFixable.length * UNITS_PER_VIDEO} />
            </AsyncButton>
            {applied.length > 0 && (
              <AsyncButton className="btn-ghost !py-1 ml-auto" onClick={() => undo(applied)} onError={fail}>
                {t('Undo all')} ({applied.length})
              </AsyncButton>
            )}
          </div>

          {flagged.length === 0 && <p className="text-sm text-success">{t('Nothing to fix. Every video is inside the limits.')}</p>}
          <ul className="divide-y divide-raised/60">
            {flagged.map((v) => (
              <VideoRow
                key={v.video_id}
                v={v}
                applied={applied.includes(v.video_id)}
                checked={picked.has(v.video_id)}
                open={open === v.video_id}
                onToggle={() => toggle(v.video_id)}
                onOpen={() => setOpen(open === v.video_id ? null : v.video_id)}
                onUndo={() => undo([v.video_id]).catch(fail)}
              />
            ))}
          </ul>
          {report.summary.clean > 0 && (
            <button className="text-xs text-muted hover:text-ink" onClick={() => setShowClean((s) => !s)}>
              {showClean ? t('Hide clean videos') : `${t('Show')} ${report.summary.clean} ${t('clean videos')}`}
            </button>
          )}
          {showClean && (
            <ul className="text-xs text-muted space-y-0.5">
              {report.videos
                .filter((v) => v.findings.length === 0)
                .map((v) => (
                  <li key={v.video_id} className="truncate">
                    {v.title}
                  </li>
                ))}
            </ul>
          )}
        </>
      )}
    </section>
  )
}

function VideoRow(props: {
  v: AuditVideo
  applied: boolean
  checked: boolean
  open: boolean
  onToggle: () => void
  onOpen: () => void
  onUndo: () => void
}): JSX.Element {
  const { v, applied, checked, open } = props
  const selectable = Boolean(v.proposed) && !applied
  return (
    <li className="py-2 space-y-1">
      <div className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={checked}
          disabled={!(selectable || v.manual.length > 0)}
          onChange={props.onToggle}
          aria-label={t('Select')}
        />
        <span className={`tabular-nums w-8 text-xs ${scoreStyle(v.score)}`}>{v.score}</span>
        <button className="truncate flex-1 text-left hover:underline" onClick={props.onOpen}>
          {v.title || v.video_id}
        </button>
        {applied && <span className="text-xs text-emerald-400">{t('Updated')}</span>}
        {applied && (
          <button className="text-xs text-accent hover:underline" onClick={props.onUndo}>
            {t('Undo')}
          </button>
        )}
        {!applied && v.proposed && <span className="text-xs text-amber-400">{t('Fix ready')}</span>}
        {!applied && v.manual.some((f) => BAD_TONE.has(f.code)) && (
          <span className="text-xs text-red-400">{v.rewritten ? t('Still needs wording') : t('Needs wording')}</span>
        )}
      </div>
      <ul className="text-xs text-muted pl-12 list-disc list-inside">
        {v.findings.map((f, i) => (
          <li key={i}>{f.message}</li>
        ))}
      </ul>
      {open && v.proposed && (
        <div className="pl-12 space-y-2">
          <Diff label={t('Title')} before={v.title} after={v.proposed.title} />
          <Diff label={t('Description')} before={v.description} after={v.proposed.description} />
          <Diff label={t('Tags')} before={v.tags.join(', ')} after={v.proposed.tags.join(', ')} />
        </div>
      )}
      {open && !v.proposed && <p className="pl-12 text-xs text-muted">{t('No automatic fix: it needs rewording.')}</p>}
    </li>
  )
}
