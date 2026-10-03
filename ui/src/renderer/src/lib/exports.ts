// Video Factory: exports — finished files, and the folders and clouds they go
// to. Types mirror delivery/store.py; routes are server/exports_api.py.
import { API_BASE } from './api'

export type DestinationKind = 'folder' | 'cloud_folder' | 'rclone'

export interface ExportFile {
  kind: 'clip' | 'compilation'
  /** clips.id, or compilation_renders.id for a compilation. */
  id: number
  /** Which format of a compilation render; '' for a clip. */
  canvas: string
  title: string
  /** The video a clip came from, or the compilation's name. */
  parent: string
  compilation_id?: number
  channel: string
  path: string
  bytes: number
  /** False when the file has been deleted from disk. */
  exists: boolean
  duration: number | null
  created_at: string
  /** A clip saved somewhere at least once. */
  saved: boolean
  /** Destinations it has reached. */
  sent_to: number[]
}

export interface Destination {
  id: number
  name: string
  kind: DestinationKind
  /** cloud_folder: onedrive, google_drive…; rclone: the remote's type. */
  provider: string
  /** A folder, or rclone "remote:path". */
  target: string
  auto_clips: boolean
  auto_compilations: boolean
  created_at: string
}

export interface Transfer {
  id: number
  destination_id: number
  destination_name: string | null
  destination_kind: DestinationKind | null
  via: 'copy' | 'rclone'
  target: string
  item_kind: 'clip' | 'compilation'
  item_id: number
  canvas: string
  name: string
  state: 'queued' | 'sending' | 'done' | 'failed'
  bytes: number
  sent: number
  percent: number
  error: string
  result: string
  created_at: string
  finished_at: string
}

export interface SyncFolder {
  provider: string
  label: string
  path: string
}

export interface CloudInfo {
  sync_folders: SyncFolder[]
  rclone: {
    available: boolean
    version: string
    remotes: { name: string; type: string }[]
    error?: string
  }
  providers: Record<string, string>
}

export interface ExportSummary {
  data_dir: string
  folders: Record<'downloads' | 'clips' | 'compilations' | 'transcripts', { files: number; bytes: number }>
  disk: { total: number; free: number } | null
}

export interface SendItem {
  kind: 'clip' | 'compilation'
  id: number
  canvas?: string
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
  if (!res.ok) {
    // FastAPI puts the readable reason in `detail`; show that, not the JSON.
    const body = await res.text().catch(() => '')
    let detail = body
    try {
      detail = JSON.parse(body).detail ?? body
    } catch {
      /* not JSON */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const exportsApi = {
  files: (kind: 'clips' | 'compilations') => request<ExportFile[]>(`/exports/files?kind=${kind}`),
  summary: () => request<ExportSummary>('/exports/summary'),
  cloud: () => request<CloudInfo>('/exports/cloud'),
  destinations: () => request<Destination[]>('/exports/destinations'),
  addDestination: (d: {
    kind: DestinationKind
    target: string
    path?: string
    name?: string
    provider?: string
    auto_clips?: boolean
    auto_compilations?: boolean
  }) => request<Destination>('/exports/destinations', json('POST', d)),
  patchDestination: (
    id: number,
    patch: { name?: string; auto_clips?: boolean; auto_compilations?: boolean }
  ) => request<Destination>(`/exports/destinations/${id}`, json('PATCH', patch)),
  deleteDestination: (id: number) =>
    request<{ deleted: boolean }>(`/exports/destinations/${id}`, { method: 'DELETE' }),
  checkDestination: (id: number) =>
    request<{ ok: boolean; problem: string }>(`/exports/destinations/${id}/check`, { method: 'POST' }),
  /** To a saved destination, or (a one-off) any folder. */
  send: (items: SendItem[], to: { destination_id: number } | { folder: string }) =>
    request<{ queued: number[] }>('/exports/send', json('POST', { items, ...to })),
  transfers: (limit = 100) => request<Transfer[]>(`/exports/transfers?limit=${limit}`),
  retry: (id: number) =>
    request<{ retrying: boolean }>(`/exports/transfers/${id}/retry`, { method: 'POST' }),
  drop: (id: number) => request<{ dropped: boolean }>(`/exports/transfers/${id}`, { method: 'DELETE' }),
  clearTransfers: () => request<{ cleared: number }>('/exports/transfers/clear', { method: 'POST' })
}

/** "1.4 GB", "820 MB", "12 KB". */
export function bytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`
  if (n >= 1e3) return `${Math.round(n / 1e3)} KB`
  return `${n} B`
}
