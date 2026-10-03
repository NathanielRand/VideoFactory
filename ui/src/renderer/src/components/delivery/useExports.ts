import { useCallback, useEffect, useRef, useState } from 'react'
import { exportsApi, type Destination, type Transfer } from '../../lib/exports'
import type { StudioEvent } from '../../lib/types'
import { useEvents } from '../../lib/useEvents'

/** Destinations and transfers, kept current: re-read on every 'exports'
 *  event, with a copy's byte count applied straight from its event so the
 *  bar moves without a round trip, and a slow poll in case a socket drops. */
export function useExports(): {
  destinations: Destination[] | null
  transfers: Transfer[]
  refresh: () => Promise<void>
  error: string
} {
  const [destinations, setDestinations] = useState<Destination[] | null>(null)
  const [transfers, setTransfers] = useState<Transfer[]>([])
  const [error, setError] = useState('')
  const inFlight = useRef(false)

  const refresh = useCallback(async (): Promise<void> => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const [d, t] = await Promise.all([exportsApi.destinations(), exportsApi.transfers(100)])
      setDestinations(d)
      setTransfers(t)
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      inFlight.current = false
    }
  }, [])

  useEffect(() => {
    void refresh()
    const id = setInterval(() => void refresh(), 15000)
    return () => clearInterval(id)
  }, [refresh])

  useEvents((e: StudioEvent) => {
    if (e.type !== 'exports') return
    if (e.transfer != null && e.sent != null) {
      setTransfers((list) =>
        list.map((t) =>
          t.id === e.transfer
            ? { ...t, sent: e.sent ?? t.sent, percent: t.bytes ? Math.round((100 * (e.sent ?? 0)) / t.bytes) : t.percent }
            : t
        )
      )
      return
    }
    void refresh()
  })

  return { destinations, transfers, refresh, error }
}
