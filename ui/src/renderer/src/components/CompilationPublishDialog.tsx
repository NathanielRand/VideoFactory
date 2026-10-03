import { COST } from '../lib/quota'
import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import { publishingApi, splitList, type CompilationPublishMeta } from '../lib/publishing'
import type { Clip } from '../lib/types'
import type { Provider } from '../lib/uploadpost'
import { localInputToUtc, TITLE_MAX, DESCRIPTION_MAX } from '../lib/youtube'
import PublishAllDialog from './PublishAllDialog'
import { FirstCommentField, VideoHashtagsField } from './PublishExtras'
import SeoPanel from './SeoPanel'
import YouTubeSchedule from './YouTubeSchedule'
import ThumbnailCard from './ThumbnailCard'
import ThumbnailNudge from './ThumbnailNudge'
import YouTubeQuestions, { answered, rememberedAnswers, type YouTubeAnswers } from './YouTubeQuestions'

/** Publish a rendered compilation: its metadata, then the same send a clip
 *  gets.
 *
 *  Compilations are the long videos, where search matters most, so this opens
 *  on generated metadata: a title and summary from the model, and chapters
 *  and credits COMPUTED from the recipe (a model asked for timestamps gets
 *  them wrong). Everything is editable and saved on the compilation, so a
 *  second publish starts from what went out the first time.
 *
 *  Sending goes through whichever provider clips use (WoopSocial or
 *  Upload-Post, else YouTube direct). The compilation travels under the
 *  negative of its id; see StateDB.get_publishable. */
export default function CompilationPublishDialog({
  compilationId,
  title,
  provider,
  multiReady,
  youtubeReady,
  onClose
}: {
  compilationId: number
  title: string
  provider: Provider
  /** WoopSocial or Upload-Post is connected. */
  multiReady: boolean
  /** YouTube direct is connected. */
  youtubeReady: boolean
  onClose: () => void
}): JSX.Element {
  const [meta, setMeta] = useState<CompilationPublishMeta | null>(null)
  const [outputs, setOutputs] = useState<string[]>([])
  const [publishId, setPublishId] = useState(-compilationId)
  const [busy, setBusy] = useState<'' | 'loading' | 'generating' | 'saving' | 'sending'>('loading')
  const [error, setError] = useState('')
  const [sending, setSending] = useState<Clip[] | null>(null)
  const [keywordText, setKeywordText] = useState('')
  const [hashtagText, setHashtagText] = useState('')
  // YouTube direct only.
  const [answers, setAnswers] = useState<YouTubeAnswers>(rememberedAnswers)
  const [privacy, setPrivacy] = useState('public')
  const [scheduledAt, setScheduledAt] = useState('')
  const [sent, setSent] = useState('')
  const [category, setCategory] = useState('22')
  const [hasThumb, setHasThumb] = useState(false)
  const [thumbKey, setThumbKey] = useState(0)

  useEffect(() => {
    if (!youtubeReady) return
    api
      .youtubeStatus()
      .then((s) => setCategory(s.settings?.category_id ?? '22'))
      .catch(() => {
        /* the default category still works */
      })
  }, [youtubeReady])

  const adopt = (m: CompilationPublishMeta): void => {
    setMeta(m)
    setKeywordText(m.keywords.join(', '))
    setHashtagText(m.hashtags.join(' '))
  }

  const generate = async (): Promise<void> => {
    setBusy('generating')
    setError('')
    try {
      const fresh = (await publishingApi.generateCompilationMeta(compilationId)).meta
      // A comment typed here is a choice, not generated text: keep it.
      adopt({ ...fresh, first_comment: meta?.first_comment || fresh.first_comment })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  // The model takes a minute on a local machine. Rather than a blank dialog
  // for that long, open on a draft built from the facts (chapters, credits,
  // plain title) and let the model's words replace whatever is still as
  // drafted when they arrive. Anything already edited is left alone.
  const [improving, setImproving] = useState(false)
  const improve = async (draft: CompilationPublishMeta): Promise<void> => {
    setImproving(true)
    try {
      const ai = (await publishingApi.generateCompilationMeta(compilationId)).meta
      const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)
      setMeta((m) => {
        if (!m) return ai
        const next = { ...m }
        for (const key of ['title', 'description', 'alt_titles', 'suggested_comment'] as const) {
          if (same(m[key], draft[key])) (next as Record<string, unknown>)[key] = ai[key]
        }
        return { ...next, keywords: ai.keywords, hashtags: ai.hashtags }
      })
      setKeywordText((k) => (k === draft.keywords.join(', ') ? ai.keywords.join(', ') : k))
      setHashtagText((h) => (h === draft.hashtags.join(' ') ? ai.hashtags.join(' ') : h))
    } catch {
      /* the draft stands; Regenerate can try again */
    } finally {
      setImproving(false)
    }
  }

  useEffect(() => {
    publishingApi
      .compilationMeta(compilationId)
      .then(async (got) => {
        setOutputs(got.outputs)
        setPublishId(got.publish_id)
        if (got.meta.title) {
          adopt({
            title: '',
            description: '',
            hashtags: [],
            keywords: [],
            first_comment: '',
            alt_titles: [],
            canvas: got.outputs[0] ?? '',
            ...got.meta
          } as CompilationPublishMeta)
          setBusy('')
        } else {
          const draft = (await publishingApi.generateCompilationMeta(compilationId, false)).meta
          adopt(draft)
          setBusy('')
          void improve(draft)
        }
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e))
        setBusy('')
      })
  }, [compilationId])

  const patch = (p: Partial<CompilationPublishMeta>): void =>
    setMeta((m) => (m ? { ...m, ...p } : m))

  const current = (): CompilationPublishMeta | null =>
    meta && {
      ...meta,
      keywords: splitList(keywordText),
      hashtags: hashtagText.split(/[\s,]+/).filter(Boolean)
    }

  const save = async (): Promise<CompilationPublishMeta | null> => {
    const m = current()
    if (!m) return null
    setBusy('saving')
    setError('')
    try {
      const saved = (await publishingApi.saveCompilationMeta(compilationId, m)).meta
      adopt(saved)
      return saved
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy('')
    }
  }

  const sendMulti = async (): Promise<void> => {
    const m = await save()
    if (!m) return
    // The shape PublishAllDialog reads. The server loads the real thing by id.
    setSending([
      {
        id: publishId,
        video_id: `compilation:${compilationId}`,
        start_s: 0,
        end_s: 0,
        score: 0,
        hook: '',
        path: '',
        status: 'rendered',
        scheduled_for: null,
        title: m.title,
        description: m.description,
        hashtags: m.hashtags,
        scores: {} as Clip['scores'],
        render_opts: {} as Clip['render_opts'],
        created_at: '',
        exported_at: ''
      }
    ])
  }

  const sendYoutube = async (): Promise<void> => {
    const m = await save()
    if (!m) return
    setBusy('sending')
    try {
      const publishAt = scheduledAt ? localInputToUtc(scheduledAt) : null
      await api.publishClip(publishId, {
        title: m.title,
        description: m.description,
        tags: m.keywords,
        privacy: publishAt ? 'private' : privacy,
        publish_at: publishAt,
        made_for_kids: answers.kids === true,
        contains_synthetic_media: answers.ai === true,
        paid_promotion: answers.paid === true,
        category_id: category,
        thumbnail: hasThumb
      })
      setSent(
        publishAt
          ? t('Uploading now; YouTube will publish it at the scheduled time.')
          : t('Uploading to YouTube now.')
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  if (sending) {
    return (
      <PublishAllDialog
        clips={sending}
        provider={provider}
        heading={`${t('Publish')} "${meta?.title || title}"`}
        onClose={onClose}
      />
    )
  }

  const working = busy !== ''
  const canSend = Boolean(meta?.title.trim()) && !working

  return (
    <div
      className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm grid place-items-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={t('Publish compilation')}
      onClick={onClose}
    >
      <div
        className="card w-full max-w-2xl space-y-3 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-lg truncate">
              {t('Publish')} “{title}”
            </h3>
            <p className="text-xs text-muted mt-0.5">
              {t(
                'Chapters and credits are worked out from the compilation itself; the words are yours to change.'
              )}
            </p>
          </div>
          <button
            className="btn-ghost !py-1 text-xs"
            disabled={working || improving}
            onClick={() => void generate()}
          >
            {busy === 'generating' || improving ? t('Writing…') : `↻ ${t('Regenerate')}`}
          </button>
        </div>

        {improving && meta && (
          <p className="text-xs text-accent bg-accent/10 rounded-md px-2 py-1" aria-live="polite">
            ✨{' '}
            {t('Writing a better title, summary and keywords… edit freely, your changes are kept.')}
          </p>
        )}

        {!meta ? (
          <p className="text-sm text-muted py-6 text-center">
            {busy === 'generating'
              ? t('Writing the title, description and keywords…')
              : error || t('Loading…')}
          </p>
        ) : sent ? (
          <div className="space-y-3 text-sm">
            <p className="text-success">✓ {sent}</p>
            <p className="text-xs text-muted">
              {t('Progress shows in the Publish page schedule.')}
            </p>
            <button className="btn-accent w-full !py-2" onClick={onClose}>
              {t('Done')}
            </button>
          </div>
        ) : (
          <>
            {outputs.length > 1 && (
              <label className="flex items-center gap-2 text-sm">
                <span className="label">{t('Format')}</span>
                <select
                  className="input !w-auto !py-1"
                  value={meta.canvas || outputs[0]}
                  onChange={(e) => patch({ canvas: e.target.value })}
                >
                  {outputs.map((o) => (
                    <option key={o} value={o}>
                      {o}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <div>
              <div className="flex items-center justify-between">
                <label className="label" htmlFor="cp-title">
                  {t('Title')}
                </label>
                <span className="text-[11px] tabular-nums text-muted">
                  {meta.title.length}/{TITLE_MAX}
                </span>
              </div>
              <input
                id="cp-title"
                className="input"
                maxLength={TITLE_MAX}
                value={meta.title}
                onChange={(e) => patch({ title: e.target.value })}
              />
              {meta.alt_titles.filter((a) => a !== meta.title).length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-1.5 text-[11px]">
                  <span className="text-muted">{t('Try instead:')}</span>
                  {meta.alt_titles
                    .filter((a) => a !== meta.title)
                    .map((alt) => (
                      <button
                        key={alt}
                        className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink text-left"
                        onClick={() => patch({ title: alt.slice(0, TITLE_MAX) })}
                      >
                        {alt}
                      </button>
                    ))}
                </div>
              )}
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="label" htmlFor="cp-desc">
                  {t('Description')}
                </label>
                <span className="text-[11px] tabular-nums text-muted">
                  {meta.description.length}/{DESCRIPTION_MAX}
                </span>
              </div>
              <textarea
                id="cp-desc"
                className="input min-h-[180px] resize-y font-mono text-xs"
                maxLength={DESCRIPTION_MAX}
                value={meta.description}
                onChange={(e) => patch({ description: e.target.value })}
              />
              {!meta.chapters && (
                <p className="text-[11px] text-muted mt-1">
                  {t('No chapters: YouTube needs at least three parts of 10 seconds or more.')}
                </p>
              )}
            </div>

            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className="label" htmlFor="cp-keywords">
                  {t('Search keywords')}
                </label>
                <input
                  id="cp-keywords"
                  className="input"
                  value={keywordText}
                  placeholder={t('best of ann, funny moments')}
                  onChange={(e) => setKeywordText(e.target.value)}
                />
                <p className="text-[11px] text-muted mt-1">
                  {t('Sent as YouTube tags. Comma separated.')}
                </p>
              </div>
              <VideoHashtagsField value={hashtagText} onChange={setHashtagText} id="cp-hashtags" />
            </div>

            <FirstCommentField
              publishId={publishId}
              value={meta.first_comment}
              suggestion={meta.suggested_comment ?? ''}
              onChange={(v) => patch({ first_comment: v })}
              onSuggestion={(v) => patch({ suggested_comment: v })}
            />

            <ThumbnailCard key={thumbKey} publishId={publishId} onState={setHasThumb} onSaved={() => setHasThumb(true)} />
            {!hasThumb && <ThumbnailNudge ids={[publishId]} onChanged={() => {
                setHasThumb(true)
                setThumbKey((k) => k + 1)
              }} />}

            <SeoPanel
              draft={{
                title: meta.title,
                description: meta.description,
                keywords: splitList(keywordText),
                hashtags: hashtagText.split(/[\s,]+/).filter(Boolean),
                long_form: (meta.canvas || outputs[0]) === '16:9',
                // The playlist comes from a playlist rule, not from here, so
                // it is not counted against the score.
                has_thumbnail: hasThumb,
                has_playlist: true
              }}
            />

            {!multiReady && youtubeReady && (
              <div className="space-y-3">
                <p className="text-[11px] text-muted">
                  {t('Spends 1 of your YouTube uploads for today')}
                  {hasThumb ? ` ${t('and about')} ${COST.thumbnail} ${t('units for the thumbnail')}` : ''}.
                </p>
                <YouTubeQuestions value={answers} onChange={setAnswers} via="youtube" />
                <YouTubeSchedule
                  privacy={privacy}
                  scheduledAt={scheduledAt}
                  onChange={(p) => {
                    if (p.privacy !== undefined) setPrivacy(p.privacy)
                    if (p.scheduledAt !== undefined) setScheduledAt(p.scheduledAt)
                  }}
                />
              </div>
            )}

            {!multiReady && !youtubeReady && (
              <p className="text-sm text-warn">
                {t(
                  'Connect WoopSocial, Upload-Post or YouTube in Settings to publish. Your metadata is saved either way.'
                )}
              </p>
            )}

            {error && <p className="text-xs text-error">{error}</p>}

            <div className="flex gap-2">
              <button className="btn-ghost !py-2 px-4" onClick={onClose} disabled={working}>
                {t('Close')}
              </button>
              <button
                className="btn-ghost flex-1 !py-2"
                disabled={working}
                onClick={() => void save()}
              >
                {busy === 'saving' ? t('Saving…') : t('Save metadata')}
              </button>
              {multiReady ? (
                <button
                  className="btn-accent flex-1 !py-2"
                  disabled={!canSend}
                  onClick={() => void sendMulti()}
                >
                  {t('Next: platforms & timing')} →
                </button>
              ) : youtubeReady ? (
                <button
                  className="btn-accent flex-1 !py-2"
                  disabled={!canSend || !answered(answers, 'youtube')}
                  title={
                    !answered(answers, 'youtube')
                      ? t('Answer YouTube’s questions first.')
                      : ''
                  }
                  onClick={() => void sendYoutube()}
                >
                  {busy === 'sending'
                    ? t('Uploading…')
                    : scheduledAt
                      ? t('Schedule on YouTube')
                      : t('Upload to YouTube')}
                </button>
              ) : null}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
