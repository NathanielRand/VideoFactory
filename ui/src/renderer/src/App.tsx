import { useEffect, useState } from 'react'
import Branding from './pages/Branding'
import Cloud from './pages/Cloud'
import Analytics from './pages/Analytics'
import Dashboard from './pages/Dashboard'
import Editor, { type EditorTab } from './pages/Editor'
import Creators from './pages/Creators'
import Library from './pages/Library'
import Local from './pages/Local'
import Models from './pages/Models'
import Publish from './pages/Publish'
import Queue from './pages/Queue'
import Settings from './pages/Settings'
import Watch from './pages/Watch'
import FeedbackHub from './components/FeedbackHub'
import ModelSwitcher from './components/ModelSwitcher'
import ResourceMonitor from './components/ResourceMonitor'
import SetupWizard, { setupDone } from './components/SetupWizard'
import UpdateBanner from './components/UpdateBanner'
import { activeLocale, t } from './lib/i18n'
import { api } from './lib/api'
import type { StudioEvent } from './lib/types'
import { useEvents } from './lib/useEvents'
import { useQueueNotifications } from './lib/queueNotifications'
import { useQueueBadge, type QueueBadge } from './lib/queueBadge'
import logo from './assets/logo.png'

type Page =
  | 'dashboard'
  | 'library'
  | 'queue'
  | 'watch'
  | 'editor'
  | 'publish'
  | 'analytics'
  | 'cloud'
  | 'local'
  | 'creators'
  | 'branding'
  | 'models'
  | 'settings'

// Video Factory is a fork of Clips Kitty (AGPL-3.0). Until this fork has a
// public repo of its own, the source offer points at the upstream it is based on.
const UPSTREAM_URL = 'https://github.com/ColinGPT9/clips-studio'

interface NavItem {
  page: Page
  /** For the Editor's two entries: which tab they open. */
  tab?: EditorTab
  label: string
  icon: string
}

/** The sidebar follows the work: bring videos in, edit them, send them out.
 *  Numbered so the order reads as a flow; Setup is everything you configure
 *  once and come back to rarely.
 *
 *  Clips and Compilations are two entries but ONE page — the Editor, with a
 *  tab each — so either is a click from anywhere, and moving between them
 *  inside the Editor never loses your place. */
const NAV: { step?: string; title?: string; items: NavItem[] }[] = [
  {
    // Ungrouped, straight under Home: it covers everything, not one step.
    items: [
      { page: 'dashboard', label: 'Home', icon: '◧' },
      { page: 'analytics', label: 'Analytics', icon: '▥' }
    ]
  },
  {
    step: '1',
    title: 'Imports',
    items: [
      { page: 'library', label: 'Library', icon: '▤' },
      { page: 'queue', label: 'Queue', icon: '≡' },
      { page: 'watch', label: 'Watching', icon: '◎' }
    ]
  },
  {
    step: '2',
    title: 'Edit',
    items: [
      // Was "Clip Editor" until the tabs. The label before THAT matched an
      // existing product closely enough to block the Microsoft Store listing
      // (1.1.3), so do not bring it back. "Clips" is just what the tab is.
      { page: 'editor', tab: 'clips', label: 'Clips', icon: '✂' },
      { page: 'editor', tab: 'compilations', label: 'Compilations', icon: '▦' }
    ]
  },
  {
    step: '3',
    title: 'Exports',
    items: [
      { page: 'publish', label: 'Publish', icon: '↗' },
      { page: 'cloud', label: 'Cloud', icon: '☁' },
      { page: 'local', label: 'Local', icon: '⌂' }
    ]
  },
  {
    title: 'Setup',
    items: [
      { page: 'creators', label: 'Creators', icon: '◉' },
      { page: 'branding', label: 'Branding', icon: '◈' },
      { page: 'models', label: 'Models', icon: '⬢' },
      { page: 'settings', label: 'Settings', icon: '⚙' }
    ]
  }
]

/** The Queue link's live status. Open: "42%" and "+3" (jobs still waiting) in
 *  the row, with a thin bar along its bottom edge. Collapsed there is no room
 *  for text, so the same two facts become a count pill on the icon (running +
 *  waiting) and that bottom bar; the tooltip spells it out. */
function QueueChip({ badge, collapsed }: { badge: QueueBadge; collapsed: boolean }): JSX.Element {
  const total = badge.waiting + 1
  const tip = `${t('Queue')}: ${badge.percent}%${badge.label ? ` — ${badge.label}` : ''}${
    badge.waiting > 0 ? ` · ${badge.waiting} ${t('more waiting')}` : ''
  }`
  return (
    <>
      <span
        className={`absolute bottom-0.5 h-[3px] rounded-full bg-raised overflow-hidden ${
          collapsed ? 'inset-x-2' : 'inset-x-3'
        }`}
        role="progressbar"
        aria-valuenow={badge.percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={tip}
        title={tip}
      >
        <span
          className="block h-full bg-accent rounded-full transition-[width] duration-500"
          style={{ width: `${Math.max(4, badge.percent)}%` }}
        />
      </span>
      {collapsed ? (
        <span
          className="absolute top-0.5 right-0.5 min-w-4 h-4 px-1 rounded-full bg-accent text-base text-[10px] font-bold leading-4 text-center tabular-nums"
          title={tip}
        >
          {total}
        </span>
      ) : (
        <span className="ml-auto flex items-center gap-1 text-[11px] tabular-nums" title={tip}>
          <span className="px-1.5 rounded-full bg-accent/20 text-accent font-semibold">{badge.percent}%</span>
          {badge.waiting > 0 && (
            <span className="px-1.5 rounded-full bg-raised text-ink" aria-label={`${badge.waiting} ${t('more waiting')}`}>
              +{badge.waiting}
            </span>
          )}
        </span>
      )}
    </>
  )
}

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
  const [editorTab, setEditorTab] = useState<EditorTab>('clips')
  const [compilationTarget, setCompilationTarget] = useState<number | null>(null)
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
  // And the Local page, from the Cloud page's "Choose videos to send".
  useEffect(() => {
    const open = (): void => setPage('local')
    window.addEventListener('open-local', open)
    return () => window.removeEventListener('open-local', open)
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
  // And Branding: every per-video branding picker links here to make or
  // edit profiles, so there is one editor rather than one per screen.
  useEffect(() => {
    const open = (): void => setPage('branding')
    window.addEventListener('open-branding', open)
    return () => window.removeEventListener('open-branding', open)
  }, [])
  // And the posting schedule's editor, from a publish dialog's "Edit schedule".
  // The calendar may not be mounted yet, so it is asked again once it is.
  useEffect(() => {
    const open = (): void => {
      setPage('publish')
      window.setTimeout(() => window.dispatchEvent(new Event('schedule-editor-open')), 150)
    }
    window.addEventListener('open-publish-schedule', open)
    return () => window.removeEventListener('open-publish-schedule', open)
  }, [])
  // And the Library, from the note an import leaves under the Generate bar.
  useEffect(() => {
    const open = (): void => setPage('library')
    window.addEventListener('open-library', open)
    return () => window.removeEventListener('open-library', open)
  }, [])
  // And one compilation, from anywhere that just added something to it.
  useEffect(() => {
    const open = (e: Event): void => {
      const id = (e as CustomEvent<number>).detail
      if (typeof id === 'number') openCompilation(id)
    }
    window.addEventListener('open-compilation', open)
    return () => window.removeEventListener('open-compilation', open)
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
  const queueBadge = useQueueBadge()
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
    setEditorTab('clips')
    setPage('editor')
  }

  function openCompilation(id: number): void {
    setCompilationTarget(id)
    setEditorTab('compilations')
    setPage('editor')
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
          collapsed ? 'w-14' : 'w-60'
        } shrink-0 bg-surface border-r border-raised/60 flex flex-col overflow-y-auto overflow-x-hidden`}
      >
        {/* Logo, then the name and tagline stacked beside it. `min-w-0` on
            the text column so a longer translated tagline wraps instead of
            pushing the logo out of the sidebar. Collapsed: the logo alone. */}
        <div
          className={`py-5 flex items-center gap-2.5 ${collapsed ? 'justify-center px-0' : 'px-5'}`}
        >
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
        <nav className={`flex-1 ${collapsed ? 'px-2' : 'px-3'}`}>
          {NAV.map((group, g) => (
            <div
              key={g}
              className={g > 0 ? (collapsed ? 'mt-2 pt-2 border-t border-raised/60' : 'mt-4') : ''}
            >
              {group.title && !collapsed && (
                <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted/70 flex items-center gap-1.5">
                  {group.step && (
                    <span className="inline-grid place-items-center size-4 rounded-full bg-raised text-[9px] text-muted">
                      {group.step}
                    </span>
                  )}
                  {t(group.title)}
                </p>
              )}
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active =
                    page === item.page && (item.tab === undefined || item.tab === editorTab)
                  return (
                    <button
                      key={`${item.page}-${item.tab ?? ''}`}
                      onClick={() => {
                        if (item.tab) setEditorTab(item.tab)
                        setPage(item.page)
                      }}
                      title={collapsed ? t(item.label) : undefined}
                      aria-label={collapsed ? t(item.label) : undefined}
                      aria-current={active ? 'page' : undefined}
                      className={`w-full text-left py-2 rounded-lg flex items-center gap-3 transition-colors ${
                        collapsed ? 'justify-center px-0 relative' : 'px-3 relative'
                      } ${
                        active
                          ? 'bg-accent/15 text-accent font-medium'
                          : 'text-muted hover:bg-raised hover:text-ink'
                      }`}
                    >
                      <span aria-hidden>{item.icon}</span>
                      {!collapsed && t(item.label)}
                      {item.page === 'queue' && queueBadge.active && (
                        <QueueChip badge={queueBadge} collapsed={collapsed} />
                      )}
                      {item.page === 'watch' && watching > 0 && (
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
                  )
                })}
              </div>
            </div>
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
        {/* The activity chart and model picker need the width; collapsed,
            the model is still on the Models page. */}
        <ResourceMonitor collapsed={collapsed} />
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
              <span className="font-semibold">{t('Open source')}</span> —{' '}
              {t('based on Clips Kitty')} ↗
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
        {page === 'dashboard' && <Dashboard onOpenInStudio={openInStudio} onOpenAnalytics={() => setPage('analytics')} />}
        {page === 'queue' && <Queue onOpenInStudio={(videoId) => openInStudio(videoId)} />}
        {page === 'watch' && (
          <Watch onOpenInStudio={(videoId) => openInStudio(videoId)} onOpenCreator={openCreator} />
        )}
        {page === 'library' && (
          <Library
            onOpenClips={(videoId) => openInStudio(videoId)}
            onOpenCompilation={openCompilation}
          />
        )}
        {page === 'editor' && (
          <Editor
            tab={editorTab}
            onTab={setEditorTab}
            studioTarget={studioTarget}
            onStudioTargetConsumed={() => setStudioTarget(null)}
            compilationTarget={compilationTarget}
            onCompilationTargetConsumed={() => setCompilationTarget(null)}
          />
        )}
        {page === 'publish' && (
          <Publish
            onOpenClips={(videoId) => openInStudio(videoId)}
            onOpenCompilation={openCompilation}
            onOpenWatch={() => setPage('watch')}
            onOpenAnalytics={() => setPage('analytics')}
          />
        )}
        {page === 'analytics' && <Analytics />}
        {page === 'creators' && (
          <Creators
            initialSelected={creatorTarget}
            onTargetConsumed={() => setCreatorTarget(null)}
          />
        )}
        {page === 'cloud' && <Cloud />}
        {page === 'local' && <Local />}
        {page === 'branding' && <Branding />}
        {page === 'models' && <Models />}
        {page === 'settings' && <Settings />}
      </main>
      {wizard && (
        <SetupWizard
          onClose={() => {
            setWizard(false)
            // Home is where the Generate bar is: the wizard's last words
            // tell them to paste a link there.
            setPage('dashboard')
          }}
        />
      )}
    </div>
  )
}
