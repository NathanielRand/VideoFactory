import type { StudioTarget } from '../App'
import { t } from '../lib/i18n'
import ClipStudio from './ClipStudio'
import Compilations from './Compilations'

export type EditorTab = 'clips' | 'compilations'

const TABS: { id: EditorTab; label: string; hint: string }[] = [
  {
    id: 'clips',
    label: '✂ Clips',
    hint: 'Short clips the AI cut from a long video: trim, captions, reframe, export'
  },
  {
    id: 'compilations',
    label: '▦ Compilations',
    hint: 'One video built from many sources: order, trim, transitions, credits'
  }
]

/** Step 2: editing, in one place.
 *
 *  Clips and compilations are two different jobs — one video into many, and
 *  many into one — so each keeps its whole editor, unchanged. What changes is
 *  that they sit side by side as tabs of one Editor, and the tools cross over:
 *  a clip can be sent into a compilation from the Clips tab, and the Library
 *  can open either.
 *
 *  Both tabs stay mounted. Switching away from a half-built compilation to
 *  grab a clip, then switching back, must find it exactly as it was. */
export default function Editor({
  tab,
  onTab,
  studioTarget,
  onStudioTargetConsumed,
  compilationTarget,
  onCompilationTargetConsumed
}: {
  tab: EditorTab
  onTab: (tab: EditorTab) => void
  studioTarget: StudioTarget | null
  onStudioTargetConsumed: () => void
  compilationTarget: number | null
  onCompilationTargetConsumed: () => void
}): JSX.Element {
  return (
    // Compilations is a fixed-height, three-pane layout whose panes scroll on
    // their own; Clips is a long page that scrolls as a whole.
    <div className={`flex flex-col ${tab === 'compilations' ? 'h-full' : 'min-h-full'}`}>
      <div className="px-6 pt-5 flex items-end gap-6 border-b border-raised/60 shrink-0 flex-wrap">
        <h1 className="text-xl font-bold pb-2.5">{t('Editor')}</h1>
        <div className="flex gap-1" role="tablist" aria-label={t('Editor')}>
          {TABS.map((tb) => (
            <button
              key={tb.id}
              role="tab"
              aria-selected={tab === tb.id}
              title={t(tb.hint)}
              onClick={() => onTab(tb.id)}
              className={`px-4 py-2.5 text-sm -mb-px border-b-2 transition-colors ${
                tab === tb.id
                  ? 'border-accent text-accent font-medium'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              {t(tb.label)}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted pb-3 hidden lg:block">
          {t(TABS.find((tb) => tb.id === tab)?.hint ?? '')}
        </p>
      </div>
      <div className={tab === 'clips' ? 'flex-1 min-h-0' : 'hidden'} role="tabpanel">
        <ClipStudio target={studioTarget} onTargetConsumed={onStudioTargetConsumed} />
      </div>
      <div
        className={tab === 'compilations' ? 'flex-1 min-h-0 flex flex-col' : 'hidden'}
        role="tabpanel"
      >
        <Compilations target={compilationTarget} onTargetConsumed={onCompilationTargetConsumed} />
      </div>
    </div>
  )
}
