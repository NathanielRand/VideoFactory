import { useEffect, useState } from 'react'
import { t } from '../lib/i18n'
import { publishingApi } from '../lib/publishing'

/** The standing values from the Publish page, read once per mount. */
function useStanding(): { comment: string; hashtags: string[] } {
  const [standing, setStanding] = useState({ comment: '', hashtags: [] as string[] })
  useEffect(() => {
    let alive = true
    Promise.allSettled([publishingApi.standingComment(), publishingApi.settings()]).then(
      ([c, s]) => {
        if (!alive) return
        setStanding({
          comment: c.status === 'fulfilled' ? c.value.first_comment : '',
          hashtags: s.status === 'fulfilled' ? s.value.hashtags : []
        })
      }
    )
    return () => {
      alive = false
    }
  }, [])
  return standing
}

/** One video's first comment.
 *
 *  Empty means the standing comment from the Publish page goes out. Filling
 *  it REPLACES that for this video only; it never stacks under it. The AI
 *  suggestion is written from what happens in this video and is only used
 *  when picked. */
export function FirstCommentField({
  publishId,
  value,
  suggestion,
  onChange,
  onSuggestion,
  disabled
}: {
  /** Clip id, or the negative of a compilation's id. */
  publishId: number
  value: string
  suggestion: string
  onChange: (value: string) => void
  /** A fresh suggestion arrived (it is also saved on the video). */
  onSuggestion?: (value: string) => void
  disabled?: boolean
}): JSX.Element {
  const { comment: standing } = useStanding()
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState('')

  const suggest = async (): Promise<void> => {
    setAsking(true)
    setError('')
    try {
      const got = await publishingApi.suggestComment(publishId)
      onSuggestion?.(got.suggested_comment)
      onChange(got.suggested_comment)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAsking(false)
    }
  }

  const own = value.trim() !== ''

  return (
    <div className="space-y-1.5">
      <label className="label block" htmlFor={`fc-${publishId}`}>
        {t('First comment')}
      </label>
      <input
        id={`fc-${publishId}`}
        className="input !py-1.5 text-sm"
        value={value}
        maxLength={300}
        disabled={disabled}
        placeholder={standing || t('No standing comment set on the Publish page')}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {suggestion && suggestion !== value && (
          <button
            type="button"
            disabled={disabled}
            className="px-2 py-0.5 rounded-md bg-accent/15 text-accent hover:bg-accent/25 text-left"
            title={t('Use the comment the AI wrote for this video')}
            onClick={() => onChange(suggestion)}
          >
            ✨ {suggestion}
          </button>
        )}
        <button
          type="button"
          disabled={disabled || asking}
          className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink"
          onClick={() => void suggest()}
        >
          {asking
            ? t('Writing…')
            : suggestion
              ? `↻ ${t('New suggestion')}`
              : `✨ ${t('Suggest one for this video')}`}
        </button>
        {own && (
          <button
            type="button"
            disabled={disabled}
            className="px-2 py-0.5 rounded-md bg-raised text-muted hover:text-ink"
            onClick={() => onChange('')}
          >
            {t('Use the standing comment')}
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted">
        {own
          ? t('This video posts its own comment instead of the standing one.')
          : standing
            ? t('Empty: the standing comment from the Publish page is posted (shown greyed above).')
            : t('Empty: no first comment is posted.')}
      </p>
      {error && <p className="text-[11px] text-error">{error}</p>}
    </div>
  )
}

/** This video's hashtags, shown after the always-on ones they are added to. */
export function VideoHashtagsField({
  value,
  onChange,
  disabled,
  id = 'video-hashtags'
}: {
  /** Space-separated, as typed. */
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  id?: string
}): JSX.Element {
  const { hashtags: always } = useStanding()
  return (
    <div className="space-y-1.5">
      <label className="label block" htmlFor={id}>
        {t('Video hashtags')}
      </label>
      <div className="flex flex-wrap items-center gap-1.5 input !py-1 min-h-[2.1rem]">
        {always.map((tag) => (
          <span
            key={tag}
            className="px-1.5 py-0.5 rounded bg-raised text-muted text-xs"
            title={t('Always-on hashtag, set on the Publish page')}
          >
            {tag}
          </span>
        ))}
        <input
          id={id}
          className="flex-1 min-w-[8rem] bg-transparent outline-none text-sm"
          value={value}
          disabled={disabled}
          placeholder={t('#this #video #only')}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      <p className="text-[11px] text-muted">
        {always.length
          ? t('Added after your always-on hashtags (grey), which lead every post.')
          : t('Set always-on hashtags on the Publish page to add them to every post too.')}
      </p>
    </div>
  )
}
