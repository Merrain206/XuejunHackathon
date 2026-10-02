import { useEffect, useMemo, useState } from 'react'

import { imageCandidates } from '../../imageSource.js'
import { bodyLayoutFor, fullLayoutFor, withCitationMarks } from './Layouts.jsx'

/** CSS-gradient decoration used whenever a picture is unavailable. */
function Decor({ pattern = 'grid' }) {
  return <div className={`decor decor--${String(pattern || 'grid').toLowerCase()}`} aria-hidden="true" />
}

/**
 * Renders a slide picture with a fallback chain. Pictures are fetched through our
 * own proxy only; when that fails the slide silently degrades to a gradient
 * decoration — the failure is never shown to the user.
 */
function SlideImage({ image, resolvedSrc, variant, offline = false, onUnavailable }) {
  const candidates = useMemo(() => imageCandidates(image), [image?.prompt, image?.url])
  const [cursor, setCursor] = useState(0)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    setCursor(0)
    setFailed(false)
  }, [image?.prompt, image?.url, resolvedSrc])

  useEffect(() => {
    if (failed) onUnavailable?.()
  }, [failed, onUnavailable])

  const src = resolvedSrc || (offline ? '' : candidates[cursor])
  if (!image || image.placement === 'none' || !src || failed) return <Decor pattern={image?.pattern} />

  return (
    <img
      className={`slide-image slide-image--${variant}`}
      src={src}
      alt={image.alt || ''}
      loading="eager"
      decoding="async"
      onError={() => {
        if (!resolvedSrc && cursor + 1 < candidates.length) setCursor(cursor + 1)
        else setFailed(true)
      }}
    />
  )
}

function citationLabel(citation) {
  if (citation.publisher) return citation.publisher
  if (citation.url) {
    try {
      return new URL(citation.url).host
    } catch {
      return citation.title
    }
  }
  return '本文档'
}

function CitationsFooter({ slide }) {
  if (!slide.citations?.length) return null
  return (
    <div className="slide-citations">
      {slide.citations.map((citation) => (
        <span className="slide-citation" key={citation.n}>
          <span className="cite-mark">[{citation.n}]</span> {citationLabel(citation)}
        </span>
      ))}
    </div>
  )
}

export function slideAriaLabel(slide) {
  const bullets = Array.isArray(slide?.bullets) ? slide.bullets.length : 0
  return `第 ${(slide?.index ?? 0) + 1} 页 ${slide?.type || 'bullets'}：${slide?.title || ''}`
}

/**
 * One rendered page, in three explicit zones so every content page shares the
 * same hierarchy: title zone → body zone → conclusion/sources/page zone.
 * cover / section / quote / closing own the whole canvas instead.
 */
export default function SlideView({
  slide,
  active = false,
  resolvedImage,
  resolvedImages,
  showCitations = false,
  offline = false,
  total = 0,
}) {
  const type = slide?.type || slide?.kind || 'bullets'
  const layout = slide?.layout || ''
  const FullLayout = fullLayoutFor(type)
  const BodyLayout = bodyLayoutFor(type)
  const [mediaUnavailable, setMediaUnavailable] = useState(false)

  const sideSrc = resolvedImage ?? resolvedImages?.[slide?.index]
  // `cover-image` fills the page; `-split` layouts put the picture beside the text.
  const placement = layout === 'cover-image' ? 'background' : /-split$/.test(layout) ? 'side' : slide?.image?.placement
  const hasBackground = Boolean(slide?.image) && placement === 'background'
  // An empty media column would read as a broken image: collapse to one column.
  const hasSide =
    Boolean(slide?.image) &&
    placement === 'side' &&
    !mediaUnavailable &&
    !(offline && !sideSrc)
  const pageNumber = String((slide?.index ?? 0) + 1).padStart(2, '0')

  const image = (
    <figure className="slide-media">
      <SlideImage
        image={slide.image}
        resolvedSrc={sideSrc}
        variant="side"
        offline={offline}
        onUnavailable={() => setMediaUnavailable(true)}
      />
      {slide.image?.alt ? <figcaption className="slide-media-caption">{withCitationMarks(slide.image.alt)}</figcaption> : null}
    </figure>
  )

  const head = (
    <header className="slide-head">
      {slide.eyebrow ? <div className="slide-eyebrow">{withCitationMarks(slide.eyebrow)}</div> : null}
      <h2 className="slide-title">{withCitationMarks(slide.title)}</h2>
      {slide.subtitle ? <p className="slide-subtitle">{withCitationMarks(slide.subtitle)}</p> : null}
    </header>
  )

  const body = FullLayout ? <FullLayout slide={slide} layout={layout} /> : <BodyLayout slide={slide} layout={layout} />

  const foot =
    slide?.takeaway || slide?.citations?.length || total > 0 ? (
      <footer className="slide-foot">
        {slide?.takeaway ? (
          <p className="takeaway-bar">
            <span className="takeaway-mark" aria-hidden="true">
              ▸
            </span>
            <span>{withCitationMarks(slide.takeaway)}</span>
          </p>
        ) : null}
        <div className="slide-foot-row">
          {showCitations ? <CitationsFooter slide={slide} /> : <span className="slide-foot-spacer" />}
          {total > 0 ? (
            <span className="slide-page" aria-hidden="true">
              {pageNumber}
              <span className="slide-page-total"> / {String(total).padStart(2, '0')}</span>
            </span>
          ) : null}
        </div>
      </footer>
    ) : null

  return (
    <section
      className={`slide slide--${type}${layout ? ` slide--layout-${layout}` : ''}${active ? ' is-active' : ''}`}
      data-index={slide?.index ?? 0}
      data-type={type}
      data-layout={layout || type}
      data-kind={type}
      aria-label={slideAriaLabel(slide)}
      aria-hidden={active ? undefined : 'true'}
    >
      {hasBackground ? (
        <div className="slide-bg">
          <SlideImage image={slide.image} resolvedSrc={sideSrc} variant="background" offline={offline} />
          <span className="slide-scrim" aria-hidden="true" />
        </div>
      ) : null}
      <span className="slide-texture" aria-hidden="true" />

      <div className="slide-inner">
        {FullLayout ? (
          <>
            <div className="slide-body slide-body--full">{body}</div>
            {foot}
          </>
        ) : hasSide ? (
          <div className="slide-split">
            <div className="slide-main">
              {head}
              <div className="slide-body">{body}</div>
            </div>
            {image}
          </div>
        ) : (
          <>
            {head}
            <div className="slide-body">{body}</div>
          </>
        )}
        {FullLayout ? null : foot}
      </div>
    </section>
  )
}

export { withCitationMarks }
