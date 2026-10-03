import ThumbPlaceholder from './ThumbPlaceholder'
import { useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import { autoThumbnail, thumbsApi } from '../lib/thumbnails'
import ThumbnailStudio from './ThumbnailStudio'

/** The current thumbnail of a clip or compilation, and the way into the
 *  designer. Used wherever a video is edited or published, so a thumbnail
 *  is one click away from each.
 *
 *  `publishId` is a clip id, or the negative of a compilation's id. */
export default function ThumbnailCard({
  publishId,
  onSaved,
  onState,
  compact
}: {
  publishId: number
  onSaved?: () => void
  /** Hears whether a thumbnail exists, on load and after each save. */
  onState?: (hasImage: boolean) => void
  compact?: boolean
}): JSX.Element {
  const [open, setOpen] = useState(false)
  const [version, setVersion] = useState(0)
  const [state, setState] = useState<{ image: boolean; design: boolean } | null>(null)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')

  const generate = async (): Promise<void> => {
    if (state?.image && !window.confirm(t('Replace the current thumbnail with a new automatic one?')))
      return
    setGenerating(true)
    setError('')
    try {
      // A person asked for this, so it is a different look from the last.
      await autoThumbnail(publishId, state?.image ? { variant: Date.now() % 1_000_000 } : {})
      setVersion((v) => v + 1)
      onSaved?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setGenerating(false)
    }
  }

  useEffect(() => {
    let alive = true
    thumbsApi
      .source(publishId)
      .then((s) => {
        if (!alive) return
        setState({ image: s.has_image, design: s.has_design })
        onState?.(s.has_image)
      })
      .catch(() => alive && setState(null)) // not rendered yet: nothing to design on
    return () => {
      alive = false
    }
  }, [publishId, version])

  const label = state?.design
    ? t('Edit thumbnail')
    : state?.image
      ? t('Redesign thumbnail')
      : t('Design a thumbnail')

  return (
    <div className={compact ? 'flex items-center gap-2' : 'space-y-1.5'}>
      {!compact && <p className="label">{t('Thumbnail')}</p>}
      <div className="flex items-center gap-2">
        {state?.image ? (
          <button
            onClick={() => setOpen(true)}
            className="rounded overflow-hidden border border-raised hover:border-accent shrink-0"
            title={label}
          >
            <img
              src={thumbsApi.imageUrl(publishId, version)}
              alt={t('Current thumbnail')}
              className="h-14 w-auto aspect-video object-cover"
            />
          </button>
        ) : (
          <ThumbPlaceholder className="h-14" />
        )}
        <div className="space-y-1">
          <button
            className="btn-ghost !py-1 text-xs"
            disabled={state === null}
            onClick={() => setOpen(true)}
          >
            🎨 {label}
          </button>
          <button
            className="btn-ghost !py-1 text-xs ml-1"
            disabled={state === null || generating}
            onClick={() => void generate()}
            title={t('The AI picks a frame and writes a headline, supporting line and badge that read together')}
          >
            ✨ {generating ? t('Generating…') : t('Auto-generate')}
          </button>
          {error && <p className="text-[11px] text-error">{error}</p>}
          {state === null && (
            <p className="text-[11px] text-muted">
              {t('Render it first: the designer works from its frames.')}
            </p>
          )}
          {state?.image && !compact && (
            <p className="text-[11px] text-muted">
              {t('Sent with every publish that takes a thumbnail.')}
            </p>
          )}
        </div>
      </div>
      {open && (
        <ThumbnailStudio
          publishId={publishId}
          onClose={() => setOpen(false)}
          onSaved={() => {
            setVersion((v) => v + 1)
            onSaved?.()
          }}
        />
      )}
    </div>
  )
}
