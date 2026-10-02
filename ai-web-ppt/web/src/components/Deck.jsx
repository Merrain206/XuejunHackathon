import { designVars } from '../shared/design.js'
import SlideView from './slides/SlideView.jsx'

/**
 * The deck surface: applies the model's design tokens and renders every page.
 *
 * The very same component is used by the live presentation view and by
 * `renderToStaticMarkup` for the offline HTML export, so the two cannot drift.
 *
 * @param {{slides: Array, design: object, activeIndex?: number,
 *   resolvedImages?: Record<number, string>, showCitations?: boolean,
 *   className?: string}} props
 */
export default function Deck({
  slides = [],
  design = null,
  activeIndex = 0,
  resolvedImages = {},
  showCitations = false,
  offline = false,
  className = '',
}) {
  return (
    <div
      className={`deck-surface ${className}`.trim()}
      style={designVars(design)}
      data-style-name={design?.styleName || ''}
      data-pattern={design?.decor?.pattern || 'grid'}
      data-citations={showCitations ? 'on' : 'off'}
    >
      {slides.map((slide, index) => (
        <SlideView
          key={`${index}-${slide?.type || 'slide'}`}
          slide={slide}
          active={index === activeIndex}
          resolvedImage={resolvedImages[index]}
          resolvedImages={resolvedImages}
          showCitations={showCitations}
          offline={offline}
          total={slides.length}
        />
      ))}
      {!slides.length ? <div className="deck-empty">没有可演示的页面</div> : null}
    </div>
  )
}
