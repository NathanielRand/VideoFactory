// Video Factory: render one AI clip in other shapes (per-platform versions).
// Each shape is re-rendered from the source with its own crop and captions;
// the subject tracking is computed once and shared (formats/variants.py).
import { useCallback, useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import VideoPlayer from './VideoPlayer'
import {
  CANVAS_ORDER,
  formatsApi,
  platformsFor,
  type CanvasKey,
  type ClipVariants,
  type FormatsInfo
} from '../lib/formats'

export default function FormatVariants({ clipId }: { clipId: number }): JSX.Element {
  const [info, setInfo] = useState<FormatsInfo | null>(null)
  const [data, setData] = useState<ClipVariants | null>(null)
  const [picked, setPicked] = useState<CanvasKey[]>([])
  const [waitingFor, setWaitingFor] = useState<CanvasKey[]>([])
  const [preview, setPreview] = useState<CanvasKey | null>(null)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await formatsApi.variants(clipId))
    } catch {
      /* the clip may be mid re-render */
    }
  }, [clipId])

  useEffect(() => {
    formatsApi.info().then(setInfo).catch(() => undefined)
  }, [])
  useEffect(() => {
    setPicked([])
    setWaitingFor([])
    setPreview(null)
    setNotice('')
    void load()
  }, [clipId, load])

  // While a render is queued, poll until every asked-for shape has appeared.
  useEffect(() => {
    if (waitingFor.length === 0) return
    const id = setInterval(async () => {
      const d = await formatsApi.variants(clipId).catch(() => null)
      if (!d) return
      setData(d)
      const have = new Set(d.variants.filter((v) => !v.stale).map((v) => v.canvas))
      if (waitingFor.every((c) => have.has(c))) {
        setWaitingFor([])
        setNotice(t('Formats ready.'))
      }
    }, 3000)
    return () => clearInterval(id)
  }, [waitingFor, clipId])

  const own = data?.canvas ?? '9:16'
  const byCanvas = Object.fromEntries((data?.variants ?? []).map((v) => [v.canvas, v]))

  const render = async (): Promise<void> => {
    if (picked.length === 0) return
    try {
      const r = await formatsApi.render(clipId, picked)
      setWaitingFor(picked)
      setPicked([])
      setNotice(
        r.started
          ? t('Rendering on the queue…')
          : t('Queued. The queue is paused with other videos waiting: press Start on the Queue page.')
      )
    } catch (e) {
      setNotice(String((e as Error).message))
    }
  }

  return (
    <div className="border-t border-raised/60 pt-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <h4 className="label">{t('Formats')}</h4>
        <span className="text-xs text-muted">{t('One clip, a version per platform')}</span>
      </div>
      <div className="space-y-1">
        {CANVAS_ORDER.map((c) => {
          const v = byCanvas[c]
          const isOwn = c === own
          const warn = data?.warnings?.[c] ?? []
          const pending = waitingFor.includes(c)
          return (
            <div key={c} className="flex items-center gap-2 text-sm">
              <label className="flex items-center gap-2 flex-1 min-w-0">
                <input
                  type="checkbox"
                  disabled={isOwn || pending}
                  checked={isOwn || picked.includes(c)}
                  onChange={(e) =>
                    setPicked((p) => (e.target.checked ? [...p, c] : p.filter((x) => x !== c)))
                  }
                />
                <span className="w-10 font-medium">{c}</span>
                <span className="text-xs text-muted truncate" title={platformsFor(info, c)}>
                  {platformsFor(info, c)}
                </span>
              </label>
              {warn.length > 0 && (
                <span className="text-xs text-yellow-400" title={warn.join('\n')}>
                  ⚠ {t('too long for some')}
                </span>
              )}
              {isOwn && <span className="text-xs text-muted">{t('this clip')}</span>}
              {pending && <span className="text-xs text-accent">{t('rendering…')}</span>}
              {!isOwn && v && v.exists && !pending && (
                <>
                  {v.stale ? (
                    <span className="text-xs text-yellow-400" title={t('The clip changed after this was rendered.')}>
                      {t('outdated')}
                    </span>
                  ) : (
                    <span className="text-xs text-green-400">✓</span>
                  )}
                  <button
                    className="btn-ghost px-2 py-0.5 text-xs"
                    onClick={() => setPreview(preview === c ? null : c)}
                  >
                    {preview === c ? t('Hide') : t('Preview')}
                  </button>
                </>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <button className="btn-ghost" disabled={picked.length === 0} onClick={() => void render()}>
          {picked.length > 0
            ? `${t('Render')} ${picked.join(', ')}`
            : t('Tick formats to render')}
        </button>
        {notice && <span className="text-xs text-accent">{notice}</span>}
      </div>
      {preview && byCanvas[preview] && (
        <VideoPlayer
          key={`${preview}-${byCanvas[preview].created_at}`}
          className="w-full"
          label={`${preview} version preview`}
          src={formatsApi.mediaUrl(clipId, preview, byCanvas[preview].created_at)}
        />
      )}
    </div>
  )
}
