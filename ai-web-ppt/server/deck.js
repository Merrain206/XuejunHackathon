// Tolerant incremental parser + normalizer for the deck JSONL stream.
//
// Responsibilities beyond parsing:
//   * normalize the model's design tokens and per-type slide fields
//   * strip placeholder strings ("封面页", "配图：…") that must never be rendered
//   * enforce the readability floor: split any page with too many bullets
//   * reconcile citations against the sources we actually retrieved, so the deck
//     can never display an invented link, and numbers stay continuous
import { PAGE_TYPES, PLACEHOLDER_PATTERNS } from './prompt.js'
import { normalizeImagePrompt } from './images.js'

export const DEFAULT_MAX_BULLETS = 6
const MAX_ITEM_CHARS = 140
const FONT_ALLOWLIST = [
  'system-ui',
  'Noto Sans SC',
  'Source Han Sans SC',
  'Inter',
  'Arial',
  'Georgia',
  'Times New Roman',
  'Consolas',
  'ui-monospace',
]

/** Neutral fallback design used when the model omits or mangles the design line. */
export const DEFAULT_DESIGN = {
  styleName: '中性现代',
  rationale: '未收到模型给出的设计方案，使用中性现代风格保证可读性。',
  palette: {
    bg: '#0f1420',
    bgAlt: '#1b2436',
    fg: '#f2f5fb',
    muted: '#a3adc2',
    accent: '#6ea8fe',
    accent2: '#5fd4c0',
    surface: 'rgba(255, 255, 255, 0.06)',
    border: 'rgba(255, 255, 255, 0.14)',
  },
  typography: { heading: 'system-ui', body: 'system-ui', scale: 1, headingWeight: 700, letterSpacing: 0 },
  decor: { style: 'geometric', imageStyle: 'clean modern editorial illustration, soft gradients', radius: 14, pattern: 'grid' },
}

/* ------------------------------------------------------------------ text ---- */

function firstString(...values) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function clamp(value, min, max, fallback) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(max, Math.max(min, parsed))
}

function isHex(value) {
  return typeof value === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value.trim())
}

function safeColor(value, fallback) {
  if (isHex(value)) return value.trim()
  const text = String(value || '').trim()
  // allow rgba()/color-mix-free simple functional colors provided by the model
  if (/^(rgba?|hsla?)\([0-9a-z%.,\s/+-]+\)$/i.test(text)) return text
  return fallback
}

function placeholderCore(text) {
  return String(text || '')
    .trim()
    .replace(/^[\s\-–—*•·（(【\[「『]+/, '')
    .replace(/[\s。.；;，,）)】\]」』]+$/, '')
}

export function isPlaceholderText(text) {
  const core = placeholderCore(text)
  if (!core) return false
  return PLACEHOLDER_PATTERNS.some((pattern) => pattern.test(core))
}

/** Remove trailing inline placeholder markers such as 「（配图：xxx）」. */
function stripInlinePlaceholders(text) {
  return String(text || '')
    .replace(/[（(]\s*(配图|图片|图表|示意|此处[^)）]*)\s*[:：][^)）]*[)）]/g, '')
    .replace(/\[(图片|配图|图表|图标|image|chart)\]/gi, '')
    .trim()
}

function cleanText(value, { maxLength = MAX_ITEM_CHARS } = {}) {
  const text = stripInlinePlaceholders(firstString(value))
  if (!text || isPlaceholderText(text)) return ''
  return text.slice(0, maxLength)
}

/** Strip `[n]` citation markers for numbers that are not in `allowed`. */
export function stripUnsupportedMarkers(text, allowed) {
  return String(text || '').replace(/\[(\d{1,2})\]/g, (match, digits) =>
    allowed.has(Number(digits)) ? match : '',
  )
}

export function renumberMarkers(text, mapping) {
  return String(text || '').replace(/\[(\d{1,2})\]/g, (match, digits) => {
    const next = mapping.get(Number(digits))
    return next ? `[${next}]` : ''
  })
}

/* ---------------------------------------------------------------- design ---- */

export function normalizeDesign(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const palette = raw.palette && typeof raw.palette === 'object' ? raw.palette : {}
  const typography = raw.typography && typeof raw.typography === 'object' ? raw.typography : {}
  const decor = raw.decor && typeof raw.decor === 'object' ? raw.decor : {}
  const font = (value) => {
    const name = firstString(value).replace(/["']/g, '')
    if (!name) return ''
    const found = FONT_ALLOWLIST.find((item) => item.toLowerCase() === name.toLowerCase())
    return found || ''
  }
  const heading = font(typography.heading) || DEFAULT_DESIGN.typography.heading
  const body = font(typography.body) || heading || DEFAULT_DESIGN.typography.body
  return {
    styleName: cleanText(raw.styleName, { maxLength: 24 }) || DEFAULT_DESIGN.styleName,
    rationale: firstString(raw.rationale).slice(0, 200),
    palette: {
      bg: safeColor(palette.bg, DEFAULT_DESIGN.palette.bg),
      bgAlt: safeColor(palette.bgAlt ?? palette.bg_alt, DEFAULT_DESIGN.palette.bgAlt),
      fg: safeColor(palette.fg, DEFAULT_DESIGN.palette.fg),
      muted: safeColor(palette.muted, DEFAULT_DESIGN.palette.muted),
      accent: safeColor(palette.accent, DEFAULT_DESIGN.palette.accent),
      accent2: safeColor(palette.accent2 ?? palette.accent_2, DEFAULT_DESIGN.palette.accent2),
      surface: safeColor(palette.surface ?? palette.card, DEFAULT_DESIGN.palette.surface),
      border: safeColor(palette.border, DEFAULT_DESIGN.palette.border),
    },
    typography: {
      heading,
      body,
      scale: clamp(typography.scale, 0.85, 1.25, 1),
      headingWeight: clamp(typography.headingWeight ?? typography.heading_weight, 400, 900, 700),
      letterSpacing: clamp(typography.letterSpacing ?? typography.letter_spacing, -2, 4, 0),
    },
    decor: {
      style: cleanText(decor.style, { maxLength: 30 }) || DEFAULT_DESIGN.decor.style,
      imageStyle: firstString(decor.imageStyle ?? decor.image_style).slice(0, 200) || DEFAULT_DESIGN.decor.imageStyle,
      radius: clamp(decor.radius, 0, 40, DEFAULT_DESIGN.decor.radius),
      pattern: cleanText(decor.pattern, { maxLength: 20 }) || DEFAULT_DESIGN.decor.pattern,
    },
  }
}

/* ---------------------------------------------------------------- slides ---- */

function toBullets(value, maxItemChars = MAX_ITEM_CHARS) {
  const list = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(/\r?\n|[;；]/)
      : []
  return list
    .map((item) => {
      if (item && typeof item === 'object') return cleanText(item.text ?? item.content ?? item.title, { maxLength: maxItemChars })
      return cleanText(item, { maxLength: maxItemChars })
    })
    .filter(Boolean)
}

function normalizeStats(value, max = 4) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const label = cleanText(item.label ?? item.name ?? item.title, { maxLength: 40 })
      const number = firstString(item.value ?? item.number ?? item.amount).slice(0, 16)
      if (!label && !number) return null
      return {
        value: number,
        unit: firstString(item.unit).slice(0, 8),
        label: label || number,
        delta: firstString(item.delta ?? item.change).slice(0, 16),
      }
    })
    .filter(Boolean)
    .slice(0, max)
}

function normalizeChart(value) {
  if (!value || typeof value !== 'object') return null
  const kindRaw = firstString(value.kind ?? value.type).toLowerCase()
  const kind = ['bar', 'line', 'pie', 'donut', 'area'].includes(kindRaw) ? kindRaw : 'bar'
  const seriesRaw = Array.isArray(value.series) ? value.series : []
  const series = seriesRaw
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const points = (Array.isArray(item.data) ? item.data : [])
        .map((point) => {
          if (point && typeof point === 'object') {
            const label = cleanText(point.label ?? point.name, { maxLength: 24 })
            const value = Number(point.value ?? point.y)
            return Number.isFinite(value) ? { label: label || `#${value}`, value } : null
          }
          const value = Number(point)
          return Number.isFinite(value) ? { label: String(value), value } : null
        })
        .filter(Boolean)
        .slice(0, 12)
      if (!points.length) return null
      return { name: cleanText(item.name ?? item.label, { maxLength: 24 }) || '数据', data: points }
    })
    .filter(Boolean)
    .slice(0, 4)
  if (!series.length) return null
  return {
    kind,
    title: cleanText(value.title, { maxLength: 40 }),
    unit: firstString(value.unit).slice(0, 8),
    series,
  }
}

function normalizeCompare(value) {
  if (!value || typeof value !== 'object') return null
  const side = (input, fallbackTitle) => {
    if (!input || typeof input !== 'object') return null
    const items = toBullets(input.items ?? input.bullets ?? input.points, 60)
    const title = cleanText(input.title ?? input.label, { maxLength: 30 }) || fallbackTitle
    if (!items.length && !title) return null
    return { title, items: items.slice(0, 6) }
  }
  const left = side(value.left ?? value.a, 'A')
  const right = side(value.right ?? value.b, 'B')
  if (!left && !right) return null
  return { left: left ?? { title: 'A', items: [] }, right: right ?? { title: 'B', items: [] } }
}

function normalizeTimeline(value) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const title = cleanText(item.title ?? item.label ?? item.name, { maxLength: 40 })
      const text = cleanText(item.text ?? item.detail ?? item.description, { maxLength: 80 })
      if (!title && !text) return null
      return { label: firstString(item.label ?? item.time ?? item.step).slice(0, 12), title: title || text, text: text === title ? '' : text }
    })
    .filter(Boolean)
    .slice(0, 6)
}

function normalizeTable(value) {
  if (!value || typeof value !== 'object') return null
  const headers = (Array.isArray(value.headers) ? value.headers : [])
    .map((cell) => cleanText(cell, { maxLength: 30 }))
    .filter(Boolean)
    .slice(0, 4)
  const rows = (Array.isArray(value.rows) ? value.rows : [])
    .map((row) => (Array.isArray(row) ? row : []).map((cell) => cleanText(cell, { maxLength: 40 })).slice(0, 4))
    .filter((row) => row.some(Boolean))
    .slice(0, 6)
  if (!headers.length || !rows.length) return null
  return { headers, rows }
}

function normalizeQuote(value) {
  if (typeof value === 'string') {
    const text = cleanText(value, { maxLength: 160 })
    return text ? { text, attribution: '' } : null
  }
  if (!value || typeof value !== 'object') return null
  const text = cleanText(value.text ?? value.quote ?? value.content, { maxLength: 160 })
  if (!text) return null
  return { text, attribution: cleanText(value.attribution ?? value.author ?? value.source, { maxLength: 40 }) }
}

export function normalizeImage(value, fallbackAlt = '') {
  if (!value || typeof value !== 'object') return null
  // Keywords are used verbatim as an English image query / prompt, so anything
  // that is not usable English is dropped here (and logged by the image layer).
  const rawPrompt = firstString(value.prompt ?? value.keywords ?? value.query).replace(/\s+/g, ' ').slice(0, 240)
  const prompt = normalizeImagePrompt(rawPrompt)
  const placementRaw = firstString(value.placement ?? value.position).toLowerCase()
  const placement = ['background', 'side', 'none'].includes(placementRaw) ? placementRaw : prompt ? 'side' : 'none'
  const url = /^https?:\/\//i.test(firstString(value.url)) ? firstString(value.url) : ''
  if (!prompt && !url) return null
  return {
    prompt,
    rawPrompt: prompt ? '' : rawPrompt.slice(0, 80),
    url,
    placement: placement === 'none' && url ? 'side' : placement,
    alt: cleanText(value.alt, { maxLength: 60 }) || fallbackAlt,
  }
}

function normalizeCitations(value) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const title = cleanText(item.title ?? item.name, { maxLength: 90 })
      const url = /^https?:\/\//i.test(firstString(item.url)) ? firstString(item.url).slice(0, 300) : ''
      const n = Number.parseInt(item.n ?? item.index, 10)
      if (!title && !url) return null
      return { n: Number.isFinite(n) ? n : 0, title: title || url, url, publisher: cleanText(item.publisher, { maxLength: 30 }) }
    })
    .filter(Boolean)
    .slice(0, 12)
}

/** Layout variants the renderer implements, per page type. */
export const LAYOUT_BY_TYPE = {
  cover: ['cover-center', 'cover-split', 'cover-image'],
  section: ['section-number', 'section-split'],
  bullets: ['bullets-list', 'bullets-cards', 'bullets-split', 'bullets-numbered'],
  stats: ['kpi-strip', 'stat-cards'],
  chart: ['chart-full', 'chart-split'],
  compare: ['compare-columns', 'compare-table'],
  timeline: ['timeline-horizontal', 'timeline-vertical', 'timeline-cards'],
  table: ['table-full', 'table-split'],
  quote: ['quote-hero', 'quote-split'],
  references: ['references-list'],
  closing: ['closing-cards', 'closing-cta'],
}

export const DEFAULT_LAYOUT = {
  cover: 'cover-center',
  section: 'section-number',
  bullets: 'bullets-list',
  stats: 'kpi-strip',
  chart: 'chart-full',
  compare: 'compare-columns',
  timeline: 'timeline-vertical',
  table: 'table-full',
  quote: 'quote-hero',
  references: 'references-list',
  closing: 'closing-cta',
}

/** Layouts that want a picture; when the model picks one it should supply image. */
const LAYOUTS_WITH_IMAGE = new Set(['cover-split', 'cover-image', 'bullets-split', 'quote-split', 'chart-split', 'table-split'])

function normalizeLayout(value, type) {
  const raw = firstString(value).toLowerCase().replace(/[\s_]+/g, '-')
  const allowed = LAYOUT_BY_TYPE[type] || LAYOUT_BY_TYPE.bullets
  if (allowed.includes(raw)) return raw
  return DEFAULT_LAYOUT[type] || 'bullets-list'
}

function normalizeCards(value, max = 4) {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object') return null
      const title = cleanText(item.title ?? item.label ?? item.name, { maxLength: 24 })
      const text = cleanText(item.text ?? item.content ?? item.description ?? item.body, { maxLength: 80 })
      if (!title && !text) return null
      return { title: title || text.slice(0, 12), text: text === title ? '' : text }
    })
    .filter(Boolean)
    .slice(0, max)
}

export { LAYOUTS_WITH_IMAGE }

const TYPE_ALIASES = {
  content: 'bullets',
  list: 'bullets',
  points: 'bullets',
  text: 'bullets',
  title: 'cover',
  cover_page: 'cover',
  chapter: 'section',
  divider: 'section',
  metric: 'stats',
  metrics: 'stats',
  kpi: 'stats',
  numbers: 'stats',
  graph: 'chart',
  data: 'chart',
  comparison: 'compare',
  versus: 'compare',
  process: 'timeline',
  steps: 'timeline',
  roadmap: 'timeline',
  sources: 'references',
  summary: 'closing',
  end: 'closing',
}

export function normalizeType(raw, index, bullets) {
  const value = firstString(raw).toLowerCase().replace(/[\s-]+/g, '_')
  const resolved = TYPE_ALIASES[value] || value
  if (PAGE_TYPES.includes(resolved)) return resolved
  if (index === 0 && (bullets?.length ?? 0) <= 1) return 'cover'
  return 'bullets'
}

/**
 * Normalize one model object into a slide. Returns null when nothing usable is
 * left after cleaning (for example an object that only held placeholder text).
 */
export function normalizeSlide(raw, index = 0) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const bullets = toBullets(raw.bullets ?? raw.points ?? raw.items ?? raw.keyPoints ?? raw.content)
  const type = normalizeType(firstString(raw.type ?? raw.kind ?? raw.layout), index, bullets)
  let layout = normalizeLayout(firstString(raw.layout ?? raw.variant), type)
  const image = normalizeImage(raw.image, cleanText(raw.imageAlt, { maxLength: 60 }))
  // A layout that expects a picture must not render an empty media column.
  if (LAYOUTS_WITH_IMAGE.has(layout) && !image) layout = DEFAULT_LAYOUT[type] || 'bullets-list'

  const slide = {
    index,
    type,
    kind: type, // kept for older renderers/tests
    layout,
    eyebrow: cleanText(raw.eyebrow ?? raw.label ?? raw.kicker, { maxLength: 16 }),
    title: cleanText(raw.title ?? raw.heading ?? raw.name, { maxLength: 60 }),
    subtitle: cleanText(raw.subtitle ?? raw.sub, { maxLength: 90 }),
    takeaway: cleanText(raw.takeaway ?? raw.insight ?? raw.conclusion, { maxLength: 60 }),
    bullets,
    cards: normalizeCards(raw.cards ?? raw.blocks ?? raw.groups),
    stats: normalizeStats(raw.stats ?? raw.metrics ?? raw.kpis),
    chart: normalizeChart(raw.chart ?? raw.graph),
    compare: normalizeCompare(raw.compare ?? raw.comparison),
    timeline: normalizeTimeline(raw.timeline ?? raw.steps ?? raw.process),
    table: normalizeTable(raw.table),
    quote: normalizeQuote(raw.quote),
    image,
    citations: normalizeCitations(raw.citations ?? raw.sources),
    notes: cleanText(raw.notes ?? raw.speakerNotes, { maxLength: 400 }),
  }

  const hasBody =
    slide.bullets.length > 0 ||
    slide.cards.length > 0 ||
    slide.stats.length > 0 ||
    Boolean(slide.chart || slide.compare || slide.table || slide.quote) ||
    slide.timeline.length > 0

  if (!slide.title && !slide.subtitle && !hasBody && !slide.image) return null

  // Structure-aware minimal repair: a type that lost its body falls back to bullets.
  if (type === 'stats' && !slide.stats.length && (slide.bullets.length || slide.cards.length)) {
    slide.type = slide.kind = 'bullets'
  }
  if (type === 'chart' && !slide.chart) slide.type = slide.kind = slide.bullets.length ? 'bullets' : 'section'
  if (type === 'compare' && !slide.compare) slide.type = slide.kind = 'bullets'
  if (type === 'timeline' && !slide.timeline.length) slide.type = slide.kind = 'bullets'
  if (type === 'table' && !slide.table) slide.type = slide.kind = 'bullets'
  if (type === 'quote' && !slide.quote) slide.type = slide.kind = 'bullets'
  if (slide.type !== type) slide.layout = normalizeLayout('', slide.type)
  return slide
}

/* ------------------------------------------------------------ postprocess ---- */

const TYPE_FALLBACK_TITLE = {
  cover: '演示文稿',
  section: '章节',
  bullets: '要点',
  stats: '关键指标',
  chart: '数据',
  compare: '对比',
  timeline: '流程',
  table: '信息表',
  quote: '观点',
  references: '参考来源',
  closing: '总结',
}

/** Split pages that exceed the bullet ceiling into continuation pages. */
export function splitOverflowSlides(slides, maxBullets = DEFAULT_MAX_BULLETS) {
  const out = []
  let splits = 0
  for (const slide of slides) {
    if (slide.bullets.length <= maxBullets) {
      out.push(slide)
      continue
    }
    const chunks = []
    for (let i = 0; i < slide.bullets.length; i += maxBullets) chunks.push(slide.bullets.slice(i, i + maxBullets))
    chunks.forEach((chunk, chunkIndex) => {
      splits += 1
      out.push({
        ...slide,
        bullets: chunk,
        title: chunkIndex === 0 ? slide.title : `${slide.title || TYPE_FALLBACK_TITLE[slide.type]}（续）`,
        subtitle: chunkIndex === 0 ? slide.subtitle : '',
        image: chunkIndex === 0 ? slide.image : null,
        // Continuation pages keep only the citations they still reference.
        citations: slide.citations,
      })
    })
  }
  return { slides: out.map((slide, index) => ({ ...slide, index })), splits }
}

function collectTextFields(slide) {
  return [
    ['title', slide.title],
    ['subtitle', slide.subtitle],
    ['notes', slide.notes],
    ['chart', slide.chart?.title],
    ['quote', slide.quote?.text],
    ['quoteAttr', slide.quote?.attribution],
  ].filter(([, value]) => typeof value === 'string' && value)
}

/**
 * Strip unsupported `[n]` markers, drop citations we cannot verify, renumber the
 * rest continuously and guarantee a references page that matches reality.
 */
export function reconcileCitations(slides, { allowedUrls = [], showCitations = false } = {}) {
  const allowed = new Set(allowedUrls.filter(Boolean))
  const stats = { dropped: 0, used: 0 }

  if (!showCitations) {
    for (const slide of slides) {
      slide.citations = []
      slide.bullets = slide.bullets.map((item) => stripUnsupportedMarkers(item, new Set()).trim())
      for (const [key, value] of collectTextFields(slide)) {
        const cleaned = stripUnsupportedMarkers(value, new Set()).trim()
        if (key === 'title') slide.title = cleaned
        else if (key === 'subtitle') slide.subtitle = cleaned
        else if (key === 'notes') slide.notes = cleaned
        else if (key === 'chart' && slide.chart) slide.chart.title = cleaned
        else if (key === 'quote' && slide.quote) slide.quote.text = cleaned
        else if (key === 'quoteAttr' && slide.quote) slide.quote.attribution = cleaned
      }
    }
    return { ...stats, slides: slides.filter((slide) => slide.type !== 'references') }
  }

  // Pass 1: drop unverifiable citations (unknown URL), keep the rest in order.
  const mapping = new Map()
  const registry = new Map() // new number -> citation
  let nextNumber = 1
  for (const slide of slides) {
    const kept = []
    for (const citation of slide.citations) {
      const verifiable = citation.url === '' || allowed.has(citation.url)
      if (!verifiable) {
        stats.dropped += 1
        continue
      }
      const key = `${citation.title}|${citation.url}`
      let number = mapping.get(citation.n)
      const existingByKey = [...registry.values()].find((item) => `${item.title}|${item.url}` === key)
      if (existingByKey) {
        number = existingByKey.n
      } else {
        number = nextNumber
        nextNumber += 1
        registry.set(number, { ...citation, n: number })
      }
      if (citation.n) mapping.set(citation.n, number)
      kept.push({ ...citation, n: number })
    }
    slide.citations = kept
  }
  stats.used = registry.size

  // Pass 2: rewrite markers to the verified numbering, dropping the rest.
  const rewrite = (text) => renumberMarkers(text, mapping).replace(/\s+([。，、；：!?！？])/g, '$1').trim()
  for (const slide of slides) {
    slide.bullets = slide.bullets.map((item) => rewrite(item)).filter(Boolean)
    slide.title = rewrite(slide.title)
    slide.subtitle = rewrite(slide.subtitle)
    slide.notes = rewrite(slide.notes)
    if (slide.chart) slide.chart.title = rewrite(slide.chart.title)
    if (slide.quote) {
      slide.quote.text = rewrite(slide.quote.text)
      slide.quote.attribution = rewrite(slide.quote.attribution)
    }
    if (slide.compare) {
      for (const side of [slide.compare.left, slide.compare.right]) {
        side.title = rewrite(side.title)
        side.items = side.items.map((item) => rewrite(item)).filter(Boolean)
      }
    }
    if (slide.timeline) {
      slide.timeline = slide.timeline.map((item) => ({
        ...item,
        title: rewrite(item.title),
        text: rewrite(item.text),
      }))
    }
    if (slide.table) {
      slide.table.headers = slide.table.headers.map((cell) => rewrite(cell))
      slide.table.rows = slide.table.rows.map((row) => row.map((cell) => rewrite(cell)))
    }
  }

  // Pass 3: one authoritative references page, always last.
  const withoutReferences = slides.filter((slide) => slide.type !== 'references')
  if (registry.size) {
    const entries = [...registry.values()]
      .sort((a, b) => a.n - b.n)
      .slice(0, 6)
      .map((citation) => `[${citation.n}] ${citation.title}${citation.url ? ` — ${citation.url}` : ''}`)
    withoutReferences.push({
      index: withoutReferences.length,
      type: 'references',
      kind: 'references',
      title: '参考来源',
      subtitle: '',
      bullets: entries,
      stats: [],
      chart: null,
      compare: null,
      timeline: [],
      table: null,
      quote: null,
      image: null,
      citations: [...registry.values()].sort((a, b) => a.n - b.n),
      notes: '',
    })
  }
  return { ...stats, slides: withoutReferences.map((slide, index) => ({ ...slide, index })) }
}

/** Replace bare placeholder titles with something structural but truthful. */
export function repairTitles(slides, { documentTitle = '' } = {}) {
  return slides.map((slide, index) => {
    if (slide.title) return slide
    if (slide.type === 'cover' && documentTitle) return { ...slide, title: documentTitle }
    const fromBullet = slide.bullets[0]?.slice(0, 24)
    return { ...slide, title: fromBullet || TYPE_FALLBACK_TITLE[slide.type] || `第 ${index + 1} 页` }
  })
}

/**
 * Full post-processing pipeline applied to the slides a model produced.
 */
export function finalizeDeck({
  slides = [],
  design = null,
  showCitations = false,
  allowedUrls = [],
  maxBulletsPerSlide = DEFAULT_MAX_BULLETS,
  documentTitle = '',
} = {}) {
  const cleaned = slides
    .map((slide, index) => ({ ...slide, index }))
    .filter(Boolean)
  const repaired = repairTitles(cleaned, { documentTitle })
  const { slides: split, splits } = splitOverflowSlides(repaired, maxBulletsPerSlide)
  const citations = reconcileCitations(split, { allowedUrls, showCitations })
  return {
    design: design ?? DEFAULT_DESIGN,
    slides: citations.slides,
    stats: { splits, droppedCitations: citations.dropped, usedCitations: citations.used },
  }
}

/* ---------------------------------------------------------------- parser ---- */

/**
 * Index of the first character after the JSON object that starts at `start`,
 * or -1 when the object is not complete yet. String-aware so braces inside
 * strings do not confuse the scan.
 */
export function findObjectEnd(text, start = 0) {
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth === 0) return i + 1
      if (depth < 0) return -1
    }
  }
  return -1
}

/**
 * Streaming parser. Feed raw text deltas; it returns the events that became
 * complete: `{ kind: 'design', value }` and `{ kind: 'slide', value }`.
 */
export function createDeckParser({ maxBulletsPerSlide = DEFAULT_MAX_BULLETS } = {}) {
  let buffer = ''
  let count = 0
  let design = null

  function expand(raw) {
    const events = []
    const designRaw = raw?.design ?? raw?.theme ?? raw?.designSystem
    if (designRaw && typeof designRaw === 'object') {
      const normalized = normalizeDesign(designRaw)
      if (normalized) {
        design = normalized
        events.push({ kind: 'design', value: normalized })
      }
    }
    const nested = Array.isArray(raw?.slides) ? raw.slides : Array.isArray(raw?.data) ? raw.data : null
    const list = nested ?? (designRaw && Object.keys(raw).length <= 2 ? [] : [raw])
    for (const item of list) {
      const slide = normalizeSlide(item, count)
      if (slide) {
        count += 1
        events.push({ kind: 'slide', value: slide })
      }
    }
    return events
  }

  function drain() {
    const found = []
    for (;;) {
      const start = buffer.indexOf('{')
      if (start === -1) {
        if (buffer.length > 512) buffer = ''
        break
      }
      if (start > 0) buffer = buffer.slice(start)
      const end = findObjectEnd(buffer, 0)
      if (end === -1) {
        // The leading object never closes: a stray brace, a truncated page, or a
        // wrapper array arriving piecewise. Recover only from an object that
        // starts at a LINE START — inner objects of a multi-key page (stats,
        // compare, chart …) are mid-line, and recovering from those would shred
        // one page into fragments.
        let recovered = false
        for (let marker = buffer.indexOf('\n{'); marker !== -1; marker = buffer.indexOf('\n{', marker + 1)) {
          const next = marker + 1
          if (findObjectEnd(buffer, next) !== -1) {
            buffer = buffer.slice(next)
            recovered = true
            break
          }
        }
        if (!recovered) break // genuinely need more data
        continue
      }
      const chunkText = buffer.slice(0, end)
      buffer = buffer.slice(end)
      let parsed
      try {
        parsed = JSON.parse(chunkText)
      } catch {
        continue
      }
      found.push(...expand(parsed))
      if (buffer.length > 1_000_000) buffer = ''
    }
    return found
  }

  return {
    push(chunk) {
      if (typeof chunk !== 'string' || !chunk) return []
      buffer += chunk
      const events = drain()
      if (!maxBulletsPerSlide) return events
      return events
    },
    flush() {
      const events = drain()
      const leftover = buffer.trim()
      buffer = ''
      if (!leftover) return { events, partial: '' }
      const start = leftover.indexOf('{')
      if (start === -1) return { events, partial: leftover }
      const candidate = leftover.slice(start)
      for (const suffix of ['"}', '"]}', ']}', '}', '"}]}']) {
        try {
          const repaired = JSON.parse(candidate + suffix)
          events.push(...expand(repaired))
          return { events, partial: '' }
        } catch {
          /* try next suffix */
        }
      }
      return { events, partial: candidate.slice(0, 200) }
    },
    get design() {
      return design
    },
    get count() {
      return count
    },
    get pending() {
      return buffer
    },
  }
}
