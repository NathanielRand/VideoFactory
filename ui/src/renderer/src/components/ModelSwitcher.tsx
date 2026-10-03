import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import ModelGradeBadge from './ModelGradeBadge'
import { gradeModel } from '../lib/modelGrade'
import type { InstalledModel, ModelRuntime } from '../lib/types'

function gb(bytes: number): string {
  return `${(bytes / 1e9).toFixed(1)} GB`
}

type RuntimeAction = 'start' | 'stop' | 'load' | 'unload'

/** Start, stop, load and unload the local Ollama runtime, so "the model is
 *  unreachable" is a button press rather than a trip to a terminal. */
function RuntimeControls({
  runtime,
  onChanged
}: {
  runtime: ModelRuntime | null
  onChanged: (r: ModelRuntime | null) => void
}): JSX.Element {
  const [pending, setPending] = useState<RuntimeAction | null>(null)
  const [error, setError] = useState('')

  const run = async (action: RuntimeAction): Promise<void> => {
    setPending(action)
    setError('')
    try {
      onChanged(await api.modelRuntimeAction(action))
    } catch (e) {
      // "503 /models/runtime/start: {"detail":"..."}" -> the detail.
      const text = String(e instanceof Error ? e.message : e)
      const detail = /"detail":"([^"]+)"/.exec(text)?.[1]
      setError(detail ?? 'That did not work. The backend log has the details.')
      onChanged(await api.modelRuntime().catch(() => null))
    } finally {
      setPending(null)
    }
  }

  if (!runtime) {
    return <p className="mt-1 px-2 text-[11px] text-muted">Checking the model runtime…</p>
  }

  const loaded = runtime.loaded.find((m) => m.name === runtime.model) ?? runtime.loaded[0]
  // Some of the model in system RAM runs several times slower than all of it
  // on the GPU. Only meaningful when a GPU holds any of it at all.
  const spilling = !!loaded && loaded.size_vram > 0 && loaded.size_vram < loaded.size * 0.95
  const starting = runtime.starting || pending === 'start'
  let dot = 'bg-muted'
  let text = 'Stopped'
  if (starting) {
    dot = 'bg-warn animate-pulse'
    text = 'Starting…'
  } else if (runtime.running && runtime.busy) {
    dot = 'bg-accent animate-pulse'
    text = 'Working'
  } else if (runtime.running && loaded) {
    dot = 'bg-success'
    text = `Loaded · ${gb(loaded.size_vram || loaded.size)}${loaded.size_vram ? ' VRAM' : ' RAM'}`
    if (spilling) {
      dot = 'bg-warn'
      text = `Loaded · only ${Math.round((loaded.size_vram / loaded.size) * 100)}% on GPU`
    }
  } else if (runtime.running) {
    dot = 'bg-success/50'
    text = 'Running · model not loaded'
  }

  const btn =
    'rounded-md px-2 py-0.5 text-[11px] font-medium bg-raised text-ink hover:bg-raised/70 disabled:opacity-40 disabled:cursor-not-allowed'

  return (
    <div className="mt-1.5 px-2">
      <div className="flex items-center gap-1.5 text-[11px]">
        <span aria-hidden className={`inline-block size-2 shrink-0 rounded-full ${dot}`} />
        <span className="truncate text-muted" title={runtime.host}>
          {text}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {!runtime.running ? (
          <button
            className={btn}
            disabled={pending !== null || !runtime.can_start}
            title={runtime.can_start ? 'Start the local model server' : 'Ollama is not installed'}
            onClick={() => run('start')}
          >
            ▶ Start
          </button>
        ) : (
          <>
            {loaded ? (
              <button
                className={btn}
                disabled={pending !== null || runtime.busy}
                title="Free the model's memory. It loads again on the next request"
                onClick={() => run('unload')}
              >
                ⏸ Unload
              </button>
            ) : (
              <button
                className={btn}
                disabled={pending !== null || !runtime.model}
                title="Load the model now so the next job starts at once"
                onClick={() => run('load')}
              >
                {pending === 'load' ? 'Loading…' : '⏵ Load'}
              </button>
            )}
            <button
              className={btn}
              disabled={pending !== null || runtime.busy}
              title={
                runtime.managed
                  ? 'Stop the model server'
                  : 'Stop the model server. Ollama was started outside this app, and its tray app may start it again'
              }
              onClick={() => run('stop')}
            >
              ■ Stop
            </button>
          </>
        )}
      </div>
      {spilling && runtime.running && !runtime.busy && (
        <p className="mt-1 text-[11px] leading-snug text-warn">
          Part of this model is running on the CPU, which makes clipping much slower. Pick a smaller
          model, or use a cloud one.
        </p>
      )}
      {(error || (!runtime.running && runtime.error)) && (
        <p className="mt-1 text-[11px] leading-snug text-error">{error || runtime.error}</p>
      )}
    </div>
  )
}

/** Always-visible model selector in the sidebar: switch the active LLM
 *  from anywhere in the app without opening the Models page. */
export default function ModelSwitcher(): JSX.Element {
  const [installed, setInstalled] = useState<InstalledModel[]>([])
  const [active, setActive] = useState('')
  const [switching, setSwitching] = useState(false)
  const [vram, setVram] = useState<number | null>(null)
  // A cloud model on the user's own key, as "OpenRouter · model", or "".
  const [cloud, setCloud] = useState('')
  const [runtime, setRuntime] = useState<ModelRuntime | null>(null)

  const refresh = async (): Promise<void> => {
    try {
      const status = await api.ai()
      const label = status.providers.find((p) => p.id === status.active.provider)?.label
      setCloud(status.active.local ? '' : `${label ?? status.active.provider} · ${status.active.model}`)
    } catch {
      setCloud('')
    }
    try {
      const info = await api.models()
      setInstalled(info.installed)
      setActive(info.active.replace(/^ollama\//, ''))
    } catch {
      setInstalled([])
    }
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 30000)
    return () => clearInterval(id)
  }, [])

  // The runtime moves faster than the model list: it starts, loads and
  // unloads on its own, so it is polled on its own, more often.
  useEffect(() => {
    let alive = true
    const poll = (): void => {
      api
        .modelRuntime()
        .then((r) => alive && setRuntime(r))
        .catch(() => alive && setRuntime(null))
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  const onRuntime = (r: ModelRuntime | null): void => {
    const wasRunning = runtime?.running
    setRuntime(r)
    // Just came up: the installed list was empty while it was down.
    if (r?.running && !wasRunning) void refresh()
  }

  // Read the card once, so the note can warn when a model will not fit it —
  // by far the biggest speed cliff there is. Best-effort: without a reading
  // the note still describes relative speed.
  useEffect(() => {
    api
      .systemStats()
      .then((s) => setVram(s.gpu?.vram_total ?? null))
      .catch(() => setVram(null))
  }, [])

  const onChange = async (tag: string): Promise<void> => {
    setSwitching(true)
    try {
      await api.activateModel(tag)
      setActive(tag)
    } catch {
      await refresh()
    } finally {
      setSwitching(false)
    }
  }

  if (cloud) {
    // Chosen in Settings → AI, so that is where it is changed. Picking a local
    // model from a dropdown here would quietly switch the user off their cloud
    // provider, which is exactly the kind of surprise this must not spring.
    return (
      <div className="px-3 pb-2">
        <label className="label px-2">AI model</label>
        <button
          className="input mt-1 text-sm text-left truncate"
          title={cloud}
          onClick={() => window.dispatchEvent(new Event('open-settings'))}
        >
          {cloud}
        </button>
        <p className="mt-1 px-2 text-[11px] leading-snug text-muted">Your own key · change in Settings</p>
      </div>
    )
  }

  if (installed.length === 0) {
    return (
      <div className="px-3 pb-2">
        <label className="label px-2">AI model</label>
        <RuntimeControls runtime={runtime} onChanged={onRuntime} />
      </div>
    )
  }

  const current = installed.find((m) => m.name === active)

  return (
    <div className="px-3 pb-2">
      <label className="label px-2">AI model</label>
      <select
        className="input mt-1 text-sm"
        value={active}
        disabled={switching}
        onChange={(e) => onChange(e.target.value)}
      >
        {installed.map((m) => (
          <option key={m.name} value={m.name}>
            {m.name} · {gradeModel(m).grade} ({m.cloud ? 'cloud' : `${m.size_gb.toFixed(1)} GB`})
          </option>
        ))}
      </select>
      {current && (
        <div className="mt-2 px-1">
          <ModelGradeBadge model={current} installed={installed} vram={vram} compact />
        </div>
      )}
      <RuntimeControls runtime={runtime} onChanged={onRuntime} />
    </div>
  )
}
