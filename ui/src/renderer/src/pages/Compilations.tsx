// Video Factory: build one video from segments of many sources.
//
// Left: compilations and templates. Middle: the recipe (look + segments),
// render, and the result. Right: the library, where segments come from:
// the clips the AI already found, or any range picked by scrubbing the source.
// Every edit autosaves; the server re-validates and reports a `problem`.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AsyncButton from '../components/AsyncButton'
import { api } from '../lib/api'
import CompilationVersions from '../components/CompilationVersions'
import CreditControls from '../components/CreditControls'
import RenderProgress from '../components/RenderProgress'
import VideoPlayer from '../components/VideoPlayer'
import { t } from '../lib/i18n'
import { CANVAS_ORDER, formatsApi, platformsFor, type FormatsInfo } from '../lib/formats'
import BrandingSection from '../components/BrandingSection'
import ProcessingBar from '../components/ProcessingBar'
import { ItemBadge } from '../components/PublishBadge'
import { usePublishStates, type ItemState } from '../lib/publishState'
import { DEFAULT_WATERMARK } from '../components/WatermarkControls'
import type { BrandingProfile, Clip, StudioEvent, WatermarkConfig } from '../lib/types'
import { useEvents } from '../lib/useEvents'
import ThumbnailCard from '../components/ThumbnailCard'
import {
  applyTemplate,
  compilationsApi,
  dbToVolume,
  LOUDNESS_TARGETS,
  volumeToDb,
  type LoudnessPart,
  type LoudnessReport,
  duplicateOf,
  duplicates,
  usesTemplate,
  type BlurRegion,
  type Compilation,
  type CompilationOptions,
  type CompilationTemplate,
  type LibraryVideo,
  type Recipe,
  type RenderProgress as RenderProgressInfo,
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

export default function Compilations({
  target = null,
  onTargetConsumed
}: {
  /** Open this compilation — sent from the Library, an upload, or a clip. */
  target?: number | null
  onTargetConsumed?: () => void
} = {}): JSX.Element {
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
  // The last template swap, kept for a while so it can be undone: a swap
  // replaces the whole look, and one click back beats a confirm box.
  const [swapped, setSwapped] = useState<{ compId: number; name: string; before: Recipe } | null>(null)
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Inline rename in the Templates list.
  const [renaming, setRenaming] = useState<{ id: number; name: string; error: string } | null>(null)

  const refreshList = useCallback(async () => {
    try {
      setList(await compilationsApi.list())
    } catch (e) {
      setError(String((e as Error).message))
    }
  }, [])

  // Every render waiting or running, keyed by compilation id. Read once a
  // second while any is, which is how often the engine has something new.
  const [renders, setRenders] = useState<Record<string, RenderProgressInfo>>({})
  const lastRenders = useRef<Record<string, RenderProgressInfo>>({})
  const [cancelling, setCancelling] = useState<number | null>(null)
  const anyRendering = list.some((c) => c.status === 'queued' || c.status === 'rendering')
  useEffect(() => {
    if (!anyRendering) {
      lastRenders.current = {}
      setRenders({})
      return
    }
    let alive = true
    const read = async (): Promise<void> => {
      try {
        const next = await compilationsApi.progress()
        if (!alive) return
        // One that dropped out has finished, failed or been cancelled: the
        // list's status chips need reading again.
        if (Object.keys(lastRenders.current).some((id) => !(id in next))) void refreshList()
        lastRenders.current = next
        setRenders(next)
      } catch {
        /* next tick */
      }
    }
    void read()
    const id = setInterval(() => void read(), 1000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [anyRendering, refreshList])

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
    const loadBranding = (): void => {
      api
        .branding()
        .then((all) => setBranding(all.filter((p) => p.kind === 'compilation')))
        .catch(() => undefined)
    }
    loadBranding()
    window.addEventListener('branding-changed', loadBranding)
    return () => window.removeEventListener('branding-changed', loadBranding)
  }, [refreshList, refreshLibrary, refreshUnused])

  // Bumped to re-read the selected compilation from the server.
  const [reload, setReload] = useState(0)

  // Asked to open a particular one from elsewhere in the app.
  useEffect(() => {
    if (target === null) return
    setSelectedId(target)
    setReload((n) => n + 1)
    void refreshList()
    onTargetConsumed?.()
  }, [target])

  // Segments can now arrive from outside this editor (an upload's "add to
  // compilation", the Clips tab, the Library), and a new upload is a new
  // source for the library pane. Re-read rather than patch. A reload is
  // skipped while an edit is waiting to save, so it cannot undo the edit.
  useEvents((e: StudioEvent) => {
    if (e.type === 'compilation') {
      void refreshList()
      if (e.compilation_id === selectedId && !saveTimer.current) setReload((n) => n + 1)
    }
    if (e.type === 'library' || (e.type === 'progress' && e.stage === 'done')) void refreshLibrary()
  })

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
  }, [selectedId, reload])

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
      saveTimer.current = null
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

  const addSegment = (seg: SegmentSpec): void => {
    const dup = duplicateOf(comp?.recipe.segments ?? [], seg)
    if (dup >= 0) {
      setError(`${t('Already in this compilation')} (${t('segment')} ${dup + 1}).`)
      return
    }
    setError('')
    setSegments((s) => [...s, { credit: true, ...seg }])
  }

  const creating = useRef(false)
  const create = async (): Promise<void> => {
    if (creating.current) return // Enter in the title box skips the button's own lock
    creating.current = true
    try {
      const c = await compilationsApi.create(newTitle || t('Untitled compilation'), newTemplate || null)
      setNewTitle('')
      await refreshList()
      setSelectedId(c.id)
    } catch (e) {
      setError(String((e as Error).message))
    } finally {
      creating.current = false
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

  /** Swap the open compilation's look for a template's, keeping its segments. */
  const swapTemplate = (tp: CompilationTemplate): void => {
    if (!comp || busy) return
    const before = comp.recipe
    editRecipe((r) => applyTemplate(tp.config, r))
    setSwapped({ compId: comp.id, name: tp.name, before })
    if (undoTimer.current) clearTimeout(undoTimer.current)
    undoTimer.current = setTimeout(() => setSwapped(null), 20_000)
  }

  /** Put the look back exactly as it was, keeping any segment changes since. */
  const undoSwap = (): void => {
    if (!swapped || !comp || comp.id !== swapped.compId) return
    const before = swapped.before
    editRecipe((r) => ({ ...before, segments: r.segments }))
    setSwapped(null)
  }

  const renameTemplate = async (): Promise<void> => {
    if (!renaming) return
    const name = renaming.name.trim()
    const current = templates.find((tp) => tp.id === renaming.id)
    if (!name || name === current?.name) {
      setRenaming(null)
      return
    }
    try {
      await compilationsApi.renameTemplate(renaming.id, name)
      setTemplates(await compilationsApi.templates())
      setRenaming(null)
    } catch (e) {
      setRenaming({ ...renaming, error: String((e as Error).message) })
    }
  }

  /** Save the look as a new template, or over `replaceId`. Resolves to why
   *  it failed, or '' once saved, so the form can stay open on a failure. */
  const saveTemplate = async (name: string, replaceId?: number): Promise<string> => {
    if (!comp || !name.trim()) return t('Name the template first.')
    try {
      if (replaceId != null) await compilationsApi.updateTemplate(replaceId, name.trim(), comp.recipe)
      else await compilationsApi.saveTemplate(name.trim(), comp.recipe)
      setTemplates(await compilationsApi.templates())
      return ''
    } catch (e) {
      return String((e as Error).message)
    }
  }

  // Where each compilation stands on the way out, keyed by its negative id.
  const compStates = usePublishStates(useMemo(() => list.map((c) => -c.id), [list]))
  const libById = useMemo(() => Object.fromEntries(library.map((v) => [v.video_id, v])), [library])

  return (
    <div className="flex flex-col xl:h-full xl:min-h-0">
      {/* The same live queue snapshot the Clips page shows, in the same place. */}
      <div className="px-4 pt-4 empty:hidden">
        <ProcessingBar />
      </div>
      {/* Three columns on a wide screen. Narrower (a portrait monitor), they
          stack: the list, the editor, then the library, and the page scrolls as
          one. Stacked, the list and library are each capped to a share of the
          screen and scroll on their own, and the list lays out in a grid, so
          neither crowds out the other. */}
    <div className="flex flex-col xl:flex-row xl:min-h-0 xl:flex-1">
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
            <AsyncButton className="btn-accent w-full sm:w-auto xl:w-full shrink-0" busyLabel={t('Creating…')} onClick={create}>
              {t('New compilation')}
            </AsyncButton>
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
                <div className="flex items-center gap-2 min-w-0">
                  <span className="truncate text-sm font-medium">{c.title}</span>
                  {c.status === 'done' && <ItemBadge state={compStates[-c.id]} />}
                </div>
                <div className="text-xs text-muted flex gap-2">
                  <span className={`px-1.5 rounded ${STATUS_CHIP[c.status]}`}>
                    {c.status === 'rendering' && renders[String(c.id)]?.percent != null
                      ? `${t('rendering')} ${renders[String(c.id)]?.percent}%`
                      : t(c.status)}
                  </span>
                  <span>
                    {(c.recipe.segments ?? []).length} {t('segments')}
                  </span>
                  {duplicates(c.recipe.segments ?? []).size > 0 && (
                    <span className="px-1.5 rounded bg-amber-500/15 text-amber-400">
                      {duplicates(c.recipe.segments ?? []).size} {t('repeated')}
                    </span>
                  )}
                </div>
                {(c.status === 'queued' || c.status === 'rendering') && (
                  <RenderProgress compact progress={renders[String(c.id)]} />
                )}
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="text-xs text-muted col-span-full">{t('No compilations yet.')}</li>}
        </ul>
        {templates.length > 0 && (
          <div className="space-y-1">
            <h3 className="label">{t('Templates')}</h3>
            {templates.map((tp) => {
              const inUse = comp ? usesTemplate(comp.recipe, tp) : false
              if (renaming?.id === tp.id) {
                return (
                  <div key={tp.id} className="space-y-1">
                    <input
                      className="input !py-1 text-sm w-full"
                      autoFocus
                      value={renaming.name}
                      aria-label={t('Template name')}
                      onChange={(e) => setRenaming({ ...renaming, name: e.target.value, error: '' })}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') void renameTemplate()
                        if (e.key === 'Escape') setRenaming(null)
                      }}
                      onBlur={() => void renameTemplate()}
                    />
                    {renaming.error && <p className="text-xs text-red-300">{renaming.error}</p>}
                  </div>
                )
              }
              return (
                <div key={tp.id} className="flex items-center gap-1.5 text-sm">
                  <button
                    className="truncate flex-1 text-left hover:text-accent"
                    title={t('Rename')}
                    onDoubleClick={() => setRenaming({ id: tp.id, name: tp.name, error: '' })}
                  >
                    {tp.name}
                    {inUse && <span className="ml-1.5 text-[10px] text-accent">● {t('in use')}</span>}
                  </button>
                  {comp && !inUse && (
                    <button
                      className="text-xs text-accent hover:underline shrink-0 disabled:opacity-40"
                      disabled={busy}
                      title={t('Give the open compilation this look; its segments stay')}
                      onClick={() => swapTemplate(tp)}
                    >
                      {t('Use')}
                    </button>
                  )}
                  <button
                    className="text-xs text-muted hover:text-ink shrink-0"
                    title={t('Rename')}
                    aria-label={`${t('Rename')} ${tp.name}`}
                    onClick={() => setRenaming({ id: tp.id, name: tp.name, error: '' })}
                  >
                    ✎
                  </button>
                  <AsyncButton
                    className="text-xs text-muted hover:text-red-400 shrink-0"
                    aria-label={`${t('Delete')} ${tp.name}`}
                    onClick={async () => {
                      if (!window.confirm(`${t('Delete the template')} “${tp.name}”? ${t('Compilations made with it keep their look.')}`)) return
                      await compilationsApi.deleteTemplate(tp.id).catch(() => undefined)
                      setTemplates(await compilationsApi.templates())
                    }}
                  >
                    ✕
                  </AsyncButton>
                </div>
              )
            })}
          </div>
        )}
        {unused.length > 0 && (
          <div className="space-y-1 col-span-full">
            <h3 className="label">{t('Storage')}</h3>
            <div className="flex items-center justify-between gap-2 text-xs text-muted">
              <span title={unused.map((f) => f.name).join('\n')}>
                {unused.length} {t('unused video(s)')} · {(unused.reduce((n, f) => n + f.bytes, 0) / 1e6).toFixed(0)} MB
              </span>
              <AsyncButton className="text-accent hover:underline shrink-0" busyLabel={t('Cleaning up…')} onClick={cleanUnused}>
                {t('Clean up…')}
              </AsyncButton>
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
            renderProgress={renders[String(comp.id)]}
            cancelling={cancelling === comp.id}
            onCancelRender={async () => {
              setCancelling(comp.id)
              try {
                await compilationsApi.cancelRender(comp.id)
                setComp(await compilationsApi.get(comp.id))
                void refreshList()
              } catch (e) {
                setError(String((e as Error).message))
              } finally {
                setCancelling(null)
              }
            }}
            onReplaced={(c) => {
              setComp(c)
              void refreshList()
              void refreshUnused()
            }}
            templates={templates}
            onSaveTemplate={saveTemplate}
            onSwapTemplate={swapTemplate}
            swappedTo={swapped && swapped.compId === comp.id ? swapped.name : null}
            onUndoSwap={undoSwap}
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
          compilations={list}
          states={compStates}
        />
      </section>
    </div>
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
  error: string
  templates: CompilationTemplate[]
  onSave: (name: string, replaceId?: number) => void
  onCancel: () => void
}): JSX.Element {
  const { name, setName, error, templates, onSave, onCancel } = props
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
      {error && (
        <span role="alert" className="basis-full text-right text-xs text-red-300">
          {error}
        </span>
      )}
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
  /** Where its render is, while it is queued or rendering. */
  renderProgress?: RenderProgressInfo
  onCancelRender: () => void
  cancelling: boolean
  /** The server changed the compilation (a version restored or deleted). */
  onReplaced: (c: Compilation) => void
  templates: CompilationTemplate[]
  onSaveTemplate: (name: string, replaceId?: number) => Promise<string>
  /** Swap this compilation's look for a template's. */
  onSwapTemplate: (tp: CompilationTemplate) => void
  /** The template just swapped in, while it can still be undone. */
  swappedTo: string | null
  onUndoSwap: () => void
  onDelete: () => void
}): JSX.Element {
  const { comp, busy, options, branding, libById, editRecipe, setSegments } = props
  const r = comp.recipe
  const segs = r.segments ?? []
  const repeated = duplicates(segs)
  const [title, setTitle] = useState(comp.title)
  const [templateName, setTemplateName] = useState<string | null>(null)
  const [templateError, setTemplateError] = useState('')
  useEffect(() => setTitle(comp.title), [comp.id, comp.title])

  // Measured loudness, per part. Tied to the segments it was measured for:
  // moving, trimming or swapping one makes it stale, so it is dropped.
  const [levels, setLevels] = useState<{ key: string; report: LoudnessReport } | null>(null)
  const [measuring, setMeasuring] = useState(false)
  const [levelsError, setLevelsError] = useState('')
  const segKey = JSON.stringify([
    (r.segments ?? []).map((s) => [s.video_id, s.start, s.end, s.volume ?? 1]),
    r.intro?.path ?? '',
    r.outro?.path ?? '',
    r.loudness_target ?? -14
  ])
  const report = levels?.key === segKey ? levels.report : null
  const measure = async (): Promise<void> => {
    setMeasuring(true)
    setLevelsError('')
    try {
      setLevels({ key: segKey, report: await compilationsApi.measureLoudness(comp.id) })
    } catch (e) {
      setLevelsError(String((e as Error).message))
    } finally {
      setMeasuring(false)
    }
  }
  const segLevel = (i: number): LoudnessPart | undefined =>
    report?.parts.find((p) => p.kind === 'segment' && p.index === i)

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
  const chosenProfile = branding.find((b) => b.id === r.banner?.profile_id)
  // With a profile, credits follow it unless this compilation set its own; a
  // recipe from before that switch existed counts as its own if it has credits.
  const creditCustom = chosenProfile ? (r.credits_custom ?? r.credits !== undefined) : true
  const credits = r.credits ?? (chosenProfile ? (chosenProfile.config.credit ?? { enabled: false }) : {})
  const ownWatermark: WatermarkConfig = chosenProfile
    ? (({ credit: _c, captions: _k, ...w }) => w)(chosenProfile.config)
    : { ...DEFAULT_WATERMARK, type: 'none' }

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
          {!busy
            ? t('Render')
            : props.renderProgress?.state === 'running'
              ? `${t('Rendering…')} ${props.renderProgress.percent ?? 0}%`
              : t('Queued…')}
        </button>
      </div>
      {busy && (
        <RenderProgress
          progress={props.renderProgress}
          onCancel={props.onCancelRender}
          cancelling={props.cancelling}
        />
      )}
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

      {Object.keys(comp.outputs ?? {}).length > 0 && (
        <div className="card">
          <ThumbnailCard publishId={-comp.id} />
        </div>
      )}

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

        <BrandingSection
          kind="compilation"
          profiles={branding}
          profile={r.banner?.profile_id ?? null}
          disabled={busy}
          onProfile={(v) =>
            editRecipe((rr) => {
              const id = typeof v === 'number' ? v : undefined
              const custom = rr.banner?.custom
              return {
                ...rr,
                banner: id || custom ? { ...(id ? { profile_id: id } : {}), ...(custom ? { custom } : {}) } : null
              }
            })
          }
          customWatermark={!!r.banner?.custom}
          onCustomWatermark={(on) =>
            editRecipe((rr) => {
              const id = rr.banner?.profile_id
              if (!on) return { ...rr, banner: id ? { profile_id: id } : null }
              return { ...rr, banner: { ...(id ? { profile_id: id } : {}), custom: ownWatermark } }
            })
          }
          watermark={r.banner?.custom ?? ownWatermark}
          onWatermark={(patch) =>
            editRecipe((rr) => ({
              ...rr,
              banner: {
                ...(rr.banner?.profile_id ? { profile_id: rr.banner.profile_id } : {}),
                custom: { ...(rr.banner?.custom ?? ownWatermark), ...patch }
              }
            }))
          }
          customCredit={creditCustom}
          onCustomCredit={(on) =>
            editRecipe((rr) => {
              if (!on) {
                const { credits: _drop, ...rest } = rr
                return { ...rest, credits_custom: false }
              }
              return { ...rr, credits_custom: true, credits: { ...(chosenProfile?.config.credit ?? { enabled: false }) } }
            })
          }
          credit={credits}
          onCredit={(patch) =>
            editRecipe((rr) => ({ ...rr, credits_custom: true, credits: { ...(rr.credits ?? credits), ...patch } }))
          }
          positions={options?.credit_positions ?? ['bottom_left']}
          formats={outputs}
          sizes={options?.canvases ?? {}}
          samples={segs.map((s) => {
            const v = libById[s.video_id]
            return { channel: v?.channel_name ?? '', title: v?.title ?? '', url: v?.source_url ?? '' }
          })}
        />
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
        <div className="col-span-2 lg:col-span-4 space-y-1.5">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={r.normalize_audio ?? true}
                onChange={(e) => editRecipe((rr) => ({ ...rr, normalize_audio: e.target.checked }))}
              />
              <span className="text-sm">{t('Even out loudness between creators')}</span>
            </label>
            {(r.normalize_audio ?? true) && (
              <select
                className="input !w-auto !py-1 text-xs"
                value={r.loudness_target ?? -14}
                onChange={(e) => editRecipe((rr) => ({ ...rr, loudness_target: Number(e.target.value) }))}
                aria-label={t('Loudness target')}
              >
                {LOUDNESS_TARGETS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {t(o.label)}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              className="btn-ghost !py-1 text-xs"
              disabled={measuring || segs.length === 0}
              onClick={() => void measure()}
              title={t('Measure how loud each creator is. Also makes the next render quicker.')}
            >
              {measuring ? t('Measuring…') : report ? `↻ ${t('Measure again')}` : t('Measure loudness')}
            </button>
          </div>
          <p className="text-[11px] text-muted">
            {(r.normalize_audio ?? true)
              ? t(
                  'Each part is measured, then raised or lowered as a whole to the target, so nobody is too quiet or too loud and nothing pumps. Each segment’s volume below is a trim on top: 0 dB is matched to the rest.'
                )
              : t('Off: each part keeps its own level. Each segment’s volume below is applied as it is.')}
          </p>
          {report && (
            <p className="text-xs" role="status">
              {report.spread > 0
                ? (r.normalize_audio ?? true)
                  ? `${t('The creators were')} ${report.spread} LU ${t('apart; they will all come out at')} ${report.target} LUFS.`
                  : `${t('The creators are')} ${report.spread} LU ${t('apart. Turn on evening out to match them.')}`
                : t('Measured. Loudness is shown on each segment below.')}
            </p>
          )}
          {levelsError && <p className="text-xs text-red-300">{levelsError}</p>}
        </div>
        {props.swappedTo && (
          <div className="col-span-2 lg:col-span-4 flex items-center justify-end gap-2 text-xs" role="status">
            <span className="text-accent">
              {t('Switched to')} “{props.swappedTo}”. {t('Segments are unchanged.')}
            </span>
            <button type="button" className="underline hover:text-ink" onClick={props.onUndoSwap}>
              {t('Undo')}
            </button>
          </div>
        )}
        <div className="col-span-2 lg:col-span-4 flex flex-wrap gap-2 justify-end">
          {templateName === null && props.templates.length > 0 && (
            <select
              className="input !w-auto"
              value={props.templates.find((tp) => usesTemplate(r, tp))?.id ?? ''}
              onChange={(e) => {
                const tp = props.templates.find((x) => x.id === Number(e.target.value))
                if (tp) props.onSwapTemplate(tp)
              }}
              aria-label={t('Change template')}
              title={t('Give this compilation a saved look. Its segments stay as they are.')}
            >
              <option value="" disabled>
                {t('Change template…')}
              </option>
              {props.templates.map((tp) => (
                <option key={tp.id} value={tp.id}>
                  {tp.name}
                </option>
              ))}
            </select>
          )}
          {templateName === null ? (
            <button type="button" className="btn-ghost" onClick={() => setTemplateName(comp.title)}>
              {t('Save look as template')}
            </button>
          ) : (
            <SaveTemplateForm
              name={templateName}
              setName={setTemplateName}
              error={templateError}
              onCancel={() => {
                setTemplateName(null)
                setTemplateError('')
              }}
              templates={props.templates}
              onSave={async (name, replaceId) => {
                const problem = await props.onSaveTemplate(name, replaceId)
                setTemplateError(problem)
                if (!problem) setTemplateName(null)
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
        {repeated.size > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
            <span>
              {repeated.size === 1
                ? t('1 segment repeats footage already in this compilation.')
                : `${repeated.size} ${t('segments repeat footage already in this compilation.')}`}
            </span>
            <button
              type="button"
              className="btn-ghost !py-1 text-amber-200"
              disabled={busy}
              onClick={() => setSegments((ss) => ss.filter((_, k) => !duplicates(ss).has(k)))}
            >
              {t('Remove repeats')}
            </button>
          </div>
        )}
        {segs.map((s, i) => {
          const v = libById[s.video_id]
          const first = repeated.get(i)
          return (
            <div
              key={i}
              className={`flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-sm ${
                first !== undefined ? 'bg-amber-500/10 ring-1 ring-amber-500/40' : 'bg-raised/40'
              }`}
            >
              <span className="w-6 text-muted">{i + 1}</span>
              <div className="flex-1 min-w-[10rem]">
                <div className="truncate font-medium">{v?.title || s.video_id}</div>
                <div className="text-xs text-muted truncate">
                  {first !== undefined && (
                    <span className="text-amber-400">
                      {t('Repeats segment')} {first + 1} ·{' '}
                    </span>
                  )}
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
              <SegmentVolume
                volume={s.volume ?? 1}
                level={segLevel(i)}
                evened={r.normalize_audio ?? true}
                disabled={busy}
                onChange={(volume) => patchSeg(i, { volume })}
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
                  className={`btn-ghost px-2 hover:text-red-400 ${first !== undefined ? 'text-amber-300' : ''}`}
                  disabled={busy}
                  title={first !== undefined ? t('Remove this repeat') : t('Remove')}
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

/** One segment's volume, in dB. With evening out on, 0 dB means "matched to
 *  the others" and the slider is a trim on top; with it off, it is the
 *  segment's plain volume. Stored as a multiplier (compilation/recipe.py). */
function SegmentVolume({
  volume,
  level,
  evened,
  disabled,
  onChange
}: {
  volume: number
  level?: LoudnessPart
  evened: boolean
  disabled: boolean
  onChange: (volume: number) => void
}): JSX.Element {
  const db = volumeToDb(volume)
  const muted = db === null
  const shown = muted ? 0 : Math.round(db)
  const sign = (n: number): string => (n > 0 ? `+${n}` : `${n}`)
  const measured = level && !level.silent && level.lufs !== null
  const hint = measured
    ? evened
      ? `${t('Measured')} ${level.lufs} LUFS, ${t('matched with')} ${sign(level.match_db ?? 0)} dB`
      : `${t('Measured')} ${level.lufs} LUFS`
    : level?.silent
      ? t('Silent, or nearly: left as it is')
      : ''
  return (
    <div className="flex items-center gap-1" title={hint}>
      <button
        type="button"
        className={`px-1 text-xs rounded ${muted ? 'text-red-400' : 'text-muted hover:text-ink'}`}
        disabled={disabled}
        onClick={() => onChange(muted ? 1 : 0)}
        aria-label={muted ? t('Unmute') : t('Mute')}
        title={muted ? t('Unmute') : t('Mute')}
      >
        {muted ? '🔇' : '🔊'}
      </button>
      <input
        type="range"
        min={-24}
        max={12}
        step={1}
        className="w-20"
        value={shown}
        disabled={disabled || muted}
        onChange={(e) => onChange(dbToVolume(Number(e.target.value)))}
        aria-label={evened ? t('Volume trim (dB)') : t('Volume (dB)')}
      />
      <button
        type="button"
        className="w-11 text-right text-[11px] tabular-nums text-muted hover:text-ink"
        disabled={disabled || muted}
        onDoubleClick={() => onChange(1)}
        title={t('Double-click to reset to 0 dB')}
      >
        {muted ? t('muted') : `${sign(shown)} dB`}
      </button>
      {measured && (
        <span className="text-[10px] text-muted tabular-nums w-16" aria-label={hint}>
          {level.lufs} LUFS
        </span>
      )}
    </div>
  )
}

// ---- library ---------------------------------------------------------------------------------

/** How many compilations use a video, and how many of those are scheduled or posted. */
function usageOf(
  comps: Compilation[],
  states: Record<number, ItemState>,
  videoId: string
): { used: number; posted: number; scheduled: number } {
  const using = comps.filter((c) => (c.recipe.segments ?? []).some((sg) => sg.video_id === videoId))
  const stateOf = (c: Compilation): string | undefined => states[-c.id]?.state
  return {
    used: using.length,
    posted: using.filter((c) => ['published', 'partial'].includes(stateOf(c) ?? '')).length,
    scheduled: using.filter((c) => ['scheduled', 'publishing', 'partial'].includes(stateOf(c) ?? '')).length
  }
}

function Library(props: {
  library: LibraryVideo[]
  options: CompilationOptions | null
  canAdd: boolean
  hasCompilation: boolean
  onAdd: (seg: SegmentSpec) => void
  onCreditSaved: () => void
  /** Every compilation, and where each stands, to show how each video is used. */
  compilations: Compilation[]
  states: Record<number, ItemState>
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
          {t('Nothing imported yet. Add videos in the Library, or on Home with Library only or Compilation, to bring them in without clipping.')}
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
          usage={usageOf(props.compilations, props.states, v.video_id)}
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
  usage: { used: number; posted: number; scheduled: number }
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
        {/* Where this video has ended up: unused, in a draft, scheduled or posted. */}
        <div className="mt-1 flex flex-wrap gap-1.5 text-[10px] font-semibold">
          {props.usage.used === 0 ? (
            <span className="px-1.5 py-0.5 rounded bg-raised text-muted">○ {t('Not used yet')}</span>
          ) : (
            <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300">
              ▦ {t('In')} {props.usage.used} {props.usage.used === 1 ? t('compilation') : t('compilations')}
            </span>
          )}
          {props.usage.scheduled > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300">
              ◷ {props.usage.scheduled} {t('scheduled')}
            </span>
          )}
          {props.usage.posted > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300">
              ✓ {props.usage.posted} {t('posted')}
            </span>
          )}
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
              <VideoPlayer ref={player} className="w-full" label="Source video" src={compilationsApi.sourceUrl(v.video_id)} />
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
