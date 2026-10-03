import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { bytes, exportsApi, type ExportFile, type ExportSummary } from '../lib/exports'
import type { StudioEvent } from '../lib/types'
import { useEvents } from '../lib/useEvents'
import { useExports } from '../components/delivery/useExports'
import DestinationRow from '../components/delivery/DestinationRow'
import SendMenu from '../components/delivery/SendMenu'
import TransferList from '../components/delivery/TransferList'
import { Film, Folder, Scissors } from '../components/icons'
import { t } from '../lib/i18n'

type Tab = 'clips' | 'compilations'

const keyOf = (f: ExportFile): string => `${f.kind}:${f.id}:${f.canvas}`

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function Stat({
  label,
  value,
  sub,
  children
}: {
  label: string
  value: string
  sub?: string
  children?: ReactNode
}): JSX.Element {
  return (
    <div className="card !p-3 space-y-1 min-w-0">
      <p className="label">{label}</p>
      <p className="text-xl font-bold tabular-nums">{value}</p>
      {sub && <p className="text-xs text-muted truncate">{sub}</p>}
      {children}
    </div>
  )
}

/** Local: every finished video on this PC, and the folders they are saved to.
 *
 *  Save folders are destinations like a cloud's, so a folder can take new
 *  clips and compilations on its own as they finish. Anything can also be
 *  sent anywhere by hand, one file or a selection. */
export default function Local(): JSX.Element {
  const { destinations, transfers, refresh, error } = useExports()
  const [tab, setTab] = useState<Tab>('clips')
  const [files, setFiles] = useState<ExportFile[] | null>(null)
  const [summary, setSummary] = useState<ExportSummary | null>(null)
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')

  const loadFiles = useCallback(async (): Promise<void> => {
    try {
      setFiles(await exportsApi.files(tab))
    } catch {
      setFiles([])
    }
  }, [tab])

  useEffect(() => {
    setFiles(null)
    setSelected(new Set())
    void loadFiles()
  }, [loadFiles])

  useEffect(() => {
    exportsApi.summary().then(setSummary).catch(() => undefined)
  }, [])

  // New clips and renders, and "sent to" marks, arrive as the work finishes.
  useEvents((e: StudioEvent) => {
    if (e.type === 'job' && e.status === 'done') void loadFiles()
    if (e.type === 'exports' && e.transfer == null) void loadFiles()
  })

  const folders = (destinations ?? []).filter((d) => d.kind === 'folder')
  const names = useMemo(
    () => Object.fromEntries((destinations ?? []).map((d) => [d.id, d.name])),
    [destinations]
  )
  const shown = (files ?? []).filter((f) => {
    const q = search.trim().toLowerCase()
    return !q || `${f.title} ${f.parent} ${f.channel}`.toLowerCase().includes(q)
  })
  const picked = shown.filter((f) => selected.has(keyOf(f)) && f.exists)
  const localTransfers = transfers.filter((x) => x.destination_kind === 'folder' || x.destination_id === 0)

  const addFolder = async (folder: string | null | undefined): Promise<void> => {
    if (!folder) return
    setAdding(true)
    setAddError('')
    try {
      await exportsApi.addDestination({ kind: 'folder', target: folder })
      await refresh()
    } catch (e) {
      setAddError(e instanceof Error ? e.message : String(e))
    } finally {
      setAdding(false)
    }
  }

  const toggle = (f: ExportFile): void =>
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(keyOf(f))) next.delete(keyOf(f))
      else next.add(keyOf(f))
      return next
    })
  const allPicked = shown.length > 0 && shown.filter((f) => f.exists).every((f) => selected.has(keyOf(f)))

  const sent = (message: string): void => {
    setNote(message)
    setSelected(new Set())
    void refresh()
    setTimeout(() => setNote(''), 4000)
  }

  const clips = summary?.folders.clips
  const comps = summary?.folders.compilations
  const sources = summary?.folders.downloads
  const used = summary?.disk ? summary.disk.total - summary.disk.free : 0

  return (
    <div className="p-6 space-y-6 w-full max-w-6xl">
      <header className="flex items-start gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">{t('Local')}</h1>
          <p className="text-sm text-muted mt-1 max-w-2xl">
            {t('Every finished video on this PC, and the folders they are saved to. A folder can take new clips and compilations on its own as they finish.')}
          </p>
        </div>
        {summary && window.studio?.openFolder && (
          <button
            className="btn-ghost text-sm flex items-center gap-2"
            title={summary.data_dir}
            onClick={() => void window.studio?.openFolder?.(summary.data_dir)}
          >
            <Folder size={14} /> {t('Open the app’s folder')}
          </button>
        )}
      </header>

      {/* ---- at a glance ---- */}
      <section className="grid gap-3 grid-cols-2 lg:grid-cols-4" aria-label={t('Storage')}>
        <Stat label={t('Clips')} value={clips ? String(clips.files) : '—'} sub={clips ? bytes(clips.bytes) : ''} />
        <Stat
          label={t('Compilation files')}
          value={comps ? String(comps.files) : '—'}
          sub={comps ? bytes(comps.bytes) : ''}
        />
        <Stat
          label={t('Source videos')}
          value={sources ? bytes(sources.bytes) : '—'}
          sub={sources ? `${sources.files} ${t('files')} · ${t('clean up in Settings')}` : ''}
        />
        <Stat
          label={t('Free space')}
          value={summary?.disk ? bytes(summary.disk.free) : '—'}
          sub={summary?.disk ? `${t('of')} ${bytes(summary.disk.total)}` : ''}
        >
          {summary?.disk && (
            <div className="h-1.5 rounded-full bg-raised overflow-hidden mt-1">
              <div
                className={`h-full ${summary.disk.free / summary.disk.total < 0.1 ? 'bg-error' : 'bg-accent'}`}
                style={{ width: `${Math.round((100 * used) / summary.disk.total)}%` }}
              />
            </div>
          )}
        </Stat>
      </section>

      {/* ---- save folders ---- */}
      <section className="card space-y-3" aria-label={t('Save folders')}>
        <div className="flex items-start gap-3 flex-wrap">
          <div className="flex-1 min-w-0">
            <h2 className="font-semibold">{t('Save folders')}</h2>
            <p className="text-sm text-muted">
              {t('Folders on this PC or a drive. Switch on New clips or New compilations to fill one automatically.')}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              className="btn-ghost text-sm"
              disabled={adding}
              onClick={async () => void addFolder(await window.studio?.getDownloadsPath())}
            >
              {t('Use Downloads')}
            </button>
            <button
              className="btn-accent text-sm"
              disabled={adding}
              onClick={async () => void addFolder(await window.studio?.pickFolder())}
            >
              ＋ {t('Add a folder')}
            </button>
          </div>
        </div>
        {addError && <p className="text-sm text-error">{addError}</p>}
        {destinations === null ? (
          <p className="text-sm text-muted">{t('Loading…')}</p>
        ) : folders.length === 0 ? (
          <p className="text-sm text-muted rounded-lg border border-dashed border-raised px-3 py-4 text-center">
            {t('No save folders yet. You can still save any video to any folder from the list below.')}
          </p>
        ) : (
          <div className="space-y-2">
            {folders.map((d) => (
              <DestinationRow key={d.id} dest={d} onChanged={() => void refresh()} />
            ))}
          </div>
        )}
      </section>

      {/* ---- finished videos ---- */}
      <section className="card space-y-3" aria-label={t('Finished videos')}>
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="font-semibold">{t('Finished videos')}</h2>
          <div className="inline-flex rounded-lg bg-raised/60 p-1 gap-1" role="tablist">
            {(
              [
                ['clips', 'Clips', <Scissors key="s" size={13} />],
                ['compilations', 'Compilations', <Film key="f" size={13} />]
              ] as const
            ).map(([id, label, icon]) => (
              <button
                key={id}
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`rounded-md px-3 py-1 text-sm flex items-center gap-1.5 transition-colors ${
                  tab === id ? 'bg-accent text-base font-semibold' : 'text-muted hover:text-ink'
                }`}
              >
                {icon} {t(label)}
              </button>
            ))}
          </div>
          <input
            className="input !w-64 !py-1.5 ml-auto"
            placeholder={t('Search by title, video or channel')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {picked.length > 0 && (
          <div className="flex items-center gap-3 rounded-lg bg-accent/10 border border-accent/30 px-3 py-2 text-sm">
            <span className="font-medium">
              {picked.length} {t('selected')} · {bytes(picked.reduce((n, f) => n + f.bytes, 0))}
            </span>
            <button className="btn-ghost !px-2 !py-1 text-xs" onClick={() => setSelected(new Set())}>
              {t('Clear')}
            </button>
            <span className="ml-auto">
              <SendMenu
                className="btn-accent !px-3 !py-1.5 text-sm"
                label="Send selected to…"
                items={picked.map((f) => ({ kind: f.kind, id: f.id, canvas: f.canvas }))}
                destinations={destinations ?? []}
                onSent={sent}
              />
            </span>
          </div>
        )}
        {note && <p className="text-sm text-success">{note}</p>}

        {files === null ? (
          <p className="text-sm text-muted">{t('Loading…')}</p>
        ) : shown.length === 0 ? (
          <p className="text-sm text-muted rounded-lg border border-dashed border-raised px-3 py-6 text-center">
            {search
              ? t('Nothing matches that search.')
              : tab === 'clips'
                ? t('No clips yet. They appear here as soon as a video is clipped.')
                : t('No compilation renders yet. Render one on the Compilations page.')}
          </p>
        ) : (
          <div className="rounded-lg border border-raised/60 divide-y divide-raised/60 max-h-[60vh] overflow-y-auto">
            <label className="flex items-center gap-3 px-3 py-2 text-xs text-muted bg-raised/30 sticky top-0 backdrop-blur">
              <input
                type="checkbox"
                className="size-4 accent-[#38BDF8]"
                checked={allPicked}
                onChange={(e) =>
                  setSelected(e.target.checked ? new Set(shown.filter((f) => f.exists).map(keyOf)) : new Set())
                }
              />
              {t('Select all')} ({shown.length})
            </label>
            {shown.map((f) => (
              <div
                key={keyOf(f)}
                className={`flex items-center gap-3 px-3 py-2 hover:bg-raised/30 ${f.exists ? '' : 'opacity-50'}`}
              >
                <input
                  type="checkbox"
                  className="size-4 accent-[#38BDF8]"
                  disabled={!f.exists}
                  checked={selected.has(keyOf(f))}
                  onChange={() => toggle(f)}
                  aria-label={f.title}
                />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium truncate" title={f.path}>
                    {f.title}
                    {f.canvas && (
                      <span className="ml-2 text-[10px] font-semibold rounded bg-raised px-1.5 py-0.5 text-muted">
                        {f.canvas}
                      </span>
                    )}
                  </p>
                  <p className="text-xs text-muted truncate">
                    {[
                      f.kind === 'clip' ? f.parent : null,
                      f.duration ? `${Math.round(f.duration)}s` : null,
                      f.exists ? bytes(f.bytes) : t('File deleted'),
                      when(f.created_at)
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                </div>
                <div className="hidden md:flex gap-1 flex-wrap justify-end max-w-64">
                  {f.sent_to.map((id) => (
                    <span key={id} className="text-[10px] rounded-full bg-success/10 text-success px-2 py-0.5">
                      ✓ {names[id] ?? t('removed destination')}
                    </span>
                  ))}
                  {f.saved && f.sent_to.length === 0 && (
                    <span className="text-[10px] rounded-full bg-success/10 text-success px-2 py-0.5">
                      ✓ {t('Saved')}
                    </span>
                  )}
                </div>
                {f.exists && window.studio?.showInFolder && (
                  <button
                    className="btn-ghost !px-2 !py-1 text-xs"
                    title={t('Show in folder')}
                    onClick={() => void window.studio?.showInFolder?.(f.path)}
                  >
                    <Folder size={13} />
                  </button>
                )}
                {f.exists && (
                  <SendMenu
                    items={[{ kind: f.kind, id: f.id, canvas: f.canvas }]}
                    destinations={destinations ?? []}
                    onSent={sent}
                  />
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ---- copies ---- */}
      <section className="card space-y-3" aria-label={t('Copies')}>
        <h2 className="font-semibold">{t('Copies')}</h2>
        {error && <p className="text-sm text-error">{error}</p>}
        <TransferList
          transfers={localTransfers}
          onChanged={() => void refresh()}
          empty="Nothing copied yet. Files sent to a folder show here as they go."
        />
      </section>
    </div>
  )
}
