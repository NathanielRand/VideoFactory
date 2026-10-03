import { useEffect, useState, type ReactNode } from 'react'
import { compilationsApi, type Compilation } from '../../lib/compilations'
import type { WatchActions } from '../../lib/types'
import { Film, Scissors } from '../icons'
import { t } from '../../lib/i18n'

/** How many of each video's best clips a compilation takes. 0 is all. */
const CLIP_COUNTS = [1, 2, 3, 5, 10, 0]

export const DEFAULT_ACTIONS: WatchActions = {
  clips: true,
  compile: false,
  compilation: { compilation_id: null, what: 'whole', max_clips: 3 }
}

/** What a watch does with each new video: make clips, add it to a
 *  compilation, both, or neither (it only lands in the Library).
 *
 *  Each choice is a tile, and its own settings open beneath it only while it
 *  is on, so the form holds no settings for something that will not happen.
 *  The clip settings are the caller's (`clipsPanel`); the compilation ones
 *  live here, since the add form and every watch card offer the same.
 *
 *  `live` is a watch card, which saves each change as it is made: there a new
 *  compilation is made with its own button, not on every keystroke of its
 *  name. Without it (the add form) the name rides along until Add. */
export default function WatchActionsPicker({
  value,
  onChange,
  name,
  clipsPanel,
  live = false,
  disabled = false
}: {
  value: WatchActions
  onChange: (next: WatchActions) => void
  /** The channel or playlist, for the name a new compilation gets. */
  name: string
  clipsPanel?: ReactNode
  live?: boolean
  disabled?: boolean
}): JSX.Element {
  const [compilations, setCompilations] = useState<Compilation[] | null>(null)
  // Live only: the name being typed for a compilation not made yet.
  const [creating, setCreating] = useState<string | null>(null)
  const target = value.compilation
  // Left empty, the server names it after the channel or playlist.
  const fallbackName = name
    ? `${name} ${t('compilation')}`
    : t('Named after the channel or playlist')

  useEffect(() => {
    if (!value.compile) return
    let alive = true
    compilationsApi
      .list()
      .then((list) => alive && setCompilations(list))
      .catch(() => alive && setCompilations([]))
    return () => {
      alive = false
    }
  }, [value.compile, target.compilation_id])

  const set = (patch: Partial<WatchActions>): void => onChange({ ...value, ...patch })
  const setTarget = (patch: Partial<WatchActions['compilation']>): void =>
    onChange({ ...value, compilation: { ...target, ...patch } })

  const newMode = live ? creating !== null : target.new_title !== undefined
  const picked = newMode ? 'new' : target.compilation_id != null ? String(target.compilation_id) : ''

  const toggleClips = (on: boolean): void => {
    // Best clips cannot be added without making clips.
    const compilation = !on && target.what === 'clips' ? { ...target, what: 'whole' as const } : target
    onChange({ ...value, clips: on, compilation })
  }

  const toggleCompile = (on: boolean): void => {
    if (!on) {
      setCreating(null)
      return set({ compile: false })
    }
    if (target.compilation_id != null) return set({ compile: true })
    if (live) {
      // Nothing to save until a compilation is chosen or made.
      setCreating('')
      return
    }
    onChange({ ...value, compile: true, compilation: { ...target, new_title: '' } })
  }

  const pick = (choice: string): void => {
    if (choice === 'new') {
      if (live) setCreating('')
      else setTarget({ compilation_id: null, new_title: '' })
      return
    }
    setCreating(null)
    const { new_title: _drop, ...rest } = target
    onChange({ ...value, compile: true, compilation: { ...rest, compilation_id: Number(choice) } })
  }

  // In a card, compile shows as on while a new one is being named, even
  // though nothing is saved until it is made.
  const compileOn = value.compile || (live && creating !== null)
  const editable = (compilations ?? []).filter(
    (c) => c.status !== 'queued' && c.status !== 'rendering'
  )
  const deleted = target.compilation_id != null && target.title === null

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Tile
          on={value.clips}
          disabled={disabled}
          icon={<Scissors size={18} />}
          title={t('Generate clips')}
          text={t('Find the best moments with AI and cut them into short clips, ready to publish.')}
          onToggle={toggleClips}
        />
        <Tile
          on={compileOn}
          disabled={disabled}
          icon={<Film size={18} />}
          title={t('Add to compilation')}
          text={t('Put each new video, or its best clips, into a compilation you render when it is ready.')}
          onToggle={toggleCompile}
        />
      </div>

      {value.clips && clipsPanel && <Reveal title={t('Clips')}>{clipsPanel}</Reveal>}

      {compileOn && (
        <Reveal title={t('Compilation')}>
          <div className="flex gap-x-6 gap-y-3 flex-wrap items-end">
            <label className="text-sm space-y-1">
              <span className="label block">{t('Add to')}</span>
              <select
                className="input !w-72"
                value={picked}
                disabled={disabled}
                onChange={(e) => pick(e.target.value)}
              >
                {picked === '' && <option value="">{t('Choose a compilation…')}</option>}
                {deleted && (
                  <option value={String(target.compilation_id)}>{t('(deleted compilation)')}</option>
                )}
                {editable.map((c) => (
                  <option key={c.id} value={String(c.id)}>
                    {c.title} · {(c.recipe.segments ?? []).length} {t('segments')}
                  </option>
                ))}
                <option value="new">＋ {t('New compilation')}</option>
              </select>
            </label>
            {newMode && (
              <label className="text-sm space-y-1 flex-1 min-w-56">
                <span className="label block">{t('Name')}</span>
                <span className="flex gap-2">
                  <input
                    className="input"
                    value={live ? (creating ?? '') : (target.new_title ?? '')}
                    placeholder={fallbackName}
                    disabled={disabled}
                    onChange={(e) =>
                      live ? setCreating(e.target.value) : setTarget({ new_title: e.target.value })
                    }
                  />
                  {live && (
                    <button
                      type="button"
                      className="btn-accent shrink-0"
                      disabled={disabled}
                      onClick={() => {
                        const { compilation_id: _id, ...rest } = target
                        onChange({
                          ...value,
                          compile: true,
                          compilation: { ...rest, compilation_id: null, new_title: creating ?? '' }
                        })
                        setCreating(null)
                      }}
                    >
                      {t('Create')}
                    </button>
                  )}
                </span>
              </label>
            )}
          </div>
          {deleted && (
            <p className="text-sm text-warn">
              {t('That compilation was deleted. Choose another, or make a new one.')}
            </p>
          )}

          <div className="space-y-2">
            <span className="label block">{t('What goes in')}</span>
            <div className="inline-flex rounded-lg bg-raised/60 p-1 gap-1" role="radiogroup">
              <Segment
                on={target.what === 'whole'}
                disabled={disabled}
                onClick={() => setTarget({ what: 'whole' })}
              >
                {t('The whole video')}
              </Segment>
              <Segment
                on={target.what === 'clips'}
                disabled={disabled || !value.clips}
                title={value.clips ? undefined : t('Switch on Generate clips to add the best clips.')}
                onClick={() => setTarget({ what: 'clips' })}
              >
                {t('Its best clips')}
              </Segment>
            </div>
            {target.what === 'clips' ? (
              <label className="flex items-center gap-2 text-sm flex-wrap">
                <span className="text-muted">{t('Add the best')}</span>
                <select
                  className="input !w-24 !py-1"
                  value={target.max_clips}
                  disabled={disabled}
                  onChange={(e) => setTarget({ max_clips: Number(e.target.value) })}
                >
                  {CLIP_COUNTS.map((n) => (
                    <option key={n} value={n}>
                      {n === 0 ? t('all') : n}
                    </option>
                  ))}
                </select>
                <span className="text-muted">
                  {t('clips of each video, in the order they happen.')}
                </span>
              </label>
            ) : (
              <p className="text-xs text-muted">
                {value.clips
                  ? t('Each new video goes in start to end. Pick “Its best clips” to add only the highlights.')
                  : t('Each new video goes in start to end, as it is. Good for short videos and Shorts, which are kept.')}
              </p>
            )}
          </div>
        </Reveal>
      )}

      {!value.clips && !compileOn && (
        <p className="text-sm text-muted rounded-lg bg-raised/40 px-3 py-2">
          {t('Neither is on: each new video is downloaded into your Library, ready for whenever you want it.')}
        </p>
      )}
    </div>
  )
}

function Tile({
  on,
  disabled,
  icon,
  title,
  text,
  onToggle
}: {
  on: boolean
  disabled: boolean
  icon: ReactNode
  title: string
  text: string
  onToggle: (on: boolean) => void
}): JSX.Element {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onToggle(!on)}
      className={`group relative flex gap-3 rounded-xl border p-3 text-left transition-colors disabled:opacity-50 ${
        on ? 'border-accent/70 bg-accent/10' : 'border-raised hover:border-accent/40 hover:bg-raised/30'
      }`}
    >
      <span
        className={`mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg ${
          on ? 'bg-accent/20 text-accent' : 'bg-raised text-muted group-hover:text-ink'
        }`}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-sm">{title}</span>
        <span className="block text-xs text-muted mt-0.5">{text}</span>
      </span>
      <span
        className={`mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border text-[11px] font-bold ${
          on ? 'border-accent bg-accent text-base' : 'border-muted/50 text-transparent'
        }`}
        aria-hidden
      >
        ✓
      </span>
    </button>
  )
}

function Segment({
  on,
  disabled,
  title,
  onClick,
  children
}: {
  on: boolean
  disabled: boolean
  title?: string
  onClick: () => void
  children: ReactNode
}): JSX.Element {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-sm transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
        on ? 'bg-accent text-base font-semibold' : 'text-muted hover:text-ink'
      }`}
    >
      {children}
    </button>
  )
}

/** A choice's own settings, shown under it while it is on. */
export function Reveal({ title, children }: { title: string; children: ReactNode }): JSX.Element {
  return (
    <div className="space-y-3 border-l-2 border-accent/50 pl-4 py-1">
      <p className="text-xs font-bold uppercase tracking-widest text-accent">{title}</p>
      {children}
    </div>
  )
}
