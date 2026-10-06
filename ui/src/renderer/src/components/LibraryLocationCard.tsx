import { useEffect, useState } from 'react'
import AsyncButton from './AsyncButton'
import { api } from '../lib/api'
import type { StorageMode, StorageVolume } from '../lib/api'
import { t } from '../lib/i18n'
import { Folder } from './icons'

const GB = (bytes: number): string => `${(bytes / 1e9).toFixed(2)} GB`

const FOLDER_LABELS: Record<string, string> = {
  downloads: 'Source videos',
  clips: 'Clips',
  transcripts: 'Transcripts',
  previews: 'Editor previews',
  posters: 'Clip stills',
  branding: 'Branding',
  logs: 'Logs',
  other: 'Database and settings'
}

/** The error text out of a failed request ("400 /path: {"detail": "..."}"). */
function detailOf(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  const m = raw.match(/"detail"\s*:\s*"((?:[^"\\]|\\.)*)"/)
  return m ? m[1] : raw
}

function DriveBar({ v }: { v: StorageVolume }): JSX.Element {
  const pct = v.total_bytes ? Math.min(100, (v.used_bytes / v.total_bytes) * 100) : 0
  const low = v.free_bytes < 10e9
  return (
    <div className="h-1.5 rounded bg-raised/70 overflow-hidden" aria-hidden>
      <div className={`h-full ${low ? 'bg-red-400' : 'bg-accent'}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function DriveName({ v }: { v: StorageVolume }): JSX.Element {
  const kind = v.network ? t('Network') : v.removable ? t('Removable') : ''
  return (
    <span>
      {v.label ? `${v.label} (${v.mount})` : v.mount}
      <span className="text-muted"> · {v.filesystem}</span>
      {kind && <span className="ml-1.5 rounded bg-raised px-1.5 py-0.5 text-[10px]">{kind}</span>}
    </span>
  )
}

const MODES: [StorageMode, string, string][] = [
  ['move', 'Move my library there', 'Copies everything and keeps the original until you delete it.'],
  ['fresh', 'Start empty there', 'Your current library stays where it is, untouched.'],
  ['existing', 'Use a library already there', 'For a drive that already holds a Video Factory library.']
]

/** Where the library lives, which drive that is, how full it is, and a way to
 *  point it somewhere else. The library is everything the app keeps: source
 *  videos, clips, transcripts and the database. Exported clips are separate
 *  (see Export location). A change takes effect after a restart: the location
 *  is read once at startup. */
export default function LibraryLocationCard(): JSX.Element {
  const [loc, setLoc] = useState<Awaited<ReturnType<typeof api.storageLocation>> | null>(null)
  const [drives, setDrives] = useState<Awaited<ReturnType<typeof api.storageVolumes>>['volumes']>([])
  const [changing, setChanging] = useState(false)
  const [target, setTarget] = useState('')
  const [mode, setMode] = useState<StorageMode>('move')
  const [check, setCheck] = useState<{ ok: boolean; problems: string[]; library_bytes: number } | null>(null)
  const [error, setError] = useState('')
  const [move, setMove] = useState<Awaited<ReturnType<typeof api.storageMove>> | null>(null)
  const [saved, setSaved] = useState(false)

  const load = (): void => {
    api.storageLocation().then(setLoc).catch(() => setLoc(null))
    api.storageVolumes().then((r) => setDrives(r.volumes)).catch(() => setDrives([]))
  }
  useEffect(load, [])

  // Validate the chosen folder as it changes, so the reason a move is refused
  // is on screen before the button is pressed, not after.
  useEffect(() => {
    setCheck(null)
    setError('')
    if (!changing || !target.trim()) return
    const id = setTimeout(() => {
      api.storageCheck(target.trim(), mode).then(setCheck).catch(() => setCheck(null))
    }, 350)
    return () => clearTimeout(id)
  }, [target, mode, changing])

  // Follow a running move.
  useEffect(() => {
    if (move?.status !== 'running') return
    const id = setInterval(() => {
      api
        .storageMove()
        .then((m) => {
          setMove(m)
          if (m.status === 'done') {
            setSaved(true)
            setChanging(false)
            load()
          }
        })
        .catch(() => undefined)
    }, 1000)
    return () => clearInterval(id)
  }, [move?.status])

  const browse = async (): Promise<void> => {
    const chosen = await window.studio.pickLibraryFolder?.()
    if (chosen) setTarget(chosen)
  }

  const apply = async (): Promise<void> => {
    setError('')
    try {
      const r = await api.storageSetLocation(target.trim(), mode)
      if (r.started) {
        setMove(await api.storageMove())
      } else {
        setSaved(true)
        setChanging(false)
        load()
      }
    } catch (e) {
      setError(detailOf(e))
    }
  }

  const v = loc?.volume ?? null
  const moving = move?.status === 'running'
  const pct = move && move.total_bytes ? Math.min(100, (move.done_bytes / move.total_bytes) * 100) : 0
  const canApply = !!target.trim() && !!check?.ok && !moving && !loc?.locked

  return (
    <div className="card space-y-3" aria-label="Library location">
      <h3 className="font-semibold">{t('Library location')}</h3>
      <p className="text-xs text-muted">
        {t(
          'Where Video Factory keeps source videos, clips, transcripts and its database. Exported clips go to the export folder above, not here.'
        )}
      </p>

      {!loc ? (
        <p className="text-xs text-muted">{t('Checking…')}</p>
      ) : (
        <>
          {loc.fallback_from && (
            <div className="rounded border border-red-400/50 bg-red-400/10 p-2 text-xs space-y-1">
              <p className="font-medium">{t('Your library is not available right now')}</p>
              <p className="break-all">{loc.fallback_from}</p>
              <p className="text-muted">
                {loc.fallback_reason}{' '}
                {t(
                  'Video Factory started on the default location instead. Reconnect the drive and restart to get your library back. Nothing was deleted.'
                )}
              </p>
              <AsyncButton
                className="btn-ghost !py-1 text-xs"
                busyLabel={t('Resetting…')}
                onError={(e) => setError(e instanceof Error ? e.message : String(e))}
                onClick={async () => {
                  await api.storageResetLocation()
                  setSaved(true)
                }}
              >
                {t('Use the default location instead')}
              </AsyncButton>
            </div>
          )}

          <div className="space-y-1">
            <p className="text-sm break-all" title={loc.path}>
              {loc.path}
            </p>
            {v ? (
              <>
                <p className="text-xs">
                  <DriveName v={v} />
                </p>
                <DriveBar v={v} />
                <p className="text-xs text-muted tabular-nums">
                  {GB(v.free_bytes)} {t('free of')} {GB(v.total_bytes)} · {t('library')}{' '}
                  {GB(loc.total_bytes)}
                </p>
                {v.free_bytes < 10e9 && (
                  <p className="text-xs text-red-400">
                    {t('This drive is nearly full. Downloads and renders will fail when it runs out.')}
                  </p>
                )}
                {!v.writable && (
                  <p className="text-xs text-red-400">{t('This drive is read-only.')}</p>
                )}
              </>
            ) : (
              <p className="text-xs text-muted">{t('Could not tell which drive this is on.')}</p>
            )}
          </div>

          {loc.parts.length > 0 && (
            <div className="space-y-1 text-xs border-t border-raised/60 pt-2">
              {loc.parts.map((p) => (
                <div key={p.name} className="flex justify-between gap-3">
                  <span className="text-muted">{t(FOLDER_LABELS[p.name] ?? p.name)}</span>
                  <span className="tabular-nums">{GB(p.bytes)}</span>
                </div>
              ))}
              {loc.models_bytes > 0 && (
                <div className="flex justify-between gap-3">
                  <span className="text-muted">{t('AI models (stay here when you move)')}</span>
                  <span className="tabular-nums">{GB(loc.models_bytes)}</span>
                </div>
              )}
            </div>
          )}

          {drives.length > 0 && (
            <div className="space-y-2 border-t border-raised/60 pt-2">
              <p className="text-xs font-medium">{t('Drives')}</p>
              {drives.map((d) => (
                <div key={d.mount} className="space-y-1">
                  <div className="flex items-center justify-between gap-3 text-xs">
                    <DriveName v={d} />
                    <span className="flex items-center gap-2 shrink-0">
                      <span className="tabular-nums text-muted">
                        {GB(d.free_bytes)} {t('free')}
                      </span>
                      {d.current ? (
                        <span className="text-muted">{t('in use')}</span>
                      ) : (
                        <button
                          className="btn-ghost !py-0.5 !px-2 text-xs"
                          disabled={!d.writable || loc.locked}
                          onClick={() => {
                            const sep = d.mount.includes('\\') ? '\\' : '/'
                            setTarget(`${d.mount.replace(/[\\/]+$/, '')}${sep}Video Factory`)
                            setChanging(true)
                          }}
                        >
                          {t('Use this drive')}
                        </button>
                      )}
                    </span>
                  </div>
                  <DriveBar v={d} />
                </div>
              ))}
            </div>
          )}

          {(saved || loc.pending_path || loc.restart_pending) && (
            <div className="rounded border border-accent/50 bg-accent/10 p-2 text-xs space-y-1">
              <p>
                {t('The new location is saved. Restart Video Factory to start using it.')}
                {loc.pending_path && (
                  <span className="block break-all text-muted">{loc.pending_path}</span>
                )}
              </p>
              {move?.status === 'done' && (
                <p className="text-muted">
                  {t('Your old library is still at')}{' '}
                  <span className="break-all">{move.old_path}</span>.{' '}
                  {t(
                    'Once you have checked the new one you can delete its downloads, clips and transcripts folders. Keep its models folder: the AI models stay there.'
                  )}
                </p>
              )}
              {window.studio.relaunch && (
                <button
                  className="btn-accent !py-1 text-xs"
                  onClick={() => void window.studio.relaunch?.()}
                >
                  {t('Restart now')}
                </button>
              )}
            </div>
          )}

          {loc.locked ? (
            <p className="text-xs text-muted">
              {t(
                'The location is fixed by the environment this app was started in, so it cannot be changed here.'
              )}
            </p>
          ) : !changing ? (
            <button className="btn-ghost !py-1 text-xs" onClick={() => setChanging(true)}>
              {t('Change location…')}
            </button>
          ) : (
            <div className="space-y-2 border-t border-raised/60 pt-2">
              <div className="flex items-center gap-2">
                <input
                  className="input flex-1"
                  value={target}
                  disabled={moving}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder={t('Full path of the new folder')}
                  title={target}
                />
                <button
                  className="btn-ghost shrink-0"
                  disabled={moving || !window.studio.pickLibraryFolder}
                  onClick={browse}
                >
                  <Folder className="mr-1.5" />
                  {t('Browse…')}
                </button>
              </div>

              <div className="space-y-1 text-xs">
                {MODES.map(([m, title, hint]) => (
                  <label key={m} className="flex items-start gap-2">
                    <input
                      type="radio"
                      name="storage-mode"
                      checked={mode === m}
                      disabled={moving}
                      onChange={() => setMode(m)}
                    />
                    <span>
                      {t(title)}
                      <span className="block text-muted">{t(hint)}</span>
                    </span>
                  </label>
                ))}
              </div>

              {check && !check.ok && (
                <ul className="text-xs text-red-400 list-disc pl-4">
                  {check.problems.map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {check?.ok && mode === 'move' && (
                <p className="text-xs text-muted">
                  {t('Will copy')} {GB(check.library_bytes)}.
                </p>
              )}
              {error && <p className="text-xs text-red-400">{error}</p>}

              {move && (moving || move.status === 'error') && (
                <div className="space-y-1">
                  <div className="h-1.5 rounded bg-raised/70 overflow-hidden">
                    <div className="h-full bg-accent" style={{ width: `${pct}%` }} />
                  </div>
                  <p className="text-xs text-muted">
                    {move.status === 'error'
                      ? `${t('Move failed')}: ${move.error} ${t('Your library was not changed.')}`
                      : `${move.phase} · ${GB(move.done_bytes)} / ${GB(move.total_bytes)}`}
                  </p>
                </div>
              )}

              <div className="flex items-center gap-2">
                <button className="btn-accent !py-1 text-xs" disabled={!canApply} onClick={apply}>
                  {moving
                    ? t('Moving…')
                    : mode === 'move'
                      ? t('Move library')
                      : t('Switch location')}
                </button>
                <button
                  className="btn-ghost !py-1 text-xs"
                  disabled={moving}
                  onClick={() => setChanging(false)}
                >
                  {t('Cancel')}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
