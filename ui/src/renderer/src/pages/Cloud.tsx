import { useEffect, useState } from 'react'
import { exportsApi, type CloudInfo, type SyncFolder } from '../lib/exports'
import { useExports } from '../components/delivery/useExports'
import DestinationRow, { PROVIDER_ICON } from '../components/delivery/DestinationRow'
import TransferList from '../components/delivery/TransferList'
import { t } from '../lib/i18n'

/** Where each sync client comes from, for a cloud not found on this PC. */
const GET_IT: Record<string, string> = {
  onedrive: 'https://www.microsoft.com/microsoft-365/onedrive/download',
  google_drive: 'https://www.google.com/drive/download/',
  dropbox: 'https://www.dropbox.com/install',
  icloud: 'https://support.apple.com/icloud',
  box: 'https://www.box.com/drive'
}

const SUBFOLDER = 'Video Factory'

/** One sync folder found on this PC, ready to add with a subfolder name. */
function SyncTile({
  folder,
  added,
  onAdded
}: {
  folder: SyncFolder
  added: boolean
  onAdded: () => void
}): JSX.Element {
  const [sub, setSub] = useState(SUBFOLDER)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const add = async (): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      await exportsApi.addDestination({
        kind: 'cloud_folder',
        provider: folder.provider,
        name: folder.label,
        target: folder.path,
        path: sub.trim()
      })
      onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`rounded-xl border p-3 space-y-2 ${added ? 'border-success/40 bg-success/5' : 'border-raised'}`}>
      <div className="flex items-center gap-3">
        <span className="grid size-9 place-items-center rounded-lg bg-raised text-accent text-lg">
          {PROVIDER_ICON[folder.provider] ?? '☁'}
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-sm">{folder.label}</p>
          <p className="text-xs text-muted truncate" title={folder.path}>
            {folder.path}
          </p>
        </div>
        {added && <span className="text-xs text-success">✓ {t('Added')}</span>}
      </div>
      {!added && (
        <div className="flex gap-2 items-center">
          <span className="text-xs text-muted shrink-0">{t('Into')}</span>
          <input
            className="input !py-1 text-sm"
            value={sub}
            aria-label={t('Subfolder')}
            onChange={(e) => setSub(e.target.value)}
          />
          <button className="btn-accent !px-3 !py-1 text-sm shrink-0" disabled={busy} onClick={() => void add()}>
            {t('Add')}
          </button>
        </div>
      )}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}

/** rclone: any of its remotes, and a folder inside it. */
function RcloneTile({ info, onAdded }: { info: CloudInfo['rclone']; onAdded: () => void }): JSX.Element {
  const [remote, setRemote] = useState(info.remotes[0]?.name ?? '')
  const [path, setPath] = useState('video-factory')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const add = async (): Promise<void> => {
    setBusy(true)
    setError('')
    try {
      await exportsApi.addDestination({
        kind: 'rclone',
        target: remote,
        path,
        provider: info.remotes.find((r) => r.name === remote)?.type ?? ''
      })
      onAdded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const link = (url: string, text: string): JSX.Element => (
    <button className="text-accent underline underline-offset-2" onClick={() => void window.studio?.openExternal(url)}>
      {text}
    </button>
  )

  return (
    <div className="rounded-xl border border-raised p-3 space-y-2 sm:col-span-2">
      <div className="flex items-center gap-3">
        <span className="grid size-9 place-items-center rounded-lg bg-raised text-accent text-lg">⇅</span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-sm">
            rclone{' '}
            <span className="text-xs font-normal text-muted">
              {info.available ? info.version : t('not installed')}
            </span>
          </p>
          <p className="text-xs text-muted">
            {t('S3, Cloudflare R2, Backblaze B2, Google Drive, SFTP and 70 more. rclone keeps the sign-in, not Video Factory.')}
          </p>
        </div>
      </div>
      {!info.available ? (
        <p className="text-xs text-muted">
          {t('Install rclone and set up a remote with')} <code className="text-ink">rclone config</code>,{' '}
          {t('then open this page again.')} {link('https://rclone.org/install/', t('Install rclone'))} ·{' '}
          {link('https://rclone.org/overview/', t('What it supports'))}
        </p>
      ) : info.remotes.length === 0 ? (
        <p className="text-xs text-muted">
          {t('rclone has no remotes yet. Add one with')} <code className="text-ink">rclone config</code>,{' '}
          {t('then open this page again.')}
        </p>
      ) : (
        <div className="flex gap-2 items-center flex-wrap">
          <select className="input !w-48 !py-1 text-sm" value={remote} onChange={(e) => setRemote(e.target.value)}>
            {info.remotes.map((r) => (
              <option key={r.name} value={r.name}>
                {r.name} ({r.type})
              </option>
            ))}
          </select>
          <span className="text-muted">:</span>
          <input
            className="input !py-1 text-sm flex-1 min-w-40"
            value={path}
            placeholder={t('folder or bucket/folder')}
            aria-label={t('Folder in the remote')}
            onChange={(e) => setPath(e.target.value)}
          />
          <button className="btn-accent !px-3 !py-1 text-sm" disabled={busy || !remote} onClick={() => void add()}>
            {busy ? t('Checking…') : t('Add')}
          </button>
        </div>
      )}
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}

/** Cloud: finished videos sent to cloud storage.
 *
 *  Video Factory holds no cloud passwords. A cloud's own desktop app syncs a
 *  folder on this PC, and a file copied there is uploaded by it; or rclone
 *  uploads to a remote it was set up with. Either way the sign-in is theirs. */
export default function Cloud(): JSX.Element {
  const { destinations, transfers, refresh, error } = useExports()
  const [info, setInfo] = useState<CloudInfo | null>(null)
  const [scanning, setScanning] = useState(false)

  const scan = async (): Promise<void> => {
    setScanning(true)
    try {
      setInfo(await exportsApi.cloud())
    } catch {
      setInfo({ sync_folders: [], rclone: { available: false, version: '', remotes: [] }, providers: {} })
    } finally {
      setScanning(false)
    }
  }
  useEffect(() => {
    void scan()
  }, [])

  const clouds = (destinations ?? []).filter((d) => d.kind !== 'folder')
  const cloudTransfers = transfers.filter((x) => x.destination_kind === 'cloud_folder' || x.destination_kind === 'rclone')
  const addedPaths = new Set(clouds.map((d) => d.target.toLowerCase()))
  const isAdded = (f: SyncFolder): boolean =>
    [...addedPaths].some((p) => p === f.path.toLowerCase() || p.startsWith(f.path.toLowerCase() + '\\') || p.startsWith(f.path.toLowerCase() + '/'))
  const found = new Set((info?.sync_folders ?? []).map((f) => f.provider))
  const missing = Object.entries(info?.providers ?? {}).filter(([id]) => !found.has(id))
  const sending = cloudTransfers.filter((x) => x.state === 'sending' || x.state === 'queued').length

  return (
    <div className="p-6 space-y-6 w-full max-w-5xl">
      <header className="flex items-start gap-4 flex-wrap">
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold">{t('Cloud')}</h1>
          <p className="text-sm text-muted mt-1 max-w-2xl">
            {t('Send finished videos to cloud storage. Your cloud’s own app, or rclone, does the uploading with its own sign-in: Video Factory never holds a cloud password.')}
          </p>
        </div>
        <button
          className="btn-ghost text-sm"
          onClick={() => window.dispatchEvent(new CustomEvent('open-local'))}
          title={t('Pick finished videos and send them to a cloud')}
        >
          {t('Choose videos to send')} →
        </button>
      </header>

      {/* ---- the clouds in use ---- */}
      <section className="card space-y-3" aria-label={t('Your clouds')}>
        <div className="flex items-baseline gap-3">
          <h2 className="font-semibold">{t('Your clouds')}</h2>
          {sending > 0 && (
            <span className="text-xs text-accent">
              {sending} {t(sending === 1 ? 'upload in progress' : 'uploads in progress')}
            </span>
          )}
        </div>
        {destinations === null ? (
          <p className="text-sm text-muted">{t('Loading…')}</p>
        ) : clouds.length === 0 ? (
          <p className="text-sm text-muted rounded-lg border border-dashed border-raised px-3 py-4 text-center">
            {t('No clouds yet. Add one below, then switch on New clips or New compilations to send them there as they finish.')}
          </p>
        ) : (
          <div className="space-y-2">
            {clouds.map((d) => (
              <DestinationRow key={d.id} dest={d} onChanged={() => void refresh()} />
            ))}
          </div>
        )}
      </section>

      {/* ---- adding one ---- */}
      <section className="card space-y-4" aria-label={t('Add a cloud')}>
        <div className="flex items-start gap-3">
          <div className="flex-1">
            <h2 className="font-semibold">{t('Add a cloud')}</h2>
            <p className="text-sm text-muted">
              {t('Found on this PC. Files go into a subfolder, and the cloud’s app uploads them.')}
            </p>
          </div>
          <button className="btn-ghost !px-2.5 !py-1 text-xs" disabled={scanning} onClick={() => void scan()}>
            {scanning ? t('Looking…') : t('Look again')}
          </button>
        </div>
        {info === null ? (
          <p className="text-sm text-muted">{t('Looking for cloud folders…')}</p>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              {info.sync_folders.map((f) => (
                <SyncTile key={f.path} folder={f} added={isAdded(f)} onAdded={() => void refresh()} />
              ))}
              <RcloneTile info={info.rclone} onAdded={() => void refresh()} />
            </div>
            {missing.length > 0 && (
              <div className="text-xs text-muted flex flex-wrap items-center gap-x-3 gap-y-1">
                <span>{t('Not found on this PC:')}</span>
                {missing.map(([id, label]) => (
                  <button
                    key={id}
                    className="text-accent underline underline-offset-2"
                    title={t('Install its desktop app and sign in, then look again')}
                    onClick={() => void window.studio?.openExternal(GET_IT[id])}
                  >
                    {t('Get')} {label}
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {/* ---- uploads ---- */}
      <section className="card space-y-3" aria-label={t('Uploads')}>
        <h2 className="font-semibold">{t('Uploads')}</h2>
        <p className="text-xs text-muted -mt-2">
          {t('For a sync folder, Done means the file is in the folder. The cloud’s own app then uploads it, and shows its progress.')}
        </p>
        {error && <p className="text-sm text-error">{error}</p>}
        <TransferList
          transfers={cloudTransfers}
          onChanged={() => void refresh()}
          empty="Nothing sent to a cloud yet."
        />
      </section>
    </div>
  )
}
