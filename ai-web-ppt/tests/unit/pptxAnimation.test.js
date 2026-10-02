import assert from 'node:assert/strict'
import test from 'node:test'
import { XMLValidator } from 'fast-xml-parser'

import { addSlideAnimations, injectTiming, parseSlideShapes, planReveal } from '../../server/pptxAnimate.js'
import { buildPptx } from '../../server/pptx.js'
import { readZipEntries, zipEntryText } from '../helpers/zip.mjs'

const EMU = 914400
const rect = (x, y, w, h) =>
  `<p:spPr><a:xfrm><a:off x="${Math.round(x * EMU)}" y="${Math.round(y * EMU)}"/><a:ext cx="${Math.round(w * EMU)}" cy="${Math.round(h * EMU)}"/></a:xfrm></p:spPr>`

function textShape(id, paragraphs, { x = 1, y = 1, w = 4, h = 0.4, size = 1800, name = 'Text' } = {}) {
  const runs = paragraphs.map((text) => `<a:p><a:r><a:rPr sz="${size}"/><a:t>${text}</a:t></a:r></a:p>`).join('')
  return (
    `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${name} ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>` +
    `${rect(x, y, w, h)}<p:txBody><a:bodyPr/>${runs}</p:txBody></p:sp>`
  )
}

function shape(id, { x = 1, y = 1, w = 4, h = 0.1 } = {}) {
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="Decoration ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>${rect(x, y, w, h)}<p:spPr/></p:sp>`
}

function picture(id, { x = 6, y = 1, w = 3, h = 3 } = {}) {
  return (
    `<p:pic><p:nvPicPr><p:cNvPr id="${id}" name="Image ${id}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>` +
    `<p:blipFill/><p:spPr><a:xfrm><a:off x="${Math.round(x * EMU)}" y="${Math.round(y * EMU)}"/><a:ext cx="${Math.round(w * EMU)}" cy="${Math.round(h * EMU)}"/></a:xfrm></p:spPr></p:pic>`
  )
}

function graphicFrame(id, { x = 1, y = 2, w = 6, h = 2 } = {}) {
  return (
    `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${id}" name="Chart ${id}"/><p:nvPr/></p:nvGraphicFramePr>` +
    `<p:xfrm><a:off x="${Math.round(x * EMU)}" y="${Math.round(y * EMU)}"/><a:ext cx="${Math.round(w * EMU)}" cy="${Math.round(h * EMU)}"/></p:xfrm><p:graphic/></p:graphicFrame>`
  )
}

function slideXml(body) {
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">' +
    `<p:cSld><p:spTree>${body}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>`
  )
}

const META = { title: '本季度三条判断', subtitle: '副标题', eyebrow: '01 结论', takeaway: '效率提升来自流程改造' }

/** title + subtitle + eyebrow + 3 bullets + chart + picture + takeaway + furniture */
const RICH_SLIDE = slideXml(
  [
    shape(2, { x: 0.56, y: 0.56, w: 0.62, h: 0.06 }), // decoration: accent rule
    textShape(3, ['01 结论'], { x: 0.56, y: 0.55, w: 2, h: 0.26, size: 1000, name: 'Eyebrow' }),
    textShape(4, ['本季度三条判断'], { x: 0.56, y: 0.8, w: 6, h: 0.8, size: 2800, name: 'Title' }),
    textShape(5, ['副标题'], { x: 0.56, y: 1.5, w: 6, h: 0.3, size: 1200, name: 'Subtitle' }),
    textShape(6, ['第一条要点', '第二条要点', '第三条要点'], { x: 0.56, y: 2, w: 5, h: 1.6, size: 1800, name: 'Body' }),
    graphicFrame(7),
    picture(8),
    textShape(9, ['效率提升来自流程改造'], { x: 0.56, y: 4.2, w: 8, h: 0.5, size: 1300, name: 'Takeaway' }),
    textShape(10, ['[1] 本文档'], { x: 0.56, y: 4.8, w: 3, h: 0.25, size: 900, name: 'Citations' }),
    textShape(11, ['3 / 10'], { x: 8.5, y: 4.8, w: 1, h: 0.25, size: 1000, name: 'PageNumber' }),
  ].join(''),
)

/** Structural checks PowerPoint silently requires. */
function assertValidTiming(xml, { label = 'slide' } = {}) {
  const validation = XMLValidator.validate(xml)
  assert.equal(validation, true, `${label}: XML 必须良构（${JSON.stringify(validation)}）`)

  const start = xml.indexOf('<p:timing>')
  const end = xml.indexOf('</p:timing>')
  assert.ok(start !== -1 && end > start, `${label}: 应包含完整的 timing 树`)
  const timing = xml.slice(start, end)

  assert.ok(xml.indexOf('<p:cSld>') < start, `${label}: timing 在 cSld 之后`)
  assert.ok(xml.indexOf('<p:clrMapOvr>') < start, `${label}: timing 在 clrMapOvr 之后`)
  assert.ok(end < xml.indexOf('</p:sld>'), `${label}: timing 在 </p:sld> 之前`)

  const ids = [...timing.matchAll(/<p:cTn id="(\d+)"/g)].map((match) => Number(match[1]))
  assert.equal(new Set(ids).size, ids.length, `${label}: p:cTn id 必须唯一`)
  assert.equal(ids[0], 1, `${label}: tmRoot id = 1`)

  const shapeIds = new Set([...xml.matchAll(/<p:cNvPr id="(\d+)"/g)].map((match) => Number(match[1])))
  for (const target of timing.matchAll(/<p:spTgt spid="(\d+)"/g)) {
    assert.ok(shapeIds.has(Number(target[1])), `${label}: spid=${target[1]} 必须存在于页面`)
  }
  assert.ok(timing.includes('presetClass="entr"'), `${label}: 进入动画`)
  assert.ok(timing.includes('filter="fade"'), `${label}: 淡入`)
  return timing
}

test('parseSlideShapes reads ids, kinds, text, paragraphs and geometry', () => {
  const shapes = parseSlideShapes(RICH_SLIDE)
  assert.deepEqual(
    shapes.map((item) => [item.id, item.kind]),
    [
      [2, 'shape'],
      [3, 'text'],
      [4, 'text'],
      [5, 'text'],
      [6, 'text'],
      [7, 'graphic'],
      [8, 'picture'],
      [9, 'text'],
      [10, 'text'],
      [11, 'text'],
    ],
  )
  assert.equal(shapes.find((item) => item.id === 6).paragraphs, 3)
  assert.equal(Math.round(shapes.find((item) => item.id === 8).rect.x), 6)
  assert.equal(shapes.find((item) => item.id === 3).fontPt, 10)
})

test('only content animates: decoration, heading and page furniture stay static', () => {
  const { steps, static: statics } = planReveal(parseSlideShapes(RICH_SLIDE), META)
  assert.deepEqual(statics.sort((a, b) => a - b), [2, 3, 4, 5, 10, 11], '装饰/标题区/来源/页码保持静态')

  const order = steps.flat().map((target) => target.spid)
  // 3 paragraphs one by one, then chart, then picture, then the conclusion bar
  assert.deepEqual(order, [6, 6, 6, 7, 8, 9])
  assert.deepEqual(steps[0], [{ spid: 6, paragraph: 0 }])
  assert.deepEqual(steps[2], [{ spid: 6, paragraph: 2 }])
  assert.equal(steps.at(-1)[0].spid, 9, '结论条最后出现')
})

test('three stacked boxes of one KPI share a single click', () => {
  const kpi = slideXml(
    [
      textShape(2, ['2.9'], { x: 1, y: 2, w: 2, h: 0.5, size: 3200 }),
      textShape(3, ['平均交付周期'], { x: 1, y: 2.5, w: 2, h: 0.3, size: 1200 }),
      textShape(4, ['-42%'], { x: 1, y: 2.85, w: 2, h: 0.25, size: 1100 }),
      textShape(5, ['3860'], { x: 4, y: 2, w: 2, h: 0.5, size: 3200 }),
      textShape(6, ['季度支出'], { x: 4, y: 2.5, w: 2, h: 0.3, size: 1200 }),
    ].join(''),
  )
  const { steps, static: statics } = planReveal(parseSlideShapes(kpi), { title: '指标' })
  assert.equal(steps.length, 2, '两列 KPI = 两次点击')
  assert.deepEqual(steps[0].map((target) => target.spid), [2, 3, 4])
  assert.deepEqual(steps[1].map((target) => target.spid), [5, 6])
  assert.deepEqual(statics, [])
})

test('the timing tree is click-triggered: one group per click, first gated by indefinite', () => {
  const patched = injectTiming(RICH_SLIDE, META)
  const timing = assertValidTiming(patched, { label: 'rich' })

  // first click group waits for the speaker
  assert.match(timing, /<p:cond delay="indefinite"\/>/)
  // every effect is On Click, never After Previous / With Previous
  const nodeTypes = [...timing.matchAll(/nodeType="([a-zA-Z]+)"/g)].map((match) => match[1])
  assert.ok(nodeTypes.includes('mainSeq') && nodeTypes.includes('tmRoot'))
  const effectTypes = nodeTypes.filter((type) => !['mainSeq', 'tmRoot'].includes(type))
  assert.deepEqual([...new Set(effectTypes)], ['clickEffect'], '全部为单击触发')
  assert.equal((timing.match(/nodeType="clickEffect"/g) || []).length, 6, '6 次点击 = 6 个步骤')

  // paragraphs are marked as a build
  assert.ok(timing.includes('<p:bldP spid="6" grpId="0" uiExpand="1" build="p"/>'))
  // restrained effect: fade, short duration
  assert.ok(timing.includes('presetID="10"'), 'Fade 效果')
  assert.match(timing, /filter="fade"/)
  assert.ok(timing.includes('dur="350"'), '350ms 短时长')
  assert.equal(/presetID="(?!10)\d+"/.test(timing), false, '不混用其它效果')
})

test('injection is idempotent and never touches unusable slides', () => {
  const patched = injectTiming(RICH_SLIDE, META)
  assert.equal(injectTiming(patched, META), patched)
  assert.equal(injectTiming('not xml'), 'not xml')
  const empty = slideXml('')
  assert.equal(injectTiming(empty, META), empty)
})

test('every exported slide gets click-triggered animations, decoration excluded', async () => {
  const deck = {
    title: '动画验证',
    design: { palette: { bg: '#08111f', bgAlt: '#0f2036', fg: '#eaf2ff', muted: '#93a8c6', accent: '#4fc3f7', accent2: '#ffb74d' } },
    slides: [
      { index: 0, type: 'cover', title: '封面', subtitle: '副标题' },
      {
        index: 1,
        type: 'bullets',
        title: '要点页',
        eyebrow: '01 现状',
        bullets: ['第一条', '第二条', '第三条'],
        takeaway: '结论只有一句',
      },
      { index: 2, type: 'chart', title: '趋势', chart: { kind: 'bar', series: [{ name: 's', data: [{ label: 'a', value: 3 }, { label: 'b', value: 5 }] }] } },
    ],
  }
  const raw = await buildPptx({ deck })
  const { buffer, patched, slides, clickSteps } = await addSlideAnimations(raw, {
    slides: deck.slides.map((slide) => ({
      title: slide.title,
      subtitle: slide.subtitle,
      eyebrow: slide.eyebrow,
      takeaway: slide.takeaway,
    })),
  })

  assert.equal(slides, 3)
  assert.ok(patched >= 2, `有点击步骤的页面都应注入动画（实际 ${patched}/3，封面只有标题所以无步骤）`)
  assert.ok(clickSteps >= 5, `点击步骤应有若干（实际 ${clickSteps}）`)

  const entries = readZipEntries(buffer)
  // the cover has only a heading, so its one beat is the subtitle
  const coverXml = entries['ppt/slides/slide1.xml'].toString('utf8')
  assert.equal((coverXml.match(/nodeType="clickEffect"/g) || []).length, 1, '封面保留一次点击')
  for (const index of [2, 3]) {
    const xml = entries[`ppt/slides/slide${index}.xml`].toString('utf8')
    const timing = assertValidTiming(xml, { label: `slide${index}` })
    assert.ok(timing.includes('nodeType="clickEffect"'), `slide${index} 必须是单击触发`)
    assert.equal(/nodeType="(afterEffect|withEffect)"/.test(timing), false, `slide${index} 不应有自动播放`)
    const targets = [...timing.matchAll(/<p:spTgt spid="(\d+)"/g)].map((match) => Number(match[1]))
    const shapes = parseSlideShapes(xml)
    for (const spid of new Set(targets)) {
      const shape = shapes.find((item) => item.id === spid)
      assert.ok(shape, `slide${index}: spid ${spid} 应存在`)
      assert.notEqual(shape.kind, 'shape', `slide${index}: 装饰形状 ${spid} 不应有动画`)
    }
  }

  const bulletsXml = zipEntryText(buffer, 'ppt/slides/slide2.xml')
  assert.ok(bulletsXml.includes('build="p"'), '正文逐条出现')
  assert.ok(bulletsXml.includes('结论只有一句'), 'takeaway 写入幻灯片')
})

test('a page with nothing else to reveal lets the subtitle carry the click', () => {
  const cover = slideXml(
    [
      shape(2, { x: 0.56, y: 0.56, w: 0.62, h: 0.06 }),
      textShape(3, ['2026 年第三季度复盘'], { x: 0.56, y: 2, w: 7, h: 1, size: 4000, name: 'Title' }),
      textShape(4, ['AI 辅助内容生产的效率验证'], { x: 0.56, y: 3.1, w: 6, h: 0.4, size: 1600, name: 'Subtitle' }),
    ].join(''),
  )
  const { steps, static: statics } = planReveal(parseSlideShapes(cover), {
    title: '2026 年第三季度复盘',
    subtitle: 'AI 辅助内容生产的效率验证',
  })
  assert.deepEqual(steps, [[{ spid: 4, paragraph: null }]], '副标题成为这一次点击')
  assert.deepEqual(statics.sort((a, b) => a - b), [2, 3], '装饰与标题保持静态')

  const patched = injectTiming(cover, { title: '2026 年第三季度复盘', subtitle: 'AI 辅助内容生产的效率验证' })
  const timing = assertValidTiming(patched, { label: 'cover' })
  assert.equal((timing.match(/nodeType="clickEffect"/g) || []).length, 1)
})

test('a deck whose slides have no content at all stays valid', async () => {
  const raw = await buildPptx({ deck: { title: 'T', slides: [{ index: 0, type: 'quote', title: 'q', quote: { text: 'x' } }] } })
  const { buffer, patched } = await addSlideAnimations(raw, { slides: [{ title: 'q' }] })
  assert.equal(buffer.subarray(0, 2).toString(), 'PK')
  assert.ok(patched <= 1)
  assert.ok(zipEntryText(buffer, 'ppt/slides/slide1.xml').length > 0)
})
