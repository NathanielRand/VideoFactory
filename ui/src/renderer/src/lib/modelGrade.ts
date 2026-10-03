/** How good a model is at THIS job: reading a long transcript, finding the
 *  moments worth clipping, and writing titles for them, as JSON.
 *
 *  A report-card grade, on one scale for every model, where A+ is the best
 *  there is for the job (frontier-size models), D- is the least that still
 *  works, and F / F- will not do it well enough, or at all.
 *
 *  ── What the grade is made of, and what it is worth ──
 *
 *  Nothing is run. It is read from the model's name and size, the two things
 *  every installed model has:
 *
 *    size        Parameter count is the strongest single signal of how well a
 *                model follows a long, detailed prompt. Read from the tag
 *                ("12b", "0.8b", "e4b", "8x7b", "270m"), or estimated from
 *                size on disk when the tag does not say.
 *    generation  A 2025 8B model beats a 2023 13B one at this. Known newer
 *                families are raised, older ones lowered.
 *    fit         Base (not instruction-tuned) and code-only models are bad at
 *                following the clip prompt; embedding models cannot write at
 *                all, so they are F- whatever their size.
 *
 *  Like the speed note beside it, this is a direction, not a benchmark: two
 *  models a grade apart can swap places on a particular video.
 */

export type Grade =
  | 'A+' | 'A' | 'A-'
  | 'B+' | 'B' | 'B-'
  | 'C+' | 'C' | 'C-'
  | 'D+' | 'D' | 'D-'
  | 'F' | 'F-'

export interface ModelGrade {
  grade: Grade
  /** 0..100, what the ring fills to. */
  score: number
  /** Billions of parameters, as read or estimated; null when unknown. */
  params: number | null
  /** One sentence on why, for the tooltip and the Models page. */
  why: string
}

interface GradableModel {
  name: string
  size_gb: number
  cloud?: boolean
  /** Billions of parameters as Ollama read them from the model file. Better
   *  than the tag when present ("gemma:7b" is really 8.5B). */
  params_b?: number | null
}

/** Lower bound of each grade. Checked top down. */
const BANDS: [number, Grade][] = [
  [93, 'A+'], [88, 'A'], [84, 'A-'],
  [80, 'B+'], [75, 'B'], [70, 'B-'],
  [65, 'C+'], [58, 'C'], [50, 'C-'],
  [43, 'D+'], [35, 'D'], [20, 'D-'],
  [8, 'F']
]

/** Score by size, on a log scale: each doubling is worth less than the last.
 *  Anchored on what is known to work: well under a billion barely manages the
 *  task, 7-9B is solid, 30B+ is very good, and hundreds of billions (the
 *  cloud frontier) is the ceiling. */
const SIZE_CURVE: [number, number][] = [
  [0.1, 2], [0.3, 10], [0.8, 22], [1.5, 32], [3, 45], [8, 60],
  [14, 70], [32, 80], [70, 88], [200, 95], [700, 99]
]

/** Newer families do this markedly better at the same size; older worse.
 *  First match wins, so specific names come before general ones. */
const FAMILIES: [RegExp, number, string][] = [
  [/embed|nomic|bge|minilm|mxbai|arctic-embed|\be5\b|rerank/, -100, 'an embedding model: it cannot write text'],
  [/codellama|starcoder|codegemma|deepseek-coder|\bcoder\b|-coder|codestral|sqlcoder/, -10, 'made for code, not for reading transcripts'],
  [/llava|bakllava|moondream/, -10, 'an image model; text is secondary'],
  [/qwen3\.5|qwen3|gemma4|gemma3n|gemma3|nemotron|llama4|llama3\.3|gpt-oss|phi4|mistral-small3|magistral|deepseek-v3|deepseek-r1|kimi|glm-4\.[5-9]|granite4|olmo2|exaone4/, 5, 'a recent family'],
  [/qwen2\.5|llama3\.[12]|mistral-nemo|gemma2|phi3\.5|command-r|granite3|aya-expanse|hermes3/, 0, ''],
  [/phi3|mixtral|mistral(?!-)|openchat|zephyr|neural-chat/, -5, 'an older family'],
  [/llama2|llama-2|gemma(?![23])|phi2|phi-2|tinyllama|orca|vicuna|wizardlm|falcon|stablelm|dolly|gpt4all/, -12, 'an old family']
]

function band(score: number): Grade {
  for (const [min, grade] of BANDS) if (score >= min) return grade
  return 'F-'
}

function interpolate(params: number): number {
  const x = Math.log10(Math.max(0.01, params))
  if (params <= SIZE_CURVE[0][0]) return SIZE_CURVE[0][1]
  for (let i = 1; i < SIZE_CURVE.length; i++) {
    const [p1, s1] = SIZE_CURVE[i]
    if (params <= p1) {
      const [p0, s0] = SIZE_CURVE[i - 1]
      const t = (x - Math.log10(p0)) / (Math.log10(p1) - Math.log10(p0))
      return s0 + t * (s1 - s0)
    }
  }
  return SIZE_CURVE[SIZE_CURVE.length - 1][1]
}

/** Billions of parameters from a model tag, or null. "gemma3:12b" -> 12,
 *  "qwen3.5-instruct:0.8b" -> 0.8, "gemma3n:e4b" -> 4 (effective),
 *  "mixtral:8x7b" -> about 47 (experts share layers), "gemma3:270m" -> 0.27,
 *  "qwen3:30b-a3b" -> 30. */
export function paramsFromName(name: string): number | null {
  const tag = name.toLowerCase()
  const moe = tag.match(/(\d+)x(\d+(?:\.\d+)?)b/)
  if (moe) return Number(moe[1]) * Number(moe[2]) * 0.85
  const m = tag.match(/(?:^|[:\-_/])e?(\d+(?:\.\d+)?)([bm])(?![a-z])/)
  if (!m) return null
  const n = Number(m[1])
  return m[2] === 'm' ? n / 1000 : n
}

/** What the grade says in words, by where it falls. */
function verdict(score: number): string {
  if (score >= 88) return 'Among the best for picking and titling clips.'
  if (score >= 75) return 'Very good at finding the moments worth clipping.'
  if (score >= 58) return 'Solid: finds most good moments, with the odd miss.'
  if (score >= 43) return 'Works, but misses moments and writes weaker titles.'
  if (score >= 20) return 'Barely enough: expect many good moments to be missed.'
  if (score >= 8) return 'Too small to follow the clip prompt reliably.'
  return 'Cannot do this job.'
}

export function gradeModel(model: GradableModel): ModelGrade {
  const name = model.name.toLowerCase()
  let params = model.params_b && model.params_b > 0 ? model.params_b : paramsFromName(name)
  let sizeNote = ''
  if (params === null && !model.cloud && model.size_gb > 0) {
    // Most downloads are 4-bit, about 0.6 GB per billion parameters.
    params = model.size_gb / 0.6
    sizeNote = ' (size estimated from the download)'
  }

  const family = FAMILIES.find(([re]) => re.test(name))
  if (family && family[1] <= -100) {
    return { grade: 'F-', score: 0, params, why: `This is ${family[2]}, so it cannot pick clips.` }
  }

  // A cloud model with no size in its name is one of the big hosted ones.
  let score = params !== null ? interpolate(params) : model.cloud ? 92 : 50
  const reasons: string[] = []
  if (family && family[1] !== 0) {
    score += family[1]
    reasons.push(family[2])
  }
  if (/[:\-_](base|text)(?![a-z])/.test(name) && !/instruct|chat|-it\b/.test(name)) {
    score -= 20
    reasons.push('a base model, not tuned to follow instructions')
  }
  score = Math.max(0, Math.min(100, Math.round(score)))

  // One decimal below ten ("1.7B", "0.8B"), whole numbers above ("12B").
  const shown = params === null ? '' : params < 10 ? String(Number(params.toFixed(1))) : String(Math.round(params))
  const size = params !== null ? `${shown}B parameters${sizeNote}` : ''
  const detail = [size, ...reasons].filter(Boolean).join('; ')
  return {
    grade: band(score),
    score,
    params,
    why: `${verdict(score)}${detail ? ` ${detail.charAt(0).toUpperCase()}${detail.slice(1)}.` : ''}`
  }
}

/** The grade's colour: red at the bottom, amber in the middle, green at the
 *  top, as one continuous hue so neighbouring grades look like neighbours. */
export function gradeColor(score: number): string {
  const hue = Math.round((Math.max(0, Math.min(100, score)) / 100) * 140) // 0 red .. 140 green
  return `hsl(${hue} 80% 55%)`
}
