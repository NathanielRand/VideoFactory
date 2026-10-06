import { useEffect, useRef } from 'react'
import { autoThumbnail, thumbsApi } from './thumbnails'

// This session's attempts: a clip whose thumbnail could not be made (no
// frames, the model down) is not retried in a loop every time the list reloads.
const tried = new Set<number>()
let running = false

/** Makes the first thumbnail of each new clip on its own, one at a time.
 *
 *  "New" is decided by the engine (GET /thumbnails/pending): a clip with no
 *  thumbnail that has never had one made. A clip that was re-rendered, or whose
 *  thumbnail was deleted on purpose, is never in that list, so this cannot
 *  replace a design somebody chose. Regenerating is always something a person
 *  asks for (the thumbnail card, or "Generate missing").
 *
 *  The drawing happens on the page's main thread, so it waits for the page to
 *  settle, does one clip at a time, and leaves a gap between them. */
export function useAutoThumbnails(clipIds: number[], onMade?: () => void): void {
  // The latest ids and callback, read by a loop that outlives any one render.
  const ids = useRef<number[]>(clipIds)
  const made = useRef(onMade)
  ids.current = clipIds
  made.current = onMade

  useEffect(() => {
    let alive = true
    // Polled, not keyed on the list: a clip is in the list before its file is
    // rendered, and a list that changed mid-run used to drop the run. Each pass
    // asks the engine which listed clips are new and ready, and makes them.
    const pass = async (): Promise<void> => {
      if (running) return
      running = true
      try {
        const fresh = [...new Set(ids.current)].filter((id) => id > 0 && !tried.has(id))
        if (!fresh.length) return
        const pending = await thumbsApi.pending(fresh).catch(() => [] as number[])
        for (const id of pending) {
          if (!alive) return
          tried.add(id)
          try {
            await autoThumbnail(id)
            made.current?.()
          } catch {
            /* it stays without one; the publish flow offers to make it */
          }
          await new Promise((r) => setTimeout(r, 500))
        }
      } finally {
        running = false
      }
    }
    const first = setTimeout(pass, 1500)
    const timer = setInterval(pass, 5000)
    return () => {
      alive = false
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [])
}
