import { useEffect, useRef } from 'react'
import type { StudioEvent } from './types'

const WS_URL = 'ws://127.0.0.1:8765/ws'

// One connection for the whole window. Every component that listens used to
// open its own socket, so with a dozen mounted, each progress event was sent,
// received and parsed a dozen times, and each one held one of the browser's
// scarce connections to the engine. Now the first listener opens the socket,
// every listener shares it, and the last one out closes it.
type Listener = (event: StudioEvent) => void

const listeners = new Set<Listener>()
let socket: WebSocket | null = null
let retry: ReturnType<typeof setTimeout> | null = null

function connect(): void {
  if (socket || listeners.size === 0) return
  const s = new WebSocket(WS_URL)
  socket = s
  s.onmessage = (msg) => {
    let event: StudioEvent
    try {
      event = JSON.parse(msg.data as string) as StudioEvent // parsed once for everyone
    } catch {
      return // malformed event: ignore
    }
    for (const fn of [...listeners]) {
      try {
        fn(event)
      } catch (e) {
        console.error('event listener failed', e) // one bad listener must not silence the rest
      }
    }
  }
  s.onclose = () => {
    if (socket === s) socket = null
    if (listeners.size > 0) retry = setTimeout(connect, 2000)
  }
}

function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  connect()
  return () => {
    listeners.delete(fn)
    if (listeners.size === 0) {
      if (retry) clearTimeout(retry)
      retry = null
      const s = socket
      socket = null
      s?.close()
    }
  }
}

/** Subscribe to live backend events; reconnects automatically. */
export function useEvents(onEvent: (event: StudioEvent) => void): void {
  const handler = useRef(onEvent)
  handler.current = onEvent

  useEffect(() => subscribe((e) => handler.current(e)), [])
}
