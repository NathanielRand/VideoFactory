import { memo, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Clip } from '../lib/types'
import type { ItemState } from '../lib/publishState'
import ClipWorkBadge from './ClipWorkBadge'
import type { ClipWork } from '../lib/clipWork'
import { ItemBadge } from './PublishBadge'
import ScoreBadge from './ScoreBadge'
import { Star, Trash } from './icons'

const PROFILE_BADGE: Record<string, string> = {
  short_clips: '▭ 16:9',
  clips_140: '▭ 16:9',
  highlights: '▭ Highlights',
  edited_stream: '▭ Edited stream'
}

function ClipCardImpl({
  clip,
  selected,
  onClick,
  onDelete,
  onToggleExported,
  onPublish,
  onFlag,
  checked,
  onCheck,
  selecting,
  publishState,
  work
}: {
  clip: Clip
  selected: boolean
  onClick: () => void
  /** Cull this clip straight from the grid, without opening it. */
  onDelete?: () => void | Promise<unknown>
  /** Star or unstar the clip as exported, without opening it. */
  onToggleExported?: () => void | Promise<unknown>
  /** Publish this one clip, without opening the editor first. */
  onPublish?: () => void
  /** Report this clip as wrong (bad start/end, bad framing). */
  onFlag?: () => void
  /** Ticked for a bulk action; separate from `selected`, the one open above. */
  checked?: boolean
  onCheck?: () => void
  /** Something is ticked, so every card shows its box rather than on hover. */
  selecting?: boolean
  /** Where this clip stands on the way out (posted, scheduled, ...). */
  publishState?: ItemState
  /** A render, translation or format job queued, running or just failed for this clip. */
  work?: ClipWork
}): JSX.Element {
  // The card's own buttons lock while their request is out, so a double click
  // cannot delete or star twice and the card shows that it heard the click.
  const [pending, setPending] = useState<'star' | 'delete' | null>(null)
  const locked = useRef(false)
  const guard = async (which: 'star' | 'delete', fn?: () => void | Promise<unknown>): Promise<void> => {
    if (locked.current || !fn) return
    locked.current = true
    setPending(which)
    try {
      await fn()
    } finally {
      locked.current = false
      setPending(null)
    }
  }
  const duration = Math.round(clip.end_s - clip.start_s)
  const name = clip.title || clip.hook || 'Untitled clip'
  const profile = clip.render_opts?.profile
  const badge = profile ? (PROFILE_BADGE[profile] ?? '▭ 16:9') : null
  const exported = !!clip.exported_at

  // Lazy-load the thumbnail. Chromium allows only ~6 connections per host, so
  // a grid of 100+ <video> elements pointed at the local server starves its
  // own connection pool — thumbnails stay blank AND the editor's own video
  // can't get a connection to play. Load a clip's video only once its card
  // nears the viewport, capping concurrent loads to what's on screen.
  const boxRef = useRef<HTMLDivElement>(null)
  const [show, setShow] = useState(false)
  // The card shows a small still, not a video. A live <video> per card held a
  // decoder and a decoded 1080x1920 frame each, and a grid of a hundred of them
  // is what made playback everywhere else in the app stutter. The clip only
  // plays while the pointer rests on it.
  const [playing, setPlaying] = useState(false)
  const [noPoster, setNoPoster] = useState(false)
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const startHover = (): void => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    hoverTimer.current = setTimeout(() => setPlaying(true), 400)
  }
  const endHover = (): void => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
    hoverTimer.current = null
    setPlaying(false)
  }
  useEffect(() => () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current)
  }, [])
  useEffect(() => {
    const el = boxRef.current
    if (!el || show) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShow(true)
          io.disconnect()
        }
      },
      { rootMargin: '300px' } // start loading just before it scrolls in
    )
    io.observe(el)
    return () => io.disconnect()
  }, [show])
  // Wrapper (not a button): the card is a button, and the trash must be a
  // SEPARATE button, not nested inside it — nested buttons are invalid and
  // the inner click would be swallowed.
  return (
    <div
      className="relative group"
      // Cards scrolled out of view are not laid out or painted at all.
      style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 340px' }}
    >
      <button
        onClick={onClick}
        onMouseEnter={startHover}
        onMouseLeave={endHover}
        onFocus={startHover}
        onBlur={endHover}
        aria-label={`${name}, ${duration} seconds, score ${clip.score}${
          badge ? ', horizontal longform' : ', vertical Short'
        }${exported ? ', exported' : ''}${selected ? ', selected' : ''}${
          work ? `, ${work.state === 'running' ? 'working' : work.state}: ${work.kind}` : ''
        }`}
        aria-pressed={selected}
        className={`w-full text-left rounded-xl overflow-hidden bg-surface border transition-colors ${
          selected ? 'border-accent' : checked ? 'border-accent/60' : 'border-raised/60 hover:border-raised'
        }`}
      >
        <div
          ref={boxRef}
          className={`aspect-[9/16] bg-base relative ${
            work && work.state !== 'failed' ? 'opacity-70' : ''
          }`}
        >
          {show && !noPoster ? (
            <img
              src={api.posterUrl(clip.id)}
              alt=""
              decoding="async"
              // Behind whatever the editor is streaming, on the same few connections.
              fetchPriority="low"
              draggable={false}
              onError={() => setNoPoster(true)}
              className={`w-full h-full ${badge ? 'object-contain' : 'object-cover'}`}
            />
          ) : (
            // Placeholder until the card scrolls into view — no network load.
            <div className="w-full h-full bg-base flex items-center justify-center text-muted/30 text-2xl">
              ▶
            </div>
          )}
          {playing && (
            <video
              src={api.mediaUrl(clip.id)}
              autoPlay
              muted
              loop
              playsInline
              preload="auto"
              className={`absolute inset-0 w-full h-full ${badge ? 'object-contain' : 'object-cover'}`}
            />
          )}
          <span className="absolute top-2 left-2">
            <ScoreBadge score={clip.score} />
          </span>
          {badge && (
            <span
              className={`absolute ${onDelete ? 'top-9' : 'top-2'} right-2 bg-amber-500/90 text-black px-1.5 py-0.5 rounded text-[10px] font-bold`}
            >
              {badge}
            </span>
          )}
          <span className="absolute bottom-2 right-2 bg-base/80 px-1.5 py-0.5 rounded text-xs tabular-nums">
            {duration}s
          </span>
        </div>
        <div className="p-1.5 space-y-1">
          <p className="text-xs font-medium line-clamp-2">
            {clip.title || clip.hook || 'Untitled clip'}
          </p>
          {/* Work in flight outranks where the clip stands on the way out: a
              clip being replaced is not "Ready", whatever its last state was. */}
          {work ? <ClipWorkBadge work={work} /> : <ItemBadge state={publishState} next />}
        </div>
      </button>
      {onCheck && (
        <label
          className={`absolute bottom-2 left-2 z-10 p-1 rounded-md bg-black/60 cursor-pointer transition-opacity ${
            checked || selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={!!checked}
            onChange={onCheck}
            aria-label={`Select ${name}`}
          />
        </label>
      )}
      {onToggleExported && (
        <button
          aria-label={exported ? `Unstar ${name} (not exported)` : `Star ${name} as exported`}
          aria-pressed={exported}
          aria-busy={pending === 'star'}
          disabled={pending !== null}
          title={
            exported
              ? 'Exported. Click to unstar.'
              : 'Star as exported. Export all skips starred clips.'
          }
          onClick={(e) => {
            e.stopPropagation()
            void guard('star', onToggleExported)
          }}
          // Starred, the star sits in the corner on its own. On hover the trash
          // takes the corner, so the star steps in beside it. Unstarred, the
          // star only appears then, in that same spot beside the trash.
          className={`absolute top-2 z-10 p-1.5 rounded-md bg-black/60 transition-all ${
            exported
              ? `text-amber-400 opacity-100 ${
                  onDelete ? 'right-2 group-hover:right-10 group-focus-within:right-10' : 'right-2'
                }`
              : `text-white/80 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-amber-400 ${
                  onDelete ? 'right-10' : 'right-2'
                }`
          }`}
        >
          {pending === 'star' ? <span className="spinner" aria-hidden /> : <Star className={exported ? 'fill-current' : ''} />}
        </button>
      )}
      {onDelete && (
        <button
          aria-label={`Delete ${name}`}
          title="Delete this clip and its file. The video and other clips stay."
          onClick={(e) => {
            e.stopPropagation()
            if (window.confirm(`Delete this clip and its file?\n\n"${name}"\n\nOnly this clip is removed — the video and your other clips stay. Can't be undone.`)) {
              void guard('delete', onDelete)
            }
          }}
          disabled={pending !== null}
          className="absolute top-2 right-2 z-10 p-1.5 rounded-md bg-black/60 text-white/80 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-red-500 hover:text-white transition"
        >
          {pending === 'delete' ? <span className="spinner" aria-hidden /> : <Trash />}
        </button>
      )}
      {onFlag && (
        <button
          aria-label={`Flag ${name} as wrong`}
          title="Flag this clip: wrong moment or wrong framing"
          onClick={(e) => {
            e.stopPropagation()
            onFlag()
          }}
          className="absolute top-9 left-2 z-10 px-1.5 py-1 rounded-md bg-black/60 text-white/80 text-[10px] font-semibold opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-amber-500 hover:text-black transition"
        >
          ⚑ Flag
        </button>
      )}
      {/* Publishing one clip meant opening the editor and finding the Publish
          tab, three steps in. Bottom left: the score badge owns the top left,
          the star and trash the top right, and the duration the bottom right,
          so this is the one free corner. */}
      {onPublish && (
        <button
          aria-label={`Publish ${name}`}
          title="Publish just this clip"
          onClick={(e) => {
            e.stopPropagation()
            onPublish()
          }}
          className="absolute bottom-14 left-2 z-10 px-2 py-1 rounded-md bg-black/70 text-white/90 text-[10px] font-semibold opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-accent hover:text-black transition"
        >
          Publish ↗
        </button>
      )}
    </div>
  )
}

/** A card only needs to redraw when what it SHOWS changes. The handlers are new
 *  closures on every render of the grid, but each one only acts on this card's
 *  own clip, so they are left out of the comparison: without that, one clip
 *  finishing a render redrew every card in the grid. */
const ClipCard = memo(
  ClipCardImpl,
  (a, b) =>
    a.clip === b.clip &&
    a.selected === b.selected &&
    a.checked === b.checked &&
    a.selecting === b.selecting &&
    a.publishState === b.publishState &&
    a.work === b.work
)
export default ClipCard
