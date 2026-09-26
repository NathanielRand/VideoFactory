import { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import ClipStudio from './pages/ClipStudio'
import Compilations from './pages/Compilations'
import Creators from './pages/Creators'
import Models from './pages/Models'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Watch from './pages/Watch'
import FeedbackHub from './components/FeedbackHub'
import ModelSwitcher from './components/ModelSwitcher'
import SetupWizard, { setupDone } from './components/SetupWizard'
import UpdateBanner from './components/UpdateBanner'
import { activeLocale, t } from './lib/i18n'
import { api } from './lib/api'
import type { StudioEvent } from './lib/types'
import { useEvents } from './lib/useEvents'
import { useQueueNotifications } from './lib/queueNotifications'
import logo from './assets/logo.png'

type Page = 'dashboard' | 'queue' | 'watch' | 'studio' | 'compilations' | 'creators' | 'models' | 'settings'

// Video Factory is a fork of Clips Kitty (AGPL-3.0). Until this fork has a
// public repo of its own, the source offer points at the upstream it is based on.
const UPSTREAM_URL = 'https://github.com/ColinGPT9/clips-studio'

const NAV: { id: Page; label: string; icon: string }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: '◧' },
  { id: 'queue', label: 'Queue', icon: '≡' },
  { id: 'watch', label: 'Watched channels', icon: '◎' },
  // Renamed in 1.1.3. The previous label matched an existing product closely
  // enough to block the Microsoft Store listing, so do not change it back —
  // "Clip Editor" is also just what the page is.
  { id: 'studio', label: 'Clip Editor', icon: '✂' },
  { id: 'compilations', label: 'Compilations', icon: '▦' },
  { id: 'creators', label: 'Creators', icon: '◉' },
  { id: 'models', label: 'Models', icon: '⬢' },
  { id: 'settings', label: 'Settings', icon: '⚙' }
]

// Sidebar collapse. Until the user picks, it follows the window: collapsed
// below NARROW_PX (a portrait monitor, a half-screen snap), open above.
// Once they toggle it, their choice sticks across launches.
const SIDEBAR_KEY = 'sidebar-collapsed'
const NARROW_PX = 1280

function readSidebarPref(): boolean | null {
  try {
    const raw = localStorage.getItem(SIDEBAR_KEY)
    return raw === null ? null : raw === '1'
  } catch {
    return null
  }
}

function useSidebarCollapsed(): [boolean, () => void] {
  const [pref, setPref] = useState<boolean | null>(readSidebarPref)
  const [narrow, setNarrow] = useState(() => window.innerWidth < NARROW_PX)
  useEffect(() => {
    const onResize = (): void => setNarrow(window.innerWidth < NARROW_PX)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  const collapsed = pref ?? narrow
  const toggle = (): void => {
    setPref(!collapsed)
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? '0' : '1')
    } catch {
      // Private storage off: the toggle still works for this session.
    }
  }
  return [collapsed, toggle]
}

export interface StudioTarget {
  videoId: string
  clipId?: number
}

export default function App(): JSX.Element {
  const [page, setPage] = useState<Page>('dashboard')
  const [collapsed, toggleSidebar] = useSidebarCollapsed()
  const [studioTarget, setStudioTarget] = useState<StudioTarget | null>(null)
  const [creatorTarget, setCreatorTarget] = useState<number | null>(null)
  // First run: walk the creator through what the installer can't bundle
  // (Ollama, a model) before they hit a video that fails for want of it.
  // Settings can re-open it, so this is not a one-shot.
  const [wizard, setWizard] = useState(!setupDone())
  useEffect(() => {
    const open = (): void => setWizard(true)
    window.addEventListener('open-setup-wizard', open)
    return () => window.removeEventListener('open-setup-wizard', open)
  }, [])
  // Jump to the queue from anywhere (the Generate bar links here after
  // queueing). Same window-event mechanism as the setup wizard above —
  // this app navigates by state, not a router.
  useEffect(() => {
    const open = (): void => setPage('queue')
    window.addEventListener('open-queue', open)
    return () => window.removeEventListener('open-queue', open)
  }, [])
  // Same door for Settings: the editor's YouTube panel sends people here to
  // connect an account, rather than duplicating the setup flow inside a tab.
  useEffect(() => {
    const open = (): void => setPage('settings')
    window.addEventListener('open-settings', open)
    return () => window.removeEventListener('open-settings', open)
  }, [])
  // And Models: Settings → AI links here to manage local models.
  useEffect(() => {
    const open = (): void => setPage('models')
    window.addEventListener('open-models', open)
    return () => window.removeEventListener('open-models', open)
  }, [])
  // Whether any AI runs in the cloud on the user's own key, so the sidebar's
  // "100% local" line is only ever shown when it is true.
  const [cloudAI, setCloudAI] = useState('')
  useEffect(() => {
    const read = (): void => {
      api
        .ai()
        .then((s) => {
          const label = (id: string): string => s.providers.find((p) => p.id === id)?.label ?? id
          const parts: string[] = []
          if (!s.active.local) parts.push(`${t('AI')}: ${label(s.active.provider)}`)
          if (s.transcription.backend !== 'local') {
            parts.push(`${t('transcription')}: ${label(s.transcription.backend)}`)
          }
          setCloudAI(parts.join(', '))
        })
        .catch(() => setCloudAI(''))
    }
    read()
    const id = setInterval(read, 30000)
    return () => clearInterval(id)
  }, [])
  // Mounted at the shell, not on the queue page: the point of a notification
  // is to reach someone who is NOT looking at the queue.
  useQueueNotifications()
  // How many channels are being watched, for the live dot beside "Watched
  // channels", so it shows from every page that the PC is on the job.
  const [watching, setWatching] = useState(0)
  useEffect(() => {
    const load = (): void => {
      api
        .automation()
        .then((s) => setWatching(s.enabled ? s.watching : 0))
        .catch(() => setWatching(0))
    }
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])
  useEvents((e: StudioEvent) => {
    if (e.type === 'automation' && !('activity' in e) && !('doing' in e)) {
      api
        .automation()
        .then((s) => setWatching(s.enabled ? s.watching : 0))
        .catch(() => {})
    }
  })
  // Language switches re-render the tree IN PLACE (no reload): the page
  // state lives here, so the user stays wherever they were (e.g. Settings).
  const [locale, setLocale] = useState(activeLocale())
  useEffect(() => {
    const onLang = (): void => setLocale(activeLocale())
    window.addEventListener('app-language-changed', onLang)
    return () => window.removeEventListener('app-language-changed', onLang)
  }, [])

  const openInStudio = (videoId: string, clipId?: number): void => {
    setStudioTarget({ videoId, clipId })
    setPage('studio')
  }

  // A watched channel's card opens its creator profile, already selected.
  const openCreator = (creatorId: number): void => {
    setCreatorTarget(creatorId)
    setPage('creators')
  }

  return (
    <div className="flex h-screen" key={locale}>
      <aside
        className={`${
          collapsed ? 'w-14' : 'w-52'
        } shrink-0 bg-surface border-r border-raised/60 flex flex-col overflow-y-auto overflow-x-hidden`}
      >
        {/* Logo, then the name and tagline stacked beside it. `min-w-0` on
            the text column so a longer translated tagline wraps instead of
            pushing the logo out of the sidebar. Collapsed: the logo alone. */}
        <div className={`py-5 flex items-center gap-2.5 ${collapsed ? 'justify-center px-0' : 'px-5'}`}>
          <img
            src={logo}
            alt={collapsed ? 'Video Factory' : ''}
            width={34}
            height={34}
            className="shrink-0 w-[34px] h-[34px]"
          />
          {!collapsed && (
            <div className="min-w-0">
              <h1 className="text-lg font-bold leading-tight">
                Video <span className="text-accent">Factory</span>
              </h1>
              <p className="text-xs text-muted mt-px">{t('source to post, one flow')}</p>
            </div>
          )}
        </div>
        <nav className={`flex-1 space-y-1 ${collapsed ? 'px-2' : 'px-3'}`}>
          {NAV.map((item) => (
            <button
              key={item.id}
              onClick={() => setPage(item.id)}
              title={collapsed ? t(item.label) : undefined}
              aria-label={collapsed ? t(item.label) : undefined}
              aria-current={page === item.id ? 'page' : undefined}
              className={`w-full text-left py-2.5 rounded-lg flex items-center gap-3 transition-colors ${
                collapsed ? 'justify-center px-0 relative' : 'px-3'
              } ${
                page === item.id
                  ? 'bg-accent/15 text-accent font-medium'
                  : 'text-muted hover:bg-raised hover:text-ink'
              }`}
            >
              <span aria-hidden>{item.icon}</span>
              {!collapsed && t(item.label)}
              {item.id === 'watch' && watching > 0 && (
                <span
                  className={`${collapsed ? 'absolute top-1.5 right-1.5' : 'ml-auto relative'} flex size-2.5`}
                  title={`${t('Watching')} ${watching}`}
                  aria-label={t('Watching')}
                >
                  <span className="absolute inline-flex size-full rounded-full bg-success opacity-60 animate-ping" />
                  <span className="relative inline-flex size-2.5 rounded-full bg-success" />
                </span>
              )}
            </button>
          ))}
        </nav>
        <div className={collapsed ? 'px-2 pb-1' : 'px-3 pb-1'}>
          <button
            onClick={toggleSidebar}
            title={collapsed ? t('Expand sidebar') : t('Collapse sidebar')}
            aria-label={collapsed ? t('Expand sidebar') : t('Collapse sidebar')}
            aria-expanded={!collapsed}
            className={`w-full text-left py-2.5 rounded-lg flex items-center gap-3 transition-colors text-muted hover:bg-raised hover:text-ink ${
              collapsed ? 'justify-center px-0' : 'px-3'
            }`}
          >
            <span aria-hidden>{collapsed ? '»' : '«'}</span>
            {!collapsed && t('Collapse')}
          </button>
        </div>
        {/* Pinned below the nav: reachable from every page — bugs don't
            only happen on the Dashboard. */}
        <div className={collapsed ? 'px-2 pb-1' : 'px-3 pb-1'}>
          <FeedbackHub compact={collapsed} />
        </div>
        {/* The model picker needs the width; collapsed, it is still on the
            Models page. */}
        {!collapsed && <ModelSwitcher />}
        {collapsed ? (
          // The AGPL source offer stays reachable when collapsed, as a link.
          <a
            href={UPSTREAM_URL}
            target="_blank"
            rel="noreferrer"
            title={`${t('Open source')} — ${t('based on Clips Kitty')} · AGPL-3.0`}
            aria-label={t('Open source')}
            className="py-4 border-t border-raised/60 text-center text-xs text-muted hover:text-accent transition-colors"
          >
            ↗
          </a>
        ) : (
          <div className="px-5 py-4 border-t border-raised/60">
            <a
              href={UPSTREAM_URL}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-muted hover:text-accent transition-colors"
            >
              <span className="font-semibold">{t('Open source')}</span> — {t('based on Clips Kitty')} ↗
            </a>
            {/* The AGPL expects anyone running the program to be able to find
                its source. The link above is that offer, so it names the
                licence rather than leaving "open source" to mean anything. */}
            <p className="text-[10px] text-muted/60 mt-1.5">
              {cloudAI
                ? `AGPL-3.0 · ${t('cloud AI on your key')} (${cloudAI})`
                : t('AGPL-3.0 · 100% local · no cloud AI')}
            </p>
          </div>
        )}
      </aside>
      <main className="flex-1 min-w-0 overflow-y-auto flex flex-col">
        <UpdateBanner />
        {page === 'dashboard' && <Dashboard onOpenInStudio={openInStudio} />}
        {page === 'queue' && <Queue onOpenInStudio={(videoId) => openInStudio(videoId)} />}
        {page === 'watch' && (
          <Watch onOpenInStudio={(videoId) => openInStudio(videoId)} onOpenCreator={openCreator} />
        )}
        {page === 'studio' && (
          <ClipStudio target={studioTarget} onTargetConsumed={() => setStudioTarget(null)} />
        )}
        {page === 'compilations' && <Compilations />}
        {page === 'creators' && (
          <Creators initialSelected={creatorTarget} onTargetConsumed={() => setCreatorTarget(null)} />
        )}
        {page === 'models' && <Models />}
        {page === 'settings' && <Settings />}
      </main>
      {wizard && (
        <SetupWizard
          onClose={() => {
            setWizard(false)
            setPage('studio')
          }}
        />
      )}
    </div>
  )
}
