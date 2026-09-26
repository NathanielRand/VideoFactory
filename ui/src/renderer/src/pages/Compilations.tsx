// Video Factory: build one video from segments of many sources.
//
// Left: compilations and templates. Middle: the recipe (look + segments),
// render, and the result. Right: the library, where segments come from:
// the clips the AI already found, or any range picked by scrubbing the source.
// Every edit autosaves; the server re-validates and reports a `problem`.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import CompilationVersions from '../components/CompilationVersions'
import CreditControls from '../components/CreditControls'
import { t } from '../lib/i18n'
import { CANVAS_ORDER, formatsApi, platformsFor, type FormatsInfo } from '../lib/formats'
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

  // Videos in the compilations folder that nothing points at any more.
  const [unused, setUnused] = useState<{ name: string; bytes: number }[]>([])
  const refreshUnused = useCallback(async () => {
    setUnused(await compilationsApi.unusedFiles().catch(() => []))
  }, [])
  const cleanUnused = async (): Promise<void> => {
    const size = unused.reduce((n, f) => n + f.bytes, 0) / 1e6
    const names = unused.slice(0, 8).map((f) => `• ${f.name}`).join('\n') + (unused.length > 8 ? '\n…' : '')
    if (!window.confirm(`${t('Delete these videos no compilation uses?')} (${size.toFixed(0)} MB)\n\n${names}`)) return
    try {
      const out = await compilationsApi.cleanUnused()
      if (out.kept.length) setError(`${t('Could not delete (open in another program?)')}: ${out.kept.join(', ')}`)
    } catch (e) {
      setError(String((e as Error).message))
    }
    void refreshUnused()
  }

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
    void refreshUnused()
    compilationsApi.templates().then(setTemplates).catch(() => undefined)
    compilationsApi.options().then(setOptions).catch(() => undefined)
    api.branding().then(setBranding).catch(() => undefined)
  }, [refreshList, refreshLibrary, refreshUnused])

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
        if (c.status !== 'queued' && c.status !== 'rendering') {
          void refreshList()
          void refreshUnused() // pruning or a rename may have changed it
        }
      } catch {
        /* next tick */
      }
    }, 2000)
    return () => clearInterval(id)
  }, [busy, selectedId, refreshList, refreshUnused])

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

  /** Save the look as a new template, or over `replaceId`. */
  const saveTemplate = async (name: string, replaceId?: number): Promise<void> => {
    if (!comp || !name.trim()) return
    try {
      if (replaceId != null) await compilationsApi.updateTemplate(replaceId, name.trim(), comp.recipe)
      else await compilationsApi.saveTemplate(name.trim(), comp.recipe)
      setTemplates(await compilationsApi.templates())
    } catch (e) {
      setError(String((e as Error).message))
    }
  }

  const libById = useMemo(() => Object.fromEntries(library.map((v) => [v.video_id, v])), [library])

  return (
    // Three columns on a wide screen. Narrower (a portrait monitor), they
    // stack: the list, the editor, then the library, and the page scrolls as
    // one. Stacked, the list and library are each capped to a share of the
    // screen and scroll on their own, and the list lays out in a grid, so
    // neither crowds out the other.
    <div className="flex flex-col xl:flex-row xl:h-full xl:min-h-0">
      {/* ---- compilations + templates ---- */}
      <section className="xl:w-64 xl:shrink-0 border-b xl:border-b-0 xl:border-r border-raised/60 p-4 space-y-4 max-h-[40vh] xl:max-h-none overflow-y-auto">
        <div className="space-y-2">
          <h2 className="font-semibold">{t('Compilations')}</h2>
          {/* One row when stacked; a column in the narrow left pane. */}
          <div className="flex flex-col sm:flex-row xl:flex-col gap-2">
            <input
              className="input w-full sm:flex-1 xl:flex-none"
              placeholder={t('New compilation title')}
              value={newTitle}
              onChange={(e) => setNewTitle(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void create()}
            />
            <select
              className="input w-full sm:!w-48 xl:!w-full"
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
            <button className="btn-accent w-full sm:w-auto xl:w-full shrink-0" onClick={() => void create()}>
              {t('New compilation')}
            </button>
          </div>
        </div>
        <ul className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-1 gap-1">
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
          {list.length === 0 && <li className="text-xs text-muted col-span-full">{t('No compilations yet.')}</li>}
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
        {unused.length > 0 && (
          <div className="space-y-1 col-span-full">
            <h3 className="label">{t('Storage')}</h3>
            <div className="flex items-center justify-between gap-2 text-xs text-muted">
              <span title={unused.map((f) => f.name).join('\n')}>
                {unused.length} {t('unused video(s)')} · {(unused.reduce((n, f) => n + f.bytes, 0) / 1e6).toFixed(0)} MB
              </span>
              <button type="button" className="text-accent hover:underline shrink-0" onClick={() => void cleanUnused()}>
                {t('Clean up…')}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ---- editor ---- */}
      <section className="xl:flex-1 min-w-0 p-5 space-y-4 xl:overflow-y-auto">
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
              void refreshUnused()
            }}
            editRecipe={editRecipe}
            setSegments={setSegments}
            onRender={() => void render()}
            onReplaced={(c) => {
              setComp(c)
              void refreshList()
              void refreshUnused()
            }}
            templates={templates}
            onSaveTemplate={(name, replaceId) => void saveTemplate(name, replaceId)}
            onDelete={async () => {
              if (!window.confirm(t('Delete this compilation? Its rendered videos stay on disk; remove them any time with Storage → Clean up.'))) return
              try {
                await compilationsApi.remove(comp.id)
                setSelectedId(null)
                void refreshList()
                void refreshUnused()
              } catch (e) {
                setError(String((e as Error).message))
              }
            }}
          />
        )}
      </section>

      {/* ---- library ---- */}
      <section className="xl:w-96 xl:shrink-0 border-t xl:border-t-0 xl:border-l border-raised/60 p-4 max-h-[50vh] xl:max-h-none overflow-y-auto">
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

const sameName = (a: string, b: string): boolean => a.trim().toLowerCase() === b.trim().toLowerCase()

/** "Name (2)", "Name (3)", …: the first one no template has. */
function nextFreeName(name: string, templates: CompilationTemplate[]): string {
  const base = name.trim().replace(/\s*\(\d+\)$/, '')
  for (let n = 2; ; n++) {
    const candidate = `${base} (${n})`
    if (!templates.some((tp) => sameName(tp.name, candidate))) return candidate
  }
}

/** Name the template. A name already in use is caught as you type, with the
 *  choice to replace that template or save alongside it under a new name. */
function SaveTemplateForm(props: {
  name: string
  setName: (name: string) => void
  templates: CompilationTemplate[]
  onSave: (name: string, replaceId?: number) => void
  onCancel: () => void
}): JSX.Element {
  const { name, setName, templates, onSave, onCancel } = props
  const clash = templates.find((tp) => sameName(tp.name, name))
  const alt = clash ? nextFreeName(name, templates) : ''
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      <input
        className="input !w-56"
        autoFocus
        placeholder={t('Template name')}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && name.trim() && !clash && onSave(name)}
        aria-describedby={clash ? 'template-clash' : undefined}
      />
      {clash ? (
        <>
          <span id="template-clash" className="text-xs text-amber-400">
            {t('A template with this name exists.')}
          </span>
          <button type="button" className="btn-accent" onClick={() => onSave(clash.name, clash.id)}>
            {t('Replace it')}
          </button>
          <button type="button" className="btn-ghost" onClick={() => onSave(alt)}>
            {t('Save as')} “{alt}”
          </button>
        </>
      ) : (
        <button type="button" className="btn-accent" disabled={!name.trim()} onClick={() => onSave(name)}>
          {t('Save')}
        </button>
      )}
      <button type="button" className="btn-ghost" onClick={onCancel}>
        {t('Cancel')}
      </button>
    </div>
  )
}

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
  /** The server changed the compilation (a version restored or deleted). */
  onReplaced: (c: Compilation) => void
  templates: CompilationTemplate[]
  onSaveTemplate: (name: string, replaceId?: number) => void
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
  const outputs = r.outputs && r.outputs.length > 0 ? r.outputs : [canvas]
  const [formats, setFormats] = useState<FormatsInfo | null>(null)
  useEffect(() => {
    formatsApi.info().then(setFormats).catch(() => undefined)
  }, [])
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
      {Object.entries(comp.warnings ?? {}).map(([c, ws]) => (
        <div key={c} className="text-xs text-yellow-400">
          ⚠ {c}: {ws.join(' ')}
        </div>
      ))}
      {comp.status === 'failed' && comp.error && (
        <pre className="card text-xs text-red-300 whitespace-pre-wrap max-h-40 overflow-y-auto">{comp.error}</pre>
      )}

      <CompilationVersions comp={comp} onChanged={props.onReplaced} />

      {/* look */}
      <fieldset className="card grid grid-cols-2 lg:grid-cols-4 gap-3" disabled={busy}>
        <div className="space-y-1 col-span-2 lg:col-span-4">
          <span className="label">{t('Formats to render (first is the main one)')}</span>
          <div className="flex flex-wrap gap-2">
            {CANVAS_ORDER.map((c) => {
              const on = outputs.includes(c)
              const size = options?.canvases?.[c]
              return (
                <label
                  key={c}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm cursor-pointer ${
                    on ? 'border-accent/60 bg-accent/10' : 'border-raised'
                  }`}
                  title={platformsFor(formats, c)}
                >
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={(e) =>
                      editRecipe((rr) => {
                        const cur = rr.outputs ?? [rr.canvas ?? '16:9']
                        const next = e.target.checked ? [...cur, c] : cur.filter((x) => x !== c)
                        if (next.length === 0) return rr // always keep one format
                        return { ...rr, outputs: next, canvas: next[0] }
                      })
                    }
                  />
                  <span className="font-medium">{c}</span>
                  {outputs[0] === c && outputs.length > 1 && <span className="text-xs text-accent">{t('main')}</span>}
                  <span className="text-xs text-muted">
                    {size ? `${size.width}×${size.height}` : ''} · {platformsFor(formats, c)}
                  </span>
                </label>
              )
            })}
          </div>
        </div>
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

        <CreditControls
          credits={credits}
          positions={options?.credit_positions ?? ['bottom_left']}
          formats={outputs}
          sizes={options?.canvases ?? {}}
          samples={segs.map((s) => {
            const v = libById[s.video_id]
            return { channel: v?.channel_name ?? '', title: v?.title ?? '', url: v?.source_url ?? '' }
          })}
          onChange={(patch) => editRecipe((rr) => ({ ...rr, credits: { ...(rr.credits ?? {}), ...patch } }))}
        />

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
            <SaveTemplateForm
              name={templateName}
              setName={setTemplateName}
              onCancel={() => setTemplateName(null)}
              templates={props.templates}
              onSave={(name, replaceId) => {
                props.onSaveTemplate(name, replaceId)
                setTemplateName(null)
              }}
            />
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
                className="input !w-20"
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
                className="input !w-20"
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
                className="input !w-28 text-xs"
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
