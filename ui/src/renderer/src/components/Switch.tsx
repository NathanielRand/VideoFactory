/** An on/off switch: a checkbox underneath, so it is a real form control to
 *  the keyboard and to screen readers, drawn as a sliding pill. */
export default function Switch({
  checked,
  onChange,
  disabled = false,
  label,
  size = 'md'
}: {
  checked: boolean
  onChange: (on: boolean) => void
  disabled?: boolean
  /** Read out by screen readers; not drawn. */
  label: string
  size?: 'sm' | 'md'
}): JSX.Element {
  const track = size === 'sm' ? 'h-5 w-9' : 'h-6 w-11'
  const knob = size === 'sm' ? 'size-4 peer-checked:translate-x-4' : 'size-5 peer-checked:translate-x-5'
  return (
    <label
      className={`relative inline-flex shrink-0 items-center ${disabled ? 'opacity-50' : 'cursor-pointer'}`}
    >
      <input
        type="checkbox"
        role="switch"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        aria-label={label}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span
        className={`${track} rounded-full bg-raised transition-colors peer-checked:bg-accent peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent`}
        aria-hidden
      />
      <span
        className={`${knob} absolute left-0.5 rounded-full bg-ink shadow transition-transform`}
        aria-hidden
      />
    </label>
  )
}
