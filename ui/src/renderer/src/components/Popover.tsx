import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

/** A menu or panel that opens from a button and is never cut off.
 *
 *  Menus used to be absolutely positioned inside their button's box, pinned
 *  to one side at a fixed width. Near the window's edge they ran off it, and
 *  inside a card, a scrolling list or a dialog (anything with overflow
 *  hidden or auto) they were clipped by it.
 *
 *  This renders into <body> with fixed positioning, so no container can clip
 *  it, and places itself against the viewport on every open, scroll and
 *  resize:
 *   * below the button, or above it (a drop-up) when there is more room there;
 *   * lined up with the button's start or end edge, then slid inward to stay
 *     inside the window;
 *   * no wider than the window, and no taller than the room it has, scrolling
 *     inside instead.
 *  It closes on a click outside it and its button, and on Escape. */

const MARGIN = 8 // px kept clear of the window edge
const GAP = 4 // px between the button and the menu

export interface Placement {
  top: number
  left: number
  width: number
  maxHeight: number
  above: boolean
}

/** Where a panel of the given size goes for an anchor rectangle: pure, so the
 *  rules are testable without a browser. */
export function place(
  anchor: { top: number; bottom: number; left: number; right: number },
  panel: { width: number; height: number },
  viewport: { width: number; height: number },
  align: 'start' | 'end' = 'start'
): Placement {
  const width = Math.min(panel.width, viewport.width - MARGIN * 2)
  const below = viewport.height - anchor.bottom - GAP - MARGIN
  const aboveRoom = anchor.top - GAP - MARGIN
  // Below unless it does not fit there and there is more room above.
  const above = panel.height > below && aboveRoom > below
  const room = Math.max(80, above ? aboveRoom : below)
  const height = Math.min(panel.height, room)
  const top = above ? anchor.top - GAP - height : anchor.bottom + GAP
  const wanted = align === 'end' ? anchor.right - width : anchor.left
  const left = Math.max(MARGIN, Math.min(wanted, viewport.width - MARGIN - width))
  return { top, left, width, maxHeight: room, above }
}

export default function Popover({
  anchor,
  open,
  onClose,
  width = 288,
  align = 'start',
  className = 'card !p-1.5 shadow-xl space-y-0.5',
  label,
  children
}: {
  /** The element the menu opens from (usually its button). */
  anchor: React.RefObject<HTMLElement>
  open: boolean
  onClose: () => void
  /** Preferred width in px; narrowed to fit the window. */
  width?: number
  /** Which edge of the button the menu lines up with first. */
  align?: 'start' | 'end'
  className?: string
  label?: string
  children: React.ReactNode
}): JSX.Element | null {
  const panel = useRef<HTMLDivElement>(null)
  const [spot, setSpot] = useState<Placement | null>(null)
  // Callers pass a fresh onClose on every render; held here so the listeners
  // below are not torn down and re-attached (and the observer re-fired) each time.
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  const update = useCallback((): void => {
    const a = anchor.current
    const p = panel.current
    if (!a || !p) return
    const r = a.getBoundingClientRect()
    // Measured at its natural height: max-height is lifted while measuring.
    const kept = p.style.maxHeight
    p.style.maxHeight = 'none'
    const natural = p.scrollHeight
    p.style.maxHeight = kept
    const next = place(r, { width, height: natural }, { width: window.innerWidth, height: window.innerHeight }, align)
    // Unchanged: keep the same object, so nothing re-renders (and the
    // resize observer is not woken by its own update).
    setSpot((prev) =>
      prev &&
      prev.top === next.top &&
      prev.left === next.left &&
      prev.width === next.width &&
      prev.maxHeight === next.maxHeight
        ? prev
        : next
    )
  }, [anchor, width, align])

  useLayoutEffect(() => {
    if (!open) {
      setSpot(null)
      return
    }
    update()
  }, [open, update])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent): void => {
      const target = e.target as Node
      if (panel.current?.contains(target) || anchor.current?.contains(target)) return
      closeRef.current()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeRef.current()
      }
    }
    // Any scroll moves the button, so follow it (capture: scrolls in any
    // container, not just the window).
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    // The content can grow after opening (a list arriving from the network).
    const observer = new ResizeObserver(update)
    if (panel.current) observer.observe(panel.current)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
      observer.disconnect()
    }
  }, [open, update, anchor])

  if (!open) return null
  return createPortal(
    <div
      ref={panel}
      role="menu"
      aria-label={label}
      className={`fixed z-[70] overflow-y-auto overscroll-contain ${className}`}
      style={
        spot
          ? { top: spot.top, left: spot.left, width: spot.width, maxHeight: spot.maxHeight }
          : // First paint, before measuring: placed nowhere visible.
            { top: -9999, left: -9999, width, visibility: 'hidden' }
      }
    >
      {children}
    </div>,
    document.body
  )
}
