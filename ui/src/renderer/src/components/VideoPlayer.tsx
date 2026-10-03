import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'

/** The app's video player, v3. One component for every screen that plays a file.
 *
 *  Carried over from ClipPlayer (v2): it is memoized on its few props, and
 *  everything that changes while a video plays (position, buffer, readout) is
 *  written straight to DOM nodes from one animation-frame loop, never through
 *  React state. Dropped frames and stalls are counted and logged
 *  (`[VideoPlayer]`); press `i` for a live readout.
 *
 *  New in v3:
 *  - One active player. Starting one pauses every other, so two decoders and
 *    two held connections never run at once.
 *  - The decoder is released on unmount and on a source change (`src` removed,
 *    `load()` called). A closed editor no longer keeps a 1080x1920 decoder and
 *    its connection alive.
 *  - Scrubbing never queues seeks. A seek is issued only when the last one has
 *    landed, so the picture follows the pointer instead of lagging behind it.
 *  - A failed or stalled load is retried once from the same position before the
 *    error is shown (a dropped local connection is not a broken file).
 *  - The raw `<video>` is exposed through the ref, for callers that drive it
 *    (the editor's live overlays and filters, in/out points).
 *  - Nothing but the active player preloads media; the rest fetch metadata only.
 */

const RATES = [0.5, 1, 1.25, 1.5, 2]
const FRAME = 1 / 30
const STALL_RETRY_MS = 8000

const fmt = (t: number): string => {
  if (!Number.isFinite(t) || t < 0) t = 0
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`
}

const Icon = ({ d, size = 16 }: { d: string; size?: number }): JSX.Element => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d={d} />
  </svg>
)
const PLAY = 'M8 5v14l11-7z'
const PAUSE = 'M6 5h4v14H6zM14 5h4v14h-4z'
const VOL = 'M3 9v6h4l5 5V4L7 9H3zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z'
const MUTE =
  'M16.5 12a4.5 4.5 0 0 0-2.5-4v2.2l2.5 2.5v-.7zM19 12a7 7 0 0 1-.9 3.4l1.5 1.5A9 9 0 0 0 21 12a9 9 0 0 0-6-8.5v2.1A7 7 0 0 1 19 12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a8 8 0 0 1-2.3 1.3v2.1a10 10 0 0 0 3.7-2l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z'
const FULL = 'M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z'

// Every mounted player, so starting one can pause the rest.
const mounted = new Set<HTMLVideoElement>()

export interface VideoPlayerProps {
  src: string
  poster?: string
  label: string
  className?: string
  /** Fill the parent box (which must be positioned) instead of sizing to the video. */
  fill?: boolean
  autoPlay?: boolean
  /** Drawn over the picture (overlays, badges), under the controls. */
  children?: React.ReactNode
}

const VideoPlayer = forwardRef<HTMLVideoElement, VideoPlayerProps>(function VideoPlayer(
  { src, poster, label, className = '', fill = false, autoPlay = false, children },
  ref
): JSX.Element {
  const box = useRef<HTMLDivElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const bar = useRef<HTMLDivElement>(null)
  const fillRef = useRef<HTMLDivElement>(null)
  const bufRef = useRef<HTMLDivElement>(null)
  const knobRef = useRef<HTMLDivElement>(null)
  const timeRef = useRef<HTMLSpanElement>(null)
  const statsRef = useRef<HTMLPreElement>(null)
  const scrub = useRef<{ active: boolean; wasPlaying: boolean; want: number | null }>({
    active: false,
    wasPlaying: false,
    want: null
  })
  const diag = useRef({ stalls: 0, seeks: 0, dropped: 0, total: 0, windowDropped: 0, retries: 0 })
  const showStats = useRef(false)
  const retried = useRef(false)

  useImperativeHandle(ref, () => video.current as HTMLVideoElement)

  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [rate, setRate] = useState(1)
  const [waiting, setWaiting] = useState(false)
  const [failed, setFailed] = useState(false)
  const [ui, setUi] = useState(true) // controls visible
  const [engaged, setEngaged] = useState(autoPlay) // has been played or asked to: only then preload media

  // ---- registry + decoder release ---------------------------------------------
  useEffect(() => {
    const el = video.current
    if (!el) return
    mounted.add(el)
    if (!el.getAttribute('src')) el.src = src // a dev-mode remount runs the cleanup below first
    return () => {
      mounted.delete(el)
      el.pause()
      el.removeAttribute('src')
      el.load() // drops the decoder and the connection
    }
  }, [src])

  // ---- the one loop that follows the video -----------------------------------
  useEffect(() => {
    const el = video.current
    if (!el) return
    let raf = 0
    let lastT = NaN
    let lastBuf = -1
    let lastStat = 0
    let lastQ = { dropped: 0, total: 0 }
    const tick = (): void => {
      raf = requestAnimationFrame(tick)
      const dur = el.duration
      const t = scrub.current.want ?? el.currentTime
      if (t !== lastT && Number.isFinite(dur) && dur > 0) {
        lastT = t
        const f = Math.min(1, t / dur)
        if (fillRef.current) fillRef.current.style.transform = `scaleX(${f})`
        if (knobRef.current) knobRef.current.style.left = `${(f * 100).toFixed(2)}%`
        if (timeRef.current) timeRef.current.textContent = `${fmt(t)} / ${fmt(dur)}`
      }
      // The buffered range that contains the playhead, not just the last one.
      let end = 0
      for (let i = 0; i < el.buffered.length; i++) {
        if (el.buffered.start(i) <= el.currentTime + 0.25 && el.buffered.end(i) >= end) end = el.buffered.end(i)
      }
      if (end !== lastBuf && Number.isFinite(dur) && dur > 0) {
        lastBuf = end
        if (bufRef.current) bufRef.current.style.transform = `scaleX(${Math.min(1, end / dur)})`
      }
      // Scrubbing: one seek in flight, the newest wanted time goes next.
      const w = scrub.current.want
      if (w !== null && !el.seeking && Math.abs(el.currentTime - w) > 0.01 && scrub.current.active) {
        el.currentTime = w
      }
      const now = performance.now()
      if (now - lastStat > 1000) {
        lastStat = now
        const q = el.getVideoPlaybackQuality?.()
        if (q && !el.paused) {
          const dd = q.droppedVideoFrames - lastQ.dropped
          const dt = q.totalVideoFrames - lastQ.total
          if (dt > 0 && dd / dt > 0.05) {
            console.warn(
              `[VideoPlayer] ${dd}/${dt} frames dropped in the last second at ${el.currentTime.toFixed(1)}s ` +
                `(${el.videoWidth}x${el.videoHeight}, rate ${el.playbackRate})`
            )
          }
          diag.current.windowDropped = dd
        }
        if (q) {
          lastQ = { dropped: q.droppedVideoFrames, total: q.totalVideoFrames }
          diag.current.dropped = q.droppedVideoFrames
          diag.current.total = q.totalVideoFrames
        }
        if (showStats.current && statsRef.current) {
          const d = diag.current
          statsRef.current.textContent =
            `${el.videoWidth}x${el.videoHeight}  rate ${el.playbackRate}\n` +
            `dropped ${d.dropped}/${d.total}  (last s: ${d.windowDropped})\n` +
            `stalls ${d.stalls}  seeks ${d.seeks}  retries ${d.retries}\n` +
            `buffered to ${lastBuf.toFixed(1)}s  ready ${el.readyState}`
        }
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [src])

  // A new source is a new element's worth of state.
  useEffect(() => {
    setPlaying(false)
    setWaiting(false)
    setFailed(false)
    setEngaged(autoPlay)
    retried.current = false
    diag.current = { stalls: 0, seeks: 0, dropped: 0, total: 0, windowDropped: 0, retries: 0 }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  // ---- recover from a stalled or dropped load, once, from the same place -------------
  const retry = useCallback((): boolean => {
    const el = video.current
    if (!el || retried.current) return false
    retried.current = true
    diag.current.retries++
    const at = el.currentTime
    const was = !el.paused
    console.warn(`[VideoPlayer] reloading ${src} at ${at.toFixed(2)}s`)
    el.load()
    el.addEventListener(
      'loadedmetadata',
      () => {
        el.currentTime = at
        if (was) void el.play().catch(() => undefined)
      },
      { once: true }
    )
    return true
  }, [src])

  const stallTimer = useRef(0)
  useEffect(() => {
    window.clearTimeout(stallTimer.current)
    if (waiting && !failed) stallTimer.current = window.setTimeout(() => void retry(), STALL_RETRY_MS)
    return () => window.clearTimeout(stallTimer.current)
  }, [waiting, failed, retry])

  // ---- transport ------------------------------------------------------------------
  const toggle = useCallback((): void => {
    const el = video.current
    if (!el) return
    setEngaged(true)
    if (el.paused || el.ended) void el.play().catch(() => setPlaying(false))
    else el.pause()
  }, [])

  const seekBy = useCallback((s: number): void => {
    const el = video.current
    if (!el || !Number.isFinite(el.duration)) return
    el.currentTime = Math.max(0, Math.min(el.duration, el.currentTime + s))
  }, [])

  // ---- scrubbing: pointer capture, one seek in flight, resume where it was ---------
  const timeAt = (clientX: number): number => {
    const el = video.current
    const rect = bar.current?.getBoundingClientRect()
    if (!el || !rect || !Number.isFinite(el.duration)) return 0
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * el.duration
  }
  const onScrubDown = (e: React.PointerEvent): void => {
    const el = video.current
    if (!el) return
    e.currentTarget.setPointerCapture(e.pointerId)
    scrub.current = { active: true, wasPlaying: !el.paused, want: timeAt(e.clientX) }
    if (!el.paused) el.pause()
  }
  const onScrubMove = (e: React.PointerEvent): void => {
    if (scrub.current.active) scrub.current.want = timeAt(e.clientX)
  }
  const onScrubUp = (e: React.PointerEvent): void => {
    const el = video.current
    if (!scrub.current.active || !el) return
    const t = timeAt(e.clientX)
    const resume = scrub.current.wasPlaying
    scrub.current = { active: false, wasPlaying: false, want: null }
    el.currentTime = t
    if (resume) void el.play().catch(() => undefined)
  }

  // ---- keyboard (when the player has focus) ---------------------------------------------
  const toggleFullscreen = async (): Promise<void> => {
    const b = box.current
    if (!b) return
    if (document.fullscreenElement) await document.exitFullscreen()
    else await b.requestFullscreen().catch(() => undefined)
  }
  const onKey = (e: React.KeyboardEvent): void => {
    const el = video.current
    if (!el) return
    switch (e.key) {
      case ' ':
      case 'k':
        e.preventDefault()
        toggle()
        break
      case 'ArrowLeft':
        e.preventDefault()
        seekBy(e.shiftKey ? -10 : -2)
        break
      case 'ArrowRight':
        e.preventDefault()
        seekBy(e.shiftKey ? 10 : 2)
        break
      case ',':
        el.pause()
        seekBy(-FRAME)
        break
      case '.':
        el.pause()
        seekBy(FRAME)
        break
      case 'm':
        el.muted = !el.muted
        break
      case 'f':
        void toggleFullscreen()
        break
      case 'i':
        showStats.current = !showStats.current
        if (statsRef.current) statsRef.current.style.display = showStats.current ? 'block' : 'none'
        break
      default:
        return
    }
  }

  const cycleRate = (): void => {
    const el = video.current
    if (!el) return
    el.playbackRate = RATES[(RATES.indexOf(el.playbackRate) + 1) % RATES.length] ?? 1
  }

  // Controls fade while playing and the pointer is still.
  const hideTimer = useRef(0)
  const poke = (): void => {
    setUi(true)
    window.clearTimeout(hideTimer.current)
    hideTimer.current = window.setTimeout(() => setUi(false), 2500)
  }
  useEffect(() => () => window.clearTimeout(hideTimer.current), [])
  const showControls = ui || !playing || waiting

  return (
    <div
      ref={box}
      tabIndex={0}
      role="group"
      aria-label={label}
      onKeyDown={onKey}
      onPointerMove={poke}
      onPointerLeave={() => playing && setUi(false)}
      className={`relative group bg-black rounded-lg overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        fill ? 'absolute inset-0 w-full h-full' : ''
      } ${className}`}
    >
      <video
        ref={video}
        key={src}
        src={src}
        poster={poster}
        preload={engaged ? 'auto' : 'metadata'}
        autoPlay={autoPlay}
        playsInline
        aria-label={label}
        className={fill ? 'absolute inset-0 w-full h-full object-contain bg-black' : 'block w-full max-h-[30rem] object-contain bg-black'}
        onClick={toggle}
        onDoubleClick={() => void toggleFullscreen()}
        onPlay={(e) => {
          setEngaged(true)
          setPlaying(true)
          for (const other of mounted) if (other !== e.currentTarget && !other.paused) other.pause()
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onWaiting={() => {
          diag.current.stalls++
          setWaiting(true)
          console.warn(`[VideoPlayer] waiting at ${video.current?.currentTime.toFixed(2)}s`)
        }}
        onPlaying={() => setWaiting(false)}
        onCanPlay={() => setWaiting(false)}
        onSeeking={() => {
          diag.current.seeks++
        }}
        onVolumeChange={(e) => {
          setMuted(e.currentTarget.muted)
          setVolume(e.currentTarget.volume)
        }}
        onRateChange={(e) => setRate(e.currentTarget.playbackRate)}
        onError={() => {
          if (!retry()) setFailed(true)
        }}
      />

      {children}

      {waiting && !failed && (
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="size-9 rounded-full border-2 border-white/25 border-t-white animate-spin" />
        </div>
      )}
      {failed && (
        <div className="absolute inset-0 z-30 grid place-items-center bg-black/70 text-sm text-white/80 px-4 text-center">
          This video could not be played. Re-render it, or check that the file still exists.
        </div>
      )}
      {!playing && !waiting && !failed && (
        <button
          type="button"
          onClick={toggle}
          aria-label="Play"
          className="absolute inset-0 z-10 m-auto size-14 rounded-full bg-black/55 text-white grid place-items-center hover:bg-black/75"
        >
          <Icon d={PLAY} size={26} />
        </button>
      )}

      <pre
        ref={statsRef}
        style={{ display: 'none' }}
        className="absolute top-2 left-2 z-30 text-[10px] leading-tight bg-black/75 text-white/90 p-1.5 rounded pointer-events-none"
      />

      <div
        className={`absolute inset-x-0 bottom-0 z-20 px-2.5 pb-2 pt-8 bg-gradient-to-t from-black/80 to-transparent transition-opacity duration-200 ${
          showControls ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
      >
        <div
          ref={bar}
          role="slider"
          aria-label="Seek"
          aria-valuemin={0}
          tabIndex={-1}
          onPointerDown={onScrubDown}
          onPointerMove={onScrubMove}
          onPointerUp={onScrubUp}
          onPointerCancel={onScrubUp}
          className="relative h-4 cursor-pointer touch-none flex items-center"
        >
          <div className="relative h-1 group-hover:h-1.5 transition-all w-full rounded-full bg-white/20 overflow-hidden">
            <div ref={bufRef} className="absolute inset-0 origin-left bg-white/30" style={{ transform: 'scaleX(0)' }} />
            <div ref={fillRef} className="absolute inset-0 origin-left bg-accent" style={{ transform: 'scaleX(0)' }} />
          </div>
          <div
            ref={knobRef}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 size-3 rounded-full bg-white shadow"
            style={{ left: '0%' }}
          />
        </div>
        <div className="flex items-center gap-2 text-white text-xs mt-0.5">
          <button type="button" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'} className="p-1 hover:text-accent">
            <Icon d={playing ? PAUSE : PLAY} />
          </button>
          <button
            type="button"
            onClick={() => {
              const el = video.current
              if (el) el.muted = !el.muted
            }}
            aria-label={muted ? 'Unmute' : 'Mute'}
            className="p-1 hover:text-accent"
          >
            <Icon d={muted || volume === 0 ? MUTE : VOL} />
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.02}
            value={muted ? 0 : volume}
            aria-label="Volume"
            onChange={(e) => {
              const el = video.current
              if (!el) return
              el.volume = Number(e.target.value)
              el.muted = el.volume === 0
            }}
            className="w-16 accent-[var(--accent,#fff)]"
          />
          <span ref={timeRef} className="tabular-nums text-white/80 ml-1">
            0:00 / 0:00
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={cycleRate}
            aria-label="Playback speed"
            className="px-1.5 py-0.5 rounded hover:bg-white/15 tabular-nums"
          >
            {rate}×
          </button>
          <button type="button" onClick={() => void toggleFullscreen()} aria-label="Fullscreen" className="p-1 hover:text-accent">
            <Icon d={FULL} />
          </button>
        </div>
      </div>
    </div>
  )
})

export default memo(VideoPlayer)
