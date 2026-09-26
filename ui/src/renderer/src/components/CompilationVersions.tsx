// Every render of a compilation, kept as a version (compilation/store.py):
// play any of them, see what differs from the current settings, put a
// version's settings back, or delete it. How many are kept is a setting here
// too; older ones are deleted after each render.
import { useCallback, useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import {
  compilationsApi,
  type Compilation,
  type CompilationRender,
  type Recipe
} from '../lib/compilations'

const KEEP_CHOICES = [1, 3, 5, 10, 20, 0]

const RECIPE_PARTS: [keyof Recipe, string][] = [
  ['segments', 'segments'],
  ['credits', 'credit'],
  ['canvas', 'format'],
  ['outputs', 'formats'],
  ['fit', 'fill'],
  ['transition', 'transition'],
  ['banner', 'banner'],
  ['intro', 'intro'],
  ['outro', 'outro'],
  ['normalize_audio', 'loudness']
]

/** What differs between a version's recipe and the current one, in words. */
function differences(a: Recipe, b: Recipe): string[] {
  return RECIPE_PARTS.filter(([k]) => JSON.stringify(a[k] ?? null) !== JSON.stringify(b[k] ?? null)).map(
    ([, label]) => t(label)
  )
}

function mb(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(0.1, bytes / 1e6).toFixed(1)} MB`
}

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function CompilationVersions({
  comp,
  onChanged
}: {
  comp: Compilation
  /** The compilation changed (restore, delete): the caller reloads it. */
  onChanged: (c: Compilation) => void
}): JSX.Element | null {
  const [versions, setVersions] = useState<CompilationRender[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [canvas, setCanvas] = useState('')
  const [keep, setKeep] = useState<number | null>(null)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const list = await compilationsApi.renders(comp.id)
      setVersions(list)
      setSelected((s) => (s != null && list.some((v) => v.id === s) ? s : (list[0]?.id ?? null)))
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [comp.id])

  // A finished render (updated_at moves) brings a new version: show it. A
  // rename moves the files, so their paths are re-read too.
  useEffect(() => {
    setSelected(null)
    void load()
  }, [comp.id, comp.status === 'done' ? comp.updated_at : '', comp.title, load]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    compilationsApi
      .settings()
      .then((s) => setKeep(s.keep_versions))
      .catch(() => undefined)
  }, [])

  const current = versions.find((v) => v.id === selected) ?? null
  const formats = current ? Object.keys(current.outputs) : []
  const shown = formats.includes(canvas) ? canvas : (formats[0] ?? '')
  const busy = comp.status === 'queued' || comp.status === 'rendering'

  if (versions.length === 0) return null

  const changeKeep = async (n: number): Promise<void> => {
    const older = n > 0 ? versions.length - n : 0
    if (older > 0 && !window.confirm(t(`Keep only the newest ${n}? This deletes ${older} older version(s) of every compilation that has more.`)))
      return
    try {
      const saved = await compilationsApi.saveSettings({ keep_versions: n })
      setKeep(saved.keep_versions)
      setNote(saved.removed ? `${t('Deleted')} ${saved.removed} ${t('older version(s).')}` : '')
      void load()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const restore = async (v: CompilationRender): Promise<void> => {
    if (
      !window.confirm(
        t(`Put back the settings and segments v${v.version} was rendered with? Your current settings are replaced; they are only kept if you rendered them.`)
      )
    )
      return
    try {
      onChanged(await compilationsApi.restoreRender(comp.id, v.id))
      setNote(`${t('Restored the settings from')} v${v.version}.`)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const remove = async (v: CompilationRender): Promise<void> => {
    if (!window.confirm(t(`Delete v${v.version} and its video files (${mb(v.bytes)})? This cannot be undone.`))) return
    try {
      const out = await compilationsApi.deleteRender(comp.id, v.id)
      if (!out.deleted) setError(t('Some files could not be deleted (open in another program?). They are still listed.'))
      onChanged(out.compilation)
      void load()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const total = versions.reduce((n, v) => n + v.bytes, 0)

  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">
          {t('Versions')} <span className="text-muted font-normal text-sm">({versions.length} · {mb(total)})</span>
        </h3>
        <label className="flex items-center gap-2 text-xs text-muted">
          {t('Keep')}
          <select
            className="input !w-auto !py-1 text-xs"
            value={keep ?? 5}
            disabled={keep === null || busy}
            onChange={(e) => void changeKeep(Number(e.target.value))}
            title={t('How many renders each compilation keeps. Older ones are deleted after each render.')}
          >
            {KEEP_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? t('all versions') : `${t('newest')} ${n}`}
              </option>
            ))}
          </select>
        </label>
      </div>

      {current && (
        <div className="space-y-2">
          {formats.length > 1 && (
            <div className="flex gap-1">
              {formats.map((c) => (
                <button
                  key={c}
                  type="button"
                  className={`px-3 py-1 rounded text-sm ${shown === c ? 'bg-accent/15 text-accent' : 'hover:bg-raised'}`}
                  onClick={() => setCanvas(c)}
                >
                  {c}
                </button>
              ))}
            </div>
          )}
          {current.missing.includes(shown) ? (
            <div className="text-sm text-red-300">{t('This file is no longer on disk.')}</div>
          ) : (
            <video
              key={`${current.id}-${shown}`}
              className="max-h-[420px] mx-auto rounded bg-black"
              controls
              src={compilationsApi.renderMediaUrl(comp.id, current.id, shown)}
            />
          )}
          <div className="text-xs text-muted break-all">{current.outputs[shown]}</div>
          {current.id === versions[0].id && comp.status === 'draft' && (
            <div className="text-xs text-amber-400">{t('The settings have changed since this render. Render again to see them.')}</div>
          )}
        </div>
      )}

      <ul className="divide-y divide-raised/60 text-sm">
        {versions.map((v, i) => {
          const diff = v.recipe ? differences(v.recipe, comp.recipe) : null
          return (
            <li
              key={v.id}
              className={`flex flex-wrap items-center gap-x-3 gap-y-1 py-2 px-2 rounded ${v.id === selected ? 'bg-accent/10' : ''}`}
            >
              <button
                type="button"
                className="flex items-center gap-2 text-left min-w-0 flex-1"
                onClick={() => setSelected(v.id)}
                aria-pressed={v.id === selected}
                title={t('Play this version')}
              >
                <span className={`font-semibold tabular-nums ${v.id === selected ? 'text-accent' : ''}`}>v{v.version}</span>
                {i === 0 && <span className="text-[10px] px-1.5 rounded bg-green-500/15 text-green-400">{t('latest')}</span>}
                <span className="text-muted text-xs">{when(v.created_at)}</span>
                <span className="text-muted text-xs">· {Object.keys(v.outputs).join(', ')}</span>
                <span className="text-muted text-xs">· {mb(v.bytes)}</span>
              </button>
              <span className="text-xs text-muted basis-full sm:basis-auto">
                {diff === null
                  ? t('settings not recorded')
                  : diff.length === 0
                    ? t('same settings as now')
                    : `${t('differs in')}: ${diff.join(', ')}`}
              </span>
              <div className="flex gap-1">
                <button
                  type="button"
                  className="btn-ghost !px-2 !py-1 text-xs"
                  disabled={busy || !v.recipe || diff?.length === 0}
                  onClick={() => void restore(v)}
                  title={
                    !v.recipe
                      ? t('Rendered before settings were saved with each render')
                      : t('Put these settings and segments back')
                  }
                >
                  {t('Restore settings')}
                </button>
                <button
                  type="button"
                  className="btn-ghost !px-2 !py-1 text-xs text-red-300"
                  disabled={busy}
                  onClick={() => void remove(v)}
                  aria-label={`${t('Delete')} v${v.version}`}
                  title={`${t('Delete')} v${v.version}`}
                >
                  ✕
                </button>
              </div>
            </li>
          )
        })}
      </ul>
      {note && <p className="text-xs text-muted">{note}</p>}
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )
}
