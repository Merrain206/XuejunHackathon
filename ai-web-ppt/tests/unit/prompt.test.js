import assert from 'node:assert/strict'
import test from 'node:test'

import { PAGE_TYPES, buildMessages, buildSearchQueries } from '../../server/prompt.js'

const SEARCH_RESULTS = [
  { title: '行业报告', url: 'https://example.com/report', snippet: '2026 年行业增长 12%', publisher: 'example.com' },
  { title: '标准原文', url: 'https://standard.example/doc', snippet: '定义与适用范围', publisher: 'standard.example' },
]

function systemOf(messages) {
  return messages[0].content
}

test('style preference is injected into the constraint zone, not into the document', () => {
  const messages = buildMessages({ content: '文档正文', stylePreference: '主色用深蓝；活泼一点' })
  const system = systemOf(messages)
  assert.match(system, /风格约束区/)
  assert.match(system, /主色用深蓝；活泼一点/)
  assert.match(system, /硬约束/)
  assert.match(system, /软参考/)
  assert.match(system, /绝对不得改变文档本身的信息/)
  assert.match(messages[1].content, /文档正文/)
  assert.doesNotMatch(messages[1].content, /主色用深蓝/, '风格描述不得混进文档正文')
})

test('without a preference the model is told to derive the style itself', () => {
  const system = systemOf(buildMessages({ content: 'x', stylePreference: '' }))
  assert.match(system, /没有指定风格偏好/)
  assert.match(system, /自主推导/)
})

test('citations on: markers, per-page citations and a references page are required', () => {
  const system = systemOf(
    buildMessages({ content: 'x', showCitations: true, searchResults: SEARCH_RESULTS }),
  )
  assert.match(system, /来源与角标（本轮开启）/)
  assert.match(system, /角标/)
  assert.match(system, /type":?"references/)
  assert.match(system, /绝不允许编造/)
  // the document is source [1], retrieved pages are numbered after it
  assert.match(system, /\[1\] 用户提供的文档/)
  assert.match(system, /\[2\] 行业报告 — https:\/\/example\.com\/report/)
  assert.match(system, /\[3\] 标准原文/)
})

test('citations off: no markers, no citation fields, no references page', () => {
  const system = systemOf(buildMessages({ content: 'x', showCitations: false, searchResults: SEARCH_RESULTS }))
  assert.match(system, /本轮\*\*不需要\*\*来源标注/)
  assert.match(system, /不要输出任何角标/)
  assert.doesNotMatch(system, /用户提供的文档（本文档）/, '关闭时不得给出可引用来源清单')
})

test('retrieved material is scoped: enrich and verify, never derail the topic', () => {
  const withResults = systemOf(buildMessages({ content: 'x', searchResults: SEARCH_RESULTS }))
  assert.match(withResults, /联网检索结果/)
  assert.match(withResults, /不得偏离用户文档主题/)
  assert.match(withResults, /以用户文档为准/)

  const withoutResults = systemOf(buildMessages({ content: 'x', searchResults: [] }))
  assert.match(withoutResults, /不要凭空补充/)
})

test('readability floors and the placeholder blacklist are stated in the prompt', () => {
  const system = systemOf(buildMessages({ content: 'x', maxBulletsPerSlide: 5, maxSlides: 9 }))
  assert.match(system, /最多 5 条/)
  assert.match(system, /18px/)
  assert.match(system, /10%/)
  assert.match(system, /严禁占位符文字/)
  for (const word of ['封面页', '收尾页', '配图：', '此处插入', '谢谢观看']) {
    assert.ok(system.includes(word), `黑名单应包含 ${word}`)
  }
})

test('every page type is documented for the model', () => {
  const system = systemOf(buildMessages({ content: 'x' }))
  for (const type of PAGE_TYPES) assert.ok(system.includes(`| ${type} |`), `缺少页面类型 ${type}`)
  assert.match(system, /JSONL/)
  assert.match(system, /第一行必须是 design 对象/)
})

test('the layout catalog and composition rules are part of the contract', () => {
  const system = systemOf(buildMessages({ content: 'x' }))
  assert.match(system, /版式设计/)
  for (const layout of [
    'cover-center',
    'cover-split',
    'cover-image',
    'bullets-list',
    'bullets-cards',
    'bullets-split',
    'bullets-numbered',
    'kpi-strip',
    'stat-cards',
    'chart-full',
    'chart-split',
    'compare-columns',
    'compare-table',
    'timeline-horizontal',
    'timeline-vertical',
    'timeline-cards',
    'table-full',
    'table-split',
    'quote-hero',
    'quote-split',
    'closing-cards',
    'closing-cta',
  ]) {
    assert.ok(system.includes(layout), `版式目录缺少 ${layout}`)
  }
  assert.match(system, /eyebrow/)
  assert.match(system, /takeaway/)
  assert.match(system, /cards/)
  assert.match(system, /至少出现 4 种不同 layout/)
  assert.match(system, /留白是设计的一部分/)
})

test('image keywords must be concrete English usable as a query', () => {
  const system = systemOf(buildMessages({ content: 'x' }))
  assert.match(system, /\*\*`prompt` 必须是英文\*\*/)
  assert.match(system, /原样使用/)
  assert.match(system, /具体、有画面感/)
  assert.match(system, /不要用抽象概念词/)
  assert.match(system, /不要包含任何文字、字母、logo、水印/)
})

test('buildSearchQueries derives a title query plus data-bearing phrases', () => {
  const content = [
    '2026 年第三季度产品复盘',
    '本季度我们把交付周期从 5 天压到 2.9 天，成本节省 26%。',
    '订阅转化率只有 2.3%，未达到 3% 的目标。',
    '下一季度计划接入可溯源的资料库。',
  ].join('\n')
  const queries = buildSearchQueries(content, { max: 3 })
  assert.equal(queries.length, 3)
  assert.equal(queries[0], '2026 年第三季度产品复盘')
  assert.match(queries[1], /交付周期从 5 天压到 2\.9 天/)
  assert.ok(queries.every((query) => query.length >= 4))
  assert.equal(new Set(queries).size, queries.length, '去重')
})

test('buildSearchQueries copes with empty and query-less input', () => {
  assert.deepEqual(buildSearchQueries('', { max: 3 }), [])
  assert.deepEqual(buildSearchQueries('短', { max: 3 }), [])
})
