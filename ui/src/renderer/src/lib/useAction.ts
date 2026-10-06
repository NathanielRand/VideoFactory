import { useCallback, useRef, useState } from 'react'

/** Runs one async action at a time and says so while it runs.
 *
 *  The lock is a ref, not state: two clicks in the same frame both see the
 *  pre-render `busy === false`, so a state-only guard lets a double click run
 *  the action twice. The ref closes that gap. A second call while the first is
 *  running is dropped and resolves to undefined.
 *
 *  `run` never throws: a failure lands in `error` (the message, ready to show)
 *  and the lock is released either way. */
export function useAction(): {
  busy: boolean
  error: string | null
  run: <T>(fn: () => Promise<T>) => Promise<T | undefined>
  clearError: () => void
} {
  const locked = useRef(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (locked.current) return undefined
    locked.current = true
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return undefined
    } finally {
      locked.current = false
      setBusy(false)
    }
  }, [])

  return { busy, error, run, clearError: () => setError(null) }
}

/** The same lock, per key, for a list where each row has its own button: busy
 *  rows lock themselves and nothing else. */
export function useActions(): {
  isBusy: (key: string | number) => boolean
  busyCount: number
  error: string | null
  run: <T>(key: string | number, fn: () => Promise<T>) => Promise<T | undefined>
  clearError: () => void
} {
  const locked = useRef(new Set<string>())
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async <T,>(key: string | number, fn: () => Promise<T>): Promise<T | undefined> => {
    const k = String(key)
    if (locked.current.has(k)) return undefined
    locked.current.add(k)
    setBusy(new Set(locked.current))
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return undefined
    } finally {
      locked.current.delete(k)
      setBusy(new Set(locked.current))
    }
  }, [])

  return {
    isBusy: (key) => busy.has(String(key)),
    busyCount: busy.size,
    error,
    run,
    clearError: () => setError(null)
  }
}
