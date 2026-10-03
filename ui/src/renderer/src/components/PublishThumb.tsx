import ThumbPlaceholder from './ThumbPlaceholder'
import { useEffect, useState } from 'react'
import { thumbsApi } from '../lib/thumbnails'

/** Which of these clips (negative: compilations) have a saved thumbnail.
 *  One cheap request for the whole list; `version` re-asks after a save. */
export function useThumbStatus(ids: number[], version = 0): Record<number, boolean> {
  const [have, setHave] = useState<Record<number, boolean>>({})
  const key = ids.join(',')
  useEffect(() => {
    let alive = true
    thumbsApi
      .status(ids)
      .then((r) => alive && setHave(r))
      .catch(() => alive && setHave({}))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version])
  return have
}

/** A clip's or compilation's thumbnail, or a quiet placeholder when it has
 *  none, never a broken-image icon. `has` comes from useThumbStatus. */
export default function PublishThumb({
  id,
  has,
  version = 0,
  className = 'h-10'
}: {
  id: number
  has: boolean | undefined
  version?: number
  className?: string
}): JSX.Element {
  return has ? (
    <img
      src={thumbsApi.imageUrl(id, version)}
      alt=""
      loading="lazy"
      className={`${className} aspect-video object-cover rounded shrink-0 bg-raised`}
    />
  ) : (
    // Undefined: still asking, so a plain skeleton. False: it has none.
    <ThumbPlaceholder className={className} loading={has === undefined} />
  )
}
