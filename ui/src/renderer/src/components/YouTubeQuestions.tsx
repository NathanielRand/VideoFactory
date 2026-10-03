import { t } from '../lib/i18n'

/** The questions YouTube asks on every upload and holds processing for
 *  until they are answered. Worded as YouTube words them, so the answer
 *  given here is the answer to the question YouTube asks.
 *
 *  Nothing is pre-selected on a first publish: these are declarations, and
 *  answering them on someone's behalf is not the app's call. After that the
 *  last answers are offered again, since most channels answer the same way
 *  every time. */
export interface YouTubeAnswers {
  ai: boolean | null
  paid: boolean | null
  kids: boolean | null
}

const KEY = 'video-factory-youtube-answers'

export function rememberedAnswers(): YouTubeAnswers {
  try {
    const got = JSON.parse(localStorage.getItem(KEY) || '{}')
    const pick = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)
    return { ai: pick(got.ai), paid: pick(got.paid), kids: pick(got.kids) }
  } catch {
    return { ai: null, paid: null, kids: null }
  }
}

export function rememberAnswers(a: YouTubeAnswers): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(a))
  } catch {
    /* only a convenience */
  }
}

/** Which questions this route to YouTube can actually answer. WoopSocial's
 *  API has a field for "made for kids" only. */
export type Carrier = 'youtube' | 'uploadpost' | 'woopsocial'

export function answered(a: YouTubeAnswers, via: Carrier): boolean {
  if (via === 'woopsocial') return a.kids !== null
  return a.ai !== null && a.paid !== null && a.kids !== null
}

export default function YouTubeQuestions({
  value,
  onChange,
  via,
  disabled,
  showKids = true
}: {
  value: YouTubeAnswers
  onChange: (next: YouTubeAnswers) => void
  via: Carrier
  disabled?: boolean
  /** False where the form already asks "made for kids" itself. */
  showKids?: boolean
}): JSX.Element {
  const set = (patch: Partial<YouTubeAnswers>): void => {
    const next = { ...value, ...patch }
    rememberAnswers(next)
    onChange(next)
  }
  const carries = via !== 'woopsocial'

  const yesNo = (
    name: string,
    current: boolean | null,
    yes: string,
    no: string,
    onPick: (v: boolean) => void
  ): JSX.Element => (
    <div className="flex flex-col gap-1 text-sm">
      {[
        [true, yes],
        [false, no]
      ].map(([v, label]) => (
        <label key={String(v)} className="inline-flex items-start gap-2">
          <input
            type="radio"
            className="mt-1"
            name={name}
            checked={current === v}
            disabled={disabled}
            onChange={() => onPick(v as boolean)}
          />
          <span>{label as string}</span>
        </label>
      ))}
    </div>
  )

  return (
    <fieldset className="border border-raised/60 rounded-lg p-3 space-y-3">
      <legend className="label px-1">{t('YouTube asks')}</legend>

      {carries ? (
        <>
          <div className="space-y-1.5">
            <p className="text-sm font-medium">{t('AI use')}</p>
            <p className="text-xs text-muted">
              {t('Was AI used to generate or edit your content in any of the following ways?')}
            </p>
            <ul className="text-xs text-muted list-disc pl-5 space-y-0.5">
              <li>{t('Makes a real person appear to say or do something they didn’t say or do')}</li>
              <li>{t('Alters footage of a real event or place')}</li>
              <li>{t('Generates a realistic-looking scene that didn’t actually occur')}</li>
            </ul>
            {yesNo('yt-ai', value.ai, t('Yes'), t('No'), (v) => set({ ai: v }))}
          </div>

          <div className="space-y-1.5">
            <p className="text-sm font-medium">{t('Paid promotion')}</p>
            <p className="text-xs text-muted">
              {t(
                'You’re required to tell YouTube if your video has a paid promotion, like product placement or sponsorships, and it will let viewers know.'
              )}
            </p>
            {yesNo(
              'yt-paid',
              value.paid,
              t('Yes, my video includes paid promotion'),
              t('No, my video doesn’t include paid promotion'),
              (v) => set({ paid: v })
            )}
          </div>
        </>
      ) : (
        <p className="text-xs text-warn">
          {t(
            'WoopSocial’s API cannot answer YouTube’s “AI use” and “Paid promotion” questions, so YouTube Studio will ask them before it processes the video. Upload-Post or YouTube direct can answer them from here.'
          )}
        </p>
      )}

      {showKids && (
        <div className="space-y-1.5">
          <p className="text-sm font-medium">{t('Audience')}</p>
          <p className="text-xs text-muted">{t('Is this video made for kids?')}</p>
          {yesNo('yt-kids', value.kids, t('Yes, it’s made for kids'), t('No, it’s not made for kids'), (v) =>
            set({ kids: v })
          )}
        </div>
      )}

      {!answered(value, via) && (
        <p className="text-[11px] text-warn">{t('Answer these to publish to YouTube.')}</p>
      )}
    </fieldset>
  )
}

/** The answers as Upload-Post's per-platform overrides for YouTube. */
export function uploadPostYoutubeOverrides(a: YouTubeAnswers): Record<string, string> {
  const out: Record<string, string> = {}
  if (a.ai !== null) out.ai_disclosure = String(a.ai)
  if (a.paid !== null) out.paid_promotion = String(a.paid)
  if (a.kids !== null) out.made_for_kids = String(a.kids)
  return out
}
