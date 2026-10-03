import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Activity, ActivitySample, PerformanceMode } from '../lib/types'

/** Validated as a set against the sidebar surface (dataviz validator, dark
 *  mode): distinct under every common colour-vision deficiency. Text never
 *  wears these; the legend's swatches carry identity. */
const SERIES = [
  { key: 'cpu', label: 'CPU', color: '#3987e5' },
  { key: 'ram', label: 'RAM', color: '#d95926' },
  { key: 'gpu', label: 'GPU', color: '#199e70' }
] as const

type SeriesKey = (typeof SERIES)[number]['key']

const MODES: { id: PerformanceMode; label: string; hint: string }[] = [
  {
    id: 'auto',
    label: 'Auto',
    hint: 'Full speed while the PC is free; backs off when other apps need it'
  },
  {
    id: 'eco',
    label: 'Eco',
    hint: 'One render at a time, and waits whenever other apps are busy'
  },
  {
    id: 'max',
    label: 'Max',
    hint: 'Normal priority and every render slot. Still pauses before RAM or VRAM run out'
  }
]

const LEVEL = {
  ok: { label: 'Normal', dot: 'bg-success', icon: '●' },
  high: { label: 'Backing off', dot: 'bg-warn', icon: '▲' },
  critical: { label: 'Paused for safety', dot: 'bg-error', icon: '■' }
} as const

const W = 184
const H = 52

function value(s: ActivitySample, key: SeriesKey): number | null {
  return key === 'gpu' ? s.gpu : s[key]
}

function path(samples: ActivitySample[], key: SeriesKey, slots: number): string {
  let d = ''
  let pen = false
  const offset = slots - samples.length
  samples.forEach((s, i) => {
    const v = value(s, key)
    if (v === null) {
      pen = false
      return
    }
    const x = ((offset + i) / (slots - 1)) * W
    const y = H - (Math.min(100, Math.max(0, v)) / 100) * H
    d += `${pen ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    pen = true
  })
  return d
}

/** One ring per series for the collapsed sidebar: the arc is the value, the
 *  number sits inside in ink, and the colour matches the expanded chart. */
function Ring({ label, color, value }: { label: string; color: string; value: number | null }): JSX.Element {
  const r = 14
  const c = 2 * Math.PI * r
  const v = value === null ? 0 : Math.min(100, Math.max(0, value))
  const text = value === null ? '—' : `${Math.round(v)}`
  return (
    <div className="flex flex-col items-center" title={`${label} ${value === null ? 'n/a' : `${Math.round(v)}%`}`}>
      <svg viewBox="0 0 36 36" className="size-9" role="img" aria-label={`${label} ${text}%`}>
        <circle cx={18} cy={18} r={r} fill="none" stroke="currentColor" className="text-raised" strokeWidth={3.5} />
        {value !== null && (
          <circle
            cx={18}
            cy={18}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth={3.5}
            strokeLinecap="round"
            strokeDasharray={`${(v / 100) * c} ${c}`}
            transform="rotate(-90 18 18)"
            className="transition-[stroke-dasharray] duration-500"
          />
        )}
        <text x={18} y={18} textAnchor="middle" dominantBaseline="central" className="fill-ink text-[10px] font-semibold tabular-nums">
          {text}
        </text>
      </svg>
      <span className="text-[9px] leading-none text-muted mt-0.5">{label}</span>
    </div>
  )
}

/** A small live chart of CPU, RAM and GPU for the sidebar, with what the
 *  resource governor is doing about them and the mode that steers it.
 *  Collapsed, the same three readings as stacked rings. */
export default function ResourceMonitor({ collapsed = false }: { collapsed?: boolean }): JSX.Element | null {
  const [activity, setActivity] = useState<Activity | null>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const svgRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    let alive = true
    const poll = async (): Promise<void> => {
      try {
        const a = await api.activity()
        if (alive) setActivity(a)
      } catch {
        if (alive) setActivity(null)
      }
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  if (!activity || activity.samples.length === 0) return null

  const samples = activity.samples
  const slots = 90
  const last = samples[samples.length - 1]
  const hasGpu = samples.some((s) => s.gpu !== null)
  const series = SERIES.filter((s) => s.key !== 'gpu' || hasGpu)
  const shown = hover !== null ? samples[hover] : last
  const level = LEVEL[activity.level]

  if (collapsed) {
    return (
      <div
        className="flex flex-col items-center gap-1.5 py-2"
        title={activity.reasons.join(' · ') || level.label}
      >
        <span aria-label={level.label} className={`inline-block size-2 rounded-full ${level.dot}`} />
        {series.map((s) => (
          <Ring key={s.key} label={s.label} color={s.color} value={value(last, s.key)} />
        ))}
      </div>
    )
  }

  const onMove = (e: React.MouseEvent<SVGSVGElement>): void => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const slot = Math.round(((e.clientX - rect.left) / rect.width) * (slots - 1))
    const i = slot - (slots - samples.length)
    setHover(i >= 0 && i < samples.length ? i : null)
  }

  const setMode = async (mode: PerformanceMode): Promise<void> => {
    if (mode === activity.mode) return
    setSaving(true)
    try {
      await api.setPerformanceMode(mode)
      setActivity({ ...activity, mode })
    } finally {
      setSaving(false)
    }
  }

  const ago = hover !== null ? Math.round(last.t - samples[hover].t) : 0
  const summary = `CPU ${Math.round(last.cpu)}%, RAM ${Math.round(last.ram)}%${
    last.gpu !== null ? `, GPU ${last.gpu}%` : ''
  }. ${level.label}.`

  return (
    <div className="px-3 pb-3">
      <div className="flex items-center justify-between px-2">
        <span className="label">Activity</span>
        <span
          className="flex items-center gap-1.5 text-[11px] text-muted"
          title={activity.reasons.join(' · ') || 'Nothing is holding the job back'}
        >
          <span aria-hidden className={`inline-block size-2 rounded-full ${level.dot}`} />
          {level.label}
        </span>
      </div>

      <div className="relative mt-1.5 rounded-lg bg-raised/50 px-1.5 pt-1.5 pb-1">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="block w-full h-13 overflow-visible"
          role="img"
          aria-label={summary}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {/* Recessive guides at 50% and the baseline. */}
          <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke="currentColor" className="text-raised" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          <line x1={0} x2={W} y1={H} y2={H} stroke="currentColor" className="text-raised" strokeWidth={1} vectorEffect="non-scaling-stroke" />
          {series.map((s) => (
            <path
              key={s.key}
              d={path(samples, s.key, slots)}
              fill="none"
              stroke={s.color}
              strokeWidth={1.75}
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {hover !== null && (
            <line
              x1={((slots - samples.length + hover) / (slots - 1)) * W}
              x2={((slots - samples.length + hover) / (slots - 1)) * W}
              y1={0}
              y2={H}
              stroke="currentColor"
              className="text-muted"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        <div className="mt-1 flex items-center justify-between text-[11px] tabular-nums">
          {series.map((s) => {
            const v = value(shown, s.key)
            return (
              <span key={s.key} className="flex items-center gap-1">
                <span aria-hidden className="inline-block h-0.5 w-2.5 rounded-full" style={{ background: s.color }} />
                <span className="text-muted">{s.label}</span>
                <span className="font-semibold text-ink">{v === null ? '—' : `${Math.round(v)}%`}</span>
              </span>
            )
          })}
        </div>
        <p className="mt-0.5 text-[10px] text-muted tabular-nums">
          {hover !== null ? `${ago}s ago · ` : ''}Video Factory {Math.round(shown.own_cpu)}% CPU
          {shown.vram != null ? ` · VRAM ${Math.round(shown.vram)}%` : ''}
          {shown.commit != null ? ` · Commit ${Math.round(shown.commit)}%` : ''}
        </p>
      </div>

      {(activity.level !== 'ok' || activity.blocked_on) && (
        <p
          className={`mt-1.5 px-2 text-[11px] leading-snug ${
            activity.level === 'critical' ? 'text-error' : 'text-warn'
          }`}
        >
          <span aria-hidden>{level.icon} </span>
          {activity.reasons.join(' · ')}
          {activity.blocked_on
            ? `. Waiting for memory to load ${activity.blocked_on}`
            : activity.waiting
              ? '. Job paused until it clears'
              : activity.level === 'high'
                ? `. Renders limited to ${activity.render_budget}`
                : ''}
        </p>
      )}

      <div className="mt-1.5 grid grid-cols-3 gap-1 rounded-lg bg-raised/50 p-0.5" role="radiogroup" aria-label="Performance mode">
        {MODES.map((m) => (
          <button
            key={m.id}
            role="radio"
            aria-checked={activity.mode === m.id}
            title={m.hint}
            disabled={saving}
            onClick={() => setMode(m.id)}
            className={`rounded-md py-1 text-[11px] font-medium transition-colors ${
              activity.mode === m.id ? 'bg-accent/20 text-accent' : 'text-muted hover:text-ink'
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  )
}
