import { useRef, useState } from 'react'
import { t } from '../lib/i18n'

export type Tone = 'default' | 'accent' | 'danger'

const TONE: Record<Tone, string> = {
  default: 'text-muted hover:text-ink hover:bg-raised',
  accent: 'text-accent hover:bg-accent/15',
  danger: 'text-error/80 hover:text-error hover:bg-error/10'
}

/** What an action costs, in YouTube's quota points. Shown in the tooltip, not
 *  on the button, so a row of actions stays a row of icons. */
export interface ActionCost {
  units?: number
  uploads?: number
  /** Extra line, e.g. "Cannot be undone". */
  note?: string
}

function costLine(c: ActionCost | undefined): string {
  if (!c) return ''
  const parts = [
    c.uploads ? `${c.uploads} ${c.uploads === 1 ? t('upload') : t('uploads')}` : '',
    c.units ? `≈${c.units} ${t('units')}` : ''
  ].filter(Boolean)
  return parts.length ? parts.join(' + ') : t('No quota cost')
}

/** A square icon button whose tooltip names the action and what it spends.
 *  The tooltip is fixed-positioned from the button's own rectangle, so a table
 *  wrapper that scrolls sideways (which clips anything absolutely positioned
 *  above or below it) cannot cut it off. Shows on hover and on keyboard focus. */
export default function ActionButton({
  icon,
  label,
  cost,
  tone = 'default',
  disabled,
  disabledReason,
  busy,
  onClick
}: {
  icon: JSX.Element
  label: string
  cost?: ActionCost
  tone?: Tone
  disabled?: boolean
  /** Replaces the cost line while the action is unavailable. */
  disabledReason?: string
  busy?: boolean
  onClick: () => void | Promise<unknown>
}): JSX.Element {
  const ref = useRef<HTMLButtonElement>(null)
  const [tip, setTip] = useState<{ x: number; y: number; below: boolean } | null>(null)
  // A handler that returns a promise keeps the button locked until it settles.
  const [pending, setPending] = useState(false)
  const locked = useRef(false)

  const show = (): void => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    // Above the button, unless that would leave the window.
    const below = r.top < 90
    setTip({ x: r.left + r.width / 2, y: below ? r.bottom + 8 : r.top - 8, below })
  }
  const hide = (): void => setTip(null)

  return (
    <>
      <button
        ref={ref}
        type="button"
        aria-label={label}
        aria-disabled={disabled || busy || pending}
        aria-busy={busy || pending}
        disabled={disabled || busy || pending}
        onClick={() => {
          hide()
          if (locked.current) return
          const out = onClick()
          if (out && typeof (out as Promise<unknown>).then === 'function') {
            locked.current = true
            setPending(true)
            const done = (): void => {
              locked.current = false
              setPending(false)
            }
            void (out as Promise<unknown>).then(done, done)
          }
        }}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        className={`size-8 grid place-items-center rounded-lg transition-colors disabled:opacity-35 disabled:hover:bg-transparent disabled:cursor-not-allowed ${TONE[tone]} ${busy || pending ? 'animate-pulse' : ''}`}
      >
        {icon}
      </button>
      {tip && (
        <div
          role="tooltip"
          className="fixed z-[70] pointer-events-none -translate-x-1/2 rounded-lg bg-base border border-raised shadow-lg px-2.5 py-1.5 text-xs whitespace-nowrap"
          style={{ left: tip.x, top: tip.y, transform: `translate(-50%, ${tip.below ? '0' : '-100%'})` }}
        >
          <p className="font-medium">{label}</p>
          <p className={disabled && disabledReason ? 'text-warn' : 'text-muted'}>
            {disabled && disabledReason ? disabledReason : costLine(cost)}
          </p>
          {cost?.note && !disabled && <p className="text-error/90">{cost.note}</p>}
        </div>
      )}
    </>
  )
}
