import assert from 'node:assert/strict'
import test from 'node:test'

import { deckCss } from '../../web/src/shared/deckCss.js'
import { buildStandaloneHtml, escapeHtml } from '../../web/src/shared/exportHtml.js'

const BODY = '<section class="slide slide--cover is-active" data-index="0"><h1 class="slide-title">季度复盘</h1></section>'

function build(overrides = {}) {
  return buildStandaloneHtml({
    bodyHtml: BODY,
    title: '季度复盘',
    slideCount: 3,
    generatedAt: '2026/9/12',
    model: 'deepseek-flash',
    showCitations: true,
    designStyleName: '深海金融',
    data: { title: '季度复盘', slides: [{ title: '季度复盘' }] },
    ...overrides,
  })
}

test('the rendered deck is embedded verbatim and the shared stylesheet is inlined', () => {
  const html = build()
  assert.ok(html.includes(BODY), 'the React-rendered deck must be embedded as-is')
  assert.ok(html.includes(deckCss()), 'exported CSS must be the shared deck stylesheet')
})

test('the export is fully self-contained: no external resources at all', () => {
  const html = build()
  assert.doesNotMatch(html, /<link\b/i)
  assert.doesNotMatch(html, /@import/i)
  assert.doesNotMatch(html, /https?:\/\//i)
  assert.doesNotMatch(html, /\bsrc\s*=/i)
  assert.doesNotMatch(html, /\bfetch\s*\(/)
})

test('navigation script, dots host and counter are present', () => {
  const html = build()
  assert.match(html, /ArrowRight/)
  assert.match(html, /ArrowLeft/)
  assert.match(html, /requestFullscreen/)
  assert.match(html, /id="dots"/)
  assert.match(html, /id="counter"/)
  assert.match(html, /1 \/ 3/)
  assert.match(html, /id="deckFrame"/)
})

test('design name, citation state and metadata are surfaced', () => {
  const html = build()
  assert.match(html, /深海金融/)
  assert.match(html, /来源标注：开/)
  assert.match(html, /deepseek-flash/)
  assert.match(html, /<title>季度复盘<\/title>/)

  const plain = build({ showCitations: false, designStyleName: '' })
  assert.doesNotMatch(plain, /来源标注：开/)
})

test('the embedded deck JSON cannot break out of its script tag', () => {
  const html = build({ data: { title: 'x</script><script>alert(1)</script>' } })
  const payload = /<script type="application\/json" id="deck-data">([\s\S]*?)<\/script>/.exec(html)
  assert.ok(payload)
  assert.doesNotMatch(payload[1], /<\/script>/i)
  assert.match(JSON.parse(payload[1]).title, /alert\(1\)/)
})

test('titles and body content are HTML-escaped in the shell', () => {
  const html = build({ title: '<img src=x onerror=alert(1)>' })
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'))
  assert.equal(escapeHtml('a"b\'c&d<e>f'), 'a&quot;b&#39;c&amp;d&lt;e&gt;f')
})

test('an empty deck still produces a valid document', () => {
  const html = buildStandaloneHtml({ bodyHtml: '', slideCount: 0 })
  assert.match(html, /没有可演示的页面/)
  assert.match(html, /1 \/ 0/)
})

test('the shared stylesheet carries the two readability floors', () => {
  const css = deckCss()
  assert.match(css, /--f-body: max\(18px/, 'body text floor of 18px')
  assert.match(css, /--s-pad-block: 10cqh/, 'at least 10% of the slide height on every edge')
  assert.match(css, /container-type: size/)
  assert.match(css, /\.slide-split/)
  assert.match(css, /\.stat-card/)
  assert.match(css, /\.timeline/)
  assert.match(css, /\.data-table/)
  assert.match(css, /\.reference-url/)
})
