import { useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react'

/** A button for anything that takes time.
 *
 *  While its handler's promise is pending the button is locked, shows a spinner
 *  and (optionally) a busy label, so a second press cannot start the action
 *  again and the person can see it was received. `holdWhile` keeps the lock on
 *  after the promise settles, for work the server only starts when it answers
 *  (a cancel that lands at the next checkpoint). A handler that throws unlocks
 *  the button and rethrows into `onError`, if given; otherwise the error is
 *  left to the handler, which is expected to show it. */
export default function AsyncButton({
  onClick,
  busyLabel,
  holdWhile = false,
  onError,
  disabled,
  className = '',
  children,
  ...rest
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onClick'> & {
  onClick: () => unknown | Promise<unknown>
  /** Replaces the children while busy. */
  busyLabel?: ReactNode
  /** Stay locked (and show the busy state) while this is true, after the handler returns. */
  holdWhile?: boolean
  onError?: (e: unknown) => void
}): JSX.Element {
  const locked = useRef(false)
  const [running, setRunning] = useState(false)
  const busy = running || holdWhile

  const press = async (): Promise<void> => {
    if (locked.current || busy) return
    locked.current = true
    setRunning(true)
    try {
      await onClick()
    } catch (e) {
      onError?.(e)
    } finally {
      locked.current = false
      setRunning(false)
    }
  }

  return (
    <button
      type="button"
      {...rest}
      className={`${className} ${busy ? 'cursor-progress' : ''}`}
      disabled={disabled || busy}
      aria-busy={busy}
      onClick={() => void press()}
    >
      {busy && <span className="spinner mr-1.5 align-[-2px]" aria-hidden />}
      {busy && busyLabel !== undefined ? busyLabel : children}
    </button>
  )
}
