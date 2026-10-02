// Page-type renderers.
//
// Two families:
//   * FULL_LAYOUTS  — cover / section / quote / closing compose the whole canvas
//     themselves (they own their header).
//   * BODY_LAYOUTS  — everything else renders ONLY the body; the title zone and
//     the footer zone belong to SlideView, so every content page shares the same
//     hierarchy (label+title → body → conclusion/sources/page number).
//
// Structure only: colours, fonts, spacing and decor come from the design tokens.
import Chart from './Chart.jsx'

/** Render `[1]` markers in text as styled citation superscripts. */
export function withCitationMarks(text) {
  const value = String(text ?? '')
  return value.split(/(\[\d{1,2}\])/g).map((part, index) =>
    /^\[\d{1,2}\]$/.test(part) ? (
      <sup className="cite-mark" key={`cite-${index}`}>
        {part}
      </sup>
    ) : (
      part
    ),
  )
}

/* ------------------------------------------------------------------ atoms -- */

function BulletList({ items, className = '' }) {
  if (!items?.length) return null
  return (
    <ul className={`slide-bullets ${className}`.trim()}>
      {items.map((item, index) => (
        <li key={index}>
          <span className="slide-bullet-mark" aria-hidden="true" />
          <span>{withCitationMarks(item)}</span>
        </li>
      ))}
    </ul>
  )
}

function NumberedList({ items }) {
  if (!items?.length) return null
  return (
    <ol className="numbered-list">
      {items.map((item, index) => (
        <li key={index}>
          <span className="numbered-index" aria-hidden="true">
            {String(index + 1).padStart(2, '0')}
          </span>
          <span>{withCitationMarks(item)}</span>
        </li>
      ))}
    </ol>
  )
}

function CardGrid({ cards, bullets }) {
  const items = cards?.length ? cards : (bullets || []).map((text) => ({ title: '', text }))
  if (!items.length) return null
  return (
    <div className={`card-grid card-grid--${Math.min(items.length, 4)}`}>
      {items.map((card, index) => (
        <article className="info-card" key={index}>
          {!card.title ? (
            <span className="info-card-index" aria-hidden="true">
              {String(index + 1).padStart(2, '0')}
            </span>
          ) : null}
          {card.title ? <h3 className="info-card-title">{withCitationMarks(card.title)}</h3> : null}
          {card.text ? <p className="info-card-text">{withCitationMarks(card.text)}</p> : null}
        </article>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------ full canvas -- */

function CoverLayout({ slide, layout }) {
  return (
    <div className={`layout layout--cover layout--${layout}`}>
      <div className="cover-lead">
        <div className="cover-rule" aria-hidden="true" />
        <h1 className="slide-title slide-title--hero">{withCitationMarks(slide.title)}</h1>
        {slide.subtitle ? <p className="slide-subtitle slide-subtitle--hero">{withCitationMarks(slide.subtitle)}</p> : null}
      </div>
      {slide.bullets?.length ? <BulletList items={slide.bullets} className="slide-bullets--hero" /> : null}
    </div>
  )
}

function SectionLayout({ slide, layout }) {
  const number = slide.eyebrow || String((slide.index ?? 0) + 1).padStart(2, '0')
  return (
    <div className={`layout layout--section layout--${layout}`}>
      <div className="section-number" aria-hidden="true">
        {number}
      </div>
      <div className="section-body">
        <h2 className="slide-title slide-title--section">{withCitationMarks(slide.title)}</h2>
        {slide.subtitle ? <p className="slide-subtitle">{withCitationMarks(slide.subtitle)}</p> : null}
        <div className="section-bar" aria-hidden="true" />
      </div>
    </div>
  )
}

function QuoteLayout({ slide }) {
  const quote = slide.quote ?? { text: slide.title, attribution: '' }
  return (
    <div className="layout layout--quote">
      <div className="quote-mark" aria-hidden="true">
        &ldquo;
      </div>
      <blockquote className="quote-text">{withCitationMarks(quote.text)}</blockquote>
      {quote.attribution ? (
        <div className="quote-attribution">
          <span className="quote-rule" aria-hidden="true" />
          {withCitationMarks(quote.attribution)}
        </div>
      ) : null}
      <BulletList items={slide.bullets} className="slide-bullets--tight" />
    </div>
  )
}

function ClosingLayout({ slide, layout }) {
  const head = (
    <header className="slide-head">
      {slide.eyebrow ? <div className="slide-eyebrow">{withCitationMarks(slide.eyebrow)}</div> : null}
      <h2 className="slide-title">{withCitationMarks(slide.title)}</h2>
      {slide.subtitle ? <p className="slide-subtitle">{withCitationMarks(slide.subtitle)}</p> : null}
    </header>
  )
  if (layout === 'closing-cards') {
    return (
      <div className="layout layout--closing layout--closing-cards">
        {head}
        <CardGrid cards={slide.cards} bullets={slide.bullets} />
      </div>
    )
  }
  return (
    <div className="layout layout--closing layout--closing-cta">
      {head}
      <div className="closing-panel">
        <BulletList items={slide.bullets} />
      </div>
      {slide.cards?.length ? <CardGrid cards={slide.cards} /> : null}
    </div>
  )
}

/* ------------------------------------------------------------- body only -- */

function BulletsLayout({ slide, layout }) {
  if (layout === 'bullets-cards') return <CardGrid cards={slide.cards} bullets={slide.bullets} />
  if (layout === 'bullets-numbered') return <NumberedList items={slide.bullets} />
  return <BulletList items={slide.bullets} className={layout === 'bullets-split' ? 'slide-bullets--tight' : ''} />
}

function StatsLayout({ slide, layout }) {
  const stats = slide.stats ?? []
  if (layout === 'stat-cards') {
    return (
      <div className={`stat-grid stat-grid--${Math.min(stats.length, 4)}`}>
        {stats.map((stat, index) => (
          <article className="stat-card" key={index}>
            <div className="stat-value">
              <span className="stat-number">{stat.value}</span>
              {stat.unit ? <span className="stat-unit">{stat.unit}</span> : null}
            </div>
            <div className="stat-label">{withCitationMarks(stat.label)}</div>
            {stat.delta ? <div className="stat-delta">{withCitationMarks(stat.delta)}</div> : null}
          </article>
        ))}
      </div>
    )
  }
  return (
    <div className={`kpi-strip kpi-strip--${Math.min(stats.length, 4)}`}>
      {stats.map((stat, index) => (
        <div className="kpi" key={index}>
          <div className="kpi-value">
            <span className="kpi-number">{stat.value}</span>
            {stat.unit ? <span className="kpi-unit">{stat.unit}</span> : null}
          </div>
          <div className="kpi-label">{withCitationMarks(stat.label)}</div>
          {stat.delta ? <div className="kpi-delta">{withCitationMarks(stat.delta)}</div> : null}
        </div>
      ))}
    </div>
  )
}

function ChartLayout({ slide, layout }) {
  const chart = (
    <figure className="chart-frame">
      <Chart chart={slide.chart} />
    </figure>
  )
  if (layout === 'chart-split') {
    return (
      <div className="chart-split-grid">
        {chart}
        <div className="chart-side">
          <BulletList items={slide.bullets} className="slide-bullets--tight" />
        </div>
      </div>
    )
  }
  return (
    <div className="chart-stack">
      {chart}
      <BulletList items={slide.bullets} className="slide-bullets--tight" />
    </div>
  )
}

function CompareLayout({ slide, layout }) {
  const compare = slide.compare ?? { left: { title: '', items: [] }, right: { title: '', items: [] } }
  return (
    <div className={`compare-grid${layout === 'compare-table' ? ' compare-grid--table' : ''}`}>
      {[compare.left, compare.right].map((side, index) => (
        <section className={`compare-col compare-col--${index === 0 ? 'a' : 'b'}`} key={index}>
          <h3 className="compare-title">{withCitationMarks(side.title)}</h3>
          <BulletList items={side.items} className="slide-bullets--tight" />
        </section>
      ))}
    </div>
  )
}

function TimelineLayout({ slide, layout }) {
  const items = slide.timeline ?? []
  if (layout === 'timeline-cards') {
    return <CardGrid cards={items.map((item) => ({ title: item.title, text: item.text || item.label }))} />
  }
  const direction = layout === 'timeline-vertical' || items.length > 4 ? 'vertical' : 'horizontal'
  return (
    <ol className={`timeline timeline--${direction}`}>
      {items.map((item, index) => (
        <li className="timeline-item" key={index}>
          <span className="timeline-node" aria-hidden="true">
            {item.label || index + 1}
          </span>
          <div className="timeline-body">
            <div className="timeline-title">{withCitationMarks(item.title)}</div>
            {item.text ? <div className="timeline-text">{withCitationMarks(item.text)}</div> : null}
          </div>
        </li>
      ))}
    </ol>
  )
}

function TableLayout({ slide, layout }) {
  const table = slide.table ?? { headers: [], rows: [] }
  const grid = (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {table.headers.map((header, index) => (
              <th key={index}>{withCitationMarks(header)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className={cellIndex === 0 ? 'is-key' : ''}>
                  {withCitationMarks(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
  if (layout === 'table-split') {
    return (
      <div className="table-split-grid">
        {grid}
        <BulletList items={slide.bullets} className="slide-bullets--tight" />
      </div>
    )
  }
  return (
    <div className="table-stack">
      {grid}
      <BulletList items={slide.bullets} className="slide-bullets--tight" />
    </div>
  )
}

function ReferencesLayout({ slide }) {
  return (
    <div className="layout layout--references">
      <ol className="reference-list">
        {(slide.bullets ?? []).map((item, index) => {
          const match = /^\[(\d{1,2})\]\s*(.*)$/.exec(item)
          const number = match ? match[1] : String(index + 1)
          const rest = match ? match[2] : item
          const urlMatch = /(https?:\/\/\S+)/.exec(rest)
          const title = urlMatch ? rest.replace(urlMatch[1], '').replace(/[\s—-]+$/, '') : rest
          return (
            <li className="reference-item" key={index}>
              <span className="reference-number">[{number}]</span>
              <span className="reference-body">
                <span className="reference-title">{title}</span>
                {urlMatch ? (
                  <a className="reference-url" href={urlMatch[1]} target="_blank" rel="noreferrer noopener">
                    {urlMatch[1]}
                  </a>
                ) : null}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export const FULL_LAYOUTS = {
  cover: CoverLayout,
  section: SectionLayout,
  quote: QuoteLayout,
  closing: ClosingLayout,
}

export const BODY_LAYOUTS = {
  bullets: BulletsLayout,
  stats: StatsLayout,
  chart: ChartLayout,
  compare: CompareLayout,
  timeline: TimelineLayout,
  table: TableLayout,
  references: ReferencesLayout,
}

export function fullLayoutFor(type) {
  return FULL_LAYOUTS[type] ?? null
}

export function bodyLayoutFor(type) {
  return BODY_LAYOUTS[type] ?? BulletsLayout
}
