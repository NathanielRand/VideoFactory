import { useState } from 'react'
import { t } from '../lib/i18n'
import { publishingApi, slotToLocalInput } from '../lib/publishing'
import { isoToLocalInput, scheduleApi } from '../lib/schedule'
import { describeInstant, earliestSchedule, localInputToUtc, localTimeZone } from '../lib/youtube'

/** Visibility, and the scheduling that YouTube itself owns.
 *
 *  The wording under "Schedule" is the most important text in this feature.
 *  People assume a scheduler means the app has to stay open — this one does
 *  not, because the video is uploaded immediately and YouTube holds it. Saying
 *  so on screen is what stops someone leaving their PC on all night for
 *  nothing.
 */

interface Props {
  privacy: string
  scheduledAt: string // a datetime-local value, '' when publishing now
  onChange: (patch: { privacy?: string; scheduledAt?: string }) => void
  disabled?: boolean
}

export default function YouTubeSchedule({
  privacy,
  scheduledAt,
  onChange,
  disabled
}: Props): JSX.Element {
  const scheduling = scheduledAt !== ''
  const utc = scheduling ? localInputToUtc(scheduledAt) : null
  const zone = localTimeZone()
  const [suggesting, setSuggesting] = useState(false)
  const [basis, setBasis] = useState('')

  // The next best hour for this audience: YouTube's usual peaks, shifted
  // toward the hours this channel's own videos did best (publish/timing.py).
  // The next open slot of the posting schedule: after everything already
  // scheduled, within its days and times.
  const nextOpen = async (): Promise<void> => {
    setSuggesting(true)
    try {
      const got = await scheduleApi.nextSlots(1, ['youtube'], 'youtube')
      if (got.slots[0]) {
        onChange({ scheduledAt: isoToLocalInput(got.slots[0]) })
        setBasis(t('The next open slot in your posting schedule.'))
      } else {
        setBasis(t('No open slot in the next 60 days. Add days or times under Edit schedule on the Publish page.'))
      }
    } catch {
      setBasis('')
    } finally {
      setSuggesting(false)
    }
  }

  const suggest = async (): Promise<void> => {
    setSuggesting(true)
    try {
      const got = await publishingApi.bestTimes('youtube', 1)
      if (got.slots[0]) onChange({ scheduledAt: slotToLocalInput(got.slots[0]) })
      setBasis(
        got.learned_from
          ? t("Picked from your last {n} videos and YouTube's usual peaks.").replace(
              '{n}',
              String(got.learned_from)
            )
          : t(
              "Picked from YouTube's usual peak hours. It learns from your videos as they get views."
            )
      )
    } catch {
      setBasis('')
    } finally {
      setSuggesting(false)
    }
  }

  return (
    <fieldset className="border border-raised/60 rounded-lg p-3 space-y-3">
      <legend className="label px-1">{t('Visibility')}</legend>

      <div className="flex flex-wrap gap-3 text-sm">
        {(['public', 'unlisted', 'private'] as const).map((option) => (
          <label key={option} className="inline-flex items-center gap-2">
            <input
              type="radio"
              name="yt-visibility"
              checked={!scheduling && privacy === option}
              disabled={disabled}
              onChange={() => onChange({ privacy: option, scheduledAt: '' })}
            />
            {t(option === 'public' ? 'Public' : option === 'unlisted' ? 'Unlisted' : 'Private')}
          </label>
        ))}
        <label className="inline-flex items-center gap-2">
          <input
            type="radio"
            name="yt-visibility"
            checked={scheduling}
            disabled={disabled}
            onChange={() => onChange({ scheduledAt: earliestSchedule(30) })}
          />
          {t('Schedule')}
        </label>
      </div>

      {scheduling && (
        <div className="space-y-2">
          <input
            type="datetime-local"
            className="input"
            value={scheduledAt}
            min={earliestSchedule()}
            disabled={disabled}
            aria-label={t('Publish date and time')}
            onChange={(e) => onChange({ scheduledAt: e.target.value })}
          />
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              className="btn-ghost !py-1 text-xs"
              disabled={disabled || suggesting}
              onClick={suggest}
            >
              {suggesting ? t('Finding…') : t('Suggest best time')}
            </button>
            <button
              type="button"
              className="btn-accent !py-1 text-xs"
              disabled={disabled || suggesting}
              onClick={nextOpen}
            >
              {t('Next open slot')}
            </button>
            {basis && <span className="text-[11px] text-muted">{basis}</span>}
          </div>

          <p className="text-[11px] text-muted">
            {t('Times are in your timezone')} ({zone}).
          </p>

          {utc && (
            <p className="text-xs text-accent" aria-live="polite">
              {t('Goes live')} {describeInstant(utc)}
            </p>
          )}

          {/* The whole justification for using YouTube's publishAt instead of
              a timer in this app. It has to be on screen, not in the docs. */}
          <p className="text-xs bg-raised/40 border border-raised/60 rounded-md p-2">
            {t(
              'Video Factory uploads the video to YouTube now and asks YouTube to publish it at that time. You can close Video Factory and turn off your computer - YouTube handles the rest.'
            )}
          </p>
        </div>
      )}
    </fieldset>
  )
}
