import { t } from '../lib/i18n'
import type { ClipWork } from '../lib/clipWork'

// What the clip is going through. Colour carries the urgency (waiting amber,
// working blue and pulsing, failed red), and the icon and words carry it too.
const KIND: Record<ClipWork['kind'], { queued: string; running: string; failed: string }> = {
  render: { queued: 'Queued to re-render', running: 'Re-rendering', failed: 'Re-render failed' },
  formats: { queued: 'Queued: other formats', running: 'Making other formats', failed: 'Formats failed' },
  translate: { queued: 'Queued to translate', running: 'Translating', failed: 'Translation failed' }
}
const TONE: Record<ClipWork['state'], { icon: string; tone: string }> = {
  queued: { icon: '◷', tone: 'bg-amber-500/15 text-amber-300' },
  running: { icon: '↻', tone: 'bg-accent/20 text-accent animate-pulse' },
  failed: { icon: '!', tone: 'bg-red-500/15 text-red-300' }
}

const pill =
  'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold leading-none whitespace-nowrap'

export default function ClipWorkBadge({ work }: { work: ClipWork }): JSX.Element {
  const label = t(KIND[work.kind][work.state])
  const v = TONE[work.state]
  return (
    <span
      className={`${pill} ${v.tone}`}
      role="status"
      title={work.state === 'failed' && work.error ? `${label}: ${work.error}` : label}
    >
      <span aria-hidden>{v.icon}</span>
      {label}
    </span>
  )
}
