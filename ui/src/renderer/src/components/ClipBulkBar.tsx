import { useRef, useState } from 'react'
import { api } from '../lib/api'
import { clipProfiles, useBrandingProfiles } from '../lib/branding'
import { getExportFolder } from '../lib/exportFolder'
import type { Clip } from '../lib/types'
import AddToCompilation from './AddToCompilation'
import SendMenu from './delivery/SendMenu'
import { useExports } from './delivery/useExports'
import Popover from './Popover'
import { useChannelPlaylists } from './PlaylistSelect'

/** What can be done to the clips ticked on the Clips page, all at once.
 *  Nothing here is new machinery: each action loops the same call the
 *  single-clip button makes, and says how many worked. */
export default function ClipBulkBar({
  picked,
  total,
  onSelectAll,
  onClear,
  onChanged,
  onPublish,
  publishReady
}: {
  picked: Clip[]
  /** How many clips "Select all" would tick (the ones currently shown). */
  total: number
  onSelectAll: () => void
  onClear: () => void
  /** Clips were re-rendered, deleted or starred: re-read the list. */
  onChanged: (deleted?: number[]) => void
  onPublish: (clips: Clip[], when: 'now' | 'schedule') => void
  publishReady: boolean
}): JSX.Element {
  const [busy, setBusyState] = useState('')
  // A ref beside the state: two presses in one frame both see the old `busy`,
  // so the state alone would let the second one start a second batch.
  const working = useRef(false)
  const setBusy = (label: string): void => {
    working.current = label !== ''
    setBusyState(label)
  }
  const [note, setNote] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [brandOpen, setBrandOpen] = useState(false)
  const exportBtn = useRef<HTMLButtonElement>(null)
  const brandBtn = useRef<HTMLButtonElement>(null)
  const playlistBtn = useRef<HTMLButtonElement>(null)
  const [playlistOpen, setPlaylistOpen] = useState(false)
  const { playlists, available: playlistsAvailable } = useChannelPlaylists()
  const { destinations } = useExports()
  const profiles = clipProfiles(useBrandingProfiles().profiles)
  const n = picked.length
  const s = n === 1 ? '' : 's'

  /** Run `fn` on every picked clip, carrying on past a failure. */
  const each = async (
    label: string,
    fn: (c: Clip) => Promise<unknown>
  ): Promise<{ ok: Clip[]; failed: number }> => {
    if (working.current) return { ok: [], failed: 0 }
    setBusy(label)
    setNote('')
    const ok: Clip[] = []
    let failed = 0
    for (const c of picked) {
      try {
        await fn(c)
        ok.push(c)
      } catch {
        failed++
      }
    }
    setBusy('')
    return { ok, failed }
  }
  const failNote = (failed: number): string => (failed ? ` ${failed} failed.` : '')

  const rerender = async (): Promise<void> => {
    if (working.current) return
    if (
      !window.confirm(
        `Re-render ${n} clip${s}?\n\nEach one is rendered again with its current settings. Clips set to follow a branding profile pick up the profile's latest watermark, credit and captions. This can take a while.`
      )
    )
      return
    const { ok, failed } = await each('rerender', (c) => api.rerenderClip(c.id))
    setNote(`Queued ${ok.length} re-render${ok.length === 1 ? '' : 's'} — watch the activity feed on Home.${failNote(failed)}`)
    onChanged()
  }

  const applyProfile = async (profileId: number | null): Promise<void> => {
    setBrandOpen(false)
    if (working.current) return
    const name = profiles.find((p) => p.id === profileId)?.name ?? 'no branding'
    if (!window.confirm(`Set ${n} clip${s} to “${name}” and re-render ${n === 1 ? 'it' : 'them'}?`)) return
    const { ok, failed } = await each('branding', (c) =>
      api.rerenderClip(c.id, undefined, { branding: { profile_id: profileId }, watermark: null })
    )
    setNote(`Queued ${ok.length} re-render${ok.length === 1 ? '' : 's'} with “${name}”.${failNote(failed)}`)
    onChanged()
  }

  const applyPlaylist = async (playlistId: string, title: string): Promise<void> => {
    setPlaylistOpen(false)
    if (working.current) return
    setBusy('playlist')
    setNote('')
    try {
      const got = await api.setClipsPlaylist(
        picked.map((c) => c.id),
        playlistId
      )
      setNote(
        playlistId
          ? `${got.updated} clip${got.updated === 1 ? '' : 's'} will join “${title}” when published to YouTube.`
          : `Cleared the playlist on ${got.updated} clip${got.updated === 1 ? '' : 's'}.`
      )
      onChanged()
    } catch (e) {
      setNote(`Could not set the playlist: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  const remove = async (): Promise<void> => {
    if (working.current) return
    if (
      !window.confirm(
        `Delete ${n} clip${s} and ${n === 1 ? 'its file' : 'their files'}?\n\nThe videos and your other clips stay. This can't be undone.`
      )
    )
      return
    const { ok, failed } = await each('delete', (c) => api.deleteClip(c.id))
    setNote(`Deleted ${ok.length} clip${ok.length === 1 ? '' : 's'}.${failNote(failed)}`)
    onChanged(ok.map((c) => c.id))
    onClear()
  }

  const exportLocal = async (): Promise<void> => {
    setExportOpen(false)
    if (working.current) return
    setBusy('export')
    setNote('')
    try {
      const folder = await getExportFolder()
      const res = await api.exportBatch(
        picked.map((c) => c.id),
        folder
      )
      setNote(
        `Exported ${res.exported.length} clip${res.exported.length === 1 ? '' : 's'} to ${folder}.` +
          (res.exported.length < n ? ` ${n - res.exported.length} had no video file.` : '')
      )
      onChanged()
    } catch (e) {
      setNote(`Export failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy('')
    }
  }

  const markExported = async (exported: boolean): Promise<void> => {
    if (working.current) return
    const { ok, failed } = await each('star', (c) => api.patchClip(c.id, { exported }))
    setNote(`${exported ? 'Starred' : 'Unstarred'} ${ok.length} clip${ok.length === 1 ? '' : 's'}.${failNote(failed)}`)
    onChanged()
  }

  const btn = 'btn-ghost !py-1 !px-3 text-xs'
  const disabled = busy !== ''

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={n > 0 && n === total}
            ref={(el) => {
              if (el) el.indeterminate = n > 0 && n < total
            }}
            onChange={(e) => (e.target.checked ? onSelectAll() : onClear())}
            disabled={total === 0}
          />
          Select all ({total})
        </label>
        {n > 0 && (
          <>
            <span className="font-medium text-accent">{n} selected</span>
            <button className={btn} disabled={disabled} onClick={() => void rerender()}>
              {busy === 'rerender' ? 'Queueing…' : 'Re-render'}
            </button>
            <button ref={brandBtn} className={btn} disabled={disabled} onClick={() => setBrandOpen(!brandOpen)}>
              Apply branding ▾
            </button>
            {/* Only with YouTube connected for playlists. */}
            {playlistsAvailable && (
              <button
                ref={playlistBtn}
                className={btn}
                disabled={disabled}
                onClick={() => setPlaylistOpen(!playlistOpen)}
                title="Choose the YouTube playlist these clips join when they are published"
              >
                {busy === 'playlist' ? 'Saving…' : 'Add to playlist ▾'}
              </button>
            )}
            <AddToCompilation
              videoId={picked[0].video_id}
              segments={picked.map((c) => ({ video_id: c.video_id, start: c.start_s, end: c.end_s }))}
              label={`Add ${n} to compilation`}
              className={btn}
            />
            <button ref={exportBtn} className={btn} disabled={disabled} onClick={() => setExportOpen(!exportOpen)}>
              {busy === 'export' ? 'Exporting…' : 'Export ▾'}
            </button>
            <button className="text-muted hover:text-red-400 px-2" disabled={disabled} onClick={() => void remove()}>
              {busy === 'delete' ? 'Deleting…' : 'Delete'}
            </button>
            <button className="text-muted hover:text-ink px-2" onClick={onClear}>
              Clear
            </button>
          </>
        )}
      </div>

      <Popover anchor={brandBtn} open={brandOpen} onClose={() => setBrandOpen(false)} width={260} label="Apply branding">
        <div className="space-y-0.5 p-1">
          {profiles.map((p) => (
            <button
              key={p.id}
              className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised truncate"
              onClick={() => void applyProfile(p.id)}
            >
              {p.name}
            </button>
          ))}
          <button
            className="w-full text-left px-2 py-1.5 rounded-md text-sm text-muted hover:bg-raised"
            onClick={() => void applyProfile(null)}
          >
            No branding
          </button>
        </div>
      </Popover>

      <Popover anchor={playlistBtn} open={playlistOpen} onClose={() => setPlaylistOpen(false)} width={300} label="Add to playlist">
        <div className="space-y-0.5 p-1 max-h-72 overflow-y-auto">
          <p className="px-2 py-1 text-[11px] text-muted">
            Applied when each clip is published to YouTube, and linked in its description.
          </p>
          {playlists.map((p) => (
            <button
              key={p.id}
              className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised truncate"
              onClick={() => void applyPlaylist(p.id, p.title)}
            >
              {p.title} <span className="text-muted">({p.count})</span>
            </button>
          ))}
          {playlists.length === 0 && (
            <p className="px-2 py-1.5 text-sm text-muted">This channel has no playlists yet.</p>
          )}
          <div className="border-t border-raised/60 my-1" />
          <button
            className="w-full text-left px-2 py-1.5 rounded-md text-sm text-muted hover:bg-raised"
            onClick={() => void applyPlaylist('', '')}
          >
            Clear the playlist (use the creator’s rule)
          </button>
        </div>
      </Popover>

      <Popover anchor={exportBtn} open={exportOpen} onClose={() => setExportOpen(false)} width={280} label="Export">
        <div className="space-y-0.5 p-1">
          <button
            className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised"
            onClick={() => void exportLocal()}
          >
            Local — save to your export folder
          </button>
          <div className="px-2 py-1.5 rounded-md text-sm hover:bg-raised flex items-center justify-between gap-2">
            <span>Cloud or folder</span>
            <SendMenu
              items={picked.map((c) => ({ kind: 'clip' as const, id: c.id }))}
              destinations={destinations ?? []}
              onSent={(m) => {
                setExportOpen(false)
                setNote(m)
              }}
              label="Choose…"
            />
          </div>
          {publishReady && (
            <>
              <div className="border-t border-raised/60 my-1" />
              <button
                className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised"
                onClick={() => {
                  setExportOpen(false)
                  onPublish(picked, 'schedule')
                }}
              >
                Publish on my posting schedule ↗
                <span className="block text-xs text-muted">Queued a few a day, at your best times</span>
              </button>
              <button
                className="w-full text-left px-2 py-1.5 rounded-md text-sm hover:bg-raised"
                onClick={() => {
                  setExportOpen(false)
                  onPublish(picked, 'now')
                }}
              >
                Publish now ↗
                <span className="block text-xs text-muted">Everything goes out immediately</span>
              </button>
            </>
          )}
          <div className="border-t border-raised/60 my-1" />
          <button className="w-full text-left px-2 py-1.5 rounded-md text-xs text-muted hover:bg-raised" onClick={() => void markExported(true)}>
            Star all as exported
          </button>
          <button className="w-full text-left px-2 py-1.5 rounded-md text-xs text-muted hover:bg-raised" onClick={() => void markExported(false)}>
            Unstar all
          </button>
        </div>
      </Popover>

      {note && <p className="text-sm text-accent">{note}</p>}
    </div>
  )
}
