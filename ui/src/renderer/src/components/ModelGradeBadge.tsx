import GradeRing from './GradeRing'
import { gradeColor, gradeModel } from '../lib/modelGrade'
import { speedNote, type ModelLike } from '../lib/modelSpeed'

/** A model's grade for this job, in its ring, with what it costs in time
 *  beneath. `compact` is the sidebar's; the full one adds the reason. */
export default function ModelGradeBadge({
  model,
  installed,
  vram,
  compact = false
}: {
  model: ModelLike
  installed: ModelLike[]
  vram?: number | null
  compact?: boolean
}): JSX.Element {
  const grade = gradeModel(model)
  const speed = speedNote(model, installed, vram)
  const color = gradeColor(grade.score)
  const speedTone =
    speed?.tone === 'warn' ? 'text-red-400' : speed?.tone === 'slow' ? 'text-amber-400' : 'text-muted'

  return (
    <div className={`flex items-center ${compact ? 'gap-2.5' : 'gap-3'}`}>
      <GradeRing grade={grade} size={compact ? 40 : 54} />
      <div className="min-w-0 space-y-0.5">
        <p className={`leading-snug ${compact ? 'text-[11px]' : 'text-xs'}`}>
          <span className="text-muted">Clip picking </span>
          <span className="font-bold" style={{ color }}>
            {grade.grade}
          </span>
          {!compact && <span className="text-muted"> · {grade.score}/100</span>}
        </p>
        {!compact && <p className="text-xs text-ink/80 leading-snug">{grade.why}</p>}
        {speed && (
          <p className={`leading-snug ${compact ? 'text-[11px]' : 'text-xs'} ${speedTone}`}>
            {speed.tone === 'warn' && <span aria-hidden="true">⚠ </span>}
            {speed.text}
          </p>
        )}
      </div>
    </div>
  )
}
