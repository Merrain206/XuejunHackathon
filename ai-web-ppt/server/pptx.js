// Server-side PPTX export: turns a deck JSON into a real .pptx whose look follows
// `deck.design` (palette + typography). Every field of the deck is optional and
// every per-type renderer degrades to something sensible instead of throwing.
//
// Nothing is fetched unless a slide carries an image URL: callers may inject
// `fetchImage`, otherwise `globalThis.fetch` is used with a 15s timeout. Any
// image failure falls back to a decorative gradient, so an offline run still
// produces a fully designed deck.

import PptxGenJS from 'pptxgenjs'

// --- slide geometry (16:9 = 10in x 5.625in) ---------------------------------

const SLIDE_W = 10
const SLIDE_H = 5.625
/** >= 10% of the slide height stays free on every edge (safe area). */
const MARGIN = 0.5625
const SAFE_W = SLIDE_W - MARGIN * 2
const SAFE_H = SLIDE_H - MARGIN * 2
const SAFE_RIGHT = SLIDE_W - MARGIN
const SAFE_BOTTOM = SLIDE_H - MARGIN
/** Baseline for footers that must remain inside the safe area. */
const FOOTER_Y = SAFE_BOTTOM - 0.36
/** Height reserved at the bottom of a body slide when it carries citations. */
const CITATION_H = 1.1
/** Top of the standard content region (title block sits above it). */
const CONTENT_TOP = MARGIN + 1.32

// --- defaults ---------------------------------------------------------------

const DEFAULT_PALETTE = {
  bg: '#0B1F3A',
  bgAlt: '#12345F',
  fg: '#EAF2FF',
  muted: '#9DB4D6',
  accent: '#4FC3F7',
  accent2: '#FFB74D',
  surface: '#12294A',
  border: '#27476F',
}

const DEFAULT_TYPO = { heading: 'Arial', body: 'Arial', scale: 1, headingWeight: 700, letterSpacing: 0 }

/** Body copy is never rendered below this size, whatever `typography.scale` says. */
const MIN_BODY_PT = 14
const IMAGE_TIMEOUT_MS = 15_000

const KNOWN_TYPES = new Set([
  'cover',
  'section',
  'bullets',
  'stats',
  'compare',
  'timeline',
  'table',
  'quote',
  'chart',
  'closing',
  'references',
])

const CHART_KIND = {
  bar: 'bar',
  column: 'bar',
  line: 'line',
  pie: 'pie',
  donut: 'doughnut',
  doughnut: 'doughnut',
}

// --- small helpers ----------------------------------------------------------

const isObj = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const clamp = (value, min, max) => Math.min(max, Math.max(min, value))
const asArray = (value) => (Array.isArray(value) ? value : [])

/** Non-empty trimmed string, or ''. */
function str(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

/** First non-empty string among the candidates. */
function firstStr(...values) {
  for (const value of values) {
    const text = str(value)
    if (text) return text
  }
  return ''
}

/** Accepts 'FFF', '#FFFFFF', 'FFFFFF80'; returns an upper-case hex or null. */
function hexOf(value) {
  const raw = str(value).replace(/^#/, '')
  if (!/^[0-9a-fA-F]+$/.test(raw)) return null
  if (raw.length === 8 || raw.length === 6) return raw.toUpperCase()
  if (raw.length === 3) return raw.split('').map((c) => c + c).join('').toUpperCase()
  return null
}

/** Palette value with a guaranteed-usable fallback. */
function color(value, fallback) {
  return hexOf(value) ?? hexOf(fallback) ?? 'FFFFFF'
}

function parseNumber(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value.replace(/[^0-9.eE+-]/g, ''))
    if (Number.isFinite(parsed)) return parsed
  }
  return 0
}

/** Single-line text: newlines would overflow a fixed-size box. */
function oneLine(value, limit = 120) {
  const text = (typeof value === 'string' ? value : String(value ?? '')).replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

/** Clamp long paragraphs so a runaway answer cannot push text off-slide. */
function clip(value, limit) {
  const text = typeof value === 'string' ? value.trim() : ''
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

/** Palette + typography resolved once, so renderers never touch raw input. */
function designOf(deck) {
  const design = isObj(deck?.design) ? deck.design : {}

  const rawPalette = isObj(design.palette) ? design.palette : {}
  const palette = {}
  for (const key of Object.keys(DEFAULT_PALETTE)) palette[key] = color(rawPalette[key], DEFAULT_PALETTE[key])

  const rawTypo = isObj(design.typography) ? design.typography : {}
  const scale = parseNumber(rawTypo.scale)
  const typography = {
    heading: firstStr(rawTypo.heading, rawTypo.headingFont, DEFAULT_TYPO.heading),
    body: firstStr(rawTypo.body, rawTypo.bodyFont, DEFAULT_TYPO.body),
    scale: scale > 0 ? clamp(scale, 0.5, 2) : DEFAULT_TYPO.scale,
    headingWeight: [300, 400, 500, 600, 700, 800, 900].includes(rawTypo.headingWeight)
      ? rawTypo.headingWeight
      : DEFAULT_TYPO.headingWeight,
    letterSpacing: parseNumber(rawTypo.letterSpacing),
  }
  return { design, palette, typography }
}

// --- fonts ------------------------------------------------------------------

function headingFont(design, size, overrides = {}) {
  return {
    fontFace: design.typography.heading,
    fontSize: clamp(size * design.typography.scale, 16, 54),
    bold: design.typography.headingWeight >= 600,
    color: design.palette.fg,
    charSpacing: design.typography.letterSpacing || undefined,
    ...overrides,
  }
}

/** `size` is a floor-less request: the clamp guarantees readable body copy. */
function bodyFont(design, size, overrides = {}) {
  return {
    fontFace: design.typography.body,
    fontSize: clamp(size * design.typography.scale, MIN_BODY_PT, 30),
    color: design.palette.fg,
    ...overrides,
  }
}

/** Rich-text run. */
const run = (text, options = {}) => ({ text, options })
/** Explicit line break inside a rich-text array. */
const brk = (options = {}) => ({ text: '\n', options })

// --- slide chrome -----------------------------------------------------------

/**
 * Add a slide with an embedded background fill.
 * pptxgenjs v4 ignores a `background` key passed to `addSlide`, so the property
 * has to be assigned on the returned slide object; that is what ends up as the
 * `<p:bg>` element inside each `ppt/slides/slideN.xml` (no external reference).
 */
function addSlide(pptx, palette, bgKey = 'bg') {
  const slide = pptx.addSlide()
  slide.background = { color: palette[bgKey] }
  return slide
}

/** Full-bleed rectangle covering the whole slide. */
function fullBleed(slide, fill, options = {}) {
  slide.addShape('rect', { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H, fill, line: { color: fill.color, width: 0 }, ...options })
}

/** Decorative circle; pptxgenjs takes the diameter in `w`. */
function dot(slide, cx, cy, diameter, fill, transparency = 0) {
  slide.addShape('ellipse', {
    x: cx - diameter / 2,
    y: cy - diameter / 2,
    w: diameter,
    h: diameter,
    fill: { color: fill, transparency },
    line: { color: fill, width: 0 },
  })
}

function hexToRgb(hex) {
  const raw = (hexOf(hex) ?? '000000').slice(0, 6)
  return {
    r: Number.parseInt(raw.slice(0, 2), 16),
    g: Number.parseInt(raw.slice(2, 4), 16),
    b: Number.parseInt(raw.slice(4, 6), 16),
  }
}

const rgbToHex = (r, g, b) =>
  [r, g, b].map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, '0')).join('').toUpperCase()

/**
 * Banded stand-in for a real gradient: pptxgenjs has no `gradFill` support, so a
 * stack of interpolated strips gives the same visual ramp and stays embedded.
 */
function gradientBands(slide, x, y, w, h, from, to, bands = 24, vertical = true) {
  const a = hexToRgb(from)
  const b = hexToRgb(to)
  const count = clamp(Math.round(bands), 2, 40)
  for (let i = 0; i < count; i += 1) {
    const t = i / (count - 1)
    const fill = {
      color: rgbToHex(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t, a.b + (b.b - a.b) * t),
    }
    if (vertical) {
      const bandH = h / count
      slide.addShape('rect', { x, y: y + bandH * i, w, h: bandH + 0.01, fill, line: { color: fill.color, width: 0 } })
    } else {
      const bandW = w / count
      slide.addShape('rect', { x: x + bandW * i, y, w: bandW + 0.01, h, fill, line: { color: fill.color, width: 0 } })
    }
  }
}

// --- images -----------------------------------------------------------------

/**
 * Resolve `slide.image` into an embeddable base64 payload.
 * Returns null (never throws) when there is no URL or the fetch failed.
 */
async function loadImage(image, fetchImage) {
  const url = str(isObj(image) ? image.url : '')
  if (!url) return null

  try {
    let result = null
    if (typeof fetchImage === 'function') {
      result = await fetchImage(url)
    } else {
      const response = await globalThis.fetch(url, { signal: AbortSignal.timeout(IMAGE_TIMEOUT_MS) })
      if (!response || !response.ok) return null
      result = {
        buffer: Buffer.from(await response.arrayBuffer()),
        mime: response.headers?.get?.('content-type') ?? '',
      }
    }
    if (!result) return null
    const buffer = Buffer.isBuffer(result.buffer) ? result.buffer : Buffer.from(result.buffer ?? [])
    if (!buffer.length) return null
    const mime = str(result.mime) || 'image/png'
    return { data: `${mime};base64,${buffer.toString('base64')}` }
  } catch {
    return null // offline / blocked / bad payload -> decorative fallback
  }
}

/** Decorative replacement for a missing image, available fully offline. */
function addImageFallback(slide, rect, palette) {
  slide.addShape('rect', { ...rect, fill: { color: palette.bgAlt }, line: { color: palette.border, width: 1 } })
  gradientBands(slide, rect.x, rect.y, rect.w, rect.h, palette.bgAlt, palette.accent, 16, rect.h >= rect.w)
  dot(slide, rect.x + rect.w * 0.74, rect.y + rect.h * 0.28, Math.min(rect.w, rect.h) * 0.42, palette.accent, 45)
  dot(slide, rect.x + rect.w * 0.3, rect.y + rect.h * 0.7, Math.min(rect.w, rect.h) * 0.3, palette.accent2, 55)
}

/** Draw the resolved image (or the fallback) inside `rect`. */
function addImageBlock(slide, rect, image, palette) {
  if (!image) {
    addImageFallback(slide, rect, palette)
    return
  }
  slide.addImage({ data: image.data, ...rect, sizing: { type: 'cover', w: rect.w, h: rect.h } })
}

// --- citations & footers ----------------------------------------------------

/** Parse this slide's citations into ordered, de-duplicated entries. */
function citationEntries(citations) {
  const seen = new Set()
  const entries = []
  for (const item of asArray(citations)) {
    const raw = isObj(item) ? item : { title: String(item ?? '') }
    const title = oneLine(firstStr(raw.title, raw.text, raw.label) || str(raw.url), 64)
    const url = oneLine(str(raw.url), 78)
    const publisher = oneLine(str(raw.publisher), 30)
    const key = `${title}|${url}`
    if ((!title && !url) || seen.has(key)) continue
    seen.add(key)
    entries.push({ n: entries.length + 1, title, url, publisher })
  }
  return entries
}

/** Rich-text lines for a source list (numbers are derived, never invented). */
function citationInk(entries, design) {
  const lines = []
  for (const entry of entries) {
    const head = entry.publisher ? `${entry.n}. [${entry.publisher}] ${entry.title}` : `${entry.n}. ${entry.title}`
    lines.push(run(head, { color: design.palette.muted }))
    if (entry.url) {
      lines.push(brk())
      lines.push(run(`   ${entry.url}`, { color: design.palette.accent }))
    }
  }
  return lines
}

/** Every distinct citation in the deck, in first-appearance order. */
export function collectCitations(deck) {
  const merged = []
  for (const slide of asArray(deck?.slides)) merged.push(...asArray(slide?.citations))
  return citationEntries(merged)
}

function addCitations(slide, citations, design) {
  const ink = citationInk(citationEntries(citations), design)
  if (!ink.length) return
  slide.addText(ink, {
    x: MARGIN,
    y: SAFE_BOTTOM - CITATION_H,
    w: SAFE_W,
    h: CITATION_H - 0.05,
    align: 'left',
    valign: 'bottom',
    lineSpacingMultiple: 1.1,
    ...bodyFont(design, 9, { color: design.palette.muted }),
  })
}

function addPageNumber(slide, design, page, total) {
  if (!total) return
  slide.addText(`${page} / ${total}`, {
    x: SAFE_RIGHT - 1.2,
    y: FOOTER_Y,
    w: 1.05,
    h: 0.32,
    align: 'right',
    valign: 'middle',
    ...bodyFont(design, 10, { color: design.palette.muted }),
  })
}

/** Accent rule (or the page's short label) + title (+ optional subtitle). */
function addHeader(slide, deckSlide, design) {
  const { palette } = design
  const eyebrow = oneLine(firstStr(deckSlide.eyebrow), 18)
  if (eyebrow) {
    // The label takes the place of the rule so the title keeps its position.
    slide.addText(eyebrow, {
      x: MARGIN,
      y: MARGIN - 0.04,
      w: SAFE_W,
      h: 0.26,
      align: 'left',
      valign: 'middle',
      ...bodyFont(design, 10, { bold: true, color: palette.accent }),
    })
  } else {
    slide.addShape('rect', { x: MARGIN, y: MARGIN, w: 0.62, h: 0.06, fill: { color: palette.accent }, line: { color: palette.accent, width: 0 } })
  }
  slide.addText(oneLine(str(deckSlide.title) || '未命名页面', 90), {
    x: MARGIN,
    y: MARGIN + 0.16,
    w: SAFE_W,
    h: 0.86,
    align: 'left',
    valign: 'top',
    ...headingFont(design, 28),
  })
  const subtitle = firstStr(deckSlide.subtitle)
  if (subtitle) {
    slide.addText(oneLine(subtitle, 120), {
      x: MARGIN,
      y: MARGIN + 0.98,
      w: SAFE_W,
      h: 0.3,
      align: 'left',
      valign: 'top',
      ...bodyFont(design, 12, { color: palette.muted }),
    })
  }
}

/** Height of the one-line conclusion band, reserved when a takeaway exists. */
const TAKEAWAY_H = 0.72

/** Content region left for a body slide after the title block, citations, takeaway. */
function contentBox(deckSlide) {
  const reserved =
    (citationEntries(deckSlide.citations).length ? CITATION_H : 0) + (firstStr(deckSlide.takeaway) ? TAKEAWAY_H : 0)
  return { x: MARGIN, y: CONTENT_TOP, w: SAFE_W, h: Math.max(1, SAFE_BOTTOM - CONTENT_TOP - reserved) }
}

/** The page's one-line conclusion, rendered as an accent-bordered band. */
function addTakeaway(slide, deckSlide, design) {
  const text = oneLine(firstStr(deckSlide.takeaway), 90)
  if (!text) return
  const { palette } = design
  const citations = citationEntries(deckSlide.citations).length ? CITATION_H : 0
  const h = TAKEAWAY_H * 0.8
  const y = SAFE_BOTTOM - citations - h
  slide.addShape('roundRect', {
    x: MARGIN,
    y,
    w: SAFE_W,
    h,
    rectRadius: 0.06,
    fill: { color: palette.bgAlt },
    line: { color: palette.border, width: 0.75 },
  })
  slide.addShape('rect', { x: MARGIN, y, w: 0.06, h, fill: { color: palette.accent }, line: { width: 0 } })
  slide.addText(text, {
    x: MARGIN + 0.2,
    y,
    w: SAFE_W - 0.4,
    h,
    valign: 'middle',
    ...bodyFont(design, 13, { bold: true, color: palette.fg }),
  })
}

/** Bullet list as rich-text runs, one line each. */
const bulletInk = (design, items) =>
  items.map((text) => run(text, { bullet: { characterCode: '25CF', indent: 20 }, color: design.palette.fg, breakLine: true }))

/** Shared "no data" body: still renders something designed, never a blank box. */
function renderFallbackBody(slide, deckSlide, design, box) {
  const bullets = asArray(deckSlide.bullets)
    .map((b) => oneLine(isObj(b) ? firstStr(b.text, b.title) : b, 150))
    .filter(Boolean)
  const items = bullets.length ? bullets : [firstStr(deckSlide.subtitle) || str(deckSlide.title) || '—']
  slide.addText(bulletInk(design, items), {
    ...box,
    valign: 'top',
    lineSpacingMultiple: 1.35,
    ...bodyFont(design, clamp(19 - items.length, MIN_BODY_PT, 18)),
  })
  return slide
}

// --- per-type renderers -----------------------------------------------------

/** 'side' | 'background' when a usable image URL exists, else 'none'. */
function imageLayoutOf(deckSlide) {
  const image = isObj(deckSlide.image) ? deckSlide.image : null
  if (!image || !str(image.url)) return 'none'
  const placement = str(image.placement).toLowerCase()
  return placement === 'side' || placement === 'background' ? placement : 'none'
}

function renderCover(pptx, deckSlide, design, image, deck) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  // Full-bleed cover treatment: background ramp is never left blank.
  gradientBands(slide, 0, 0, SLIDE_W, SLIDE_H, palette.bgAlt, palette.bg, 28, true)
  dot(slide, SLIDE_W * 0.88, SLIDE_H * 0.1, 3.1, palette.accent, 78)
  dot(slide, SLIDE_W * 0.06, SLIDE_H * 0.95, 2.4, palette.accent2, 84)

  const layout = imageLayoutOf(deckSlide)
  if (layout === 'side') {
    const rect = { x: MARGIN + SAFE_W * 0.58, y: MARGIN, w: SAFE_W * 0.42, h: SAFE_H }
    addImageBlock(slide, rect, image, palette)
  } else if (layout === 'background') {
    addImageBlock(slide, { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H }, image, palette)
    fullBleed(slide, { color: palette.bg, transparency: 45 }) // scrim keeps the title readable
  }

  const textW = layout === 'side' ? SAFE_W * 0.52 : SAFE_W
  const titleY = MARGIN + SAFE_H * 0.24
  slide.addText(str(deckSlide.title) || firstStr(deck.title) || '演示文稿', {
    x: MARGIN,
    y: titleY,
    w: textW,
    h: SAFE_H * 0.5,
    align: 'left',
    valign: 'middle',
    ...headingFont(design, 40),
  })

  const subtitle = firstStr(deckSlide.subtitle, asArray(deckSlide.bullets)[0])
  if (subtitle) {
    const y = titleY + SAFE_H * 0.5 + 0.1
    slide.addText(oneLine(subtitle, 90), {
      x: MARGIN,
      y,
      w: textW,
      h: clamp(SAFE_BOTTOM - y, 0.45, 1.1),
      align: 'left',
      valign: 'top',
      ...bodyFont(design, 16, { color: palette.accent }),
    })
  }
  return slide
}

function renderSection(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bgAlt')
  gradientBands(slide, 0, 0, SLIDE_W, SLIDE_H, palette.bgAlt, palette.bg, 20, false)
  const y = MARGIN + SAFE_H * 0.34
  slide.addShape('rect', { x: MARGIN, y, w: 1.5, h: 0.07, fill: { color: palette.accent }, line: { color: palette.accent, width: 0 } })
  slide.addText(oneLine(str(deckSlide.title) || '章节', 90), {
    x: MARGIN,
    y: y + 0.24,
    w: SAFE_W * 0.86,
    h: 1.5,
    align: 'left',
    valign: 'top',
    ...headingFont(design, 34),
  })
  const subtitle = firstStr(deckSlide.subtitle, asArray(deckSlide.bullets)[0])
  if (subtitle) {
    slide.addText(oneLine(subtitle, 110), {
      x: MARGIN,
      y: y + 1.8,
      w: SAFE_W * 0.86,
      h: 0.55,
      align: 'left',
      valign: 'top',
      ...bodyFont(design, 14, { color: palette.muted }),
    })
  }
  return slide
}

function renderBullets(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const cards = asArray(deckSlide.cards)
    .map((card) => (isObj(card) ? { title: oneLine(firstStr(card.title), 40), text: oneLine(firstStr(card.text), 120) } : null))
    .filter((card) => card && (card.title || card.text))
  if (cards.length) return renderCards(slide, cards, design, box)

  const bullets = asArray(deckSlide.bullets)
    .map((b) => oneLine(isObj(b) ? firstStr(b.text, b.title) : b, 150))
    .filter(Boolean)
  if (!bullets.length) return renderFallbackBody(slide, deckSlide, design, box)
  slide.addText(bulletInk(design, bullets), {
    ...box,
    valign: 'top',
    lineSpacingMultiple: 1.35,
    ...bodyFont(design, clamp(18 - bullets.length, MIN_BODY_PT, 18)),
  })
  return slide
}

/** Card grid used for `cards` payloads (2 columns, up to 4 cards). */
function renderCards(slide, cards, design, box) {
  const { palette } = design
  const items = cards.slice(0, 4)
  const columns = items.length >= 3 ? 2 : 1
  const rows = Math.ceil(items.length / columns)
  const gap = 0.2
  const cardW = (box.w - gap * (columns - 1)) / columns
  const cardH = (box.h - gap * (rows - 1)) / rows
  items.forEach((card, index) => {
    const x = box.x + (index % columns) * (cardW + gap)
    const y = box.y + Math.floor(index / columns) * (cardH + gap)
    slide.addShape('roundRect', {
      x,
      y,
      w: cardW,
      h: cardH,
      rectRadius: 0.08,
      fill: { color: palette.bgAlt },
      line: { color: palette.border, width: 0.75 },
    })
    slide.addShape('rect', { x, y, w: 0.055, h: cardH, fill: { color: palette.accent }, line: { width: 0 } })
    slide.addText(
      [
        ...(card.title ? [run(card.title, { bold: true, color: palette.fg, breakLine: true })] : []),
        ...(card.text ? [run(card.text, { color: palette.muted })] : []),
      ],
      {
        x: x + 0.18,
        y: y + 0.12,
        w: cardW - 0.3,
        h: cardH - 0.24,
        valign: 'top',
        ...bodyFont(design, 14),
      },
    )
  })
  return slide
}

function renderStats(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const stats = asArray(deckSlide.stats).filter(isObj).slice(0, 6)
  if (!stats.length) return renderFallbackBody(slide, deckSlide, design, box)

  const gap = 0.18
  const columns = Math.min(3, stats.length)
  const rows = Math.ceil(stats.length / columns)
  const cardW = (box.w - gap * (columns - 1)) / columns
  const rowH = (box.h - gap * (rows - 1)) / rows
  stats.forEach((stat, i) => {
    const x = box.x + (i % columns) * (cardW + gap)
    const y = box.y + Math.floor(i / columns) * (rowH + gap)
    slide.addShape('roundRect', {
      x,
      y,
      w: cardW,
      h: rowH,
      rectRadius: 0.12,
      fill: { color: palette.surface },
      line: { color: palette.border, width: 1 },
    })
    const unit = oneLine(str(stat.unit), 8)
    slide.addText(
      [
        run(oneLine(firstStr(stat.value, stat.text) || '—', 18), {
          fontSize: clamp(30 * design.typography.scale, 20, 40),
          bold: true,
          color: palette.accent,
          fontFace: design.typography.heading,
        }),
        run(unit ? ` ${unit}` : '', {
          fontSize: clamp(14 * design.typography.scale, MIN_BODY_PT, 20),
          color: palette.muted,
          fontFace: design.typography.body,
        }),
      ],
      { x: x + 0.16, y: y + rowH * 0.14, w: cardW - 0.32, h: rowH * 0.42, align: 'left', valign: 'middle' },
    )
    slide.addText(oneLine(str(stat.label), 60), {
      x: x + 0.16,
      y: y + rowH * 0.56,
      w: cardW - 0.32,
      h: rowH * 0.24,
      align: 'left',
      valign: 'middle',
      ...bodyFont(design, 12, { color: palette.fg }),
    })
    const delta = oneLine(str(stat.delta), 16)
    if (delta) {
      slide.addText(delta, {
        x: x + 0.16,
        y: y + rowH * 0.79,
        w: cardW - 0.32,
        h: rowH * 0.2,
        align: 'left',
        valign: 'middle',
        ...bodyFont(design, 11, { color: /^[-−▼↓]/.test(delta) ? palette.accent2 : palette.accent, bold: true }),
      })
    }
  })
  return slide
}

function renderCompare(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const compare = isObj(deckSlide.compare) ? deckSlide.compare : {}
  const sides = [compare.left, compare.right].filter(isObj)
  if (!sides.length) return renderFallbackBody(slide, deckSlide, design, box)

  const gap = 0.24
  const columnW = (box.w - gap * (sides.length - 1)) / sides.length
  sides.forEach((side, i) => {
    const x = box.x + i * (columnW + gap)
    const tint = i === 0 ? palette.muted : palette.accent
    slide.addShape('roundRect', {
      x,
      y: box.y,
      w: columnW,
      h: box.h,
      rectRadius: 0.1,
      fill: { color: palette.surface },
      line: { color: i === 0 ? palette.border : palette.accent, width: 1 },
    })
    slide.addText(oneLine(firstStr(side.title, i === 0 ? '现状' : '改进后'), 30), {
      x: x + 0.2,
      y: box.y + 0.14,
      w: columnW - 0.4,
      h: 0.45,
      align: 'left',
      valign: 'middle',
      ...headingFont(design, 18, { bold: true, color: tint }),
    })
    const items = asArray(side.items ?? side.bullets)
      .map((b) => oneLine(isObj(b) ? firstStr(b.text, b.title) : b, 110))
      .filter(Boolean)
    slide.addText(
      items.map((text) => run(text, { bullet: { characterCode: '25AA', indent: 16 }, color: palette.fg, breakLine: true })),
      {
        x: x + 0.2,
        y: box.y + 0.68,
        w: columnW - 0.4,
        h: Math.max(0.5, box.h - 0.86),
        valign: 'top',
        lineSpacingMultiple: 1.3,
        ...bodyFont(design, clamp(17 - items.length, MIN_BODY_PT, 17)),
      },
    )
  })
  return slide
}

function renderTimeline(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const items = asArray(deckSlide.timeline).filter(isObj).slice(0, 6)
  if (!items.length) return renderFallbackBody(slide, deckSlide, design, box)

  const slot = box.w / items.length
  const nodeSize = 0.22
  const lineY = box.y + 0.46
  slide.addShape('rect', {
    x: box.x + nodeSize / 2,
    y: lineY - 0.02,
    w: box.w - nodeSize,
    h: 0.04,
    fill: { color: palette.border },
    line: { color: palette.border, width: 0 },
  })
  items.forEach((item, i) => {
    const cx = box.x + slot * (i + 0.5)
    dot(slide, cx, lineY, nodeSize, i === 0 ? palette.accent2 : palette.accent)
    slide.addText(oneLine(firstStr(item.label, `0${i + 1}`), 12), {
      x: cx - slot / 2,
      y: box.y - 0.04,
      w: slot,
      h: 0.32,
      align: 'center',
      valign: 'middle',
      ...bodyFont(design, 11, { color: palette.muted, bold: true }),
    })
    slide.addText(oneLine(str(item.title), 40), {
      x: cx - slot / 2 + 0.06,
      y: lineY + 0.24,
      w: slot - 0.12,
      h: 0.44,
      align: 'center',
      valign: 'top',
      ...headingFont(design, 15),
    })
    slide.addText(clip(str(item.text), 90), {
      x: cx - slot / 2 + 0.06,
      y: lineY + 0.72,
      w: slot - 0.12,
      h: Math.max(0.6, box.y + box.h - (lineY + 0.72)),
      align: 'center',
      valign: 'top',
      lineSpacingMultiple: 1.2,
      ...bodyFont(design, 11, { color: palette.muted }),
    })
  })
  return slide
}

function renderTable(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const table = isObj(deckSlide.table) ? deckSlide.table : {}
  const headers = asArray(table.headers)
    .map((h) => oneLine(isObj(h) ? firstStr(h.text, h.title) : h, 32))
    .filter(Boolean)
  const rows = asArray(table.rows)
    .map((row) => asArray(row).map((cell) => oneLine(isObj(cell) ? firstStr(cell.text, cell.value) : cell, 48)))
    .filter((row) => row.length)
  if (!headers.length && !rows.length) return renderFallbackBody(slide, deckSlide, design, box)

  const width = Math.max(headers.length, ...rows.map((r) => r.length), 1)
  const grid = []
  if (headers.length) {
    grid.push(
      Array.from({ length: width }, (_, c) => ({
        text: headers[c] ?? '',
        options: { bold: true, color: palette.bg, fill: { color: palette.accent } },
      })),
    )
  }
  for (const row of rows) {
    grid.push(Array.from({ length: width }, (_, c) => ({ text: row[c] ?? '', options: {} })))
  }
  const bodyRows = grid.length - (headers.length ? 1 : 0)
  slide.addTable(grid, {
    x: box.x,
    y: box.y,
    w: box.w,
    h: Math.min(box.h, 0.45 + Math.max(1, bodyRows) * 0.4),
    colW: Array.from({ length: width }, () => box.w / width),
    border: { pt: 1, color: palette.border },
    fill: { color: palette.surface },
    color: palette.fg,
    fontFace: design.typography.body,
    fontSize: clamp(20 - grid.length, 12, 16),
    valign: 'middle',
    autoPage: false,
  })
  return slide
}

function renderQuote(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const quote = isObj(deckSlide.quote) ? deckSlide.quote : {}
  const text = clip(firstStr(quote.text, quote.quote, asArray(deckSlide.bullets)[0], deckSlide.subtitle), 260)
  if (!text) return renderFallbackBody(slide, deckSlide, design, box)

  const attribution = oneLine(firstStr(quote.attribution, quote.author, quote.source), 80)
  const height = clamp(0.6 + text.length * 0.032, 1.3, attribution ? box.h - 1.0 : box.h)
  const y = box.y + (box.h - height - (attribution ? 0.7 : 0)) / 2
  slide.addText(text, {
    x: box.x + 0.4,
    y,
    w: box.w - 0.8,
    h: height,
    align: 'center',
    valign: 'middle',
    italic: true,
    lineSpacingMultiple: 1.25,
    ...headingFont(design, clamp(30 - text.length * 0.05, 20, 30), { bold: false }),
  })
  if (attribution) {
    slide.addText(attribution, {
      x: box.x,
      y: y + height + 0.14,
      w: box.w,
      h: 0.45,
      align: 'center',
      valign: 'middle',
      ...bodyFont(design, 14, { color: palette.accent }),
    })
  }
  return slide
}

function renderChart(pptx, deckSlide, design) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const chart = isObj(deckSlide.chart) ? deckSlide.chart : {}
  const series = asArray(chart.series)
    .filter(isObj)
    .map((serie) => {
      const points = asArray(serie.data).filter(isObj)
      return {
        name: oneLine(firstStr(serie.name, serie.title) || '系列', 24),
        labels: points.map((point) => oneLine(firstStr(point.label, point.name), 16)),
        values: points.map((point) => parseNumber(point.value ?? point.v)),
      }
    })
    .filter((serie) => serie.values.length > 0)
  if (!series.length) return renderFallbackBody(slide, deckSlide, design, box)

  const kind = CHART_KIND[str(chart.kind).toLowerCase()] ?? 'bar'
  slide.addChart(
    pptx.ChartType[kind] ?? pptx.ChartType.bar,
    series.map(({ name, labels, values }) => ({ name, labels, values })),
    {
      x: box.x,
      y: box.y,
      w: box.w,
      h: box.h,
      showLegend: series.length > 1,
      legendPos: 'b',
      legendColor: palette.muted,
      legendFontSize: 11,
      chartColors: [palette.accent, palette.accent2, palette.muted, palette.border],
      chartColorsOpacity: 92,
      catAxisLabelColor: palette.muted,
      catAxisLabelFontSize: 11,
      catGridLine: { style: 'none' },
      valAxisLabelColor: palette.muted,
      valAxisLabelFontSize: 11,
      valGridLine: { color: palette.border, style: 'dash', size: 0.5 },
      dataLabelColor: palette.fg,
      dataLabelFontSize: 11,
      showValue: series.length === 1 && series[0].values.length <= 12,
      showTitle: false,
      showCatName: false,
      showPercent: kind === 'pie' || kind === 'doughnut',
      barDir: kind === 'bar' ? 'bar' : undefined,
      barGapWidthPct: 60,
      lineDataSymbol: 'circle',
      lineSize: 2,
      holeSize: kind === 'doughnut' ? 55 : undefined,
    },
  )
  const unit = oneLine(str(chart.unit), 12)
  if (unit) {
    slide.addText(`单位：${unit}`, {
      x: SAFE_RIGHT - 1.8,
      y: FOOTER_Y,
      w: 1.7,
      h: 0.3,
      align: 'right',
      valign: 'middle',
      ...bodyFont(design, 10, { color: palette.muted }),
    })
  }
  return slide
}

function renderClosing(pptx, deckSlide, design, image) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bgAlt')
  gradientBands(slide, 0, 0, SLIDE_W, SLIDE_H, palette.bg, palette.bgAlt, 22, true)
  if (image) addImageBlock(slide, { x: 0, y: 0, w: SLIDE_W, h: SLIDE_H }, image, palette)
  fullBleed(slide, { color: palette.bg, transparency: 35 })

  const ruleY = MARGIN + SAFE_H * 0.2
  slide.addShape('rect', { x: SLIDE_W / 2 - 0.6, y: ruleY, w: 1.2, h: 0.07, fill: { color: palette.accent }, line: { color: palette.accent, width: 0 } })
  slide.addText(oneLine(str(deckSlide.title) || '谢谢观看', 90), {
    x: MARGIN,
    y: ruleY + 0.3,
    w: SAFE_W,
    h: 1.3,
    align: 'center',
    valign: 'middle',
    ...headingFont(design, 34),
  })
  const lines = []
  const bullets = asArray(deckSlide.bullets)
  const subtitle = firstStr(deckSlide.subtitle, bullets[0])
  // Only drop the first bullet when it was consumed as the subtitle.
  const rest = str(deckSlide.subtitle) ? bullets : bullets.slice(1)
  if (subtitle) lines.push(run(oneLine(subtitle, 120), bodyFont(design, 16, { color: palette.accent })))
  for (const bullet of rest.slice(0, 3)) {
    if (lines.length) lines.push(brk())
    lines.push(run(oneLine(isObj(bullet) ? firstStr(bullet.text, bullet.title) : bullet, 120), bodyFont(design, 14, { color: palette.muted })))
  }
  if (lines.length) {
    const y = ruleY + 1.75
    slide.addText(lines, {
      x: MARGIN,
      y,
      w: SAFE_W,
      h: clamp(SAFE_BOTTOM - y, 0.5, 1.5),
      align: 'center',
      valign: 'top',
      lineSpacingMultiple: 1.3,
    })
  }
  return slide
}

function renderReferences(pptx, deckSlide, design, image, deck) {
  const { palette } = design
  const slide = addSlide(pptx, palette, 'bg')
  const box = contentBox(deckSlide)
  const own = asArray(deckSlide.citations)
  const entries = (own.length ? citationEntries(own) : collectCitations(deck)).slice(0, 10)
  const ink = citationInk(entries, design)
  if (!ink.length) return renderFallbackBody(slide, deckSlide, design, box)
  slide.addText(ink, {
    ...box,
    valign: 'top',
    lineSpacingMultiple: 1.35,
    ...bodyFont(design, 13, { color: palette.fg }),
  })
  return slide
}

const RENDERERS = {
  cover: renderCover,
  section: renderSection,
  bullets: renderBullets,
  stats: renderStats,
  compare: renderCompare,
  timeline: renderTimeline,
  table: renderTable,
  quote: renderQuote,
  chart: renderChart,
  closing: renderClosing,
  references: renderReferences,
}

/** Types that paint their own full canvas and therefore skip the shared header. */
const FULL_CANVAS_TYPES = new Set(['cover', 'section', 'closing'])

// --- entry point ------------------------------------------------------------

/**
 * Build a complete .pptx for `deck`.
 *
 * @param {object} options
 * @param {object} options.deck           normalized deck ({ title, design, slides, meta })
 * @param {Function} [options.fetchImage] async (url) => { buffer, mime } | null
 * @param {string} [options.generatedAt]  timestamp rendered in the footer
 * @returns {Promise<Buffer>} a complete .pptx file
 */
export async function buildPptx({ deck, fetchImage, generatedAt = '' } = {}) {
  const safeDeck = isObj(deck) ? deck : {}
  const design = designOf(safeDeck)
  const deckSlides = asArray(safeDeck.slides)
  const total = deckSlides.length

  const pptx = new PptxGenJS()
  pptx.layout = 'LAYOUT_16x9'

  for (const [index, raw] of deckSlides.entries()) {
    const deckSlide = isObj(raw) ? raw : {}
    const requested = str(deckSlide.type).toLowerCase()
    const type = KNOWN_TYPES.has(requested) ? requested : 'bullets'
    const image = await loadImage(deckSlide.image, fetchImage)
    const render = RENDERERS[type] ?? renderBullets
    const slide = render(pptx, deckSlide, design, image, safeDeck)

    if (!FULL_CANVAS_TYPES.has(type)) addHeader(slide, deckSlide, design)
    if (type !== 'cover' && type !== 'references') addTakeaway(slide, deckSlide, design)
    if (type !== 'cover') addPageNumber(slide, design, index + 1, total)
    if (type !== 'references' && type !== 'cover') addCitations(slide, deckSlide.citations, design)

    const notes = firstStr(deckSlide.notes)
    if (notes) slide.addNotes(notes)
  }

  const buffer = await pptx.write({ outputType: 'nodebuffer' })
  return Buffer.isBuffer(buffer) ? buffer : Buffer.from(buffer)
}

/** Provenance line for callers that want it on a custom master slide. */
export function deckFooter({ deck, generatedAt = '' } = {}) {
  const model = firstStr(deck?.meta?.model)
  const stamp = firstStr(generatedAt, deck?.meta?.generatedAt)
  return [firstStr(deck?.title), model, stamp].filter(Boolean).join(' · ')
}
