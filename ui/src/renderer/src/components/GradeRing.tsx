import { gradeColor, type ModelGrade } from '../lib/modelGrade'

/** A model's grade in a radial bar: the ring fills with the score and takes
 *  its colour from it, red through amber to green, and the letter sits in the
 *  middle. `size` is the diameter in pixels. */
export default function GradeRing({
  grade,
  size = 44,
  stroke
}: {
  grade: ModelGrade
  size?: number
  stroke?: number
}): JSX.Element {
  const width = stroke ?? Math.max(3, Math.round(size / 11))
  const r = (size - width) / 2
  const circumference = 2 * Math.PI * r
  // A sliver even at zero, so an F- still reads as a (nearly empty) ring.
  const filled = Math.max(0.03, grade.score / 100)
  const color = gradeColor(grade.score)
  const letterSize = grade.grade.length > 1 ? size * 0.34 : size * 0.4

  return (
    <div
      className="relative shrink-0 grid place-items-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Grade ${grade.grade}, ${grade.score} out of 100`}
      title={`${grade.grade} · ${grade.score}/100\n${grade.why}`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={width}
          className="text-raised"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={width}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - filled)}
          style={{
            transition: 'stroke-dashoffset 700ms ease-out, stroke 700ms ease-out',
            filter: `drop-shadow(0 0 ${Math.max(2, size / 14)}px ${color.replace(')', ' / 0.45)')})`
          }}
        />
      </svg>
      <span
        className="absolute font-extrabold leading-none tracking-tight"
        style={{ color, fontSize: letterSize, transition: 'color 700ms ease-out' }}
      >
        {grade.grade}
      </span>
    </div>
  )
}
