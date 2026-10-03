import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { t } from '../lib/i18n'
import type { Playlist } from '../lib/youtube'

/** The channel's playlists, when YouTube is connected with the playlist
 *  permission; null while unknown, and an empty list when it is not available
 *  (callers then show nothing, or a hint to reconnect). */
export function useChannelPlaylists(): { playlists: Playlist[]; available: boolean | null } {
  const [state, setState] = useState<{ playlists: Playlist[]; available: boolean | null }>({
    playlists: [],
    available: null
  })
  useEffect(() => {
    let alive = true
    api
      .youtubeStatus()
      .then(async (s) => {
        if (!alive) return
        if (!(s.enabled && s.connected && s.playlists_available)) {
          setState({ playlists: [], available: false })
          return
        }
        const got = await api.youtubePlaylists()
        if (alive) setState({ playlists: got.playlists, available: true })
      })
      .catch(() => alive && setState({ playlists: [], available: false }))
    return () => {
      alive = false
    }
  }, [])
  return state
}

/** Pick a YouTube playlist. "" means none chosen here: the creator's playlist
 *  rule (Publish page) applies, if there is one. */
export default function PlaylistSelect({
  value,
  onChange,
  playlists,
  disabled,
  id,
  noneLabel
}: {
  value: string
  onChange: (id: string) => void
  playlists: Playlist[]
  disabled?: boolean
  id?: string
  noneLabel?: string
}): JSX.Element {
  // A playlist that was chosen but is not in the list (deleted on YouTube, or
  // the list is still loading) must still show, not silently read as "none".
  const known = !value || playlists.some((p) => p.id === value)
  return (
    <select
      id={id}
      className="input"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{noneLabel ?? t('None (use the creator’s rule)')}</option>
      {!known && <option value={value}>{value}</option>}
      {playlists.map((p) => (
        <option key={p.id} value={p.id}>
          {p.title} ({p.count})
        </option>
      ))}
    </select>
  )
}
