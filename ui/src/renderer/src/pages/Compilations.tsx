// Video Factory: build one video from segments of many sources.
//
// Left: compilations and templates. Middle: the recipe (look + segments),
// render, and the result. Right: the library, where segments come from:
// the clips the AI already found, or any range picked by scrubbing the source.
// Every edit autosaves; the server re-validates and reports a `problem`.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { BrandingProfile, Clip } from '../lib/types'
import {
  compilationsApi,
  type BlurRegion,
  type Compilation,
  type CompilationOptions,
  type CompilationTemplate,
  type LibraryVideo,
  type Recipe,
  type Rights,
  type SegmentSpec
} from '../lib/compilations'

const BLUR_PRESETS: Record<string, BlurRegion[]> = {
  none: [],
  bottom: [[0, 0.82, 1, 0.18]],
  top: [[0, 0, 1, 0.15]],
  both: [
    [0, 0, 1, 0.15],
    [0, 0.82, 1, 0.18]
  ]
}
const RIGHTS_LABEL: Record<Rights, string> = {
  own: 'My own',
  licensed: 'Licensed',
  permission: 'Have permission',
  fair_use: 'Fair use claim',
  unknown: 'Unknown'
}
const STATUS_CHIP: Record<Compilation['status'], string> = {
  draft: 'bg-raised text-muted',
  queued: 'bg-yellow-500/15 text-yellow-400',
  rendering: 'bg-accent/15 text-accent',
  done: 'bg-green-500/15 text-green-400',
  failed: 'bg-red-500/15 text-red-400'
}

const fmt = (s: number): string => {
  const m = Math.floor(s / 60)
  const r = s - m * 60
  return `${m}:${r.toFixed(1).padStart(4, '0')}`
}

function presetOf(regions: BlurRegion[] | undefined): string {
  const key = JSON.stringify(regions ?? [])
  const hit = Object.entries(BLUR_PRESETS).find(([, v]) => JSON.stringify(v) === key)
  return hit ? hit[0] : 'custom'
}

function runtime(recipe: Recipe): number {
  const segs = recipe.segments ?? []
  const total = segs.reduce((sum, s) => sum + Math.max(0, s.end - s.start), 0)
  const tr = recipe.transition
  const overlap = tr && tr.type !== 'none' && segs.length > 1 ? (segs.length - 1) * (tr.duration ?? 0.5) : 0
  return Math.max(0, total - overlap)
}

export default function Compilations(): JSX.Element {
  const [list, setList] = useState<Compilation[]>([])
  const [templates, setTemplates] = useState<CompilationTemplate[]>([])
  const [library, setLibrary] = useState<LibraryVideo[]>([])
  const [options, setOptions] = useState<CompilationOptions | null>(null)
  const [branding, setBranding] = useState<BrandingProfile[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [comp, setComp] = useState<Compilation | null>(null)
  const [error, setError] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [newTemplate, setNewTemplate] = useState<number | ''>('')
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refreshList = useCallback(async () => {
    try {
      setList(await compilationsApi.list())
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  const refreshLibrary = useCallback(async () => {
    try {
      setLibrary(await compilationsApi.library())
    } catch {
      /* shown as an empty library */
    }
  }, [])

  useEffect(() => {
    void refreshList()
    void refreshLibrary()
    compilationsApi.templates().then(setTemplates).catch(() => undefined)
    compilationsApi.options().then(setOptions).catch(() => undefined)
    api.branding().then(setBranding).catch(() => undefined)
  }, [refreshList, refreshLibrary])

  // Load the selected compilation, and keep polling while it renders.
  useEffect(() => {
    if (selectedId === null) {
      setComp(null)
      return
    }
    let stop = false
    const load = async (): Promise<void> => {
      try {
        const c = await compilationsApi.get(selectedId)
        if (!stop) setComp(c)
      } catch (e) {
        if (!stop) setError(String((e as Error).message))
      }
    }
    void load()
    return () => {
      stop = true
    }
  }, [selectedId])

  const busy = comp?.status === 'queued' || comp?.status === 'rendering'
  useEffect(() => {
    if (!busy || selectedId === null) return
    const id = setInterval(async () => {
      try {
        const c = await compilationsApi.get(selectedId)
        setComp(c)
        if (c.status !== 'queued' && c.status !== 'rendering') void refreshList()
      } catch {
        /* next tick */
      }
    }, 2000)
    return () => clearInterval(id)
  }, [busy, selectedId, refreshList])

  /** Apply a change locally now and save it shortly after the last edit. */
  const editRecipe = (fn: (r: Recipe) => Recipe): void => {
    if (!comp || busy) return
    const recipe = fn(comp.recipe)
    setComp({ ...comp, recipe })
    if (saveTimer.current) clearTimeout(saveTimer.current)
    const id = comp.id
    saveTimer.current = setTimeout(async () => {
      try {
        const saved = await compilationsApi.update(id, { recipe })
        setComp((c) => (c && c.id === id ? { ...c, problem: saved.problem, status: saved.status } : c))
        setError('')
      } catch (e) {
        setError(String((e as Error).message))
      }
    }, 500)
  }

  const setSegments = (fn: (s: SegmentSpec[]) => SegmentSpec[]): void =>
    editRecipe((r) => ({ ...r, segments: fn(r.segments ?? []) }))

  const addSegment = (seg: SegmentSpec): void => setSegments((s) => [...s, { credit: true, ...seg }])

  const create = async (): Promise<void> => {
    try {
      const c = await compilationsApi.create(newTitle || t('Untitled compilation'), newTemplate || null)
      setNewTitle('')
      await refreshList()
      setSelectedId(c.id)
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const render = async (): Promise<void> => {
    if (!comp) return
    if (saveTimer.current) {
      clearTimeout(saveTimer.current)
      await compilationsApi.update(comp.id, { recipe: comp.recipe }).catch(() => undefined)
    }
    try {
      const r = await compilationsApi.render(comp.id)
      setError(r.started ? '' : t('Queued. Other videos are waiting in the paused queue: press Start on the Queue page.'))
      setComp(await compilationsApi.get(comp.id))
      void refreshList()
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const saveTemplate = async (name: string): Promise<void> => {
    if (!comp || !name.trim()) return
    try {
      await compilationsApi.saveTemplate(name, comp.recipe)
      setTemplates(await compilationsApi.templates())
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const libById = useMemo(() => Object.fromEntries(library.map((v) => [v.video_id, v])), [library])

  return (
    <div className="flex h-full min-h-0">
      {/* ---- compilations + templates ---- */}
      <section className="w-64 shrink-0 border-r border-raised/60 p-4 space-y-4 overflow-y-auto">
        <div className="space-y-2">
          <h2 className="font-semibold">{t('Compilations')}</h2>
          <input
            className="input w-full"
            placeholder={t('New compilation title')}
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void create()}
          />
          <select
            className="input w-full"
            value={newTemplate}
            onChange={(e) => setNewTemplate(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">{t('No template')}</option>
            {templates.map((tp) => (
              <option key={tp.id} value={tp.id}>
                {tp.name}
              </option>
            ))}
          </select>
          <button className="btn-accent w-full" onClick={() => void create()}>
            {t('New compilation')}
          </button>
        </div>
        <ul className="space-y-1">
          {list.map((c) => (
            <li key={c.id}>
              <button
                onClick={() => setSelectedId(c.id)}
                className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                  c.id === selectedId ? 'bg-accent/15 text-accent' : 'hover:bg-raised'
                }`}
              >
                <div className="truncate text-sm font-medium">{c.title}</div>
                <div className="text-xs text-muted flex gap-2">
                  <span className={`px-1.5 rounded ${STATUS_CHIP[c.status]}`}>{t(c.status)}</span>
                  <span>
                    {(c.recipe.segments ?? []).length} {t('segments')}
                  </span>
                </div>
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="text-xs text-muted">{t('No compilations yet.')}</li>}
        </ul>
        {templates.length > 0 && (
          <div className="space-y-1">
            <h3 className="label">{t('Templates')}</h3>
            {templates.map((tp) => (
              <div key={tp.id} className="flex items-center justify-between text-sm">
                <span className="truncate">{tp.name}</span>
                <button
                  className="text-xs text-muted hover:text-red-400"
                  onClick={async () => {
                    await compilationsApi.deleteTemplate(tp.id).catch(() => undefined)
                    setTemplates(await compilationsApi.templates())
                  }}
                >
                  {t('Delete')}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ---- editor ---- */}
      <section className="flex-1 min-w-0 p-5 space-y-4 overflow-y-auto">
        {error && <div className="card border border-red-500/40 text-sm text-red-300">{error}</div>}
        {!comp && (
          <div className="card text-muted text-sm">
            {t('Create a compilation, or pick one on the left. Segments come from your library on the right: the clips the AI found, or any range of an imported video.')}
          </div>
        )}
        {comp && (
          <Editor
            comp={comp}
            busy={busy}
            options={options}
            branding={branding}
            libById={libById}
            onRename={async (title) => {
              const saved = await compilationsApi.update(comp.id, { title }).catch(() => null)
              if (saved) setComp({ ...comp, title: saved.title })
              void refreshList()
            }}
            editRecipe={editRecipe}
            setSegments={setSegments}
            onRender={() => void render()}
            onSaveTemplate={(name) => void saveTemplate(name)}
            onDelete={async () => {
              if (!window.confirm(t('Delete this compilation? The rendered file stays on disk.'))) return
              try {
                await compilationsApi.remove(comp.id)
                setSelectedId(null)
                void refreshList()
              } catch (e) {
                setError(String((e as Error).message))
              }
            }}
          />
        )}
      </section>

      {/* ---- library ---- */}
      <section className="w-96 shrink-0 border-l border-raised/60 p-4 overflow-y-auto">
        <Library
          library={library}
          options={options}
          canAdd={!!comp && !busy}
          hasCompilation={!!comp}
          onAdd={addSegment}
          onCreditSaved={refreshLibrary}
        />
      </section>
    </div>
  )
}

// ---- editor -----------------------------------------------------------------------------------

function Editor(props: {
  comp: Compilation
  busy: boolean
  options: CompilationOptions | null
  branding: BrandingProfile[]
  libById: Record<string, LibraryVideo>
  onRename: (title: string) => void
  editRecipe: (fn: (r: Recipe) => Recipe) => void
  setSegments: (fn: (s: SegmentSpec[]) => SegmentSpec[]) => void
  onRender: () => void
  onSaveTemplate: (name: string) => void
  onDelete: () => void
}): JSX.Element {
  const { comp, busy, options, branding, libById, editRecipe, setSegments } = props
  const r = comp.recipe
  const segs = r.segments ?? []
  const [title, setTitle] = useState(comp.title)
  const [templateName, setTemplateName] = useState<string | null>(null)
  useEffect(() => setTitle(comp.title), [comp.id, comp.title])

  const move = (i: number, delta: number): void =>
    setSegments((s) => {
      const j = i + delta
      if (j < 0 || j >= s.length) return s
      const next = [...s]
      ;[next[i], next[j]] = [next[j], next[i]]
      return next
    })
  const patchSeg = (i: number, patch: Partial<SegmentSpec>): void =>
    setSegments((s) => s.map((seg, k) => (k === i ? { ...seg, ...patch } : seg)))

  const pickBumper = async (which: 'intro' | 'outro'): Promise<void> => {
    const path = await window.studio.pickVideoFile()
    if (path) editRecipe((rr) => ({ ...rr, [which]: { path } }))
  }

  const canvas = r.canvas ?? '16:9'
  const transition = r.transition ?? { type: 'none', duration: 0.5 }
  const credits = r.credits ?? {}

  return (
    <>
      <div className="flex items-center gap-3">
        <input
          className="input flex-1 text-lg font-semibold"
          value={title}
          disabled={busy}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => title !== comp.title && props.onRename(title)}
        />
        <span className={`text-xs px-2 py-1 rounded ${STATUS_CHIP[comp.status]}`}>{t(comp.status)}</span>
        <button
          className="btn-accent px-6"
          disabled={busy || !!comp.problem || segs.length === 0}
          onClick={props.onRender}
          title={comp.problem || ''}
        >
          {busy ? t('Rendering…') : t('Render')}
        </button>
      </div>
      {comp.problem && segs.length > 0 && (
        <div className="text-sm text-yellow-300">{comp.problem}</div>
      )}
      {comp.status === 'failed' && comp.error && (
        <pre className="card text-xs text-red-300 whitespace-pre-wrap max-h-40 overflow-y-auto">{comp.error}</pre>
      )}

      {comp.status === 'done' && comp.output_path && (
        <div className="card space-y-2">
          <video
            key={comp.updated_at}
            className="max-h-[420px] mx-auto rounded bg-black"
            controls
            src={compilationsApi.mediaUrl(comp.id, comp.updated_at)}
          />
          <div className="text-xs text-muted break-all">{comp.output_path}</div>
        </div>
      )}

      {/* look */}
      <fieldset className="card grid grid-cols-2 lg:grid-cols-4 gap-3" disabled={busy}>
        <label className="space-y-1">
          <span className="label">{t('Format')}</span>
          <select
            className="input w-full"
            value={canvas}
            onChange={(e) => editRecipe((rr) => ({ ...rr, canvas: e.target.value as Recipe['canvas'] }))}
          >
            {Object.entries(options?.canvases ?? { '16:9': {}, '9:16': {}, '1:1': {}, '4:5': {} }).map(
              ([k, v]) => (
                <option key={k} value={k}>
                  {k}
                  {'width' in v ? ` (${v.width}×${v.height})` : ''}
                </option>
              )
            )}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t('Fill mismatched shapes')}</span>
          <select
            className="input w-full"
            value={r.fit ?? 'blur'}
            onChange={(e) => editRecipe((rr) => ({ ...rr, fit: e.target.value as Recipe['fit'] }))}
          >
            <option value="blur">{t('Blurred background')}</option>
            <option value="pad">{t('Black bars')}</option>
            <option value="crop">{t('Crop to fill')}</option>
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t('Transition')}</span>
          <select
            className="input w-full"
            value={transition.type}
            onChange={(e) => editRecipe((rr) => ({ ...rr, transition: { ...transition, type: e.target.value } }))}
          >
            {(options?.transitions ?? ['none', 'fade']).map((tr) => (
              <option key={tr} value={tr}>
                {tr === 'none' ? t('Hard cut') : tr}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t('Transition seconds')}</span>
          <input
            type="number"
            min={0.1}
            max={2}
            step={0.1}
            className="input w-full"
            disabled={transition.type === 'none'}
            value={transition.duration ?? 0.5}
            onChange={(e) =>
              editRecipe((rr) => ({ ...rr, transition: { ...transition, duration: Number(e.target.value) } }))
            }
          />
        </label>

        <label className="flex items-center gap-2 col-span-2 lg:col-span-1">
          <input
            type="checkbox"
            checked={credits.enabled ?? true}
            onChange={(e) => editRecipe((rr) => ({ ...rr, credits: { ...credits, enabled: e.target.checked } }))}
          />
          <span className="text-sm">{t('Credit each creator')}</span>
        </label>
        <label className="space-y-1">
          <span className="label">{t('Credit text')}</span>
          <input
            className="input w-full"
            value={credits.template ?? 'Clip: {channel}'}
            onChange={(e) => editRecipe((rr) => ({ ...rr, credits: { ...credits, template: e.target.value } }))}
            title="{channel} {title} {url}"
          />
        </label>
        <label className="space-y-1">
          <span className="label">{t('Credit position')}</span>
          <select
            className="input w-full"
            value={credits.position ?? 'bottom_left'}
            onChange={(e) => editRecipe((rr) => ({ ...rr, credits: { ...credits, position: e.target.value } }))}
          >
            {(options?.credit_positions ?? ['bottom_left']).map((p) => (
              <option key={p} value={p}>
                {p.replace('_', ' ')}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="label">{t('Credit seconds')}</span>
          <input
            type="number"
            min={1}
            max={15}
            step={0.5}
            className="input w-full"
            value={credits.seconds ?? 4}
            onChange={(e) =>
              editRecipe((rr) => ({ ...rr, credits: { ...credits, seconds: Number(e.target.value) } }))
            }
          />
        </label>

        <label className="space-y-1 col-span-2">
          <span className="label">{t('Banner (branding profile)')}</span>
          <select
            className="input w-full"
            value={r.banner?.profile_id ?? ''}
            onChange={(e) =>
              editRecipe((rr) => ({ ...rr, banner: e.target.value ? { profile_id: Number(e.target.value) } : null }))
            }
          >
            <option value="">{t('None')}</option>
            {branding.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        {(['intro', 'outro'] as const).map((which) => (
          <div key={which} className="space-y-1">
            <span className="label">{which === 'intro' ? t('Intro clip') : t('Outro clip')}</span>
            <div className="flex gap-1">
              <button type="button" className="btn-ghost flex-1 truncate text-left" onClick={() => void pickBumper(which)}>
                {r[which]?.path ? r[which]!.path.split(/[\\/]/).pop() : t('Choose file…')}
              </button>
              {r[which] && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => editRecipe((rr) => ({ ...rr, [which]: null }))}
                >
                  ✕
                </button>
              )}
            </div>
          </div>
        ))}
        <label className="flex items-center gap-2 col-span-2">
          <input
            type="checkbox"
            checked={r.normalize_audio ?? true}
            onChange={(e) => editRecipe((rr) => ({ ...rr, normalize_audio: e.target.checked }))}
          />
          <span className="text-sm">{t('Even out loudness between creators')}</span>
        </label>
        <div className="col-span-2 lg:col-span-4 flex gap-2 justify-end">
          {templateName === null ? (
            <button type="button" className="btn-ghost" onClick={() => setTemplateName(comp.title)}>
              {t('Save look as template')}
            </button>
          ) : (
            <>
              <input
                className="input"
                autoFocus
                placeholder={t('Template name')}
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
              />
              <button
                type="button"
                className="btn-accent"
                disabled={!templateName.trim()}
                onClick={() => {
                  props.onSaveTemplate(templateName)
                  setTemplateName(null)
                }}
              >
                {t('Save')}
              </button>
              <button type="button" className="btn-ghost" onClick={() => setTemplateName(null)}>
                {t('Cancel')}
              </button>
            </>
          )}
          <button type="button" className="btn-ghost text-red-300" onClick={props.onDelete}>
            {t('Delete compilation')}
          </button>
        </div>
      </fieldset>

      {/* segments */}
      <div className="card space-y-2">
        <div className="flex justify-between items-baseline">
          <h3 className="font-semibold">
            {t('Segments')} ({segs.length})
          </h3>
          <span className="text-sm text-muted">
            {t('Runtime')} {fmt(runtime(r))}
          </span>
        </div>
        {segs.length === 0 && (
          <p className="text-sm text-muted">{t('Add segments from the library on the right.')}</p>
        )}
        {segs.map((s, i) => {
          const v = libById[s.video_id]
          return (
            <div key={i} className="flex flex-wrap items-center gap-2 bg-raised/40 rounded-lg px-3 py-2 text-sm">
              <span className="w-6 text-muted">{i + 1}</span>
              <div className="flex-1 min-w-[10rem]">
                <div className="truncate font-medium">{v?.title || s.video_id}</div>
                <div className="text-xs text-muted truncate">
                  {v?.channel_name || t('no channel name: set one in the library')}
                  {v && !v.has_source && <span className="text-red-400"> · {t('source file missing')}</span>}
                </div>
              </div>
              <input
                type="number"
                step={0.1}
                min={0}
                className="input w-20"
                value={s.start}
                disabled={busy}
                onChange={(e) => patchSeg(i, { start: Number(e.target.value) })}
                title={t('In (seconds)')}
              />
              <span className="text-muted">→</span>
              <input
                type="number"
                step={0.1}
                min={0}
                className="input w-20"
                value={s.end}
                disabled={busy}
                onChange={(e) => patchSeg(i, { end: Number(e.target.value) })}
                title={t('Out (seconds)')}
              />
              <span className="w-12 text-muted text-xs">{fmt(Math.max(0, s.end - s.start))}</span>
              <label className="flex items-center gap-1 text-xs" title={t('Show the credit on this segment')}>
                <input
                  type="checkbox"
                  checked={s.credit ?? true}
                  disabled={busy}
                  onChange={(e) => patchSeg(i, { credit: e.target.checked })}
                />
                {t('credit')}
              </label>
              <select
                className="input w-28 text-xs"
                value={presetOf(s.blur_regions)}
                disabled={busy}
                onChange={(e) =>
                  e.target.value !== 'custom' && patchSeg(i, { blur_regions: BLUR_PRESETS[e.target.value] })
                }
                title={t('Blur part of the frame, e.g. the original creator’s own captions or watermark')}
              >
                <option value="none">{t('No blur')}</option>
                <option value="bottom">{t('Blur bottom')}</option>
                <option value="top">{t('Blur top')}</option>
                <option value="both">{t('Blur top + bottom')}</option>
                {presetOf(s.blur_regions) === 'custom' && <option value="custom">{t('Custom')}</option>}
              </select>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                className="w-20"
                value={s.volume ?? 1}
                disabled={busy}
                onChange={(e) => patchSeg(i, { volume: Number(e.target.value) })}
                title={`${t('Volume')} ${Math.round((s.volume ?? 1) * 100)}%`}
              />
              <div className="flex gap-0.5">
                <button className="btn-ghost px-2" disabled={busy || i === 0} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button
                  className="btn-ghost px-2"
                  disabled={busy || i === segs.length - 1}
                  onClick={() => move(i, 1)}
                >
                  ↓
                </button>
                <button
                  className="btn-ghost px-2 hover:text-red-400"
                  disabled={busy}
                  onClick={() => setSegments((ss) => ss.filter((_, k) => k !== i))}
                >
                  ✕
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

// ---- library ---------------------------------------------------------------------------------

function Library(props: {
  library: LibraryVideo[]
  options: CompilationOptions | null
  canAdd: boolean
  hasCompilation: boolean
  onAdd: (seg: SegmentSpec) => void
  onCreditSaved: () => void
}): JSX.Element {
  const { library, canAdd, onAdd } = props
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const shown = library.filter((v) =>
    `${v.title} ${v.channel_name}`.toLowerCase().includes(query.trim().toLowerCase())
  )
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">{t('Library')}</h2>
      {!props.hasCompilation && (
        <p className="text-xs text-yellow-300">
          {t('Open or create a compilation (left) to add segments to it.')}
        </p>
      )}
      <input
        className="input w-full"
        placeholder={t('Search videos or creators')}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {library.length === 0 && (
        <p className="text-sm text-muted">
          {t('Nothing imported yet. Paste links on the Clip Editor or Queue page first.')}
        </p>
      )}
      {shown.map((v) => (
        <LibraryItem
          key={v.video_id}
          video={v}
          open={open === v.video_id}
          onToggle={() => setOpen(open === v.video_id ? null : v.video_id)}
          canAdd={canAdd && v.has_source}
          onAdd={onAdd}
          rights={props.options?.rights ?? (Object.keys(RIGHTS_LABEL) as Rights[])}
          onCreditSaved={props.onCreditSaved}
        />
      ))}
    </div>
  )
}

function LibraryItem(props: {
  video: LibraryVideo
  open: boolean
  onToggle: () => void
  canAdd: boolean
  onAdd: (seg: SegmentSpec) => void
  rights: Rights[]
  onCreditSaved: () => void
}): JSX.Element {
  const { video: v, open, canAdd, onAdd } = props
  const [clips, setClips] = useState<Clip[] | null>(null)
  const [channel, setChannel] = useState(v.channel_name)
  const [inPt, setInPt] = useState(0)
  const [outPt, setOutPt] = useState(0)
  const player = useRef<HTMLVideoElement>(null)

  useEffect(() => setChannel(v.channel_name), [v.channel_name])
  useEffect(() => {
    if (open && clips === null) api.clips(v.video_id).then(setClips).catch(() => setClips([]))
  }, [open, clips, v.video_id])

  // In and out can be set in either order; the range is whichever is first.
  const rangeStart = Math.min(inPt, outPt)
  const rangeEnd = Math.max(inPt, outPt)
  const rangeHint = !canAdd
    ? t('Open or create a compilation first.')
    : rangeEnd - rangeStart < 0.5
      ? t('Play or scrub to a moment, press Set in, move on, then press Set out (at least 0.5s apart).')
      : ''

  const saveCredit = async (patch: { channel_name?: string; rights?: Rights }): Promise<void> => {
    await compilationsApi.setCredit(v.video_id, patch).catch(() => undefined)
    props.onCreditSaved()
  }

  return (
    <div className="card !p-3 space-y-2">
      <button className="w-full text-left" onClick={props.onToggle}>
        <div className="text-sm font-medium truncate">{v.title || v.video_id}</div>
        <div className="text-xs text-muted flex gap-2">
          <span className="truncate">{v.channel_name || t('no channel name')}</span>
          <span>· {fmt(v.duration || 0)}</span>
          {v.rights === 'unknown' && <span className="text-yellow-400">· {t('rights unknown')}</span>}
          {!v.has_source && <span className="text-red-400">· {t('source missing')}</span>}
        </div>
      </button>
      {open && (
        <div className="space-y-3 pt-1">
          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1">
              <span className="label">{t('Credit as')}</span>
              <input
                className="input w-full"
                value={channel}
                onChange={(e) => setChannel(e.target.value)}
                onBlur={() => channel !== v.channel_name && void saveCredit({ channel_name: channel })}
              />
            </label>
            <label className="space-y-1">
              <span className="label">{t('Rights')}</span>
              <select
                className="input w-full"
                value={v.rights}
                onChange={(e) => void saveCredit({ rights: e.target.value as Rights })}
              >
                {props.rights.map((rt) => (
                  <option key={rt} value={rt}>
                    {t(RIGHTS_LABEL[rt] ?? rt)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {clips && clips.length > 0 && (
            <div className="space-y-1">
              <span className="label">{t('AI-found moments')}</span>
              {clips.map((c) => (
                <div key={c.id} className="flex items-center gap-2 text-xs">
                  <span className="w-8 text-accent">{c.score}</span>
                  <span className="flex-1 truncate" title={c.title || c.hook}>
                    {c.title || c.hook || `${fmt(c.start_s)}`}
                  </span>
                  <span className="text-muted">{fmt(c.end_s - c.start_s)}</span>
                  <button
                    className="btn-ghost px-2 py-0.5"
                    disabled={!canAdd}
                    onClick={() => onAdd({ video_id: v.video_id, start: c.start_s, end: c.end_s })}
                  >
                    + {t('Add')}
                  </button>
                </div>
              ))}
            </div>
          )}

          {v.has_source && (
            <div className="space-y-1">
              <span className="label">{t('Pick a range')}</span>
              <video ref={player} className="w-full rounded bg-black" controls preload="metadata" src={compilationsApi.sourceUrl(v.video_id)} />
              <div className="flex items-center gap-1 text-xs">
                <button className="btn-ghost px-2 py-0.5" onClick={() => setInPt(player.current?.currentTime ?? 0)}>
                  {t('Set in')} {fmt(inPt)}
                </button>
                <button className="btn-ghost px-2 py-0.5" onClick={() => setOutPt(player.current?.currentTime ?? 0)}>
                  {t('Set out')} {fmt(outPt)}
                </button>
                <button
                  className="btn-accent px-2 py-0.5 ml-auto"
                  disabled={!!rangeHint}
                  title={rangeHint}
                  onClick={() => {
                    onAdd({
                      video_id: v.video_id,
                      start: Math.round(rangeStart * 10) / 10,
                      end: Math.round(rangeEnd * 10) / 10
                    })
                    setInPt(0)
                    setOutPt(0)
                  }}
                >
                  + {t('Add range')} {rangeEnd - rangeStart >= 0.5 ? `(${fmt(rangeEnd - rangeStart)})` : ''}
                </button>
              </div>
              {rangeHint && <p className="text-xs text-muted">{rangeHint}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
