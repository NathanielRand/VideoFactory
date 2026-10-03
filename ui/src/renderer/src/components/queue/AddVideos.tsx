import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { CaptionStyle, JobOptions } from '../../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../CaptionStyleControls'
import BrandingPicker from '../BrandingPicker'
import { brandingChoice, defaultBrandingId, withBranding } from '../../lib/branding'
import { Folder, Trash } from '../icons'
import { t } from '../../lib/i18n'
import { compilationsApi, type Compilation } from '../../lib/compilations'

const DRAFT_KEY = 'queue-draft'

const REASONS: Record<string, string> = {
  already_processed: 'already processed',
  already_queued: 'already in the queue',
  unrecognized: 'not a link Video Factory recognises',
  bad_option: 'a setting on this video was rejected',
  queue_full: 'the queue is full — let one finish first'
}

/** What a row is FOR. Every upload lands in the Library whatever it is for:
 *  'library' stops there (decide later, from its Library row), 'clips' runs
 *  the AI pipeline as always, 'compilation' imports it and appends it whole
 *  to a compilation. A library or compilation upload can still be clipped
 *  later from the Library. */
type Intent = 'library' | 'clips' | 'compilation'

const PREF_INTENT = 'generate-intent'
/** Sentinel in the compilation picker for "make a new one on Start". */
const NEW_COMP = -1

const intentOf = (o: JobOptions): Intent =>
  !o.import_only ? 'clips' : o.add_to_compilation !== undefined ? 'compilation' : 'library'

const INTENTS: [Intent, string, string][] = [
  [
    'library',
    '▤ Library only',
    'Add it to your Library and decide later - make clips, put it in a compilation, or both, from its Library row'
  ],
  ['clips', '✂ Make clips', 'Find the best moments with AI and cut them into clips'],
  [
    'compilation',
    '▦ Compilation',
    'Add it to your Library and straight into a compilation, whole. You can still make clips from it later.'
  ]
]

/** Switch a row's intent. The clip settings are kept on the row either way,
 *  so flipping back to Clips does not lose a caption style set a moment ago;
 *  the server ignores them for an import. `compilation` is where a row
 *  switched to Compilation goes until another is picked. */
function withIntent(
  o: JobOptions,
  intent: Intent,
  compilation?: number,
  remember = true
): JobOptions {
  const next = { ...o }
  if (intent === 'clips') {
    delete next.import_only
    delete next.add_to_compilation
  } else {
    next.import_only = true
    if (intent === 'library') delete next.add_to_compilation
    else next.add_to_compilation = next.add_to_compilation ?? compilation ?? NEW_COMP
  }
  if (remember) {
    try {
      localStorage.setItem(PREF_INTENT, intent)
    } catch {
      // Remembering is a convenience only.
    }
  }
  return next
}

/** One video being set up. Owns its own options object, so editing this
 *  video's switches cannot reach any other. */
interface Slot {
  key: string
  /** A pasted link. Empty when this slot is a local file. */
  url: string
  /** A file on this computer. Null when this slot is a link. */
  path: string | null
  /** Editable name for a local file (a link gets its title from the source). */
  title: string
  options: JobOptions
  /** Why the server refused this one, kept so it can be fixed in place. */
  error?: string
  /** Name for a compilation to create on Start, when the picker says "New". */
  newCompilation?: string
}

type ToggleKey = 'captions' | 'long_clips' | 'podcast' | 'longform'

/** The switches after Captions. Captions is rendered on its own so the
 *  "Caption style" button can sit immediately beside it, where it belongs —
 *  it configures that switch and nothing else. */
const TOGGLES: { key: ToggleKey; label: string; hint: string; title: string }[] = [
  {
    key: 'long_clips',
    label: '60s+',
    hint: '(TikTok monetization)',
    title:
      'TikTok monetization requires videos over 1 minute. On: this video’s clips run 61-180s. Off: a natural 10-60s.'
  },
  {
    key: 'longform',
    label: 'Longform',
    hint: '(16:9)',
    title:
      'Horizontal 1920x1080 outputs (YouTube, X/Twitter) using the same AI — the vertical Shorts workflow is unchanged.'
  },
  {
    key: 'podcast',
    label: 'Podcast',
    hint: '(multi-cam)',
    title:
      'For multi-camera podcasts (cuts between angles, several people). Frames shot by shot: each camera shot gets one steady crop centered on whoever is talking, and cuts land directly on the speaker’s face — no panning, no split screens. Leave OFF for normal one-camera streams.'
  }
]

const PREF_STYLE = 'generate-caption-style'

function savedStyle(): Required<CaptionStyle> {
  try {
    return {
      ...DEFAULT_CAPTION_STYLE,
      ...JSON.parse(localStorage.getItem(PREF_STYLE) ?? '{}')
    }
  } catch {
    return { ...DEFAULT_CAPTION_STYLE } // a corrupt saved style must not block the list
  }
}

/** The last caption style set on any row becomes the next row's starting
 *  style. It was read but never written, so it reset to the default every
 *  time. */
function rememberStyle(style: CaptionStyle): void {
  try {
    localStorage.setItem(PREF_STYLE, JSON.stringify(style))
  } catch {
    // Remembering is a convenience only.
  }
}

/** Starting options for the first row: whatever was last used, so the usual
 *  setup is already there. */
/** Keys seedOptions() restores from. Writing them is remember(), below —
 *  they were read but never written, so every choice was forgotten the moment
 *  the window closed and captions came back ticked however often you unticked
 *  it. */
const PREF = {
  captions: 'generate-captions',
  long_clips: 'generate-long-clips',
  podcast: 'generate-podcast',
  longform: 'generate-longform',
  longform_mode: 'generate-longform-mode'
} as const

function remember(key: ToggleKey, on: boolean, mode?: string): void {
  try {
    if (key === 'captions') localStorage.setItem(PREF.captions, String(on))
    else if (key === 'long_clips') localStorage.setItem(PREF.long_clips, String(on))
    else if (key === 'podcast') localStorage.setItem(PREF.podcast, String(on))
    else if (key === 'longform') {
      localStorage.setItem(PREF.longform, String(on))
      if (mode) localStorage.setItem(PREF.longform_mode, mode)
    }
    // Branding is absent: its default is set on the Branding page, not by
    // whatever the last video happened to use.
  } catch {
    // A full or blocked localStorage must not stop someone queueing a video.
  }
}

/** The Generate bar's current settings.
 *
 *  Exported because the assistant needs them too: a job queued by asking the
 *  chat box used to ignore every one of these, so captions came back burned
 *  in however often the box was unticked. One reader, not two. */
export function seedOptions(): JobOptions {
  const o: JobOptions = {
    captions: localStorage.getItem(PREF.captions) !== 'false',
    caption_style: savedStyle()
  }
  if (localStorage.getItem(PREF.long_clips) === 'true') o.long_clips = true
  if (localStorage.getItem(PREF.podcast) === 'true') o.podcast = true
  if (localStorage.getItem(PREF.longform) === 'true') {
    o.longform = { mode: localStorage.getItem(PREF.longform_mode) ?? 'short_clips' }
  }
  const branding = defaultBrandingId()
  if (branding) o.watermark_profile_id = branding
  return o
}

/** A fresh row: the Generate bar's settings plus the last intent picked.
 *  Kept out of seedOptions, which the assistant also reads — a chat request
 *  to "clip this" must never come back as an import because the last row
 *  here was for a compilation. */
function seedRow(): JobOptions {
  const o = seedOptions()
  // A remembered 'compilation' starts as Library only: which compilation is
  // a per-batch choice, and guessing it would file videos in the wrong one.
  const pref = localStorage.getItem(PREF_INTENT)
  if (pref === 'library' || pref === 'compilation') o.import_only = true
  return o
}

let counter = 0
const newKey = (): string => `s${Date.now()}-${counter++}`

function emptySlot(from?: JobOptions): Slot {
  // A new row copies the one above it: a batch usually shares most settings,
  // and every switch is still overridable per video. Copied, not shared.
  return { key: newKey(), url: '', path: null, title: '', options: { ...(from ?? seedRow()) } }
}

function loadDraft(): Slot[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null')
    if (Array.isArray(raw) && raw.length > 0) {
      return raw.map((s: Slot) => ({ ...s, key: newKey(), error: undefined }))
    }
  } catch {
    /* a corrupt draft is not worth failing over — start clean */
  }
  return [emptySlot()]
}

/** The generate bar: one video or ten, each with its own settings.
 *
 *  Nothing here touches the server until Generate. The list is local state
 *  (plus a saved draft), so a half-built list is not a queue: earlier versions
 *  created job rows as videos were added, which meant the queue began filling —
 *  and under the old auto-start, running — before the user had finished
 *  deciding. Building and committing are separate, and only Generate crosses
 *  that line.
 *
 *  Every row owns its own options object, so toggling video 2 writes video 2
 *  and nothing else. */
export default function AddVideos({
  onAdded,
  libraryOnly = false
}: {
  onAdded?: () => void
  /** The Library's own Add videos: every row just goes into the Library, and
   *  what it is for is chosen afterwards on its row there. No choice to make
   *  here, so none is shown. */
  libraryOnly?: boolean
}): JSX.Element {
  const [slots, setSlots] = useState<Slot[]>(loadDraft)
  const [channel, setChannel] = useState(localStorage.getItem('upload-channel') ?? '')
  const [openStyle, setOpenStyle] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [added, setAdded] = useState<number | null>(null)
  // Links refused only because they have been clipped before. Offered as a
  // prompt rather than a permanent "process again" switch: it is the rare
  // case, and a checkbox that matters once in twenty uses is clutter the
  // other nineteen times.
  const [alreadyDone, setAlreadyDone] = useState<Slot[]>([])
  // Whether a batch is already under way, so this doesn't offer to start
  // something already running. Read here rather than passed in, so it behaves
  // the same on the Dashboard and on the Queue page.
  const [queueRunning, setQueueRunning] = useState(false)
  // Free slots on the server. The list can hold at most this many, so the cap
  // is visible while building rather than a refusal after pressing Generate.
  const [capacity, setCapacity] = useState<number | null>(null)
  const [maxActive, setMaxActive] = useState(5)
  // Compilations a row can go straight into. Any not rendering right now:
  // a rendering one's recipe is frozen until it finishes.
  const [compilations, setCompilations] = useState<Compilation[]>([])
  // What the last Start did, for the links under the bar.
  const [imported, setImported] = useState<{ count: number; compilation: number | null } | null>(
    null
  )

  useEffect(() => {
    compilationsApi
      .list()
      .then((all) =>
        setCompilations(all.filter((c) => c.status !== 'queued' && c.status !== 'rendering'))
      )
      .catch(() => undefined) // only costs the picker its list
  }, [added])

  useEffect(() => {
    void api
      .queue()
      .then((q) => {
        setQueueRunning(!q.paused && q.processing.length + q.queued.length > 0)
        setCapacity(q.capacity)
        setMaxActive(q.max_active)
      })
      .catch(() => undefined) // backend not up yet; Generate still works
  }, [added])

  // Keep the draft. Several pasted links must not die to an accidental close.
  useEffect(() => {
    const keep = slots.filter((s) => s.url.trim() || s.path)
    if (keep.length > 0) localStorage.setItem(DRAFT_KEY, JSON.stringify(keep))
    else localStorage.removeItem(DRAFT_KEY)
  }, [slots])

  const ready = slots.filter((s) => s.url.trim() || s.path)
  const hasFiles = ready.some((s) => s.path)
  const intentFor = (o: JobOptions): Intent => (libraryOnly ? 'library' : intentOf(o))
  const tally = (i: Intent): number => ready.filter((s) => intentFor(s.options) === i).length
  const forClips = tally('clips')
  const forComp = tally('compilation')
  const forLibrary = tally('library')
  const room = capacity ?? maxActive
  const full = slots.length >= room

  const patch = (key: string, change: Partial<Slot>): void =>
    setSlots((prev) => prev.map((s) => (s.key === key ? { ...s, ...change, error: undefined } : s)))

  /** Merge a few fields into this video's options — only used where nothing
   *  needs removing (longform mode, caption style). */
  const patchOptions = (key: string, change: JobOptions): void =>
    setSlots((prev) =>
      prev.map((s) =>
        s.key === key ? { ...s, options: { ...s.options, ...change }, error: undefined } : s
      )
    )

  /** REPLACE this video's options wholesale.
   *
   *  Switching an option off deletes its key (an absent key is what the
   *  pipeline reads as "default"), and a spread merge cannot express a
   *  deletion — `{...old, ...new}` keeps `podcast: true` when `new` simply
   *  lacks `podcast`. Merging here is why unticking a box did nothing. */
  const replaceOptions = (key: string, options: JobOptions): void =>
    setSlots((prev) => prev.map((s) => (s.key === key ? { ...s, options, error: undefined } : s)))

  const toggle = (o: JobOptions, key: ToggleKey, on: boolean): JobOptions => {
    const next = { ...o }
    if (key === 'captions') next.captions = on
    else if (key === 'long_clips') {
      if (on) next.long_clips = true
      else delete next.long_clips
    } else if (key === 'podcast') {
      if (on) next.podcast = true
      else delete next.podcast
    } else if (key === 'longform') {
      if (on) next.longform = { mode: next.longform?.mode ?? 'short_clips' }
      else delete next.longform
    }
    remember(key, on, next.longform?.mode)
    return next
  }

  const isOn = (o: JobOptions, key: ToggleKey): boolean => {
    if (key === 'captions') return o.captions !== false
    if (key === 'long_clips') return Boolean(o.long_clips)
    if (key === 'podcast') return Boolean(o.podcast)
    return Boolean(o.longform)
  }

  const addSlot = (): void =>
    setSlots((prev) => [...prev, emptySlot(prev[prev.length - 1]?.options)])

  const addFiles = async (): Promise<void> => {
    const picked = await window.studio.pickVideoFiles()
    if (!picked || picked.length === 0) return
    setSlots((prev) => {
      const known = new Set(prev.map((s) => s.path))
      const base = prev[prev.length - 1]?.options
      const fresh = picked
        .filter((p) => !known.has(p))
        .map((p) => ({
          key: newKey(),
          url: '',
          path: p,
          title: (p.split(/[\\/]/).pop() ?? p).replace(/\.[^.]+$/, ''),
          options: { ...(base ?? seedRow()) }
        }))
      const kept = prev.filter((s) => s.url.trim() || s.path)
      return [...kept, ...fresh]
    })
  }

  /** Queue everything, then — and only then — start processing.
   *  `force` re-runs videos that were refused for having been clipped before. */
  const generate = async (force = false, only?: Slot[]): Promise<void> => {
    const picked = only ?? ready
    if (picked.length === 0) return
    setBusy(true)
    setError(null)
    setAdded(null)
    setImported(null)
    if (!force) setAlreadyDone([])
    try {
      const failures = new Map<string, string>()
      const done: Slot[] = []
      let ok = 0
      // Jobs that need the queue to run. A local file imported for a
      // compilation is finished the moment the call returns, and starting
      // the queue for it would also start whatever else was left waiting.
      let queued = 0
      let importedCount = 0
      let lastComp: number | null = null

      // Rows that asked for a NEW compilation: make each distinct name once,
      // then point those rows at it. Done first, so a failure here leaves
      // nothing half-imported.
      const made = new Map<string, number>()
      for (const s of picked) {
        if (libraryOnly || s.options.add_to_compilation !== NEW_COMP) continue
        const name = (s.newCompilation ?? '').trim() || t('Untitled compilation')
        if (!made.has(name)) made.set(name, (await compilationsApi.create(name)).id)
      }
      const list = picked.map((s) => {
        if (libraryOnly)
          return { ...s, options: withIntent(s.options, 'library', undefined, false) }
        if (s.options.add_to_compilation !== NEW_COMP) return s
        const name = (s.newCompilation ?? '').trim() || t('Untitled compilation')
        return { ...s, options: { ...s.options, add_to_compilation: made.get(name) } }
      })
      const note = (s: Slot): void => {
        if (!s.options.import_only) return
        importedCount += 1
        if (s.options.add_to_compilation) lastComp = s.options.add_to_compilation
      }

      const links = list.filter((s) => !s.path)
      if (links.length > 0) {
        const res = await api.createJobsBatch(
          links.map((s) => ({ url: s.url.trim(), ...s.options, ...(force ? { force: true } : {}) }))
        )
        ok += res.created.length
        for (const c of res.created) {
          if (c.job_id) queued += 1
          const slot = links.find((l) => l.url.trim() === c.url)
          if (slot) note(slot)
        }
        for (const s of res.skipped) {
          if (s.reason === 'already_processed') {
            const slot = links.find((l) => l.url.trim() === s.url)
            if (slot) done.push(slot)
          }
          const detail = REASONS[s.reason] ?? s.reason
          failures.set(s.url, s.detail ? `${detail}: ${s.detail}` : detail)
        }
      }

      // Local files go one at a time: each is remuxed or transcoded into the
      // pipeline's layout on the way in, which is real work per file.
      for (const slot of list.filter((s) => s.path)) {
        try {
          const res = await api.addLocalVideo({
            path: slot.path as string,
            title: slot.title,
            channel: channel.trim(),
            ...slot.options,
            ...(force ? { force: true } : {})
          })
          ok += 1
          if (res.job_id) queued += 1
          note(slot)
        } catch (e) {
          failures.set(slot.path as string, e instanceof Error ? e.message : String(e))
        }
      }

      // Only now does anything begin.
      if (queued > 0 && !queueRunning) await api.resumeQueue()
      if (made.size > 0) setCompilations(await compilationsApi.list().catch(() => compilations))

      setAdded(queued)
      if (importedCount > 0) setImported({ count: importedCount, compilation: lastComp })
      setAlreadyDone(done)
      // Rejected videos stay in the list, with their reason, so they can be
      // fixed rather than re-typed. Everything that went through is cleared.
      const left = slots
        .filter((s) => {
          const id = s.path ?? s.url.trim()
          return id !== '' && failures.has(id)
        })
        .map((s) => ({ ...s, error: failures.get(s.path ?? s.url.trim()) }))
      setSlots(left.length > 0 ? left : [emptySlot()])
      if (left.length === 0) localStorage.removeItem(DRAFT_KEY)
      if (ok > 0) onAdded?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="card space-y-2">
        {slots.map((slot, n) => (
          <div key={slot.key}>
            <div className="flex gap-3 items-center flex-wrap">
              {slot.path ? (
                <input
                  className="input w-72 max-w-full"
                  aria-label={`Title for video ${n + 1}`}
                  title={slot.path}
                  value={slot.title}
                  placeholder={t('Video title')}
                  onChange={(e) => patch(slot.key, { title: e.target.value })}
                />
              ) : (
                <input
                  className="input w-72 max-w-full"
                  placeholder={t('Paste a YouTube, Twitch, or Kick URL…')}
                  aria-label={`Video URL ${n + 1}`}
                  value={slot.url}
                  onChange={(e) => patch(slot.key, { url: e.target.value })}
                  onKeyDown={(e) => e.key === 'Enter' && generate()}
                />
              )}

              {/* What this video is for. First after the link, because it
                  decides which settings follow it. Not asked in the Library:
                  there, every upload is Library only by definition. */}
              {!libraryOnly && (
                <div
                  className="flex rounded-lg bg-raised p-0.5 shrink-0"
                  role="radiogroup"
                  aria-label={`What to do with video ${n + 1}`}
                >
                  {INTENTS.map(([value, label, hint]) => (
                    <button
                      key={value}
                      role="radio"
                      aria-checked={intentOf(slot.options) === value}
                      title={t(hint)}
                      onClick={() =>
                        replaceOptions(
                          slot.key,
                          withIntent(slot.options, value, compilations[0]?.id)
                        )
                      }
                      className={`px-2.5 py-1 rounded-md text-xs whitespace-nowrap transition-colors ${
                        intentOf(slot.options) === value
                          ? 'bg-accent/20 text-accent font-medium'
                          : 'text-muted hover:text-ink'
                      }`}
                    >
                      {t(label)}
                    </button>
                  ))}
                </div>
              )}

              {intentFor(slot.options) === 'library' ? (
                <span className="text-xs text-muted">
                  {libraryOnly
                    ? t(
                        'Goes into your Library - choose Make clips or Add to compilation on its row.'
                      )
                    : t('Goes into your Library - decide what it is for from there.')}
                </span>
              ) : intentFor(slot.options) === 'compilation' ? (
                <>
                  <select
                    className="input !w-60 shrink-0"
                    aria-label={`Compilation for video ${n + 1}`}
                    value={slot.options.add_to_compilation ?? ''}
                    onChange={(e) => {
                      const next = { ...slot.options }
                      if (e.target.value === '') delete next.add_to_compilation
                      else next.add_to_compilation = Number(e.target.value)
                      replaceOptions(slot.key, next)
                    }}
                  >
                    {compilations.map((c) => (
                      <option key={c.id} value={c.id}>
                        {t('Add to')}: {c.title}
                      </option>
                    ))}
                    <option value={NEW_COMP}>+ {t('New compilation…')}</option>
                  </select>
                  {slot.options.add_to_compilation === NEW_COMP && (
                    <input
                      className="input !w-48 shrink-0"
                      placeholder={t('New compilation title')}
                      aria-label={`New compilation title for video ${n + 1}`}
                      value={slot.newCompilation ?? ''}
                      onChange={(e) => patch(slot.key, { newCompilation: e.target.value })}
                    />
                  )}
                  <span className="text-xs text-muted">
                    {t('Added whole - trim it in the compilation editor.')}
                  </span>
                </>
              ) : (
                <>
                  <label
                    className="flex items-center gap-2 cursor-pointer text-sm shrink-0 whitespace-nowrap"
                    title="Burn captions into this video’s clips"
                  >
                    <input
                      type="checkbox"
                      className="size-4 accent-[#38BDF8]"
                      checked={slot.options.captions !== false}
                      onChange={(e) =>
                        replaceOptions(slot.key, toggle(slot.options, 'captions', e.target.checked))
                      }
                    />
                    {t('Captions')}
                  </label>

                  {/* Immediately beside Captions: it configures that switch. */}
                  <button
                    className="btn-ghost shrink-0"
                    onClick={() => setOpenStyle(openStyle === slot.key ? null : slot.key)}
                    aria-expanded={openStyle === slot.key}
                    disabled={slot.options.captions === false}
                  >
                    {t('Caption style')} {openStyle === slot.key ? '▾' : '▸'}
                  </button>

                  {TOGGLES.map((tg) => (
                    <label
                      key={tg.key}
                      className="flex items-center gap-2 text-sm shrink-0 whitespace-nowrap cursor-pointer"
                      title={tg.title}
                    >
                      <input
                        type="checkbox"
                        className="size-4 accent-[#38BDF8]"
                        checked={isOn(slot.options, tg.key)}
                        onChange={(e) =>
                          replaceOptions(slot.key, toggle(slot.options, tg.key, e.target.checked))
                        }
                      />
                      {t(tg.label)} <span className="text-muted">{t(tg.hint)}</span>
                    </label>
                  ))}

                  <BrandingPicker
                    value={brandingChoice(slot.options)}
                    onChange={(choice) =>
                      replaceOptions(slot.key, withBranding(slot.options, choice))
                    }
                  />
                </>
              )}

              {/* Always removable once it holds something. Hiding this on the
                  last row trapped a single uploaded file: its name is not an
                  editable URL, so with no remove button there was no way back
                  to an empty link row. Removing the last one leaves a fresh
                  empty row rather than nothing. */}
              {(slots.length > 1 || slot.url.trim() || slot.path) && (
                <button
                  className="btn-ghost shrink-0 !px-2"
                  title={t('Remove this video')}
                  aria-label={t('Remove this video')}
                  onClick={() =>
                    setSlots((prev) => {
                      const left = prev.filter((s) => s.key !== slot.key)
                      return left.length > 0 ? left : [emptySlot(slot.options)]
                    })
                  }
                >
                  <Trash />
                </button>
              )}
            </div>

            {slot.options.longform && intentFor(slot.options) === 'clips' && (
              <div className="flex items-center gap-3 flex-wrap mt-2">
                <span className="label shrink-0">{t('Longform output')}</span>
                <select
                  className="input !w-64"
                  value={slot.options.longform.mode}
                  onChange={(e) => patchOptions(slot.key, { longform: { mode: e.target.value } })}
                  aria-label={`Longform output type for video ${n + 1}`}
                >
                  <option value="short_clips">Short Clips (up to 60s, horizontal)</option>
                  <option value="clips_140">Clips (up to 140s — X/Twitter)</option>
                  <option value="highlights">Highlights (best-of, 8-20 min by quality)</option>
                  <option value="edited_stream">Edited Stream (downtime removed)</option>
                </select>
              </div>
            )}

            {openStyle === slot.key &&
              slot.options.captions !== false &&
              intentFor(slot.options) === 'clips' && (
                <div className="w-full space-y-3 border-t border-raised/60 pt-3 mt-2">
                  <p className="label">
                    {t('Caption style for')}{' '}
                    {slot.path ? slot.title || t('this file') : t('this video')}
                  </p>
                  <CaptionStyleControls
                    idPrefix={`slot-${slot.key}`}
                    style={{ ...DEFAULT_CAPTION_STYLE, ...(slot.options.caption_style ?? {}) }}
                    onChange={(k, v) => {
                      const caption_style = {
                        ...DEFAULT_CAPTION_STYLE,
                        ...(slot.options.caption_style ?? {}),
                        [k]: v
                      }
                      rememberStyle(caption_style)
                      patchOptions(slot.key, { caption_style })
                    }}
                  />
                </div>
              )}

            {slot.error && <p className="text-sm text-error mt-1">{slot.error}</p>}
          </div>
        ))}

        <div className="flex gap-3 items-center flex-wrap pt-1">
          <button
            className="btn-ghost shrink-0"
            onClick={addSlot}
            disabled={full}
            title={
              full
                ? `${t('The queue holds')} ${maxActive} ${t('videos at a time')}`
                : t('Add another video')
            }
          >
            + {t('Add video')}
          </button>
          <button className="btn-ghost shrink-0" onClick={addFiles} disabled={full}>
            <Folder className="mr-1.5" />
            {t('Upload video file')}
          </button>
          {/* Greyed out until there is an upload, the same way Caption style
              is greyed out until Captions is ticked. A downloaded video takes
              its channel from the source metadata; only a file off your
              computer has nobody to file it under.

              Shown blank while disabled: a greyed-out box still displaying the
              last creator reads as stuck rather than inapplicable. The value
              is remembered and returns with the next upload. */}
          <input
            className="input !w-44 shrink-0"
            placeholder={t('Creator profile')}
            aria-label="Creator profile for uploaded files"
            disabled={!hasFiles}
            title={
              hasFiles
                ? 'Files these uploads under this creator in your library and the Creators tab. Leave blank to skip.'
                : 'For uploaded files only — a downloaded video brings its own channel name'
            }
            value={hasFiles ? channel : ''}
            onChange={(e) => {
              setChannel(e.target.value)
              localStorage.setItem('upload-channel', e.target.value)
            }}
          />
          {/* A batch of twenty short clips for one compilation should not
              take twenty clicks. */}
          {slots.length > 1 && !libraryOnly && (
            <span className="text-xs text-muted flex items-center gap-1.5 shrink-0">
              {t('Set all to')}:
              {(['library', 'clips'] as const).map((intent) => (
                <button
                  key={intent}
                  className="hover:text-accent hover:underline"
                  onClick={() =>
                    setSlots((prev) =>
                      prev.map((s) => ({ ...s, options: withIntent(s.options, intent) }))
                    )
                  }
                >
                  {intent === 'library' ? t('library only') : t('clips')}
                </button>
              ))}
              <button
                className="hover:text-accent hover:underline"
                title={t('Every row goes into the compilation the first row goes into')}
                onClick={() =>
                  setSlots((prev) => {
                    const first = withIntent(prev[0].options, 'compilation', compilations[0]?.id)
                    return prev.map((s, i) => ({
                      ...s,
                      options: {
                        ...withIntent(s.options, 'compilation', undefined, false),
                        add_to_compilation: first.add_to_compilation
                      },
                      newCompilation: i > 0 ? prev[0].newCompilation : s.newCompilation
                    }))
                  })
                }
              >
                {t('compilation')}
              </button>
            </span>
          )}
          <button
            className="btn-accent shrink-0 ml-auto"
            onClick={() => generate()}
            disabled={busy || ready.length === 0}
          >
            {busy
              ? t('Starting…')
              : (() => {
                  const n = ready.length > 1 ? ` (${ready.length})` : ''
                  if (forLibrary === ready.length) return `${t('Add to Library')}${n}`
                  if (forComp === ready.length) return `${t('Add to compilation')}${n}`
                  if (forClips < ready.length) return `${t('Start')}${n}`
                  return queueRunning ? `${t('Add to queue')}${n}` : `${t('Generate clips')}${n}`
                })()}
          </button>
        </div>
      </div>

      {full && (
        <p className="text-xs text-muted px-1">
          {t('The queue holds')} {maxActive}{' '}
          {t('videos at a time - start these, then add more when one finishes.')}
        </p>
      )}

      {added !== null && added > 0 && (
        <p className="text-sm text-accent px-1">
          {added === 1
            ? t('Started - watch the progress below.')
            : `${added} ${t('videos queued - working through them one at a time.')}`}{' '}
          <button
            className="underline hover:text-ink"
            onClick={() => window.dispatchEvent(new CustomEvent('open-queue'))}
          >
            {t('View queue')}
          </button>
        </p>
      )}
      {imported && (
        <p className="text-sm text-accent px-1">
          {imported.count === 1
            ? t('Going into your Library.')
            : `${imported.count} ${t('videos going into your Library.')}`}{' '}
          {t('Links download in the queue; files are there already.')}{' '}
          {!libraryOnly && (
            <button
              className="underline hover:text-ink"
              onClick={() => window.dispatchEvent(new CustomEvent('open-library'))}
            >
              {t('Open Library')}
            </button>
          )}
          {imported.compilation !== null && (
            <>
              {' · '}
              <button
                className="underline hover:text-ink"
                onClick={() =>
                  window.dispatchEvent(
                    new CustomEvent('open-compilation', { detail: imported.compilation })
                  )
                }
              >
                {t('Open compilation')}
              </button>
            </>
          )}
        </p>
      )}
      {/* Only shown when it actually happened — the common case never sees it. */}
      {alreadyDone.length > 0 && (
        <div className="card flex items-center gap-3 flex-wrap">
          <p className="text-sm flex-1 min-w-64">
            {alreadyDone.length === 1
              ? t('That video was already processed.')
              : `${alreadyDone.length} ${t('of those were already processed.')}`}{' '}
            {t(
              'Make clips again with the settings you chose? Existing clips are kept - new ones are added alongside them.'
            )}
          </p>
          <button
            className="btn-accent shrink-0"
            disabled={busy}
            onClick={() => generate(true, alreadyDone)}
          >
            {t('Process again')}
          </button>
          <button className="btn-ghost shrink-0" onClick={() => setAlreadyDone([])}>
            {t('Cancel')}
          </button>
        </div>
      )}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
    </div>
  )
}
