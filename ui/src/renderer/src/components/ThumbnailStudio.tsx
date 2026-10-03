import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import Popover from './Popover'
import {
  EMOJIS,
  TEMPLATES,
  TEXT_PRESETS,
  THUMB_FONTS,
  THUMB_H,
  THUMB_W,
  backgroundRect,
  cutoutLayer,
  placeCutout,
  resizeText,
  textFit,
  emptyDesign,
  exportJpeg,
  frameUrl,
  noteFrames,
  hitTest,
  loadCutout,
  loadFonts,
  loadImage,
  logoLayer,
  logoUrl,
  newId,
  render,
  shapeLayer,
  textLayer,
  thumbsApi,
  type Assets,
  type Background,
  type Bounds,
  type Layer,
  type Picture,
  type ThumbDesign
} from '../lib/thumbnails'

/** Design a thumbnail for one clip or compilation.
 *
 *  A frame from the video, adjusted, with layers on top: stylized text, the
 *  channel's logos, shapes, and the subject cut out of the frame by a local
 *  model. Drag things on the canvas to move them; the wheel resizes whatever
 *  is under it. Saving makes it THE thumbnail every publisher sends, and
 *  keeps the layers so it can be edited again.
 *
 *  `publishId` is a clip id, or the negative of a compilation's id. */
export default function ThumbnailStudio({
  publishId,
  onClose,
  onSaved
}: {
  publishId: number
  onClose: () => void
  onSaved?: () => void
}): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const miniRef = useRef<HTMLCanvasElement>(null)
  const bounds = useRef<Bounds[]>([])

  const [design, setDesignState] = useState<ThumbDesign | null>(null)
  const designRef = useRef<ThumbDesign | null>(null)
  designRef.current = design
  if (design) noteFrames(publishId, design) // before any frame is asked for
  const [selected, setSelected] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [duration, setDuration] = useState(0)
  const [frame, setFrame] = useState<Picture | null>(null)
  const [cutouts, setCutouts] = useState<Map<string, Picture>>(new Map())
  const [logos, setLogos] = useState<Map<string, Picture>>(new Map())
  const [brandAssets, setBrandAssets] = useState<{ asset: string; name: string }[]>([])
  const [best, setBest] = useState<{ t: number; face: boolean }[] | null>(null)
  const [ideas, setIdeas] = useState<string[]>([])
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showSafe, setShowSafe] = useState(true)
  const [dirty, setDirty] = useState(false)

  const working = (key: string, on: boolean): void => setBusy((b) => ({ ...b, [key]: on }))
  const flash = (m: string): void => {
    setNotice(m)
    setTimeout(() => setNotice(''), 3500)
  }

  // ---- history: undo/redo over settled states --------------------------------
  const hist = useRef<{ stack: ThumbDesign[]; i: number }>({ stack: [], i: -1 })
  const timer = useRef<number | undefined>(undefined)
  const push = (d: ThumbDesign): void => {
    const h = hist.current
    if (h.stack[h.i] === d) return
    h.stack = [...h.stack.slice(0, h.i + 1), d].slice(-100)
    h.i = h.stack.length - 1
  }
  const settle = (): void => {
    if (timer.current !== undefined) {
      clearTimeout(timer.current)
      timer.current = undefined
      if (designRef.current) push(designRef.current)
    }
  }
  /** Every edit goes through here. History records it once the edit has
   *  been still for a moment, so a slider drag is one undo step, not fifty. */
  const change = useCallback((next: ThumbDesign): void => {
    setDesignState(next)
    setDirty(true)
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      timer.current = undefined
      push(next)
    }, 350)
  }, [])
  const undo = (): void => {
    settle()
    const h = hist.current
    if (h.i > 0) {
      h.i -= 1
      setDesignState(h.stack[h.i])
    }
  }
  const redo = (): void => {
    settle()
    const h = hist.current
    if (h.i < h.stack.length - 1) {
      h.i += 1
      setDesignState(h.stack[h.i])
    }
  }

  const layer = design?.layers.find((l) => l.id === selected) ?? null
  const updateLayer = (id: string, patch: Partial<Layer>): void => {
    const d = designRef.current
    if (!d) return
    change({ ...d, layers: d.layers.map((l) => (l.id === id ? ({ ...l, ...patch } as Layer) : l)) })
  }
  const updateBg = (patch: Partial<Background>): void => {
    const d = designRef.current
    if (d) change({ ...d, background: { ...d.background, ...patch } })
  }
  const addLayer = (l: Layer): void => {
    const d = designRef.current
    if (!d) return
    change({ ...d, layers: [...d.layers, l] })
    setSelected(l.id)
  }
  const removeLayer = (id: string): void => {
    const d = designRef.current
    if (!d) return
    change({ ...d, layers: d.layers.filter((l) => l.id !== id) })
    if (selected === id) setSelected(null)
  }
  const moveLayer = (id: string, by: number): void => {
    const d = designRef.current
    if (!d) return
    const i = d.layers.findIndex((l) => l.id === id)
    const j = i + by
    if (i < 0 || j < 0 || j >= d.layers.length) return
    const layers = [...d.layers]
    ;[layers[i], layers[j]] = [layers[j], layers[i]]
    change({ ...d, layers })
  }
  const duplicate = (id: string): void => {
    const d = designRef.current
    const src = d?.layers.find((l) => l.id === id)
    if (!d || !src) return
    const copy = { ...structuredClone(src), id: newId(), x: src.x + 0.03, y: src.y + 0.03 } as Layer
    if (copy.kind === 'cutout') {
      const img = cutouts.get(src.id)
      if (img) setCutouts((m) => new Map(m).set(copy.id, img))
    }
    addLayer(copy)
  }

  // ---- loading ------------------------------------------------------------
  const cutFor = async (
    layerId: string,
    at: number,
    place?: { x: number; y: number; height: number }
  ): Promise<boolean> => {
    working('cutout', true)
    try {
      const img = await loadCutout(publishId, at)
      setCutouts((m) => new Map(m).set(layerId, img))
      if (place) {
        // The frame the subject was cut from gives the geometry.
        const f = await loadImage(frameUrl(publishId, at))
        const cur = designRef.current
        if (cur) {
          const patch = placeCutout(cur.background, f, img, place)
          change({
            ...cur,
            layers: cur.layers.map((l) => (l.id === layerId ? ({ ...l, ...patch } as Layer) : l))
          })
        }
      }
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return false
    } finally {
      working('cutout', false)
    }
  }

  const start = (d: ThumbDesign): void => {
    setDesignState(d)
    hist.current = { stack: [d], i: 0 }
    for (const l of d.layers) if (l.kind === 'cutout') void cutFor(l.id, l.t)
  }

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const src = await thumbsApi.source(publishId)
        if (!alive) return
        setTitle(src.title)
        setDuration(src.duration)
        const saved = src.has_design ? (await thumbsApi.design(publishId)).design : null
        if (!alive) return
        if (saved?.version === 1) {
          start(saved)
        } else {
          // A new design opens on the headline template at the best frame,
          // so there is something good to react to straight away.
          const found = await thumbsApi.bestFrames(publishId).catch(() => ({ frames: [] }))
          if (!alive) return
          setBest(found.frames)
          const at = found.frames[0]?.t ?? src.duration * 0.3
          start(TEMPLATES.find((x) => x.id === 'headline')!.make(at, headlineFrom(src.title)))
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        start(emptyDesign())
      }
    })()
    api
      .branding()
      .then((profiles) => {
        const seen = new Map<string, string>()
        for (const p of profiles) {
          const a = p.config?.image_asset
          if (a && !seen.has(a)) seen.set(a, p.name)
        }
        setBrandAssets([...seen].map(([asset, name]) => ({ asset, name })))
      })
      .catch(() => setBrandAssets([]))
    return () => {
      alive = false
      clearTimeout(timer.current)
    }
  }, [publishId])

  // The background frame, following scrubbing with a short delay.
  const bgT = design?.background.t
  useEffect(() => {
    if (bgT === undefined) return
    let alive = true
    const id = window.setTimeout(() => {
      working('frame', true)
      loadImage(frameUrl(publishId, bgT))
        .then((img) => alive && setFrame(img))
        .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)))
        .finally(() => working('frame', false))
    }, 120)
    return () => {
      alive = false
      clearTimeout(id)
    }
  }, [publishId, bgT])

  // Logos used by the design.
  const logoKey = design?.layers
    .filter((l) => l.kind === 'logo')
    .map((l) => (l.kind === 'logo' ? l.asset : ''))
    .join('|')
  useEffect(() => {
    for (const asset of (logoKey ?? '').split('|').filter(Boolean)) {
      if (logos.has(asset)) continue
      loadImage(logoUrl(asset))
        .then((img) => setLogos((m) => new Map(m).set(asset, img)))
        .catch(() =>
          setError(t('A logo could not be loaded. It may have been removed from Branding.'))
        )
    }
  }, [logoKey])

  // ---- drawing --------------------------------------------------------------
  const assets: Assets = useMemo(() => ({ frame, cutouts, logos }), [frame, cutouts, logos])
  const [fontsTick, setFontsTick] = useState(0)
  useEffect(() => {
    if (design) void loadFonts(design).then(() => setFontsTick((n) => n + 1))
  }, [
    design?.layers.map((l) => (l.kind === 'text' ? `${l.font}${l.weight}${l.italic}` : '')).join()
  ])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !design) return
    const raf = requestAnimationFrame(() => {
      bounds.current = render(canvas.getContext('2d')!, design, assets, selected)
      // The small copy: most people meet a thumbnail at this size.
      const mini = miniRef.current
      if (mini) {
        const m = mini.getContext('2d')!
        m.clearRect(0, 0, mini.width, mini.height)
        // Drawn without the selection box.
        render(m as CanvasRenderingContext2D, design, assets, null)
      }
    })
    return () => cancelAnimationFrame(raf)
  }, [design, assets, selected, fontsTick])

  // ---- direct manipulation -----------------------------------------------------
  const drag = useRef<{ id: string | null; x: number; y: number; ox: number; oy: number } | null>(
    null
  )
  const toCanvas = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = canvasRef.current!.getBoundingClientRect()
    return [((e.clientX - r.left) / r.width) * THUMB_W, ((e.clientY - r.top) / r.height) * THUMB_H]
  }
  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    if (!design) return
    const [x, y] = toCanvas(e)
    const id = hitTest(bounds.current, x, y)
    setSelected(id)
    const l = design.layers.find((q) => q.id === id)
    drag.current = l
      ? { id, x, y, ox: l.x, oy: l.y }
      : { id: null, x, y, ox: design.background.panX, oy: design.background.panY }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const d = drag.current
    const cur = designRef.current
    if (!d || !cur) return
    const [x, y] = toCanvas(e)
    if (d.id) {
      updateLayer(d.id, { x: d.ox + (x - d.x) / THUMB_W, y: d.oy + (y - d.y) / THUMB_H })
    } else if (frame) {
      // Dragging the picture itself pans it, when there is picture to pan.
      const r = backgroundRect(cur.background, frame.width, frame.height, cur.background.fill)
      const spanX = (THUMB_W - r.w) / 2
      const spanY = (THUMB_H - r.h) / 2
      updateBg({
        panX: spanX ? clamp(d.ox + (x - d.x) / spanX, -1, 1) : cur.background.panX,
        panY: spanY ? clamp(d.oy + (y - d.y) / spanY, -1, 1) : cur.background.panY
      })
    }
  }
  const onPointerUp = (): void => {
    drag.current = null
  }

  // The wheel resizes what is under the pointer (or zooms the picture).
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const onWheel = (e: WheelEvent): void => {
      const cur = designRef.current
      if (!cur) return
      e.preventDefault()
      const [x, y] = toCanvas(e)
      const id = hitTest(bounds.current, x, y)
      const k = e.deltaY < 0 ? 1.06 : 1 / 1.06
      const l = cur.layers.find((q) => q.id === id)
      if (!l) {
        change({
          ...cur,
          background: { ...cur.background, zoom: clamp(cur.background.zoom * k, 0.5, 4) }
        })
        return
      }
      setSelected(l.id)
      const patch: Partial<Layer> =
        l.kind === 'text'
          ? resizeText(l, clamp(textFit(l).drawn * k, 16, 400))
          : l.kind === 'logo'
            ? { width: clamp(l.width * k, 0.03, 0.9) }
            : l.kind === 'cutout'
              ? { scale: clamp(l.scale * k, 0.3, 3) }
              : { size: clamp(l.size * k, 30, 900) }
      change({
        ...cur,
        layers: cur.layers.map((q) => (q.id === l.id ? ({ ...q, ...patch } as Layer) : q))
      })
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', onWheel)
  }, [design !== null])

  // Keyboard: delete, nudge, duplicate, undo/redo. Not while typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      const mod = e.ctrlKey || e.metaKey
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
        return
      }
      if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
        return
      }
      if (e.key === 'Escape') {
        setSelected(null)
        return
      }
      if (!selected) return
      if (mod && e.key.toLowerCase() === 'd') {
        e.preventDefault()
        duplicate(selected)
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        removeLayer(selected)
      } else if (e.key.startsWith('Arrow')) {
        e.preventDefault()
        const l = designRef.current?.layers.find((q) => q.id === selected)
        if (!l) return
        const step = e.shiftKey ? 0.02 : 0.004
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0
        updateLayer(selected, { x: l.x + dx, y: l.y + dy })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])

  // ---- actions ------------------------------------------------------------
  const findBest = async (): Promise<void> => {
    working('best', true)
    try {
      setBest((await thumbsApi.bestFrames(publishId, 8)).frames)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      working('best', false)
    }
  }

  const getIdeas = async (): Promise<void> => {
    working('ideas', true)
    setError('')
    try {
      setIdeas((await thumbsApi.textIdeas(publishId)).ideas)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      working('ideas', false)
    }
  }

  const useIdea = (idea: string): void => {
    if (layer?.kind === 'text') updateLayer(layer.id, { text: idea })
    else addLayer(textLayer(idea, 'yellow', { y: 0.78 }))
  }

  const addCutout = async (): Promise<void> => {
    const d = designRef.current
    if (!d) return
    const l = cutoutLayer(d.background.t)
    addLayer(l)
    if (!(await cutFor(l.id, l.t))) removeLayer(l.id)
  }

  const recut = async (id: string): Promise<void> => {
    const d = designRef.current
    if (!d) return
    if (await cutFor(id, d.background.t)) updateLayer(id, { t: d.background.t })
  }

  const applyTemplate = async (id: string): Promise<void> => {
    const d = designRef.current
    const tpl = TEMPLATES.find((x) => x.id === id)
    if (!d || !tpl) return
    if (d.layers.length && !window.confirm(t('Replace the current design with this template?')))
      return
    const next = tpl.make(d.background.t, ideas[0] ?? headlineFrom(title))
    change(next)
    setSelected(null)
    for (const l of next.layers) {
      if (l.kind !== 'cutout') continue
      if (!(await cutFor(l.id, l.t, tpl.subject))) {
        const cur = designRef.current
        if (cur) change({ ...cur, layers: cur.layers.filter((q) => q.id !== l.id) })
        setError(
          t(
            'No person in this frame to cut out, so the template goes without one. Pick a frame with someone in it.'
          )
        )
      }
    }
  }

  const uploadLogo = async (): Promise<void> => {
    const path = await window.studio.pickImageFile()
    if (!path) return
    try {
      const { asset } = await api.uploadBrandingAsset(path)
      setBrandAssets((a) => [...a, { asset, name: path.split(/[\\/]/).pop() ?? asset }])
      addLayer(logoLayer(asset))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const save = async (close: boolean): Promise<void> => {
    const d = designRef.current
    if (!d) return
    working('save', true)
    setError('')
    try {
      const image = await exportJpeg(d, assets)
      await thumbsApi.save(publishId, image, d)
      setDirty(false)
      onSaved?.()
      if (close) onClose()
      else flash(t('Saved. It goes out with this video wherever a thumbnail is used.'))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      working('save', false)
    }
  }

  const close = (): void => {
    if (dirty && !window.confirm(t('Close without saving this thumbnail?'))) return
    onClose()
  }

  if (!design) {
    return (
      <Shell onClose={onClose}>
        <p className="text-sm text-muted p-8 text-center">
          {error || t('Opening the thumbnail designer…')}
        </p>
      </Shell>
    )
  }

  const bg = design.background
  const cutFromElsewhere = layer?.kind === 'cutout' && Math.abs(layer.t - bg.t) > 0.05

  return (
    <Shell onClose={close}>
      {/* header */}
      <div className="flex items-center gap-2 flex-wrap border-b border-raised/60 pb-3">
        <h3 className="font-semibold text-lg mr-2 truncate max-w-md">
          {t('Thumbnail')} · <span className="text-muted font-normal">{title}</span>
        </h3>
        <span className="text-xs text-muted">{t('Start from')}</span>
        {TEMPLATES.map((tpl) => (
          <button
            key={tpl.id}
            className="btn-ghost !py-1 !px-2 text-xs"
            onClick={() => void applyTemplate(tpl.id)}
            title={tpl.needsCutout ? t('Uses the AI subject cut-out') : ''}
          >
            {tpl.needsCutout ? '✨ ' : ''}
            {tpl.label}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn-ghost !py-1 text-xs" onClick={undo} title="Ctrl+Z">
            ↶
          </button>
          <button className="btn-ghost !py-1 text-xs" onClick={redo} title="Ctrl+Shift+Z">
            ↷
          </button>
          <button
            className="btn-ghost !py-1.5 text-sm"
            disabled={busy.save}
            onClick={() => void save(false)}
          >
            {t('Save')}
          </button>
          <button
            className="btn-accent !py-1.5 text-sm"
            disabled={busy.save}
            onClick={() => void save(true)}
          >
            {busy.save ? t('Saving…') : t('Save & close')}
          </button>
          <button className="btn-ghost !py-1.5 text-sm" onClick={close} aria-label={t('Close')}>
            ✕
          </button>
        </div>
      </div>

      <div className="grid lg:grid-cols-[1fr_20rem] gap-4 min-h-0 flex-1 overflow-hidden pt-3">
        {/* canvas + frame strip */}
        <div className="space-y-3 min-w-0 overflow-y-auto">
          <div className="relative w-full aspect-video rounded-lg overflow-hidden bg-black select-none">
            <canvas
              ref={canvasRef}
              width={THUMB_W}
              height={THUMB_H}
              className="w-full h-full cursor-move touch-none"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              aria-label={t('Thumbnail canvas')}
            />
            {showSafe && (
              // YouTube's duration badge sits here and covers whatever is under it.
              <div
                className="absolute pointer-events-none border border-dashed border-white/50 rounded bg-black/40 text-white/80 text-[10px] grid place-items-center"
                style={{ right: '1.2%', bottom: '2%', width: '8.5%', height: '8%' }}
              >
                12:34
              </div>
            )}
            {(busy.frame || busy.cutout) && (
              <span className="absolute top-2 left-2 text-[11px] bg-black/60 text-white px-2 py-0.5 rounded">
                {busy.cutout ? `✨ ${t('Cutting out the subject…')}` : t('Loading frame…')}
              </span>
            )}
          </div>

          <div className="flex items-start gap-3 flex-wrap">
            <div className="flex-1 min-w-64 space-y-1">
              <div className="flex items-center gap-2 text-xs">
                <span className="label">{t('Frame')}</span>
                <button
                  className="px-1.5 rounded bg-raised hover:text-ink text-muted"
                  onClick={() => updateBg({ t: Math.max(0, bg.t - 1 / 30) })}
                  aria-label={t('Previous frame')}
                >
                  ‹
                </button>
                <input
                  type="range"
                  min={0}
                  max={Math.max(0.1, duration)}
                  step={0.04}
                  value={bg.t}
                  onChange={(e) => updateBg({ t: Number(e.target.value) })}
                  className="flex-1 accent-[#38BDF8]"
                  aria-label={t('Frame time')}
                />
                <button
                  className="px-1.5 rounded bg-raised hover:text-ink text-muted"
                  onClick={() => updateBg({ t: Math.min(duration, bg.t + 1 / 30) })}
                  aria-label={t('Next frame')}
                >
                  ›
                </button>
                <span className="tabular-nums text-muted w-12 text-right">{bg.t.toFixed(2)}s</span>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <button
                  className="btn-ghost !py-1 text-xs"
                  disabled={busy.best}
                  onClick={() => void findBest()}
                >
                  {busy.best ? t('Looking…') : `✨ ${t('Best frames')}`}
                </button>
                <label className="flex items-center gap-1 text-muted ml-auto">
                  <input
                    type="checkbox"
                    checked={showSafe}
                    onChange={(e) => setShowSafe(e.target.checked)}
                  />
                  {t('Show where YouTube’s time badge goes')}
                </label>
              </div>
              {best && best.length > 0 && (
                <div className="flex gap-1.5 overflow-x-auto pb-1">
                  {best.map((f) => (
                    <button
                      key={f.t}
                      onClick={() => updateBg({ t: f.t })}
                      className={`relative shrink-0 rounded overflow-hidden border-2 ${
                        Math.abs(f.t - bg.t) < 0.05 ? 'border-accent' : 'border-transparent'
                      }`}
                      title={`${f.t.toFixed(2)}s${f.face ? ' · ' + t('face') : ''}`}
                    >
                      <img src={frameUrl(publishId, f.t)} alt="" className="h-16 w-auto" />
                      {f.face && (
                        <span className="absolute bottom-0.5 right-0.5 text-[9px] bg-black/60 text-white px-1 rounded">
                          🙂
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="text-[11px] text-muted space-y-1">
              <p>{t('As most people see it:')}</p>
              <canvas
                ref={miniRef}
                width={THUMB_W}
                height={THUMB_H}
                className="w-[168px] rounded border border-raised"
              />
            </div>
          </div>
          {error && <p className="text-xs text-error">{error}</p>}
          {notice && <p className="text-xs text-success">{notice}</p>}
          <p className="text-[11px] text-muted">
            {t(
              'Drag to move. Scroll over something to resize it, or over the picture to zoom. Arrow keys nudge, Delete removes, Ctrl+D duplicates, Ctrl+Z undoes.'
            )}
          </p>
        </div>

        {/* side panel */}
        <div className="overflow-y-auto space-y-4 pr-1 text-sm">
          <section className="space-y-2">
            <p className="label">{t('Add')}</p>
            <div className="grid grid-cols-3 gap-1.5 text-xs">
              <AddButton onClick={() => addLayer(textLayer(headlineFrom(title), 'impact'))}>
                T {t('Text')}
              </AddButton>
              <AddButton onClick={() => void addCutout()} disabled={busy.cutout}>
                ✨ {t('Subject')}
              </AddButton>
              <LogoMenu
                assets={brandAssets}
                onPick={(a) => addLayer(logoLayer(a))}
                onUpload={uploadLogo}
              />
              <AddButton onClick={() => addLayer(shapeLayer('arrow'))}>➜ {t('Arrow')}</AddButton>
              <AddButton onClick={() => addLayer(shapeLayer('ring'))}>◯ {t('Ring')}</AddButton>
              <AddButton onClick={() => addLayer(shapeLayer('box'))}>▭ {t('Box')}</AddButton>
            </div>
            <div className="flex flex-wrap gap-1">
              {EMOJIS.map((em) => (
                <button
                  key={em}
                  className="text-lg leading-none px-1 rounded hover:bg-raised"
                  onClick={() => addLayer(shapeLayer('emoji', em))}
                  aria-label={`${t('Add')} ${em}`}
                >
                  {em}
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-2">
            <div className="flex items-center gap-2">
              <p className="label">{t('Text ideas')}</p>
              <button
                className="btn-ghost !py-0.5 text-xs ml-auto"
                disabled={busy.ideas}
                onClick={() => void getIdeas()}
              >
                {busy.ideas
                  ? t('Writing…')
                  : ideas.length
                    ? `↻ ${t('More')}`
                    : `✨ ${t('Suggest')}`}
              </button>
            </div>
            {ideas.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {ideas.map((idea) => (
                  <button
                    key={idea}
                    className="px-2 py-0.5 rounded-md bg-accent/15 text-accent hover:bg-accent/25 text-xs"
                    onClick={() => useIdea(idea)}
                    title={
                      layer?.kind === 'text' ? t('Use for the selected text') : t('Add as text')
                    }
                  >
                    {idea}
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="space-y-1">
            <p className="label">{t('Layers')}</p>
            <ul className="border border-raised/60 rounded-lg divide-y divide-raised/60">
              {[...design.layers].reverse().map((l) => (
                <li
                  key={l.id}
                  className={`flex items-center gap-1 px-2 py-1 text-xs cursor-pointer ${
                    selected === l.id ? 'bg-accent/15' : 'hover:bg-raised/40'
                  }`}
                  onClick={() => setSelected(l.id)}
                >
                  <span className={`flex-1 truncate ${l.hidden ? 'text-muted line-through' : ''}`}>
                    {layerName(l)}
                  </span>
                  <IconButton
                    label={l.hidden ? t('Show') : t('Hide')}
                    onClick={() => updateLayer(l.id, { hidden: !l.hidden })}
                  >
                    {l.hidden ? '◌' : '👁'}
                  </IconButton>
                  <IconButton label={t('Bring forward')} onClick={() => moveLayer(l.id, 1)}>
                    ↑
                  </IconButton>
                  <IconButton label={t('Send back')} onClick={() => moveLayer(l.id, -1)}>
                    ↓
                  </IconButton>
                  <IconButton label={t('Duplicate')} onClick={() => duplicate(l.id)}>
                    ⧉
                  </IconButton>
                  <IconButton label={t('Delete')} onClick={() => removeLayer(l.id)}>
                    ✕
                  </IconButton>
                </li>
              ))}
              <li
                className={`px-2 py-1 text-xs cursor-pointer ${selected === null ? 'bg-accent/15' : 'hover:bg-raised/40'}`}
                onClick={() => setSelected(null)}
              >
                🖼 {t('Background')}
              </li>
            </ul>
          </section>

          <section className="space-y-2 border-t border-raised/60 pt-3">
            {layer === null && <BackgroundPanel bg={bg} onChange={updateBg} />}
            {layer?.kind === 'text' && (
              <TextPanel layer={layer} onChange={(p) => updateLayer(layer.id, p)} />
            )}
            {layer?.kind === 'logo' && (
              <LogoPanel
                layer={layer}
                assets={brandAssets}
                onChange={(p) => updateLayer(layer.id, p)}
              />
            )}
            {layer?.kind === 'cutout' && (
              <>
                {cutFromElsewhere && (
                  <p className="text-[11px] text-warn">
                    {t('This cut-out is from another moment than the background.')}{' '}
                    <button className="underline" onClick={() => void recut(layer.id)}>
                      {t('Re-cut at this frame')}
                    </button>
                  </p>
                )}
                <CutoutPanel
                  layer={layer}
                  onChange={(p) => updateLayer(layer.id, p)}
                  onPlace={(target) => {
                    const img = cutouts.get(layer.id)
                    const cur = designRef.current
                    if (!img || !cur) return
                    loadImage(frameUrl(publishId, layer.t))
                      .then((f) =>
                        updateLayer(layer.id, placeCutout(cur.background, f, img, target))
                      )
                      .catch(() => undefined)
                  }}
                />
              </>
            )}
            {layer?.kind === 'shape' && (
              <ShapePanel layer={layer} onChange={(p) => updateLayer(layer.id, p)} />
            )}
          </section>
        </div>
      </div>
    </Shell>
  )
}

// ---- helpers ------------------------------------------------------------------

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

/** A first line of text from the title: the first few words, which is all a
 *  thumbnail has room for. */
function headlineFrom(title: string): string {
  const words = title.replace(/[#"]/g, '').split(/\s+/).filter(Boolean)
  return words.slice(0, 4).join(' ') || 'WATCH THIS'
}

function layerName(l: Layer): string {
  if (l.kind === 'text') return `T  ${l.text.split('\n')[0] || '(empty)'}`
  if (l.kind === 'logo') return '◆  Logo'
  if (l.kind === 'cutout') return '✨ Subject'
  if (l.shape === 'emoji') return `${l.emoji}  Emoji`
  return `${l.shape === 'arrow' ? '➜' : l.shape === 'ring' ? '◯' : '▭'}  ${l.shape[0].toUpperCase()}${l.shape.slice(1)}`
}

function Shell({
  children,
  onClose
}: {
  children: React.ReactNode
  onClose: () => void
}): JSX.Element {
  return (
    <div
      className="fixed inset-0 z-50 bg-base/85 backdrop-blur-sm p-4 flex"
      role="dialog"
      aria-modal="true"
      aria-label="Thumbnail designer"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-7xl mx-auto flex flex-col max-h-full overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}

function AddButton({
  children,
  onClick,
  disabled
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
}): JSX.Element {
  return (
    <button
      className="bg-raised hover:bg-raised/70 rounded-md px-2 py-1.5 text-left truncate disabled:opacity-50"
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  )
}

function IconButton({
  children,
  label,
  onClick
}: {
  children: React.ReactNode
  label: string
  onClick: () => void
}): JSX.Element {
  return (
    <button
      className="px-1 text-muted hover:text-ink"
      title={label}
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
    >
      {children}
    </button>
  )
}

function LogoMenu({
  assets,
  onPick,
  onUpload
}: {
  assets: { asset: string; name: string }[]
  onPick: (asset: string) => void
  onUpload: () => void
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLSpanElement>(null)
  return (
    <span ref={trigger} className="grid">
      <AddButton onClick={() => setOpen((o) => !o)}>◆ {t('Logo')}</AddButton>
      <Popover
        anchor={trigger}
        open={open}
        onClose={() => setOpen(false)}
        width={224}
        className="card !p-1 space-y-0.5 shadow-xl"
        label={t('Add a logo')}
      >
          {assets.map((a) => (
            <button
              key={a.asset}
              className="w-full flex items-center gap-2 px-2 py-1 rounded hover:bg-raised text-left text-xs"
              onClick={() => {
                onPick(a.asset)
                setOpen(false)
              }}
            >
              <img src={logoUrl(a.asset)} alt="" className="w-6 h-6 object-contain" />
              <span className="truncate">{a.name}</span>
            </button>
          ))}
          <button
            className="w-full px-2 py-1 rounded hover:bg-raised text-left text-xs text-accent"
            onClick={() => {
              setOpen(false)
              onUpload()
            }}
          >
            + {t('Upload an image…')}
          </button>
          {assets.length === 0 && (
            <p className="px-2 py-1 text-[11px] text-muted">
              {t('Logos from your Branding profiles show here.')}
            </p>
          )}
      </Popover>
    </span>
  )
}

// ---- property panels -------------------------------------------------------------

function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  format
}: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
}): JSX.Element {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 text-muted">{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="flex-1 accent-[#38BDF8]"
      />
      <span className="tabular-nums w-10 text-right">
        {format ? format(value) : Math.round(value)}
      </span>
    </label>
  )
}

function Color({
  label,
  value,
  onChange
}: {
  label: string
  value: string
  onChange: (v: string) => void
}): JSX.Element {
  return (
    <label className="flex items-center gap-1 text-xs">
      {label}
      <input
        type="color"
        value={toHex(value)}
        onChange={(e) => onChange(e.target.value)}
        className="h-6 w-8 bg-raised rounded cursor-pointer border border-raised"
      />
    </label>
  )
}

/** <input type=color> only takes #rrggbb. */
function toHex(c: string): string {
  if (/^#[0-9a-f]{6}$/i.test(c)) return c
  const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i)
  if (!m) return '#000000'
  return '#' + [m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')
}

function Toggle({
  label,
  on,
  onChange
}: {
  label: string
  on: boolean
  onChange: (on: boolean) => void
}): JSX.Element {
  return (
    <label className="flex items-center gap-1.5 text-xs font-medium">
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}

function Common({
  layer,
  onChange
}: {
  layer: Layer
  onChange: (p: Partial<Layer>) => void
}): JSX.Element {
  return (
    <>
      <Slider
        label={t('Rotate')}
        value={layer.rotation}
        min={-45}
        max={45}
        onChange={(v) => onChange({ rotation: v })}
        format={(v) => `${Math.round(v)}°`}
      />
      <Slider
        label={t('Opacity')}
        value={Math.round(layer.opacity * 100)}
        min={10}
        max={100}
        onChange={(v) => onChange({ opacity: v / 100 })}
        format={(v) => `${v}%`}
      />
      <div className="flex gap-1 text-[11px]">
        <button
          className="px-2 py-0.5 rounded bg-raised text-muted hover:text-ink"
          onClick={() => onChange({ x: 0.5 })}
        >
          {t('Centre across')}
        </button>
        <button
          className="px-2 py-0.5 rounded bg-raised text-muted hover:text-ink"
          onClick={() => onChange({ y: 0.5 })}
        >
          {t('Centre down')}
        </button>
      </div>
    </>
  )
}

function TextPanel({
  layer: l,
  onChange
}: {
  layer: Extract<Layer, { kind: 'text' }>
  onChange: (p: Partial<Layer>) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      <textarea
        className="input !py-1.5 text-sm min-h-[56px] resize-y"
        value={l.text}
        onChange={(e) => onChange({ text: e.target.value })}
        aria-label={t('Text')}
      />
      <div className="flex flex-wrap gap-1">
        {TEXT_PRESETS.map((p) => (
          <button
            key={p.id}
            className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink text-[11px]"
            onClick={() => onChange(structuredClone(p.style))}
          >
            {p.label}
          </button>
        ))}
      </div>
      <div className="flex gap-2 items-center">
        <select
          className="input !py-1 text-xs flex-1"
          value={l.font}
          onChange={(e) => onChange({ font: e.target.value })}
        >
          {THUMB_FONTS.map((f) => (
            <option key={f} value={f} style={{ fontFamily: `'${f}'` }}>
              {f}
            </option>
          ))}
        </select>
        <select
          className="input !py-1 text-xs !w-20"
          value={l.weight}
          onChange={(e) => onChange({ weight: Number(e.target.value) })}
          aria-label={t('Weight')}
        >
          <option value={400}>{t('Regular')}</option>
          <option value={700}>{t('Bold')}</option>
          <option value={900}>{t('Black')}</option>
        </select>
      </div>
      {/* The size as drawn, not as asked for: with Shrink to fit on, those
          differ, and a slider showing the asked-for size moved nothing. */}
      <Slider
        label={t('Size')}
        value={Math.round(textFit(l).drawn)}
        min={20}
        max={320}
        onChange={(v) => onChange(resizeText(l, v))}
      />
      <div className="flex items-center gap-2">
        <Toggle
          label={t('Shrink to fit')}
          on={l.maxWidth !== undefined}
          onChange={(v) => onChange({ maxWidth: v ? 0.92 : undefined })}
        />
        {l.maxWidth !== undefined && (
          <div className="flex-1">
            <Slider
              label=""
              value={Math.round(l.maxWidth * 100)}
              min={20}
              max={100}
              onChange={(v) => onChange({ maxWidth: v / 100 })}
              format={(v) => `${v}%`}
            />
          </div>
        )}
      </div>
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle label="AA" on={l.uppercase} onChange={(v) => onChange({ uppercase: v })} />
        <Toggle label={t('Italic')} on={l.italic} onChange={(v) => onChange({ italic: v })} />
        <Color label={t('Fill')} value={l.color} onChange={(v) => onChange({ color: v })} />
        <Toggle
          label={t('Gradient')}
          on={l.gradient !== null}
          onChange={(v) => onChange({ gradient: v ? '#FF3D00' : null })}
        />
        {l.gradient && (
          <Color label="→" value={l.gradient} onChange={(v) => onChange({ gradient: v })} />
        )}
      </div>
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Outline')}
          on={l.stroke !== null}
          onChange={(v) => onChange({ stroke: v ? { color: '#000000', width: 10 } : null })}
        />
        {l.stroke && (
          <Color
            label=""
            value={l.stroke.color}
            onChange={(v) => onChange({ stroke: { ...l.stroke!, color: v } })}
          />
        )}
      </div>
      {l.stroke && (
        <Slider
          label={t('Outline')}
          value={l.stroke.width}
          min={1}
          max={30}
          onChange={(v) => onChange({ stroke: { ...l.stroke!, width: v } })}
        />
      )}
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Shadow')}
          on={l.shadow !== null}
          onChange={(v) =>
            onChange({ shadow: v ? { color: 'rgba(0,0,0,0.8)', blur: 10, dx: 6, dy: 6 } : null })
          }
        />
        {l.shadow && (
          <Color
            label=""
            value={l.shadow.color}
            onChange={(v) => onChange({ shadow: { ...l.shadow!, color: v } })}
          />
        )}
      </div>
      {l.shadow && (
        <>
          <Slider
            label={t('Softness')}
            value={l.shadow.blur}
            min={0}
            max={50}
            onChange={(v) => onChange({ shadow: { ...l.shadow!, blur: v } })}
          />
          <Slider
            label={t('Depth')}
            value={l.shadow.dy}
            min={0}
            max={30}
            onChange={(v) =>
              onChange({ shadow: { ...l.shadow!, dx: l.shadow!.dx === 0 ? 0 : v, dy: v } })
            }
          />
        </>
      )}
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Glow')}
          on={l.glow !== null}
          onChange={(v) => onChange({ glow: v ? { color: '#00E5FF', blur: 30 } : null })}
        />
        {l.glow && (
          <Color
            label=""
            value={l.glow.color}
            onChange={(v) => onChange({ glow: { ...l.glow!, color: v } })}
          />
        )}
      </div>
      {l.glow && (
        <Slider
          label={t('Glow')}
          value={l.glow.blur}
          min={4}
          max={80}
          onChange={(v) => onChange({ glow: { ...l.glow!, blur: v } })}
        />
      )}
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Box')}
          on={l.box !== null}
          onChange={(v) =>
            onChange({ box: v ? { color: '#E62117', padding: 20, radius: 10 } : null })
          }
        />
        {l.box && (
          <Color
            label=""
            value={l.box.color}
            onChange={(v) => onChange({ box: { ...l.box!, color: v } })}
          />
        )}
      </div>
      {l.box && (
        <>
          <Slider
            label={t('Padding')}
            value={l.box.padding}
            min={4}
            max={60}
            onChange={(v) => onChange({ box: { ...l.box!, padding: v } })}
          />
          <Slider
            label={t('Corners')}
            value={l.box.radius}
            min={0}
            max={60}
            onChange={(v) => onChange({ box: { ...l.box!, radius: v } })}
          />
        </>
      )}
      <Slider
        label={t('Spacing')}
        value={l.letterSpacing}
        min={-6}
        max={30}
        onChange={(v) => onChange({ letterSpacing: v })}
      />
      <Slider
        label={t('Line height')}
        value={l.lineHeight}
        min={0.7}
        max={1.6}
        step={0.05}
        onChange={(v) => onChange({ lineHeight: v })}
        format={(v) => v.toFixed(2)}
      />
      <Common layer={l} onChange={onChange} />
    </div>
  )
}

function LogoPanel({
  layer: l,
  assets,
  onChange
}: {
  layer: Extract<Layer, { kind: 'logo' }>
  assets: { asset: string; name: string }[]
  onChange: (p: Partial<Layer>) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      {assets.length > 1 && (
        <select
          className="input !py-1 text-xs"
          value={l.asset}
          onChange={(e) => onChange({ asset: e.target.value })}
        >
          {assets.map((a) => (
            <option key={a.asset} value={a.asset}>
              {a.name}
            </option>
          ))}
        </select>
      )}
      <Slider
        label={t('Size')}
        value={Math.round(l.width * 100)}
        min={3}
        max={80}
        onChange={(v) => onChange({ width: v / 100 })}
        format={(v) => `${v}%`}
      />
      <div className="flex gap-1 text-xs">
        {(['free', 'square', 'circle'] as const).map((f) => (
          <button
            key={f}
            className={`px-2 py-1 rounded-md ${l.frame === f ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
            onClick={() => onChange({ frame: f })}
          >
            {f === 'free' ? t('Free form') : f === 'square' ? t('Square') : t('Circle')}
          </button>
        ))}
      </div>
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Border')}
          on={l.border !== null}
          onChange={(v) => onChange({ border: v ? { color: '#FFFFFF', width: 8 } : null })}
        />
        {l.border && (
          <Color
            label=""
            value={l.border.color}
            onChange={(v) => onChange({ border: { ...l.border!, color: v } })}
          />
        )}
        <Toggle label={t('Shadow')} on={l.shadow} onChange={(v) => onChange({ shadow: v })} />
      </div>
      {l.border && (
        <Slider
          label={t('Border')}
          value={l.border.width}
          min={1}
          max={30}
          onChange={(v) => onChange({ border: { ...l.border!, width: v } })}
        />
      )}
      <Common layer={l} onChange={onChange} />
    </div>
  )
}

function CutoutPanel({
  layer: l,
  onChange,
  onPlace
}: {
  layer: Extract<Layer, { kind: 'cutout' }>
  onChange: (p: Partial<Layer>) => void
  /** Size and move the subject by its outline, not its frame. */
  onPlace?: (target: { x: number; y: number; height: number }) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted">
        {t(
          'The people in the frame, cut out by a model on this computer. Put it over big text or a blurred background so it pops.'
        )}
      </p>
      <Slider
        label={t('Size')}
        value={Math.round(l.scale * 100)}
        min={30}
        max={300}
        onChange={(v) => onChange({ scale: v / 100 })}
        format={(v) => `${v}%`}
      />
      <div className="flex flex-wrap gap-1">
        {onPlace && (
          <>
            <button
              className="px-2 py-0.5 rounded bg-raised text-muted hover:text-ink text-[11px]"
              onClick={() => onPlace({ x: 0.72, y: 0.55, height: 1 })}
            >
              {t('Big, right')}
            </button>
            <button
              className="px-2 py-0.5 rounded bg-raised text-muted hover:text-ink text-[11px]"
              onClick={() => onPlace({ x: 0.28, y: 0.55, height: 1 })}
            >
              {t('Big, left')}
            </button>
          </>
        )}
        <button
          className="px-2 py-0.5 rounded bg-raised text-muted hover:text-ink text-[11px]"
          onClick={() => onChange({ x: 0.5, y: 0.5, scale: 1, rotation: 0 })}
        >
          {t('Line up with the background')}
        </button>
      </div>
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Sticker edge')}
          on={l.outline !== null}
          onChange={(v) => onChange({ outline: v ? { color: '#FFFFFF', width: 10 } : null })}
        />
        {l.outline && (
          <Color
            label=""
            value={l.outline.color}
            onChange={(v) => onChange({ outline: { ...l.outline!, color: v } })}
          />
        )}
      </div>
      {l.outline && (
        <Slider
          label={t('Edge')}
          value={l.outline.width}
          min={1}
          max={30}
          onChange={(v) => onChange({ outline: { ...l.outline!, width: v } })}
        />
      )}
      <div className="flex flex-wrap gap-3 items-center">
        <Toggle
          label={t('Glow')}
          on={l.glow !== null}
          onChange={(v) => onChange({ glow: v ? { color: '#000000', blur: 30 } : null })}
        />
        {l.glow && (
          <Color
            label=""
            value={l.glow.color}
            onChange={(v) => onChange({ glow: { ...l.glow!, color: v } })}
          />
        )}
      </div>
      {l.glow && (
        <Slider
          label={t('Glow')}
          value={l.glow.blur}
          min={4}
          max={80}
          onChange={(v) => onChange({ glow: { ...l.glow!, blur: v } })}
        />
      )}
      <Common layer={l} onChange={onChange} />
    </div>
  )
}

function ShapePanel({
  layer: l,
  onChange
}: {
  layer: Extract<Layer, { kind: 'shape' }>
  onChange: (p: Partial<Layer>) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      {l.shape === 'emoji' ? (
        <div className="flex flex-wrap gap-1">
          {EMOJIS.map((em) => (
            <button
              key={em}
              className={`text-lg leading-none px-1 rounded ${em === l.emoji ? 'bg-accent/20' : 'hover:bg-raised'}`}
              onClick={() => onChange({ emoji: em })}
            >
              {em}
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-3 items-center">
          <Color label={t('Colour')} value={l.color} onChange={(v) => onChange({ color: v })} />
        </div>
      )}
      <Slider
        label={t('Size')}
        value={l.size}
        min={30}
        max={900}
        onChange={(v) => onChange({ size: v })}
      />
      {l.shape !== 'emoji' && (
        <Slider
          label={t('Thickness')}
          value={l.thickness}
          min={4}
          max={120}
          onChange={(v) => onChange({ thickness: v })}
        />
      )}
      <Common layer={l} onChange={onChange} />
    </div>
  )
}

function BackgroundPanel({
  bg,
  onChange
}: {
  bg: Background
  onChange: (p: Partial<Background>) => void
}): JSX.Element {
  return (
    <div className="space-y-2">
      <p className="text-[11px] text-muted">
        {t('Drag the picture to reposition it; scroll over it to zoom.')}
      </p>
      <div className="flex gap-1 text-xs">
        {(['cover', 'fit'] as const).map((f) => (
          <button
            key={f}
            className={`px-2 py-1 rounded-md ${bg.fill === f ? 'bg-accent/20 text-accent' : 'bg-raised text-muted hover:text-ink'}`}
            onClick={() => onChange({ fill: f, zoom: 1, panX: 0, panY: 0 })}
            title={
              f === 'fit'
                ? t('The whole picture over a blurred copy: good for vertical clips')
                : t('Fill the frame, cropping')
            }
          >
            {f === 'cover' ? t('Fill') : t('Fit + blur')}
          </button>
        ))}
      </div>
      <Slider
        label={t('Zoom')}
        value={bg.zoom}
        min={0.5}
        max={4}
        step={0.05}
        onChange={(v) => onChange({ zoom: v })}
        format={(v) => `${v.toFixed(2)}×`}
      />
      <Slider
        label={t('Blur')}
        value={bg.blur}
        min={0}
        max={30}
        onChange={(v) => onChange({ blur: v })}
      />
      <Slider
        label={t('Brightness')}
        value={Math.round(bg.brightness * 100)}
        min={20}
        max={180}
        onChange={(v) => onChange({ brightness: v / 100 })}
        format={(v) => `${v}%`}
      />
      <Slider
        label={t('Contrast')}
        value={Math.round(bg.contrast * 100)}
        min={50}
        max={200}
        onChange={(v) => onChange({ contrast: v / 100 })}
        format={(v) => `${v}%`}
      />
      <Slider
        label={t('Saturation')}
        value={Math.round(bg.saturation * 100)}
        min={0}
        max={250}
        onChange={(v) => onChange({ saturation: v / 100 })}
        format={(v) => `${v}%`}
      />
      <Slider
        label={t('Vignette')}
        value={Math.round(bg.vignette * 100)}
        min={0}
        max={100}
        onChange={(v) => onChange({ vignette: v / 100 })}
        format={(v) => `${v}%`}
      />
      <div className="flex items-center gap-2">
        <Color label={t('Tint')} value={bg.tint} onChange={(v) => onChange({ tint: v })} />
        <div className="flex-1">
          <Slider
            label=""
            value={Math.round(bg.tintAmount * 100)}
            min={0}
            max={80}
            onChange={(v) => onChange({ tintAmount: v / 100 })}
            format={(v) => `${v}%`}
          />
        </div>
      </div>
    </div>
  )
}
