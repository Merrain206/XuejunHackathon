import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_DESIGN,
  createDeckParser,
  finalizeDeck,
  isPlaceholderText,
  normalizeDesign,
  normalizeSlide,
  reconcileCitations,
  splitOverflowSlides,
  stripUnsupportedMarkers,
} from '../../server/deck.js'

const DESIGN_LINE = JSON.stringify({
  design: {
    styleName: '深海金融',
    rationale: '面向投资人的年度复盘，冷色高对比',
    palette: { bg: '#08111f', bgAlt: '#0f2036', fg: '#eaf2ff', muted: '#93a8c6', accent: '#4fc3f7', accent2: '#ffb74d' },
    typography: { heading: 'Noto Sans SC', body: 'system-ui', scale: 1.05, headingWeight: 800 },
    decor: { style: 'geometric', imageStyle: 'clean 3d render, deep blue', radius: 18, pattern: 'grid' },
  },
})

const SLIDES = [
  { type: 'cover', title: '年度复盘', subtitle: '交付效率与质量', image: { prompt: 'abstract data flow, deep blue', placement: 'background' } },
  { type: 'stats', title: '关键指标', stats: [{ value: '2.9', unit: '天', label: '平均交付周期', delta: '-42%' }] },
  { type: 'chart', title: '趋势', chart: { kind: 'line', series: [{ name: '交付', data: [{ label: 'Q1', value: 5 }, { label: 'Q2', value: 3 }] }] } },
  { type: 'compare', title: '前后对比', compare: { left: { title: '现状', items: ['人工核查'] }, right: { title: '改进后', items: ['工具化核查'] } } },
  { type: 'timeline', title: '推进节奏', timeline: [{ label: 'Q1', title: '试点', text: '两个团队' }] },
  { type: 'table', title: '明细', table: { headers: ['项目', '数值'], rows: [['A', '1'], ['B', '2']] } },
  { type: 'quote', title: '结论', quote: { text: '效率提升来自流程而非工具', attribution: '编辑负责人' } },
  { type: 'closing', title: '下一步', bullets: ['核查工具化', '订阅引导 A/B'] },
]

function jsonl(slides = SLIDES) {
  return [DESIGN_LINE, ...slides.map((slide) => JSON.stringify(slide))].join('\n')
}

function collect(text, chunkSize) {
  const parser = createDeckParser()
  const events = []
  for (let i = 0; i < text.length; i += chunkSize) events.push(...parser.push(text.slice(i, i + chunkSize)))
  const flushed = parser.flush()
  events.push(...flushed.events)
  return {
    events,
    slides: events.filter((event) => event.kind === 'slide').map((event) => event.value),
    design: events.filter((event) => event.kind === 'design').map((event) => event.value).at(-1) ?? null,
    partial: flushed.partial,
  }
}

/* ------------------------------------------------------------------ parser -- */

test('design line and pages parse regardless of chunking', () => {
  for (const size of [1, 3, 7, 29, 1000]) {
    const { slides, design } = collect(jsonl(), size)
    assert.equal(slides.length, SLIDES.length, `chunk size ${size}`)
    assert.equal(design?.styleName, '深海金融', `chunk size ${size}`)
    assert.deepEqual(
      slides.map((slide) => slide.type),
      SLIDES.map((slide) => slide.type),
    )
  }
})

test('parser tolerates fences, preambles and wrapper arrays', () => {
  const fenced = collect(`这是设计：\n\`\`\`json\n${jsonl()}\n\`\`\``, 5)
  assert.equal(fenced.slides.length, SLIDES.length)
  const wrapped = collect(JSON.stringify({ design: JSON.parse(DESIGN_LINE).design, slides: SLIDES }), 6)
  assert.equal(wrapped.slides.length, SLIDES.length)
  assert.equal(wrapped.design?.styleName, '深海金融')
})

test('an unterminated object still lets later pages through', () => {
  const text = `{"nope"\n${JSON.stringify(SLIDES[1])}\n${JSON.stringify(SLIDES[2])}`
  const { slides } = collect(text, 5)
  assert.deepEqual(
    slides.map((slide) => slide.title),
    ['关键指标', '趋势'],
  )
})

/* ---------------------------------------------------------------- normalize -- */

test('design normalization rejects unknown fonts and clamps numbers', () => {
  const design = normalizeDesign({
    styleName: 'x',
    palette: { bg: 'not-a-color', accent: '#abc' },
    typography: { heading: 'Comic Sans MS', body: 'Georgia', scale: 9, letterSpacing: -50 },
    decor: { radius: 999 },
  })
  assert.equal(design.palette.bg, DEFAULT_DESIGN.palette.bg, 'invalid colour falls back')
  assert.equal(design.palette.accent, '#abc')
  assert.equal(design.typography.heading, 'system-ui', 'font outside the allowlist falls back')
  assert.equal(design.typography.body, 'Georgia')
  assert.equal(design.typography.scale, 1.25, 'scale clamped')
  assert.equal(design.typography.letterSpacing, -2, 'letter spacing clamped')
  assert.equal(design.decor.radius, 40)
})

test('normalizeDesign returns null for junk so the caller can keep its default', () => {
  assert.equal(normalizeDesign(null), null)
  assert.equal(normalizeDesign('nope'), null)
})

test('slide types are normalized, aliased and repaired when the body is missing', () => {
  assert.equal(normalizeSlide({ type: 'kpi', stats: [{ value: '1', label: 'x' }] }, 1).type, 'stats')
  assert.equal(normalizeSlide({ type: 'chart', bullets: ['只有要点'] }, 1).type, 'bullets')
  assert.equal(normalizeSlide({ type: 'process', timeline: [{ title: 'a' }] }, 1).type, 'timeline')
  assert.equal(normalizeSlide({ type: 'graph' }, 1), null, '没有正文的页会被整页丢弃')
  assert.equal(normalizeSlide({ title: 'T', bullets: ['a'] }, 0).type, 'cover')
})

test('per-type payloads are sanitized', () => {
  const slide = normalizeSlide(
    {
      type: 'stats',
      title: '指标',
      stats: [
        { value: '2.9', unit: '天', label: '交付周期', delta: '-42%' },
        { value: '', label: '' },
        { value: '9', label: 'x' },
        { value: '8', label: 'y' },
        { value: '7', label: 'z' },
      ],
    },
    1,
  )
  assert.equal(slide.stats.length, 4, 'stats capped at 4')

  const chart = normalizeSlide(
    { type: 'chart', chart: { kind: 'nonsense', series: [{ name: 's', data: [{ label: 'a', value: '3' }, { label: 'b', value: 'x' }] }] } },
    1,
  )
  assert.equal(chart.chart.kind, 'bar')
  assert.deepEqual(chart.chart.series[0].data, [{ label: 'a', value: 3 }])

  const table = normalizeSlide({ type: 'table', table: { headers: ['a'], rows: [['1']] } }, 1)
  assert.deepEqual(table.table, { headers: ['a'], rows: [['1']] })
  assert.equal(normalizeSlide({ type: 'table', table: { headers: [], rows: [] }, bullets: ['b'] }, 1).type, 'bullets')
})

/* -------------------------------------------------------------- placeholders -- */

test('placeholder strings are detected and removed from the deck', () => {
  for (const text of ['封面页', '收尾页', '封面主图：一张数据流', '配图：深蓝色背景', '图片：team photo', '（此处省略）', '谢谢观看', 'Slide 3']) {
    assert.equal(isPlaceholderText(text), true, `${text} 应判定为占位符`)
  }
  for (const text of ['封面设计推动增长', '结束语与下一步', '配图风格应该更克制']) {
    assert.equal(isPlaceholderText(text), false, `${text} 是正常内容`)
  }

  const slide = normalizeSlide({ type: 'bullets', title: '要点', bullets: ['配图：深蓝色背景', '真实要点一', '（此处插入图表）'] }, 1)
  assert.deepEqual(slide.bullets, ['真实要点一'])

  // a title that is nothing but a placeholder is dropped, then repaired by type
  const bare = normalizeSlide({ type: 'closing', title: '收尾页', bullets: ['下一步是核查工具化'] }, 3)
  assert.equal(bare.title, '')
  const { slides } = finalizeDeck({ slides: [bare] })
  assert.equal(slides[0].title, '下一步是核查工具化')
})

test('inline placeholder parentheses are stripped from real content', () => {
  const slide = normalizeSlide({ title: '要点', bullets: ['交付周期缩短 42%（配图：折线图）'] }, 1)
  assert.deepEqual(slide.bullets, ['交付周期缩短 42%'])
})

/* ------------------------------------------------------------------ splitting -- */

test('pages with too many bullets are split at the ceiling', () => {
  const slide = { ...normalizeSlide({ title: '长页', bullets: Array.from({ length: 14 }, (_, i) => `要点${i + 1}`) }, 1) }
  const { slides, splits } = splitOverflowSlides([slide], 6)
  assert.equal(slides.length, 3)
  assert.equal(splits, 3)
  assert.deepEqual(slides.map((item) => item.bullets.length), [6, 6, 2])
  assert.equal(slides[1].title, '长页（续）')
  assert.deepEqual(slides.map((item) => item.index), [0, 1, 2])
})

test('finalizeDeck enforces the configured bullet ceiling', () => {
  const raw = [normalizeSlide({ title: 'A', bullets: Array.from({ length: 9 }, (_, i) => `b${i}`) }, 0)]
  const { slides, stats } = finalizeDeck({ slides: raw, maxBulletsPerSlide: 4 })
  assert.equal(slides.length, 3)
  assert.equal(stats.splits, 3)
  for (const slide of slides) assert.ok(slide.bullets.length <= 4)
})

/* ------------------------------------------------------------------ citations -- */

test('stripUnsupportedMarkers drops unknown numbers only', () => {
  const text = stripUnsupportedMarkers('增长 23%[1]，下降 5%[9]', new Set([1]))
  assert.equal(text, '增长 23%[1]，下降 5%')
})

test('citations with unverifiable URLs are dropped, the rest renumbered continuously', () => {
  const slides = [
    {
      ...normalizeSlide({ title: 'A', bullets: ['营收增长 23%[7]', '成本下降 5%[3]'] }, 0),
    },
    {
      ...normalizeSlide({ title: 'B', bullets: ['份额提升[9]'] }, 1),
    },
  ]
  slides[0].citations = [
    { n: 7, title: '真实来源', url: 'https://real.example/a', publisher: 'real.example' },
    { n: 3, title: '编造来源', url: 'https://fake.example/b', publisher: 'fake.example' },
  ]
  slides[1].citations = [{ n: 9, title: '也是编造', url: 'https://fake.example/c', publisher: '' }]

  const result = reconcileCitations(slides, { allowedUrls: ['https://real.example/a'], showCitations: true })
  assert.equal(result.dropped, 2)
  assert.equal(result.used, 1)
  const [first, second, references] = result.slides
  assert.equal(first.bullets[0], '营收增长 23%[1]', '重新编号为连续编号')
  assert.equal(first.bullets[1], '成本下降 5%', '编造来源的角标被移除')
  assert.equal(second.bullets[0], '份额提升')
  assert.equal(references.type, 'references')
  assert.equal(references.title, '参考来源')
  assert.deepEqual(references.bullets, ['[1] 真实来源 — https://real.example/a'])
})

test('with citations off every marker, citation and references page disappears', () => {
  const slide = { ...normalizeSlide({ title: 'A', bullets: ['增长 23%[1]', '成本下降[2]'] }, 1) }
  slide.citations = [{ n: 1, title: 'doc', url: '' }]
  const references = {
    ...normalizeSlide({ type: 'references', title: '参考来源', bullets: ['[1] doc'] }, 2),
  }
  const result = reconcileCitations([slide, references], { showCitations: false })
  assert.deepEqual(result.slides.map((item) => item.type), ['bullets'])
  assert.deepEqual(result.slides[0].bullets, ['增长 23%', '成本下降'])
  assert.deepEqual(result.slides[0].citations, [])
})

test('a references page is appended when sources exist and absent when they do not', () => {
  const withSources = finalizeDeck({
    slides: [normalizeSlide({ title: 'A', bullets: ['x[1]'] }, 0)],
    showCitations: true,
    allowedUrls: [],
  })
  // the document itself is a legitimate source with an empty URL
  const docSlide = withSources.slides[0]
  docSlide.citations = [{ n: 1, title: 'doc', url: '' }]
  const again = reconcileCitations([docSlide], { allowedUrls: [], showCitations: true })
  assert.equal(again.slides.at(-1).type, 'references')

  const withoutSources = finalizeDeck({
    slides: [normalizeSlide({ title: 'A', bullets: ['x'] }, 0)],
    showCitations: true,
    allowedUrls: [],
  })
  assert.equal(withoutSources.slides.at(-1).type, 'cover')
})
