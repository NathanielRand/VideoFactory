import { useEffect, useRef, useState } from 'react'
import type { CreditStyle } from '../lib/compilations'
import { api } from '../lib/api'
import { CLIP_SIZES, CREDIT_SAMPLES } from '../lib/branding'
import type { CaptionStyle, CtaConfig, WatermarkConfig } from '../lib/types'
import { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'
import { CreditPreview, creditText, useImageAspect } from './CreditControls'

/** The branding a clip is about to be re-rendered with (credit plate,
 *  watermark, call to action, captions) drawn over the clip's own video, sized
 *  to whatever the video is showing. Fills its parent, so put it inside a
 *  `relative` box around the <video>. */
export default function ClipBrandingOverlay({
  watermark,
  credit,
  captions,
  cta,
  landscape
}: {
  watermark: WatermarkConfig | null
  credit: CreditStyle | null
  captions: CaptionStyle | null
  cta: CtaConfig | null | undefined
  landscape: boolean
}): JSX.Element {
  const boxRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const size = CLIP_SIZES[landscape ? '16:9' : '9:16']
  const c: CreditStyle = credit ?? { enabled: false }
  const [aspect] = useImageAspect(c.bg_image ? api.brandingAssetUrl(c.bg_image) : null)
  const text = creditText(c.template ?? 'Clip: {channel}', CREDIT_SAMPLES[0])
  // The video letterboxes inside its box (object-contain): fit the frame the
  // same way so the layers land where the render will put them.
  const width = Math.min(box.w, (box.h * size.width) / size.height)
  return (
    <div ref={boxRef} className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
      {width > 0 && (
        <CreditPreview
          overlay
          c={c}
          size={[size.width, size.height]}
          text={text}
          aspect={aspect}
          width={width}
          banner={watermark}
          showCredit={c.enabled ?? true}
          captions={captions ? { ...DEFAULT_CAPTION_STYLE, ...captions } : null}
          cta={cta}
        />
      )}
    </div>
  )
}
