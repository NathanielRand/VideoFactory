// The thumbnail designer: its document model, the canvas renderer, presets,
// templates and the engine routes (server/thumbnails_api.py).
//
// One renderer draws both the live editor and the exported JPEG, so what is
// on screen is exactly what gets saved. Everything is laid out on a fixed
// 1280x720 canvas (YouTube's size) in canvas pixels; the editor only scales
// the element on screen.
import { API_BASE } from './api'

export const THUMB_W = 1280
export const THUMB_H = 720
const MAX_BYTES = 2 * 1024 * 1024 // YouTube's limit, checked again on save

// ---- the document -----------------------------------------------------------

export interface Background {
  /** Seconds into the video. */
  t: number
  /** cover: fill the frame, cropping. fit: the whole picture over a blurred copy. */
  fill: 'cover' | 'fit'
  zoom: number // 1 = just filling
  panX: number // -1..1: which part of an overflowing picture shows
  panY: number
  blur: number // px
  brightness: number // 1 = unchanged
  contrast: number
  saturation: number
  vignette: number // 0..1
  tint: string
  tintAmount: number // 0..1
  /** A soft dark gradient from one side, so text over a busy frame stays
   *  readable without needing a heavier outline. */
  scrim?: { side: 'left' | 'bottom' | 'top'; amount: number }
}

interface LayerBase {
  id: string
  x: number // centre, 0..1 of the canvas
  y: number
  rotation: number // degrees
  opacity: number // 0..1
  hidden?: boolean
}

export interface Stroke {
  color: string
  width: number
}

export interface TextLayer extends LayerBase {
  kind: 'text'
  text: string
  font: string
  size: number // px
  weight: number
  italic: boolean
  uppercase: boolean
  color: string
  /** Second colour for a top-to-bottom gradient fill, or null. */
  gradient: string | null
  stroke: Stroke | null
  shadow: { color: string; blur: number; dx: number; dy: number } | null
  glow: { color: string; blur: number } | null
  box: { color: string; padding: number; radius: number } | null
  letterSpacing: number
  /** Extra space between words, in ems of the text size. Unset in designs
   *  saved before it existed, which draw as they always did. */
  wordSpacing?: number
  lineHeight: number
  /** Shrinks to stay within this share of the canvas width, so a long
   *  headline never runs off the edge. Unset: no limit. */
  maxWidth?: number
}

export interface LogoLayer extends LayerBase {
  kind: 'logo'
  asset: string
  width: number // fraction of the canvas width
  frame: 'free' | 'square' | 'circle'
  border: Stroke | null
  shadow: boolean
}

export interface CutoutLayer extends LayerBase {
  kind: 'cutout'
  /** The frame it was cut from; follows the background unless unlinked. */
  t: number
  /** Relative to the background: 1 lines up exactly with the frame. x/y 0.5 = aligned. */
  scale: number
  outline: Stroke | null
  glow: { color: string; blur: number } | null
}

export interface ShapeLayer extends LayerBase {
  kind: 'shape'
  shape: 'arrow' | 'ring' | 'box' | 'emoji'
  size: number // px, the long side
  color: string
  thickness: number
  emoji: string
}

export type Layer = TextLayer | LogoLayer | CutoutLayer | ShapeLayer

export interface ThumbDesign {
  version: 1
  /** Which video the frames come from. 'source' is the original, full-size and
   *  free of captions. Designs saved before that existed have no marker and
   *  reopen on the rendered clip's frames, so their layers still line up. */
  frames?: 'source'
  background: Background
  layers: Layer[] // bottom first
}

export const newId = (): string => Math.random().toString(36).slice(2, 10)

export const DEFAULT_BACKGROUND: Background = {
  t: 0,
  fill: 'cover',
  zoom: 1,
  panX: 0,
  panY: 0,
  blur: 0,
  brightness: 1,
  contrast: 1.1,
  saturation: 1.15,
  vignette: 0.25,
  tint: '#000000',
  tintAmount: 0
}

export function emptyDesign(t = 0): ThumbDesign {
  return { version: 1, frames: 'source', background: { ...DEFAULT_BACKGROUND, t }, layers: [] }
}

// ---- presets ------------------------------------------------------------------

// ---- colour -------------------------------------------------------------------

function rgbOf(color: string): [number, number, number] {
  const m = /^#?([0-9a-f]{6})$/i.exec(color.trim())
  if (!m) return [128, 128, 128]
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** WCAG relative luminance, 0 (black) to 1 (white). */
function luminance([r, g, b]: [number, number, number]): number {
  const lin = (v: number): number => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

/** WCAG contrast ratio between two colours, 1 (identical) to 21. */
export function contrastRatio(a: string | [number, number, number], b: string | [number, number, number]): number {
  const la = luminance(typeof a === 'string' ? rgbOf(a) : a)
  const lb = luminance(typeof b === 'string' ? rgbOf(b) : b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Whichever of white or near-black reads better on this fill. White on
 *  bright yellow is 1.1:1; this is what keeps it from ever being chosen. */
export function readableOn(fill: string): string {
  return contrastRatio('#FFFFFF', fill) >= contrastRatio('#111111', fill) ? '#FFFFFF' : '#111111'
}

/** Accents that hold a white or black label and stand apart from each other. */
const ACCENTS = ['#E11D2E', '#1D4ED8', '#F97316', '#16A34A', '#7C3AED']

/** The mood's accent, unless it would vanish into this picture, in which case
 *  the accent that stands out most from it. */
export function pickAccent(preferred: string, average: [number, number, number]): string {
  if (contrastRatio(preferred, average) >= 1.8) return preferred
  return ACCENTS.reduce((best, c) => (contrastRatio(c, average) > contrastRatio(best, average) ? c : best))
}

/** A headline as at most two balanced lines: three words on one line read as a
 *  sentence, on two they read as a headline, and shrink-to-fit then has more to work with. */
export function balanceLines(text: string): string {
  const words = text.trim().split(/\s+/).filter(Boolean)
  if (words.length < 3 || text.length <= 12) return words.join(' ')
  let best = 1
  let bestWidest = Infinity
  for (let i = 1; i < words.length; i++) {
    const widest = Math.max(words.slice(0, i).join(' ').length, words.slice(i).join(' ').length)
    if (widest < bestWidest) {
      best = i
      bestWidest = widest
    }
  }
  return `${words.slice(0, best).join(' ')}\n${words.slice(best).join(' ')}`
}

/** Fonts every Windows machine has that read at thumbnail size. */
export const THUMB_FONTS = [
  'Impact',
  'Arial Black',
  'Segoe UI Black',
  'Bahnschrift',
  'Trebuchet MS',
  'Verdana',
  'Georgia',
  'Comic Sans MS',
  'Segoe Print',
  'Arial'
]

type TextStyle = Omit<TextLayer, keyof LayerBase | 'kind' | 'text'>

const baseText: TextStyle = {
  font: 'Impact',
  size: 120,
  weight: 400,
  italic: false,
  uppercase: true,
  color: '#FFFFFF',
  gradient: null,
  stroke: { color: '#000000', width: 10 },
  shadow: { color: 'rgba(0,0,0,0.6)', blur: 12, dx: 0, dy: 6 },
  glow: null,
  box: null,
  letterSpacing: 1.5,
  wordSpacing: 0.22,
  lineHeight: 1.08
}

export const TEXT_PRESETS: { id: string; label: string; style: TextStyle }[] = [
  { id: 'impact', label: 'Impact', style: baseText },
  {
    id: 'bold',
    label: 'Bold',
    style: {
      ...baseText,
      font: 'Segoe UI Black',
      weight: 900,
      size: 116,
      letterSpacing: 1,
      stroke: { color: '#000000', width: 9 },
      shadow: { color: 'rgba(0,0,0,0.55)', blur: 14, dx: 0, dy: 6 }
    }
  },
  {
    id: 'yellow',
    label: 'Yellow Pop',
    style: {
      ...baseText,
      font: 'Arial Black',
      weight: 900,
      size: 110,
      color: '#FFE600',
      stroke: { color: '#000000', width: 14 },
      shadow: { color: 'rgba(0,0,0,0.85)', blur: 0, dx: 8, dy: 8 }
    }
  },
  {
    id: 'redbox',
    label: 'Red Box',
    style: {
      ...baseText,
      font: 'Arial Black',
      weight: 900,
      size: 84,
      stroke: null,
      shadow: null,
      box: { color: '#E62117', padding: 22, radius: 10 }
    }
  },
  {
    id: 'neon',
    label: 'Neon',
    style: {
      ...baseText,
      font: 'Segoe UI Black',
      weight: 900,
      size: 104,
      stroke: { color: '#00E5FF', width: 4 },
      shadow: null,
      glow: { color: '#00E5FF', blur: 36 }
    }
  },
  {
    id: 'fire',
    label: 'Fire',
    style: {
      ...baseText,
      size: 130,
      color: '#FFE259',
      gradient: '#FF3D00',
      stroke: { color: '#1A0000', width: 12 }
    }
  },
  {
    id: 'comic',
    label: 'Comic',
    style: {
      ...baseText,
      font: 'Comic Sans MS',
      weight: 700,
      size: 96,
      uppercase: false,
      stroke: { color: '#000000', width: 9 },
      shadow: { color: '#000000', blur: 0, dx: 5, dy: 5 }
    }
  },
  {
    id: 'clean',
    label: 'Clean',
    style: {
      ...baseText,
      font: 'Segoe UI Black',
      weight: 900,
      size: 88,
      uppercase: false,
      stroke: null,
      shadow: { color: 'rgba(0,0,0,0.7)', blur: 24, dx: 0, dy: 4 }
    }
  }
]

export function textLayer(
  text: string,
  preset = 'impact',
  at: Partial<Omit<TextLayer, 'kind'>> = {}
): TextLayer {
  const style = structuredClone(TEXT_PRESETS.find((p) => p.id === preset)?.style ?? baseText)
  // A preset's outline, shadow and box are drawn for its own size. Asked for
  // another (a 58px line from an 84px preset), they scale with it, or the
  // outline clogs the letters and the box swallows the words.
  const k = at.size ? at.size / style.size : 1
  if (k !== 1) {
    if (style.stroke) style.stroke.width = Math.max(2, Math.round(style.stroke.width * k))
    if (style.shadow) {
      style.shadow.dx = Math.round(style.shadow.dx * k)
      style.shadow.dy = Math.round(style.shadow.dy * k)
      style.shadow.blur = Math.round(style.shadow.blur * k)
    }
    if (style.box) style.box.padding = Math.round(style.box.padding * k)
  }
  const layer: TextLayer = {
    id: newId(),
    kind: 'text',
    text,
    x: 0.5,
    y: 0.5,
    rotation: 0,
    opacity: 1,
    maxWidth: 0.92,
    ...style,
    ...at
  }
  // Text on a filled box takes whichever of white or black reads on that fill.
  if (layer.box && !at.color && contrastRatio(layer.color, layer.box.color) < 4.5) {
    layer.color = readableOn(layer.box.color)
  }
  return layer
}

export const EMOJIS = [
  // reactions
  '😱', '😂', '🤯', '💀', '😡', '🥶', '😭', '🤣', '😳', '😬', '🤬', '🥵', '😈', '🤡', '🫠', '😤', '🥹', '😎', '🤔', '🙄',
  // attention
  '👀', '❗', '❓', '‼️', '⁉️', '⚠️', '🚨', '🛑', '⛔', '🔴', '🆘', '🆕', '🔞', '🔊', '📢', '🔔',
  // hype and wins
  '🔥', '💯', '🏆', '👑', '💥', '⚡', '💎', '🚀', '✅', '❌', '➡️', '⬆️', '⬇️', '🎯', '🥇', '💪', '👏', '🙌', '🎉', '✨', '💰', '💸', '📈', '📉',
  // hands and bodies
  '👆', '👇', '👉', '👈', '👍', '👎', '🤞', '🫡', '🫵', '🧠', '🫣', '🤌',
  // things
  '⚔️', '🔫', '💣', '🧨', '🎮', '🕹️', '🏀', '⚽', '🏈', '🚗', '🏎️', '✈️', '🍕', '🍔', '🐐', '🐍', '🦈', '👻', '☠️', '👽', '🤖', '🌍', '❄️', '🌪️', '☄️'
]

export function shapeLayer(shape: ShapeLayer['shape'], emoji = '😱'): ShapeLayer {
  return {
    id: newId(),
    kind: 'shape',
    shape,
    x: 0.5,
    y: 0.5,
    rotation: 0,
    opacity: 1,
    size: shape === 'emoji' ? 160 : shape === 'arrow' ? 260 : 220,
    color: shape === 'emoji' ? '#FFFFFF' : '#FF1E1E',
    thickness: shape === 'arrow' ? 46 : 16,
    emoji
  }
}

export function logoLayer(asset: string): LogoLayer {
  return {
    id: newId(),
    kind: 'logo',
    asset,
    x: 0.88,
    y: 0.16,
    rotation: 0,
    opacity: 1,
    width: 0.16,
    frame: 'free',
    border: null,
    shadow: true
  }
}

export function cutoutLayer(t: number): CutoutLayer {
  return {
    id: newId(),
    kind: 'cutout',
    t,
    x: 0.5,
    y: 0.5,
    rotation: 0,
    opacity: 1,
    scale: 1,
    outline: { color: '#FFFFFF', width: 10 },
    glow: { color: 'rgba(0,0,0,0.55)', blur: 30 }
  }
}

/** Starting layouts. Each returns a whole design; `needsCutout` tells the
 *  studio to fetch the subject for it. */
export const TEMPLATES: {
  id: string
  label: string
  needsCutout: boolean
  /** Where the template puts the subject once it is cut out. */
  subject?: { x: number; y: number; height: number }
  make: (t: number, headline: string) => ThumbDesign
}[] = [
  {
    id: 'popout',
    label: 'Pop-out',
    needsCutout: true,
    subject: { x: 0.72, y: 0.55, height: 1.0 },
    make: (t, headline) => ({
      version: 1,
      frames: 'source',
      background: { ...DEFAULT_BACKGROUND, t, blur: 6, brightness: 0.6, vignette: 0.45 },
      layers: [
        textLayer(headline, 'yellow', { x: 0.3, y: 0.5, rotation: -4, maxWidth: 0.52 }),
        cutoutLayer(t)
      ]
    })
  },
  {
    id: 'headline',
    label: 'Headline',
    needsCutout: false,
    make: (t, headline) => ({
      version: 1,
      frames: 'source',
      background: { ...DEFAULT_BACKGROUND, t, vignette: 0.55 },
      layers: [textLayer(headline, 'impact', { y: 0.8, size: 132 })]
    })
  },
  {
    id: 'reaction',
    label: 'Reaction',
    needsCutout: false,
    make: (t, headline) => ({
      version: 1,
      frames: 'source',
      background: { ...DEFAULT_BACKGROUND, t, saturation: 1.3 },
      layers: [
        textLayer(headline, 'redbox', { x: 0.3, y: 0.16, rotation: -3, maxWidth: 0.52 }),
        { ...shapeLayer('ring'), x: 0.66, y: 0.52 },
        { ...shapeLayer('arrow'), x: 0.4, y: 0.62, rotation: -20 }
      ]
    })
  },
  {
    id: 'neon',
    label: 'Neon night',
    needsCutout: true,
    subject: { x: 0.5, y: 0.5, height: 0.95 },
    make: (t, headline) => ({
      version: 1,
      frames: 'source',
      background: {
        ...DEFAULT_BACKGROUND,
        t,
        blur: 10,
        brightness: 0.45,
        tint: '#2A00FF',
        tintAmount: 0.25
      },
      layers: [
        {
          ...cutoutLayer(t),
          outline: { color: '#00E5FF', width: 8 },
          glow: { color: '#00E5FF', blur: 40 }
        },
        textLayer(headline, 'neon', { y: 0.84 })
      ]
    })
  }
]

// ---- images -------------------------------------------------------------------

// Fetched, then decoded straight to an ImageBitmap. Two traps avoided:
//  * pointing <img> at the engine taints the canvas (another origin) and the
//    export then fails;
//  * an <img> on a blob: URL is refused by the app's Content-Security-Policy
//    (img-src has no blob:), which surfaced as "could not be read" on every
//    frame. createImageBitmap involves no image element, so neither applies.
export type Picture = ImageBitmap

async function decode(blob: Blob, what: string): Promise<Picture> {
  try {
    return await createImageBitmap(blob)
  } catch {
    throw new Error(what)
  }
}

const images = new Map<string, Promise<Picture>>()

export function loadImage(url: string): Promise<Picture> {
  let got = images.get(url)
  if (!got) {
    got = fetch(url)
      .then(async (res) => {
        if (!res.ok) throw new Error((await res.text().catch(() => '')) || `HTTP ${res.status}`)
        return res.blob()
      })
      .then((blob) => decode(blob, 'That image could not be read.'))
    got.catch(() => images.delete(url)) // a failure may be retried
    images.set(url, got)
  }
  return got
}

// Clips whose open design predates source frames: their frame and cut-out
// requests ask for the render's, so the saved layers sit where they were put.
const legacyFrames = new Set<number>()
export function noteFrames(pid: number, d: ThumbDesign): void {
  if (d.frames === 'source') legacyFrames.delete(pid)
  else legacyFrames.add(pid)
}
const srcQuery = (pid: number): string => (legacyFrames.has(pid) ? '&src=render' : '')

export const frameUrl = (pid: number, t: number): string =>
  `${API_BASE}/thumbnails/${pid}/frame?t=${t.toFixed(3)}${srcQuery(pid)}`
export const cutoutUrl = (pid: number, t: number): string =>
  `${API_BASE}/thumbnails/${pid}/cutout?t=${t.toFixed(3)}${srcQuery(pid)}`
export const logoUrl = (asset: string): string => `${API_BASE}/branding/asset/${asset}`

/** The cut-out is made by a POST (it runs a model), so it has its own loader. */
const cutouts = new Map<string, Promise<Picture>>()
export function loadCutout(pid: number, t: number): Promise<Picture> {
  const key = `${pid}@${t.toFixed(3)}${srcQuery(pid)}`
  let got = cutouts.get(key)
  if (!got) {
    got = fetch(cutoutUrl(pid, t), { method: 'POST' })
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text().catch(() => '')
          let detail = body
          try {
            detail = JSON.parse(body).detail ?? body
          } catch {
            /* not JSON */
          }
          throw new Error(detail || `HTTP ${res.status}`)
        }
        return res.blob()
      })
      .then((blob) => decode(blob, 'The cut-out could not be read.'))
    got.catch(() => cutouts.delete(key))
    cutouts.set(key, got)
  }
  return got
}

// ---- rendering ---------------------------------------------------------------

/** Where each drawn layer landed, for hit-testing and the selection box. */
export interface Bounds {
  id: string
  cx: number
  cy: number
  w: number
  h: number
  rotation: number
}

export interface Assets {
  frame: Picture | null
  cutouts: Map<string, Picture> // by layer id
  logos: Map<string, Picture> // by asset name
}

interface Rect {
  x: number
  y: number
  w: number
  h: number
}

/** Where the background picture sits on the canvas. */
export function backgroundRect(
  bg: Background,
  iw: number,
  ih: number,
  mode: 'cover' | 'fit'
): Rect {
  const s =
    (mode === 'cover'
      ? Math.max(THUMB_W / iw, THUMB_H / ih)
      : Math.min(THUMB_W / iw, THUMB_H / ih)) * Math.max(0.2, bg.zoom)
  const w = iw * s
  const h = ih * s
  return {
    w,
    h,
    // pan -1 shows the left/top edge, 1 the right/bottom one.
    x: ((THUMB_W - w) * (1 + bg.panX)) / 2,
    y: ((THUMB_H - h) * (1 + bg.panY)) / 2
  }
}

function drawBackground(
  ctx: CanvasRenderingContext2D,
  bg: Background,
  frame: Picture | null
): Rect | null {
  ctx.fillStyle = '#111'
  ctx.fillRect(0, 0, THUMB_W, THUMB_H)
  if (!frame) return null
  const adjust = `brightness(${bg.brightness}) contrast(${bg.contrast}) saturate(${bg.saturation})`
  if (bg.fill === 'fit') {
    // The same picture, blurred and darker, filling behind the whole one.
    const behind = backgroundRect(
      { ...bg, zoom: 1, panX: 0, panY: 0 },
      frame.width,
      frame.height,
      'cover'
    )
    ctx.filter = `${adjust} blur(${Math.max(24, bg.blur * 2)}px) brightness(0.6)`
    ctx.drawImage(frame, behind.x, behind.y, behind.w, behind.h)
  }
  const r = backgroundRect(bg, frame.width, frame.height, bg.fill)
  ctx.filter = `${adjust}${bg.blur > 0 ? ` blur(${bg.blur}px)` : ''}`
  ctx.drawImage(frame, r.x, r.y, r.w, r.h)
  ctx.filter = 'none'
  if (bg.tintAmount > 0) {
    ctx.globalAlpha = bg.tintAmount
    ctx.fillStyle = bg.tint
    ctx.fillRect(0, 0, THUMB_W, THUMB_H)
    ctx.globalAlpha = 1
  }
  if (bg.vignette > 0) {
    const g = ctx.createRadialGradient(
      THUMB_W / 2,
      THUMB_H / 2,
      THUMB_H * 0.35,
      THUMB_W / 2,
      THUMB_H / 2,
      THUMB_W * 0.72
    )
    g.addColorStop(0, 'rgba(0,0,0,0)')
    g.addColorStop(1, `rgba(0,0,0,${bg.vignette})`)
    ctx.fillStyle = g
    ctx.fillRect(0, 0, THUMB_W, THUMB_H)
  }
  if (bg.scrim && bg.scrim.amount > 0) {
    const g =
      bg.scrim.side === 'left'
        ? ctx.createLinearGradient(0, 0, THUMB_W * 0.62, 0)
        : bg.scrim.side === 'top'
          ? ctx.createLinearGradient(0, 0, 0, THUMB_H * 0.6)
          : ctx.createLinearGradient(0, THUMB_H, 0, THUMB_H * 0.35)
    g.addColorStop(0, `rgba(0,0,0,${bg.scrim.amount})`)
    g.addColorStop(1, 'rgba(0,0,0,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, THUMB_W, THUMB_H)
  }
  return r
}

interface StickerEdge {
  canvas: HTMLCanvasElement
  /** Working-resolution pixels per source pixel. */
  k: number
  /** Padding around the image, in working pixels. */
  pad: number
}

// The sticker outline: the cut-out's shape grown by `r` source pixels, filled
// with one colour, with the edge rounded off. Growing by stamping offset copies
// leaves a scalloped rim and keeps every ragged bit of the matte, so the grown
// shape is blurred and re-thresholded: bumps smaller than the blur melt away
// while the overall width stays put. Cached, because a drag reuses it every frame.
const stickerEdges = new WeakMap<Picture, Map<string, StickerEdge>>()
const EDGE_MAX_SIDE = 1024
function stickerEdge(img: Picture, color: string, r: number): StickerEdge {
  const k = Math.min(1, EDGE_MAX_SIDE / Math.max(img.width, img.height))
  const rw = Math.max(0.5, Math.round(r * k * 2) / 2)
  const key = `${color}|${rw}`
  let cache = stickerEdges.get(img)
  if (!cache) {
    cache = new Map()
    stickerEdges.set(img, cache)
  }
  const hit = cache.get(key)
  if (hit) return hit

  const sigma = Math.max(1.5, rw * 0.3)
  const pad = Math.ceil(rw + sigma * 3) + 2
  const w = Math.max(1, Math.round(img.width * k))
  const h = Math.max(1, Math.round(img.height * k))

  // Shape in solid colour.
  const shape = document.createElement('canvas')
  shape.width = w
  shape.height = h
  const sx = shape.getContext('2d')!
  sx.drawImage(img, 0, 0, w, h)
  sx.globalCompositeOperation = 'source-in'
  sx.fillStyle = color
  sx.fillRect(0, 0, w, h)

  // Grow it: stamps spaced under a pixel apart on the rim, plus an inner ring
  // so thin parts fill in.
  const grown = document.createElement('canvas')
  grown.width = w + pad * 2
  grown.height = h + pad * 2
  const gx = grown.getContext('2d')!
  const steps = Math.min(180, Math.max(24, Math.ceil(Math.PI * 2 * rw)))
  for (const f of [0.5, 1]) {
    for (let i = 0; i < steps; i++) {
      const a = (i / steps) * Math.PI * 2
      gx.drawImage(shape, pad + Math.cos(a) * rw * f, pad + Math.sin(a) * rw * f)
    }
  }
  gx.drawImage(shape, pad, pad)

  // Round it: blur, then pull the soft alpha back to a crisp antialiased edge.
  const soft = document.createElement('canvas')
  soft.width = grown.width
  soft.height = grown.height
  const bx = soft.getContext('2d', { willReadFrequently: true })!
  bx.filter = `blur(${sigma}px)`
  bx.drawImage(grown, 0, 0)
  bx.filter = 'none'
  const px = bx.getImageData(0, 0, soft.width, soft.height)
  const d = px.data
  for (let i = 3; i < d.length; i += 4) {
    const t = Math.min(1, Math.max(0, (d[i] / 255 - 0.42) / 0.16))
    d[i] = Math.round(t * t * (3 - 2 * t) * 255)
  }
  bx.putImageData(px, 0, 0)

  const edge = { canvas: soft, k, pad }
  if (cache.size >= 8) cache.clear()
  cache.set(key, edge)
  return edge
}

/** Where the opaque part of a cut-out sits, as fractions of the image.
 *  Measured once per image on a small copy: the frame is mostly transparent
 *  and only the subject should be clickable or placed. */
const boxes = new WeakMap<Picture, { x0: number; y0: number; x1: number; y1: number }>()
export function subjectBox(img: Picture): {
  x0: number
  y0: number
  x1: number
  y1: number
} {
  const known = boxes.get(img)
  if (known) return known
  const scale = Math.min(1, 256 / Math.max(img.width, img.height))
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.round(img.width * scale))
  c.height = Math.max(1, Math.round(img.height * scale))
  const x = c.getContext('2d', { willReadFrequently: true })!
  x.drawImage(img, 0, 0, c.width, c.height)
  const { data } = x.getImageData(0, 0, c.width, c.height)
  let x0 = c.width
  let y0 = c.height
  let x1 = -1
  let y1 = -1
  for (let y = 0; y < c.height; y++) {
    for (let i = 0; i < c.width; i++) {
      if (data[(y * c.width + i) * 4 + 3] > 24) {
        if (i < x0) x0 = i
        if (i > x1) x1 = i
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
    }
  }
  const box =
    x1 < 0
      ? { x0: 0, y0: 0, x1: 1, y1: 1 }
      : { x0: x0 / c.width, y0: y0 / c.height, x1: (x1 + 1) / c.width, y1: (y1 + 1) / c.height }
  boxes.set(img, box)
  return box
}

/** Place a cut-out so its subject is `height` of the canvas tall with its
 *  centre at (x, y): the big, one-sided subject of a pop-out thumbnail. */
export function placeCutout(
  bg: Background,
  frame: { width: number; height: number },
  img: Picture,
  target: { x: number; y: number; height: number }
): { x: number; y: number; scale: number } {
  const r = backgroundRect(bg, frame.width, frame.height, bg.fill)
  const b = subjectBox(img)
  const scale = Math.max(
    0.3,
    Math.min(3, (target.height * THUMB_H) / Math.max(1, (b.y1 - b.y0) * r.h))
  )
  const w = r.w * scale
  const h = r.h * scale
  // Where the image centre must go for the subject centre to land on target.
  const cx = target.x * THUMB_W - ((b.x0 + b.x1) / 2 - 0.5) * w
  const cy = target.y * THUMB_H - ((b.y0 + b.y1) / 2 - 0.5) * h
  return {
    x: (cx - (r.x + r.w / 2)) / THUMB_W + 0.5,
    y: (cy - (r.y + r.h / 2)) / THUMB_H + 0.5,
    scale
  }
}

function drawCutout(
  ctx: CanvasRenderingContext2D,
  layer: CutoutLayer,
  img: Picture,
  bgRect: Rect
): Bounds {
  // Same place as the frame it came from, then the layer's own move/scale.
  const w = bgRect.w * layer.scale
  const h = bgRect.h * layer.scale
  const cx = bgRect.x + bgRect.w / 2 + (layer.x - 0.5) * THUMB_W
  const cy = bgRect.y + bgRect.h / 2 + (layer.y - 0.5) * THUMB_H
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate((layer.rotation * Math.PI) / 180)
  if (layer.outline && layer.outline.width > 0) {
    // Width is in canvas pixels; the edge is built in source pixels.
    const e = stickerEdge(img, layer.outline.color, (layer.outline.width * img.width) / w)
    const m = w / (img.width * e.k) // drawn pixels per working pixel
    ctx.drawImage(
      e.canvas,
      -w / 2 - e.pad * m,
      -h / 2 - e.pad * m,
      e.canvas.width * m,
      e.canvas.height * m
    )
  }
  if (layer.glow) {
    ctx.shadowColor = layer.glow.color
    ctx.shadowBlur = layer.glow.blur
  }
  ctx.drawImage(img, -w / 2, -h / 2, w, h)
  ctx.restore()
  // Clickable where the subject is, not across the whole transparent frame.
  const b = subjectBox(img)
  const ox = ((b.x0 + b.x1) / 2 - 0.5) * w
  const oy = ((b.y0 + b.y1) / 2 - 0.5) * h
  const a = (layer.rotation * Math.PI) / 180
  return {
    id: layer.id,
    cx: cx + ox * Math.cos(a) - oy * Math.sin(a),
    cy: cy + ox * Math.sin(a) + oy * Math.cos(a),
    w: (b.x1 - b.x0) * w,
    h: (b.y1 - b.y0) * h,
    rotation: layer.rotation
  }
}

function fontOf(l: TextLayer): string {
  return `${l.italic ? 'italic ' : ''}${l.weight} ${l.size}px "${l.font}", "Arial Black", sans-serif`
}

type Spaced = CanvasRenderingContext2D & { letterSpacing: string; wordSpacing: string }

/** Letter and word spacing, set the same way for measuring and for drawing so
 *  shrink-to-fit sees the width that actually gets drawn. */
function setSpacing(ctx: CanvasRenderingContext2D, l: TextLayer): void {
  ;(ctx as Spaced).letterSpacing = `${l.letterSpacing}px`
  ;(ctx as Spaced).wordSpacing = `${(l.wordSpacing ?? 0) * l.size}px`
}

let measurer: CanvasRenderingContext2D | null = null

/** How big a text layer is actually drawn, and the largest size its
 *  shrink-to-fit limit lets it reach (Infinity with no limit). Measured the
 *  way drawText measures, so the editor can show the real size: the Size
 *  slider used to show the size asked for, while the fit quietly drew it
 *  smaller, so sliding up did nothing. */
export function textFit(l: TextLayer): { drawn: number; limit: number } {
  if (!l.maxWidth) return { drawn: l.size, limit: Infinity }
  measurer ??= document.createElement('canvas').getContext('2d')
  const ctx = measurer
  if (!ctx) return { drawn: l.size, limit: Infinity }
  ctx.font = fontOf(l)
  setSpacing(ctx, l)
  const lines = (l.uppercase ? l.text.toUpperCase() : l.text).split('\n')
  const w = Math.max(1, ...lines.map((line) => ctx.measureText(line).width))
  const pad = l.box ? l.box.padding : 0
  const ratio = (l.maxWidth * THUMB_W) / (w + pad * 2)
  return { drawn: l.size * Math.min(1, ratio), limit: l.size * ratio }
}

/** The change that makes a text layer draw at `size`. Within its fit limit
 *  the limit stays (it still guards later edits to the words); past it, the
 *  size asked for wins and shrink-to-fit is switched off for that text. */
export function resizeText(l: TextLayer, size: number): Partial<TextLayer> {
  return size <= textFit(l).limit + 0.5 ? { size } : { size, maxWidth: undefined }
}

function drawText(ctx: CanvasRenderingContext2D, l: TextLayer): Bounds {
  const lines = (l.uppercase ? l.text.toUpperCase() : l.text).split('\n')
  ctx.save()
  ctx.font = fontOf(l)
  setSpacing(ctx, l)
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.lineJoin = 'round'
  const lh = l.size * l.lineHeight
  const w = Math.max(1, ...lines.map((line) => ctx.measureText(line).width))
  const h = lh * lines.length
  const cx = l.x * THUMB_W
  const cy = l.y * THUMB_H
  const pad = l.box ? l.box.padding : 0
  // Too wide for its limit: draw the whole thing smaller, outline and box
  // included, rather than letting it run off the canvas.
  const k = l.maxWidth ? Math.min(1, (l.maxWidth * THUMB_W) / (w + pad * 2)) : 1
  ctx.translate(cx, cy)
  ctx.rotate((l.rotation * Math.PI) / 180)
  ctx.scale(k, k)

  if (l.box) {
    ctx.fillStyle = l.box.color
    ctx.beginPath()
    ctx.roundRect(-w / 2 - pad, -h / 2 - pad, w + pad * 2, h + pad * 2, l.box.radius)
    ctx.fill()
  }

  const fill = (lineY: number): string | CanvasGradient => {
    if (!l.gradient) return l.color
    const g = ctx.createLinearGradient(0, lineY - l.size / 2, 0, lineY + l.size / 2)
    g.addColorStop(0, l.color)
    g.addColorStop(1, l.gradient)
    return g
  }

  lines.forEach((line, i) => {
    const y = -h / 2 + lh * (i + 0.5)
    // Glow first, underneath: a blurred fill of the same colour.
    if (l.glow) {
      ctx.save()
      ctx.shadowColor = l.glow.color
      ctx.shadowBlur = l.glow.blur
      ctx.fillStyle = l.glow.color
      ctx.fillText(line, 0, y)
      ctx.fillText(line, 0, y)
      ctx.restore()
    }
    // The shadow belongs to the outline when there is one, so the fill
    // stays crisp on top.
    if (l.shadow) {
      ctx.shadowColor = l.shadow.color
      ctx.shadowBlur = l.shadow.blur
      ctx.shadowOffsetX = l.shadow.dx
      ctx.shadowOffsetY = l.shadow.dy
    }
    if (l.stroke && l.stroke.width > 0) {
      ctx.strokeStyle = l.stroke.color
      ctx.lineWidth = l.stroke.width * 2
      ctx.strokeText(line, 0, y)
      ctx.shadowColor = 'transparent'
    }
    ctx.fillStyle = fill(y)
    ctx.fillText(line, 0, y)
    ctx.shadowColor = 'transparent'
  })
  ctx.restore()
  return { id: l.id, cx, cy, w: (w + pad * 2) * k, h: (h + pad * 2) * k, rotation: l.rotation }
}

function drawLogo(ctx: CanvasRenderingContext2D, l: LogoLayer, img: Picture): Bounds {
  const w = l.width * THUMB_W
  const cropped = l.frame !== 'free'
  const h = cropped ? w : (w * img.height) / Math.max(1, img.width)
  const cx = l.x * THUMB_W
  const cy = l.y * THUMB_H
  // Composed on its own canvas first, cropped and bordered, so the drop
  // shadow follows the logo's real outline (a circle casts a round shadow,
  // a transparent PNG casts the shape of what is drawn).
  const pad = (l.border?.width ?? 0) + 2
  const off = document.createElement('canvas')
  off.width = Math.max(1, Math.ceil(w + pad * 2))
  off.height = Math.max(1, Math.ceil(h + pad * 2))
  const o = off.getContext('2d')!
  o.translate(off.width / 2, off.height / 2)
  const path = new Path2D()
  if (l.frame === 'circle') path.arc(0, 0, w / 2, 0, Math.PI * 2)
  else path.rect(-w / 2, -h / 2, w, h)
  o.save()
  if (cropped) o.clip(path)
  // Square and circle take the centred square of the picture, as the video
  // watermark does (video_editor/watermark.py).
  const side = Math.min(img.width, img.height)
  if (cropped) {
    o.drawImage(
      img,
      (img.width - side) / 2,
      (img.height - side) / 2,
      side,
      side,
      -w / 2,
      -h / 2,
      w,
      h
    )
  } else {
    o.drawImage(img, -w / 2, -h / 2, w, h)
  }
  o.restore()
  if (l.border && l.border.width > 0) {
    o.strokeStyle = l.border.color
    o.lineWidth = l.border.width
    o.stroke(path)
  }
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate((l.rotation * Math.PI) / 180)
  if (l.shadow) {
    ctx.shadowColor = 'rgba(0,0,0,0.6)'
    ctx.shadowBlur = 18
    ctx.shadowOffsetY = 4
  }
  ctx.drawImage(off, -off.width / 2, -off.height / 2)
  ctx.restore()
  return { id: l.id, cx, cy, w, h, rotation: l.rotation }
}

function drawShape(ctx: CanvasRenderingContext2D, l: ShapeLayer): Bounds {
  const cx = l.x * THUMB_W
  const cy = l.y * THUMB_H
  const s = l.size
  let w = s
  let h = s
  ctx.save()
  ctx.translate(cx, cy)
  ctx.rotate((l.rotation * Math.PI) / 180)
  ctx.lineJoin = 'round'
  // Every shape carries a dark edge, so it reads on any background.
  const edge = Math.max(4, l.thickness * 0.18)
  if (l.shape === 'emoji') {
    ctx.font = `${s}px "Segoe UI Emoji", "Apple Color Emoji", sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.shadowColor = 'rgba(0,0,0,0.5)'
    ctx.shadowBlur = 16
    ctx.fillText(l.emoji, 0, s * 0.05)
  } else if (l.shape === 'arrow') {
    const t = l.thickness
    h = t * 2.4
    const head = Math.min(s * 0.45, t * 2.2)
    const p = new Path2D()
    p.moveTo(-s / 2, -t / 2)
    p.lineTo(s / 2 - head, -t / 2)
    p.lineTo(s / 2 - head, -h / 2)
    p.lineTo(s / 2, 0)
    p.lineTo(s / 2 - head, h / 2)
    p.lineTo(s / 2 - head, t / 2)
    p.lineTo(-s / 2, t / 2)
    p.closePath()
    ctx.strokeStyle = '#000'
    ctx.lineWidth = edge * 2
    ctx.stroke(p)
    ctx.fillStyle = l.color
    ctx.fill(p)
  } else {
    const p = new Path2D()
    if (l.shape === 'ring') {
      p.arc(0, 0, s / 2, 0, Math.PI * 2)
    } else {
      h = s * 0.62
      p.roundRect(-s / 2, -h / 2, s, h, 12)
    }
    ctx.strokeStyle = '#000'
    ctx.lineWidth = l.thickness + edge * 2
    ctx.stroke(p)
    ctx.strokeStyle = l.color
    ctx.lineWidth = l.thickness
    ctx.stroke(p)
  }
  ctx.restore()
  return { id: l.id, cx, cy, w, h, rotation: l.rotation }
}

/** Draw the whole design. Returns where each visible layer landed. */
export function render(
  ctx: CanvasRenderingContext2D,
  design: ThumbDesign,
  assets: Assets,
  selected: string | null = null
): Bounds[] {
  ctx.save()
  ctx.clearRect(0, 0, THUMB_W, THUMB_H)
  const bgRect = drawBackground(ctx, design.background, assets.frame) ?? {
    x: 0,
    y: 0,
    w: THUMB_W,
    h: THUMB_H
  }
  const bounds: Bounds[] = []
  for (const layer of design.layers) {
    if (layer.hidden) continue
    ctx.save()
    ctx.globalAlpha = layer.opacity
    let b: Bounds | null = null
    if (layer.kind === 'text') b = drawText(ctx, layer)
    else if (layer.kind === 'shape') b = drawShape(ctx, layer)
    else if (layer.kind === 'logo') {
      const img = assets.logos.get(layer.asset)
      if (img) b = drawLogo(ctx, layer, img)
    } else if (layer.kind === 'cutout') {
      const img = assets.cutouts.get(layer.id)
      if (img) b = drawCutout(ctx, layer, img, bgRect)
    }
    ctx.restore()
    if (b) bounds.push(b)
  }
  const sel = bounds.find((b) => b.id === selected)
  if (sel) {
    ctx.save()
    ctx.translate(sel.cx, sel.cy)
    ctx.rotate((sel.rotation * Math.PI) / 180)
    ctx.setLineDash([12, 8])
    ctx.lineWidth = 3
    ctx.strokeStyle = '#38BDF8'
    ctx.strokeRect(-sel.w / 2 - 8, -sel.h / 2 - 8, sel.w + 16, sel.h + 16)
    ctx.restore()
  }
  ctx.restore()
  return bounds
}

/** The topmost layer under a canvas point, or null. */
export function hitTest(bounds: Bounds[], x: number, y: number): string | null {
  for (let i = bounds.length - 1; i >= 0; i--) {
    const b = bounds[i]
    const a = (-b.rotation * Math.PI) / 180
    const dx = x - b.cx
    const dy = y - b.cy
    const lx = dx * Math.cos(a) - dy * Math.sin(a)
    const ly = dx * Math.sin(a) + dy * Math.cos(a)
    if (Math.abs(lx) <= b.w / 2 + 8 && Math.abs(ly) <= b.h / 2 + 8) return b.id
  }
  return null
}

/** Wait for every font the design uses, so the first draw is not in a fallback. */
export async function loadFonts(design: ThumbDesign): Promise<void> {
  const fonts = new Set(design.layers.filter((l): l is TextLayer => l.kind === 'text').map(fontOf))
  await Promise.all([...fonts].map((f) => document.fonts.load(f).catch(() => undefined)))
}

/** The finished JPEG, stepping quality down until it is under YouTube's 2 MB. */
export async function exportJpeg(design: ThumbDesign, assets: Assets): Promise<string> {
  const canvas = document.createElement('canvas')
  canvas.width = THUMB_W
  canvas.height = THUMB_H
  await loadFonts(design)
  render(canvas.getContext('2d')!, design, assets, null)
  for (const q of [0.92, 0.85, 0.75, 0.6]) {
    const url = canvas.toDataURL('image/jpeg', q)
    if ((url.length - url.indexOf(',') - 1) * 0.75 <= MAX_BYTES) return url
  }
  return canvas.toDataURL('image/jpeg', 0.5)
}

// ---- routes ------------------------------------------------------------------

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    let detail = body
    try {
      detail = JSON.parse(body).detail ?? body
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

export const thumbsApi = {
  source: (pid: number) =>
    call<{ duration: number; title: string; keywords: string[]; has_design: boolean; has_image: boolean }>(
      `/thumbnails/${pid}/source`
    ),
  bestFrames: (pid: number, count = 6) =>
    call<{ frames: { t: number; score: number; face: boolean; sharp?: number }[] }>(
      `/thumbnails/${pid}/best-frames?count=${count}`
    ),
  textIdeas: (pid: number) =>
    call<{ ideas: string[] }>(`/thumbnails/${pid}/text-ideas`, { method: 'POST' }),
  /** Text for a whole thumbnail, written as one piece; best of three first. */
  copy: (pid: number) => call<{ sets: ThumbCopy[] }>(`/thumbnails/${pid}/copy`, { method: 'POST' }),
  /** Which of these clips (negative: compilations) have a saved thumbnail. */
  status: async (ids: number[]): Promise<Record<number, boolean>> => {
    if (!ids.length) return {}
    const got = await call<{ thumbnails: Record<string, boolean> }>(
      `/thumbnails/status?ids=${ids.join(',')}`
    )
    return Object.fromEntries(Object.entries(got.thumbnails).map(([k, v]) => [Number(k), v]))
  },
  /** Take a scheduled YouTube video off the schedule (it stays, private). */
  unscheduleYouTube: (videoId: string, channelId?: string) =>
    call<{ unscheduled: boolean }>(`/youtube/videos/${videoId}/unschedule`, {
      method: 'POST',
      body: JSON.stringify({ channel_id: channelId ?? null })
    }),
  /** Re-render the clip a video came from and replace the video with it. */
  replaceYouTube: (videoId: string, channelId?: string) =>
    call<{ publish_job_id: number; render_job_id: number | null }>(`/youtube/videos/${videoId}/replace`, {
      method: 'POST',
      body: JSON.stringify({ channel_id: channelId ?? null })
    }),
  /** Delete a video from the channel for good (its record here is closed). */
  deleteYouTube: (videoId: string, channelId?: string) =>
    call<{ deleted: boolean }>(`/youtube/videos/${videoId}/delete`, {
      method: 'POST',
      body: JSON.stringify({ channel_id: channelId ?? null, confirm: true })
    }),
  /** Put the saved thumbnail on a video already on the YouTube channel. */
  pushToYouTube: (videoId: string, opts: { publishId?: number; channelId?: string } = {}) =>
    call<{ pushed: boolean; publish_id: number }>(`/youtube/videos/${videoId}/thumbnail`, {
      method: 'POST',
      body: JSON.stringify({ publish_id: opts.publishId ?? null, channel_id: opts.channelId ?? null })
    }),
  design: (pid: number) => call<{ design: ThumbDesign | null }>(`/thumbnails/${pid}/design`),
  save: (pid: number, image: string, design: ThumbDesign, auto = false) =>
    call<{ saved: boolean; bytes: number }>(`/thumbnails/${pid}`, {
      method: 'POST',
      body: JSON.stringify({ image, design, auto })
    }),
  /** Which of these clips are new and have never had a thumbnail made, so the
   *  app should make their first one. A clip that was re-rendered, or whose
   *  thumbnail was deleted on purpose, is not in this list. */
  pending: async (ids: number[]): Promise<number[]> => {
    if (!ids.length) return []
    const got = await call<{ pending: number[] }>(`/thumbnails/pending?ids=${ids.join(',')}`)
    return got.pending
  },
  remove: (pid: number) => call<{ removed: boolean }>(`/thumbnails/${pid}`, { method: 'DELETE' }),
  imageUrl: (pid: number, version = 0) => `${API_BASE}/thumbnails/${pid}/image?v=${version}`
}

// ---- automatic thumbnail -----------------------------------------------------

/** One message for a thumbnail (POST /thumbnails/{id}/copy): words QUOTED from
 *  the clip's peak, a supporting line, and a badge that is one of the channel's
 *  always-on hashtags without its '#'. */
export interface ThumbCopy {
  headline: string
  kicker: string
  badge: string
  emoji: string
  mood: 'hype' | 'shock' | 'funny' | 'serious' | 'wholesome'
  /** headline and kicker are two lines of a conversation, said by two sides. */
  exchange?: boolean
}

// What the video's tone does to the look: the colour the supporting pieces pick
// up so they read as one design. Headlines are always light with a dark outline
// (the one combination that reads over any picture); the accent only ever fills
// a box, and the box's text colour is chosen for it (see textLayer), so no
// pairing can come out unreadable.
const MOODS: Record<ThumbCopy['mood'], { accent: string; tilt: number; fonts: string[] }> = {
  hype: { accent: '#E11D2E', tilt: -2, fonts: ['bold', 'impact'] },
  shock: { accent: '#F97316', tilt: -2, fonts: ['bold', 'impact'] },
  funny: { accent: '#7C3AED', tilt: -2, fonts: ['bold', 'clean'] },
  serious: { accent: '#1D4ED8', tilt: 0, fonts: ['impact', 'clean'] },
  wholesome: { accent: '#16A34A', tilt: 0, fonts: ['clean', 'bold'] }
}

const words = (s: string, n: number): string => s.split(/\s+/).filter(Boolean).slice(0, n).join(' ')

/** A small stable hash: the same clip and salt always give the same choice,
 *  a different clip a different one. */
function mix(seed: number, salt: number): number {
  let h = (Math.imul(seed | 0, 2654435761) ^ Math.imul(salt + 1, 40503)) >>> 0
  h ^= h >>> 15
  h = Math.imul(h, 2246822519) >>> 0
  return h ^ (h >>> 13)
}
const pick = <T,>(items: T[], seed: number, salt: number): T => items[Math.abs(mix(seed, salt)) % items.length]

/** Mean colour of a picture, from a 16x9 copy. */
function averageColor(img: Picture): [number, number, number] {
  const c = document.createElement('canvas')
  c.width = 16
  c.height = 9
  const x = c.getContext('2d')
  if (!x) return [90, 90, 90]
  x.drawImage(img, 0, 0, 16, 9)
  const d = x.getImageData(0, 0, 16, 9).data
  let r = 0
  let g = 0
  let b = 0
  for (let i = 0; i < d.length; i += 4) {
    r += d[i]
    g += d[i + 1]
    b += d[i + 2]
  }
  const n = d.length / 4
  return [r / n, g / n, b / n]
}

export type LayoutId = 'side' | 'bottom' | 'banner' | 'punch' | 'ribbon' | 'chat'

/** Which layouts suit this picture and this text. A two-sided exchange gets the
 *  chat layout; a picture with a cut-out subject leaves a side or the top free
 *  for the words; one without can carry them across it. */
export function layoutOptions(hasCut: boolean, exchange: boolean): LayoutId[] {
  if (exchange) return ['chat']
  return hasCut ? ['side', 'banner', 'ribbon'] : ['bottom', 'punch', 'ribbon', 'banner']
}

interface Ctx {
  copy: ThumbCopy
  accent: string
  font: string
  tilt: number
  headline: string
  twoLines: boolean
  hasCut: boolean
  seed: number
}

const shorten = (s: string, n: number): string => (s.split(/\s+/).length > n ? words(s, n) : s)

/** A small label in a box (the badge). */
function label(text: string, at: Partial<Omit<TextLayer, 'kind'>>, fill: string): TextLayer {
  return textLayer(text, 'redbox', {
    size: 40,
    maxWidth: 0.26,
    rotation: 0,
    box: { color: fill, padding: 12, radius: 8 },
    ...at
  })
}

function emojiAt(c: Ctx, x: number, y: number, size = 120, rotation = 6): Layer[] {
  if (!c.copy.emoji || c.copy.mood === 'serious') return []
  return [{ ...shapeLayer('emoji', c.copy.emoji), x, y, size, rotation }]
}

// Each builder returns the text layers of one look (the cut-out, when there is
// one, is placed separately: see CUT_TARGET). Margins are at least 5%, and the
// bottom-right corner is left alone: that is where the platform draws the length.
const BUILD: Record<LayoutId, (c: Ctx) => Layer[]> = {
  // Words down the left, subject on the right.
  side: (c) => [
    textLayer(c.headline, c.font, {
      x: 0.3, y: c.twoLines ? 0.4 : 0.46, size: 112, rotation: c.tilt, maxWidth: 0.5
    }),
    ...(c.copy.kicker
      ? [textLayer(c.copy.kicker, 'redbox', {
          x: 0.3, y: c.twoLines ? 0.68 : 0.64, size: 54, rotation: c.tilt, maxWidth: 0.5,
          box: { color: c.accent, padding: 16, radius: 10 }
        })]
      : []),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.14, y: 0.1 }, c.accent)] : []),
    ...emojiAt(c, 0.9, 0.18)
  ],
  // Words low across the picture.
  bottom: (c) => [
    textLayer(c.headline, c.font, {
      x: 0.5, y: c.copy.kicker ? (c.twoLines ? 0.6 : 0.7) : c.twoLines ? 0.68 : 0.78,
      size: 128, rotation: c.tilt, maxWidth: 0.84
    }),
    ...(c.copy.kicker
      ? [textLayer(c.copy.kicker, 'redbox', {
          x: 0.5, y: 0.89, size: 54, rotation: c.tilt, maxWidth: 0.7,
          box: { color: c.accent, padding: 16, radius: 10 }
        })]
      : []),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.13, y: 0.1 }, c.accent)] : []),
    ...emojiAt(c, 0.9, 0.18)
  ],
  // The headline across the top, the subject beneath it.
  banner: (c) => [
    textLayer(c.headline, c.font, { x: 0.5, y: c.twoLines ? 0.22 : 0.15, size: 116, rotation: 0, maxWidth: 0.9 }),
    ...(c.copy.kicker
      ? [textLayer(c.copy.kicker, 'redbox', {
          x: 0.5, y: c.twoLines ? 0.44 : 0.3, size: 50, maxWidth: 0.7,
          box: { color: c.accent, padding: 14, radius: 10 }
        })]
      : []),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.12, y: 0.92 }, c.accent)] : []),
    ...emojiAt(c, 0.9, 0.82, 110, -6)
  ],
  // One enormous line in a solid block, everything else quiet.
  punch: (c) => [
    textLayer(c.headline, 'redbox', {
      x: 0.5, y: 0.48, size: 150, rotation: -2, maxWidth: 0.86,
      box: { color: c.accent, padding: 30, radius: 16 }
    }),
    ...(c.copy.kicker
      ? [textLayer(c.copy.kicker, 'bold', { x: 0.5, y: 0.83, size: 56, maxWidth: 0.8 })]
      : []),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.14, y: 0.09 }, '#111111')] : []),
    ...emojiAt(c, 0.9, 0.16, 110, 8)
  ],
  // A big label in the corner, the words smaller and lower.
  ribbon: (c) => [
    textLayer(c.headline, c.font, {
      x: 0.3, y: c.twoLines ? 0.72 : 0.78, size: 100, rotation: c.tilt, maxWidth: 0.52
    }),
    ...(c.copy.kicker
      ? [textLayer(c.copy.kicker, 'bold', { x: 0.3, y: 0.92, size: 44, maxWidth: 0.5 })]
      : []),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.84, y: 0.1, size: 52, maxWidth: 0.3, rotation: 4 }, c.accent)] : []),
    ...emojiAt(c, 0.1, 0.14, 100, -6)
  ],
  // Two sides of a conversation: a light bubble, then an accent one.
  chat: (c) => [
    textLayer(c.headline, 'clean', {
      x: 0.3, y: 0.27, size: 84, rotation: -2, maxWidth: 0.5, color: '#111111', uppercase: false,
      stroke: null, shadow: { color: 'rgba(0,0,0,0.35)', blur: 14, dx: 0, dy: 6 },
      box: { color: '#FFFFFF', padding: 24, radius: 28 }
    }),
    textLayer(c.copy.kicker || '', 'clean', {
      x: 0.7, y: 0.72, size: 84, rotation: 2, maxWidth: 0.5, uppercase: false, stroke: null,
      shadow: { color: 'rgba(0,0,0,0.35)', blur: 14, dx: 0, dy: 6 },
      box: { color: c.accent, padding: 24, radius: 28 }
    }),
    ...(c.copy.badge ? [label(c.copy.badge, { x: 0.13, y: 0.92 }, '#111111')] : [])
  ]
}

const CUT_TARGET: Record<LayoutId, { x: number; y: number; height: number }> = {
  side: { x: 0.74, y: 0.55, height: 1.0 },
  ribbon: { x: 0.72, y: 0.52, height: 1.0 },
  banner: { x: 0.5, y: 0.68, height: 0.9 },
  chat: { x: 0.5, y: 0.55, height: 1.0 },
  punch: { x: 0.5, y: 0.55, height: 1.0 },
  bottom: { x: 0.5, y: 0.55, height: 1.0 }
}

/** The picture behind the words, treated for the layout: a side or edge the
 *  words sit on is darkened, and a little variation in colour keeps a channel's
 *  thumbnails from all looking like one template. */
function backgroundFor(layout: LayoutId, at: number, hasCut: boolean, seed: number): Background {
  const base = { ...DEFAULT_BACKGROUND, t: at }
  const sat = pick([1.1, 1.2, 1.3], seed, 7)
  switch (layout) {
    case 'side':
    case 'ribbon':
      return { ...base, blur: hasCut ? 6 : 0, brightness: hasCut ? 0.62 : 0.85, vignette: 0.4, saturation: sat,
        scrim: { side: 'left', amount: 0.5 } }
    case 'banner':
      return { ...base, blur: hasCut ? 5 : 0, brightness: hasCut ? 0.66 : 0.85, vignette: 0.35, saturation: sat,
        scrim: { side: 'top', amount: 0.55 } }
    case 'punch':
      return { ...base, brightness: 0.78, vignette: 0.5, saturation: sat + 0.1, scrim: { side: 'bottom', amount: 0.4 } }
    case 'chat':
      return { ...base, blur: hasCut ? 5 : 0, brightness: hasCut ? 0.7 : 0.85, vignette: 0.4, saturation: sat }
    default:
      return { ...base, brightness: 0.95, vignette: 0.4, saturation: sat, scrim: { side: 'bottom', amount: 0.62 } }
  }
}

/** A finished thumbnail with no clicks: the sharpest expressive frame, the
 *  subject cut out when there is one, and words QUOTED from the clip's peak,
 *  laid out in one of several looks. The same clip always gets the same look,
 *  a different clip a different one; pass `variant` to get another for the
 *  same clip. Falls back to the video's own title when the copy is unavailable,
 *  and to no cut-out when nobody is in frame. Saves it as THE thumbnail. */
export async function autoThumbnail(
  pid: number,
  opts: { variant?: number; layout?: LayoutId } = {}
): Promise<{ design: ThumbDesign; copy: ThumbCopy; layout: LayoutId }> {
  legacyFrames.delete(pid) // a fresh design is made on source frames
  const seed = opts.variant ?? pid
  const src = await thumbsApi.source(pid)
  const frames = await thumbsApi.bestFrames(pid, 4).catch(() => ({ frames: [] }))
  const at = frames.frames[0]?.t ?? src.duration * 0.3
  const keyword = (src.keywords ?? []).find((k) => k.length <= 14 && k.split(' ').length <= 2) ?? ''
  const copy: ThumbCopy = await thumbsApi
    .copy(pid)
    .then((r) => {
      // The best line most of the time; the other sets (the exchange, another
      // line) now and then, or whenever a variant is asked for.
      const i = opts.variant !== undefined ? Math.abs(opts.variant) % r.sets.length : Math.abs(mix(seed, 3)) % 5 < 3 ? 0 : Math.abs(mix(seed, 4)) % r.sets.length
      return r.sets[i] as ThumbCopy
    })
    .catch(() => ({
      headline: words(src.title, 3),
      kicker: '',
      badge: keyword,
      emoji: '',
      mood: 'hype' as const
    }))
  const mood = MOODS[copy.mood] ?? MOODS.hype
  const frame = await loadImage(frameUrl(pid, at))
  const accent = pickAccent(mood.accent, averageColor(frame))

  let cut: Picture | null = null
  try {
    cut = await loadCutout(pid, at)
  } catch {
    /* nobody in frame, or the model is not installed: no subject */
  }
  const exchange = !!copy.exchange && !!copy.kicker
  const options = layoutOptions(!!cut, exchange)
  const layout = opts.layout && options.includes(opts.layout) ? opts.layout : pick(options, seed, 1)
  const headline = exchange ? copy.headline : balanceLines(shorten(copy.headline, 7))
  const ctx: Ctx = {
    copy: { ...copy, kicker: shorten(copy.kicker || '', 6) },
    accent,
    font: pick(mood.fonts, seed, 2),
    tilt: mood.tilt,
    headline,
    twoLines: headline.includes('\n'),
    hasCut: !!cut,
    seed
  }

  const bg = backgroundFor(layout, at, !!cut, seed)
  const layers: Layer[] = []
  const assets: Assets = { frame, cutouts: new Map(), logos: new Map() }
  if (cut) {
    const layer = cutoutLayer(at)
    Object.assign(layer, placeCutout(bg, frame, cut, CUT_TARGET[layout]))
    assets.cutouts.set(layer.id, cut)
    layers.push(layer)
  }
  layers.push(...BUILD[layout](ctx))
  const design: ThumbDesign = { version: 1, frames: 'source', background: bg, layers }
  const image = await exportJpeg(design, assets)
  await thumbsApi.save(pid, image, design, true)
  return { design, copy, layout }
}
