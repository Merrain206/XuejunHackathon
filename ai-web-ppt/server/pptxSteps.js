// Guaranteed "elements appear one by one" for readers that ignore (or strip) the
// timing XML injected by pptxAnimate: the same effect is achieved with plain
// slides — each step shows one more element than the previous one, so pressing
// the space bar reveals the page progressively in ANY reader.
//
// This is the classic presenter technique; the trade-off is more slides.

const STEPPED_TYPES = new Set(['bullets', 'stats', 'chart', 'compare', 'timeline', 'table', 'closing'])

/** Elements revealed per step; 1 keeps the reveal granular. */
const DEFAULT_GROUP = 1
const MAX_STEPS_PER_SLIDE = 5

function chunk(list, size) {
  const out = []
  for (let index = 0; index < list.length; index += size) out.push(list.slice(index, index + size))
  return out
}

function stepCountOf(slide, groupSize) {
  const bullets = Array.isArray(slide.bullets) ? slide.bullets.length : 0
  const cards = Array.isArray(slide.cards) ? slide.cards.length : 0
  const stats = Array.isArray(slide.stats) ? slide.stats.length : 0
  const timeline = Array.isArray(slide.timeline) ? slide.timeline.length : 0
  const rows = slide.table?.rows?.length ?? 0
  const points = slide.chart?.series?.[0]?.data?.length ?? 0
  const compareItems = (slide.compare?.left?.items?.length ?? 0) + (slide.compare?.right?.items?.length ?? 0)

  const body = Math.max(bullets, cards, stats, timeline, rows, points, compareItems ? Math.ceil(compareItems / 2) : 0)
  if (!body) return 0
  // one step for the "title only" state plus one per chunk of body elements
  return Math.min(MAX_STEPS_PER_SLIDE, Math.ceil(body / groupSize))
}

function take(items, count) {
  return Array.isArray(items) ? items.slice(0, count) : items
}

/** One cumulative view of a slide: reveals `reveal` elements and nothing more. */
function stepView(slide, reveal, groupSize, isLast) {
  const count = reveal * groupSize
  const view = { ...slide }
  const panel = (items, key) => {
    const sliced = take(items, count)
    if (Array.isArray(items) && sliced.length < items.length) view[key] = sliced
  }

  panel(slide.bullets, 'bullets')
  panel(slide.cards, 'cards')
  panel(slide.stats, 'stats')
  panel(slide.timeline, 'timeline')

  if (slide.compare) {
    const perSide = Math.max(1, Math.floor(count / 2))
    view.compare = {
      left: { ...slide.compare.left, items: take(slide.compare.left?.items, perSide) },
      right: { ...slide.compare.right, items: isLast ? slide.compare.right?.items : take(slide.compare.right?.items, Math.max(1, count - perSide)) },
    }
  }
  if (slide.table?.rows) {
    view.table = { ...slide.table, rows: take(slide.table.rows, Math.max(1, count)) }
  }
  if (slide.chart?.series) {
    const points = Math.max(1, count)
    view.chart = {
      ...slide.chart,
      series: slide.chart.series.map((serie) => ({ ...serie, data: take(serie.data, points) })),
    }
  }

  // the picture, the conclusion and the sources only land on the final step
  if (!isLast) {
    delete view.takeaway
    delete view.notes
    view.image = null
    view.citations = []
  }
  return view
}

/**
 * Expands a deck into cumulative build steps.
 * @param {Array} slides normalized deck slides
 * @returns {{slides: Array, expanded: number, added: number}}
 */
export function expandBuildSteps(slides = [], { groupSize = DEFAULT_GROUP } = {}) {
  const out = []
  let expanded = 0
  for (const slide of slides) {
    const type = slide?.type || 'bullets'
    const steps = STEPPED_TYPES.has(type) ? stepCountOf(slide, groupSize) : 0
    if (steps <= 1) {
      out.push(slide)
      continue
    }
    expanded += 1
    for (let reveal = 1; reveal <= steps; reveal += 1) {
      const isLast = reveal === steps
      out.push({
        ...stepView(slide, reveal, groupSize, isLast),
        buildStep: reveal,
        buildTotal: steps,
      })
    }
  }
  return { slides: out.map((slide, index) => ({ ...slide, index })), expanded, added: out.length - slides.length }
}

export { STEPPED_TYPES, MAX_STEPS_PER_SLIDE }
