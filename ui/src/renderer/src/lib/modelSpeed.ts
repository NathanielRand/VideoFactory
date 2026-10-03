/** What picking a bigger model actually costs you, in time.
 *
 *  The selector used to show only a name and a size on disk, which says
 *  nothing about the trade being made. Bigger models choose and title clips
 *  better and take longer per video — on a two-hour stream that difference is
 *  the difference between a coffee and an afternoon. Someone who switches and
 *  then sees "152 minutes remaining" should have been told first.
 *
 *  ── How the estimate is arrived at, and what it is worth ──
 *
 *  Nothing here is measured on your machine. Generating text is limited by
 *  how fast the weights can be read out of memory, so time per word scales
 *  with how BIG the model is — which is why the comparison uses size on disk
 *  rather than the parameter count in the name. Size also accounts for
 *  quantisation for free: a heavily compressed 12b really is faster than an
 *  uncompressed one, and their names are identical.
 *
 *  It is compared against the smallest model you actually have installed,
 *  not a fixed baseline, so the sentence always names something you can
 *  switch to. Every number is deliberately worded as "roughly": the true
 *  figure moves with your card, the length of the video, and how much of it
 *  is speech. It is a direction and an order of magnitude, not a promise.
 *
 *  The one exception is the too-big-for-your-card warning, which is not an
 *  estimate — it compares the model against the VRAM your own GPU reports.
 */

export type SpeedTone = 'ok' | 'slow' | 'warn'

export interface SpeedNote {
  tone: SpeedTone
  text: string
}

/** Just enough of an installed model to compare two of them. */
export interface ModelLike {
  name: string
  size_gb: number
  cloud?: boolean
  /** Billions of parameters, from Ollama; used by the grade (modelGrade.ts). */
  params_b?: number | null
}

/** Weights have to sit in VRAM to run at full speed. Anything above roughly
 *  the card's capacity spills into system memory, where it does not run a bit
 *  slower — it crawls, badly enough that people report it as frozen. The
 *  margin covers the context window and working buffers on top of weights. */
const VRAM_HEADROOM = 1.25

/** Below this, the difference is lost in the noise of everything else. */
const NOTICEABLE = 1.3

/**
 * A one-line honest note about what this model costs in time.
 *
 * `vramTotalBytes` is what `/system/stats` reports — BYTES, not gigabytes,
 * and taken raw here on purpose. Converting at each call site is how one of
 * them ends up comparing gigabytes against bytes, and that mistake is
 * invisible: the warning simply never appears. Optional, because without a
 * GPU reading the relative speed is still worth saying.
 */
export function speedNote(
  model: ModelLike,
  installed: ModelLike[],
  vramTotalBytes?: number | null
): SpeedNote | null {
  // Nothing about a cloud model's speed can be read from this PC: its
  // "size" is a manifest of a few hundred bytes.
  if (model.cloud) {
    return {
      tone: 'ok',
      text: "Runs on Ollama's servers, not this PC. Speed depends on your connection and their load."
    }
  }

  // Decimal GB, matching how the rest of the app reports sizes (SystemStats).
  const vramGb = vramTotalBytes && vramTotalBytes > 0 ? vramTotalBytes / 1e9 : null

  // The measurable case first, and the one that dwarfs every other
  // difference here: it does not matter how big the model is if it does not
  // fit. Read from this machine's own GPU, so each install sees its own card.
  if (vramGb !== null && model.size_gb * VRAM_HEADROOM > vramGb) {
    return {
      tone: 'warn',
      text:
        // One decimal, matching SystemStats. Rounding 12.88 to "13 GB" would
        // contradict the figure shown on the dashboard for the same card.
        `Too big for your ${vramGb.toFixed(1)} GB graphics card. It will run on the ` +
        `processor instead and be many times slower — often hours for one video.`
    }
  }

  // Only models whose weights are on this machine. A cloud model counted
  // here became "the smallest" at 346 bytes, and every local model was
  // then millions of times slower than it.
  const usable = installed.filter((m) => m.size_gb > 0 && !m.cloud)
  if (usable.length < 2 || model.size_gb <= 0) return null // nothing to compare against

  const smallest = usable.reduce((a, b) => (b.size_gb < a.size_gb ? b : a))
  if (smallest.name === model.name) {
    return { tone: 'ok', text: 'Fastest of your installed models.' }
  }

  const factor = model.size_gb / smallest.size_gb
  if (factor < NOTICEABLE) {
    return { tone: 'ok', text: `About as fast as ${smallest.name}.` }
  }

  const rounded = factor < 3 ? factor.toFixed(1) : String(Math.round(factor))
  return {
    tone: factor >= 2.5 ? 'slow' : 'ok',
    // How well it picks clips is the grade's job (lib/modelGrade.ts); this
    // says only what it costs in time, and that it is an estimate.
    text: `Roughly ${rounded}× the time of ${smallest.name}.`
  }
}
