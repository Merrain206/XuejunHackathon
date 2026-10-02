// Adversarial regression tests for the injected <p:timing> tree.
//
// Every assertion maps to a finding in docs/pptx-animation-audit.md: the exact
// element order and attribute set ECMA-376 requires, the id rules, the
// click-only trigger rule, build-list consistency, and the parser's behaviour on
// XML that is not shaped the way pptxgenjs writes it.
import assert from 'node:assert/strict'
import test from 'node:test'
import { XMLValidator } from 'fast-xml-parser'

import {
  addSlideAnimations,
  buildTimingXml,
  groupIdsOf,
  injectTiming,
  parseSlideShapes,
  planReveal,
  readSlidePrefix,
} from '../../server/pptxAnimate.js'
import { buildPptx } from '../../server/pptx.js'
import { readZipEntries } from '../helpers/zip.mjs'
import {
  META,
  RICH_SLIDE,
  auditSlide,
  decoration,
  emu,
  graphicFrame,
  picture,
  slideXml,
  textShape,
} from '../helpers/pptxAnimationFixtures.mjs'

/* --------------------------------------------------------------- tests -- */

test('readSlidePrefix reads the prefix the slide actually binds', () => {
  assert.equal(readSlidePrefix(RICH_SLIDE), 'p')
  assert.equal(readSlidePrefix(slideXml('', { prefix: 'pm' })), 'pm')
  assert.equal(readSlidePrefix(slideXml('', { prefix: '' })), '')
  assert.equal(readSlidePrefix('<x:root/>'), null)
  assert.equal(readSlidePrefix('not xml'), null)
  assert.equal(readSlidePrefix(''), null)
  assert.equal(readSlidePrefix(null), null)
})

test('the injected tree satisfies the ECMA-376 child sequences and attribute sets', () => {
  auditSlide(injectTiming(RICH_SLIDE, META), { label: 'rich' })
})

test('cTn ids are unique and contiguous, and every id is a number', () => {
  const timing = auditSlide(injectTiming(RICH_SLIDE, META), { label: 'ids' })
  const ids = [...timing.matchAll(/<p:cTn id="(\d+)"/g)].map((match) => Number(match[1]))
  assert.ok(ids.length >= 20, `a rich slide owns several nodes (${ids.length})`)
  assert.equal(ids.at(-1), ids.length)
})

test('grpId is per shape, starting at 0, and matches the build entry', () => {
  const kpi = slideXml(
    [
      textShape(2, ['一', '二', '三'], { x: 1, y: 2, w: 5, h: 1.6 }),
      graphicFrame(3),
      textShape(4, ['结语'], { x: 1, y: 4.4, w: 5, h: 0.4 }),
    ].join(''),
  )
  const steps = planReveal(parseSlideShapes(kpi), { title: 't', takeaway: '结语' }).steps
  assert.deepEqual(
    groupIdsOf(steps),
    [[0], [1], [2], [0], [0]],
    'paragraph groups count 0,1,2 for their own shape; whole-shape effects stay at 0',
  )
  const timing = auditSlide(injectTiming(kpi, { title: 't', takeaway: '结语' }), { label: 'kpi' })
  assert.ok(timing.includes('grpId="2"'), 'the third paragraph is its own build group')
  assert.ok(timing.includes('<p:bldP spid="2" grpId="0" uiExpand="1" build="p"/>'))
  assert.ok(timing.includes('<p:bldP spid="3" grpId="0"/>'), 'the chart has a build entry too')
})

test('the main sequence declares its prev/next conditions like PowerPoint', () => {
  const timing = injectTiming(RICH_SLIDE, META)
  assert.match(
    timing,
    /<p:seq concurrent="1" nextAc="seek"><p:cTn id="2"[^>]*><p:childTnLst>[\s\S]*?<\/p:childTnLst><\/p:cTn><p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt\/><\/p:tgtEl><\/p:cond><\/p:prevCondLst><p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt\/><\/p:tgtEl><\/p:cond><\/p:nextCondLst><\/p:seq>/,
    'cTn → prevCondLst → nextCondLst, the order CT_TLTimeNodeSequence enforces',
  )
  assert.equal((timing.match(/delay="indefinite"/g) || []).length, 1, 'only the first click group waits')
  assert.ok(timing.includes('<p:cond delay="0"/>'))
  assert.equal(/delay="(?!0"|indefinite")/.test(timing), false, 'a click group never uses a millisecond delay')
})

test('injection uses the prefix declared on the sld root, not a hard-coded p:', () => {
  const body = [textShape(2, ['正文'], { size: 1800 }), textShape(3, ['第二条'], { size: 1800, y: 3 })]
    .join('')
    .replace(/p:/g, 'pm:')
  const prefixed = slideXml(body, { prefix: 'pm' })
  assert.equal(readSlidePrefix(prefixed), 'pm')
  assert.deepEqual(parseSlideShapes(prefixed).map((shape) => shape.id), [2, 3], 'a foreign prefix parses just as well')

  const patched = injectTiming(prefixed, { title: '标题' })
  assert.ok(patched.includes('<pm:timing>'), 'the injected tree reuses the slide prefix')
  assert.equal(patched.includes('<p:timing>'), false, 'no undeclared p: prefix is introduced')
  assert.equal(XMLValidator.validate(patched), true)
  auditSlide(patched, { label: 'pm-prefixed', prefix: 'pm' })
  assert.ok(patched.includes('<pm:spTgt spid="2"'), 'targets use the same prefix')
})

test('a slide with no PresentationML prefix is left alone', () => {
  const bare = slideXml(textShape(2, ['正文'], { size: 1800 }), { prefix: '' })
  assert.equal(readSlidePrefix(bare), '')
  assert.equal(injectTiming(bare, { title: '标题' }), bare, 'no undeclared prefix is ever emitted')
  // The parser still reads such a slide, it is only the writer that declines.
  assert.deepEqual(parseSlideShapes(bare).map((shape) => shape.id), [2])
})

test('a slide-level extLst stays last, after the injected timing', () => {
  const tail =
    '<p:extLst><p:ext uri="{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}"><p14:creationId xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" val="1"/></p:ext></p:extLst>'
  const patched = injectTiming(slideXml(textShape(2, ['正文'], { size: 1800 }), { tail }), { title: '标题' })
  assert.ok(patched.indexOf('<p:timing>') < patched.indexOf('<p:extLst>'), 'timing precedes extLst')
  assert.ok(patched.indexOf('</p:timing>') < patched.indexOf('<p:extLst>'))
  auditSlide(patched, { label: 'extLst' })
})

test('a slide that already has a transition keeps it before timing', () => {
  const patched = injectTiming(
    slideXml(textShape(2, ['正文'], { size: 1800 }), { tail: '<p:transition spd="slow"><p:fade/></p:transition>' }),
    { title: '标题' },
  )
  assert.ok(patched.indexOf('<p:transition') < patched.indexOf('<p:timing>'))
  auditSlide(patched, { label: 'transition' })
})

test('decoration, headings, page furniture and footers are never targeted', () => {
  const slide = slideXml(
    [
      decoration(2, { x: 0, y: 0, w: 10, h: 0.1 }),
      textShape(3, ['01 结论'], { size: 1000, y: 0.5 }),
      textShape(4, ['本季度三条判断'], { size: 2800, y: 0.8 }),
      textShape(5, ['副标题'], { size: 1200, y: 1.5 }),
      textShape(6, ['一', '二'], { x: 0.56, y: 2, w: 8, h: 0.9, size: 1800 }),
      textShape(7, ['效率提升来自流程改造'], { x: 0.56, y: 4.2, w: 8, h: 0.5, size: 1300 }),
      textShape(8, ['[1] 来源'], { x: 0.56, y: 4.8, w: 3, h: 0.25, size: 900 }),
      textShape(9, ['3 / 10'], { x: 8.5, y: 4.8, w: 1, h: 0.25, size: 1000 }),
    ].join(''),
  )
  const { steps, static: statics } = planReveal(parseSlideShapes(slide), META)
  assert.deepEqual(statics.sort((a, b) => a - b), [2, 3, 4, 5, 8, 9])
  assert.deepEqual(steps.flat().map((target) => target.spid), [6, 6, 7], 'the takeaway closes the page')

  const timing = auditSlide(injectTiming(slide, META), { label: 'static' })
  for (const spid of [2, 3, 4, 5, 8, 9]) {
    assert.equal(timing.includes(`spid="${spid}"`), false, `spid ${spid} must not appear in timing`)
  }
  assert.ok(timing.indexOf('spid="7"') > timing.indexOf('spid="6"'), 'the takeaway is the last click')
})

test('paragraph builds are marked with build="p" and only for multi-paragraph boxes', () => {
  const slide = slideXml(
    [textShape(2, ['一', '二', '三'], { size: 1800, y: 2 }), textShape(3, ['单段'], { size: 1800, y: 4 })].join(''),
  )
  const timing = auditSlide(injectTiming(slide, { title: '标题' }), { label: 'builds' })
  assert.ok(timing.includes('<p:bldP spid="2" grpId="0" uiExpand="1" build="p"/>'))
  assert.equal(/bldP spid="3"[^>]*build/.test(timing), false, 'a single-paragraph box is not a paragraph build')
  assert.equal((timing.match(/<p:pRg /g) || []).length, 6, 'each paragraph effect names its range twice')
  assert.ok(timing.includes('<p:pRg st="0" end="0"/>'))
  assert.ok(timing.includes('<p:pRg st="2" end="2"/>'))
})

test('effects that share a click use withEffect inside one click group', () => {
  const slide = slideXml(
    [
      textShape(2, ['2.9'], { x: 1, y: 2, w: 2, h: 0.5, size: 3200 }),
      textShape(3, ['平均交付周期'], { x: 1, y: 2.5, w: 2, h: 0.3, size: 1200 }),
      textShape(4, ['-42%'], { x: 1, y: 2.85, w: 2, h: 0.25, size: 1100 }),
    ].join(''),
  )
  const timing = auditSlide(injectTiming(slide, { title: '指标' }), { label: 'unit' })
  assert.equal((timing.match(/nodeType="clickEffect"/g) || []).length, 1)
  assert.equal((timing.match(/nodeType="withEffect"/g) || []).length, 2)
  assert.ok(timing.indexOf('nodeType="withEffect"') > timing.indexOf('nodeType="clickEffect"'))
  assert.equal((timing.match(/<p:cond delay="indefinite"\/>/g) || []).length, 1)
})

test('the effect is always an entrance fade with a 350 ms duration', () => {
  const timing = injectTiming(RICH_SLIDE, META)
  for (const match of timing.matchAll(/<p:animEffect\b[^>]*>/g)) {
    assert.equal(match[0], '<p:animEffect transition="in" filter="fade">')
  }
  const effects = [...timing.matchAll(/<p:cTn id="\d+" ([^>]*nodeType="(?:clickEffect|withEffect)"[^>]*)>/g)].map((match) => match[1])
  assert.ok(effects.length >= 6, `every animated step owns one effect node (${effects.length})`)
  for (const attributes of effects) {
    assert.match(attributes, /presetID="10" presetClass="entr" presetSubtype="0"/)
    assert.match(attributes, /fill="hold"/)
    assert.match(attributes, /grpId="\d+"/)
  }
  assert.ok(injectTiming(RICH_SLIDE, { ...META, durationMs: 500 }).includes('dur="500"'))
})

test('click order is preserved: paragraphs, chart, picture, takeaway', () => {
  const timing = auditSlide(injectTiming(RICH_SLIDE, META), { label: 'order' })
  const order = [...timing.matchAll(/<p:cTn id="\d+" presetID="10"[^>]*nodeType="(clickEffect|withEffect)">[\s\S]*?<p:spTgt spid="(\d+)"/g)]
    .filter((match) => match[1] === 'clickEffect')
    .map((match) => Number(match[2]))
  assert.deepEqual(order, [6, 6, 6, 7, 8, 9])
})

test('injection is idempotent and never damages a slide it cannot read', () => {
  const once = injectTiming(RICH_SLIDE, META)
  assert.equal(injectTiming(once, META), once)
  assert.equal(injectTiming(injectTiming(once, META), META), once)

  const noClose = '<p:sld><p:cSld><p:spTree>'
  assert.equal(injectTiming(noClose, META), noClose, 'unterminated XML is returned untouched')
  assert.equal(injectTiming('not xml at all', META), 'not xml at all')
  assert.equal(injectTiming('', META), '')
  assert.equal(injectTiming(null, META), null)
  assert.equal(injectTiming(undefined, META), undefined)
  const empty = slideXml('')
  assert.equal(injectTiming(empty, META), empty, 'a slide without shapes stays untouched')

  const wrongRoot =
    '<x:whatever xmlns:x="u"><p:cSld><p:spTree>' + textShape(2, ['正文'], { size: 1800 }) + '</p:spTree></p:cSld></x:whatever>'
  assert.equal(injectTiming(wrongRoot, META), wrongRoot, 'only a slide root is animated')

  const unclosed = slideXml('').replace('</p:sld>', '')
  assert.equal(injectTiming(unclosed, META), unclosed)

  const already = RICH_SLIDE.replace('</p:sld>', '<p:timing><p:tnLst/></p:timing></p:sld>')
  assert.equal(injectTiming(already, META), already, 'a slide with its own timing is left alone')
})

/* ------------------------------------------------ parser robustness ----- */

test('the parser accepts attributes in any order and both quote styles', () => {
  const slide =
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">' +
    '<p:cSld><p:spTree>' +
    "<p:sp><p:nvSpPr><p:cNvPr name='Text 0' id='2'/></p:nvSpPr>" +
    "<p:spPr><a:xfrm><a:ext cy='914400' cx='1828800'/><a:off y='914400' x='457200'/></a:xfrm></p:spPr>" +
    "<p:txBody><a:p><a:r><a:rPr sz='1800'/><a:t>正文</a:t></a:r></a:p></p:txBody></p:sp>" +
    '</p:spTree></p:cSld></p:sld>'
  const [shape] = parseSlideShapes(slide)
  assert.equal(shape.id, 2)
  assert.equal(shape.kind, 'text')
  assert.deepEqual(shape.rect, { x: 0.5, y: 1, w: 2, h: 1 })
  assert.equal(shape.fontPt, 18)
  auditSlide(injectTiming(slide, { title: '标题' }), { label: 'reordered' })
})

test('the parser handles graphicFrame geometry and unknown xfrm attributes', () => {
  const frame =
    '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="Chart 1"/><p:nvPr/></p:nvGraphicFramePr>' +
    '<p:xfrm rot="0" flipH="0"><a:ext cx="5486400" cy="1828800"/><a:off x="914400" y="1828800"/></p:xfrm>' +
    '<a:graphic><a:graphicData uri="x"/></a:graphic></p:graphicFrame>'
  const [shape] = parseSlideShapes(slideXml(frame))
  assert.equal(shape.kind, 'graphic')
  assert.deepEqual(shape.rect, { x: 1, y: 2, w: 6, h: 2 })
})

test('the parser reads shapes inside a grpSp instead of ignoring them', () => {
  const group =
    '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="20" name="Group 1"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="9144000" cy="6858000"/></a:xfrm></p:grpSpPr>' +
    textShape(21, ['组内文本'], { x: 2, y: 2, w: 3, h: 0.5, size: 1800 }) +
    '</p:grpSp>'
  const slide = slideXml(group)
  assert.deepEqual(
    parseSlideShapes(slide).map((shape) => [shape.id, shape.kind]),
    [[21, 'text']],
    'the group container is skipped, its children are visible to the planner',
  )
  const timing = auditSlide(injectTiming(slide, { title: '标题' }), { label: 'group' })
  assert.ok(timing.includes('<p:spTgt spid="21"/>'), 'the grouped text box is the reveal step')
  assert.equal(timing.includes('spid="20"'), false, 'the group container itself stays static')
})

test('a grouped shape reads nothing from its container', () => {
  const group =
    '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="30" name="G"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm></p:grpSpPr>' +
    '<p:sp><p:nvSpPr><p:cNvPr id="31" name="No geometry"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>' +
    '<p:txBody><a:p><a:r><a:rPr sz="2400"/><a:t>组内</a:t></a:r></a:p></p:txBody></p:sp>' +
    '</p:grpSp>'
  const shapes = parseSlideShapes(slideXml(group))
  const child = shapes.find((shape) => shape.id === 31)
  assert.equal(child.rect, null, 'the group transform is not attributed to the child')
  assert.equal(child.fontPt, 24)
})

test('shapes without usable geometry never break the plan', () => {
  const noGeometry =
    '<p:sp><p:nvSpPr><p:cNvPr id="5" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:p><a:r><a:rPr sz="1800"/><a:t>无坐标</a:t></a:r></a:p></p:txBody></p:sp>'
  const slide = slideXml(noGeometry + textShape(6, ['正常文本框'], { size: 1800, x: 1, y: 3 }))
  const shapes = parseSlideShapes(slide)
  assert.equal(shapes.find((shape) => shape.id === 5).rect, null)
  assert.equal(shapes.find((shape) => shape.id === 6).rect.w, 4)
  const { steps } = planReveal(shapes, { title: '标题' })
  assert.deepEqual(steps.flat().map((target) => target.spid), [5, 6], 'a rect-less box still joins a step')
  auditSlide(injectTiming(slide, { title: '标题' }), { label: 'no-geometry' })
})

test('non-ASCII text and entities survive a round trip', () => {
  const body =
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Text"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>' +
    `<p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${emu(4)}" cy="${emu(1)}"/></a:xfrm></p:spPr>` +
    '<p:txBody><a:p><a:r><a:rPr sz="1800"/><a:t>效率 &amp; 质量 — 100%</a:t></a:r></a:p></p:txBody></p:sp>'
  const patched = injectTiming(slideXml(body), { title: '标题' })
  assert.ok(patched.includes('效率 &amp; 质量 — 100%'), 'the shape text is untouched')
  auditSlide(patched, { label: 'unicode' })
})

test('comments, self-closing containers and a picture are all scanned', () => {
  const body =
    '<!-- a note -->' +
    '<p:grpSp><p:nvGrpSpPr><p:cNvPr id="30" name="Empty group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:grpSp>' +
    picture(31) +
    textShape(32, ['评论之后'], { size: 1800 })
  const shapes = parseSlideShapes(slideXml(body))
  assert.deepEqual(shapes.map((shape) => [shape.id, shape.kind]), [
    [31, 'picture'],
    [32, 'text'],
  ])
})

/* ------------------------------------------------------ end to end ------- */

test('an exported deck gets a schema-clean tree on every slide', async () => {
  const deck = {
    title: '动画验证',
    design: { palette: { bg: '#08111f', bgAlt: '#0f2036', fg: '#eaf2ff', muted: '#93a8c6', accent: '#4fc3f7', accent2: '#ffb74d' } },
    slides: [
      { index: 0, type: 'cover', title: '封面', subtitle: '副标题' },
      { index: 1, type: 'bullets', title: '要点页', eyebrow: '01 现状', bullets: ['第一条', '第二条', '第三条'], takeaway: '结论只有一句' },
      { index: 2, type: 'chart', title: '趋势', chart: { kind: 'bar', series: [{ name: 's', data: [{ label: 'a', value: 3 }, { label: 'b', value: 5 }] }] } },
      { index: 3, type: 'stats', title: '指标', stats: [{ value: '2.9', label: '交付周期' }, { value: '3860', label: '季度支出' }] },
    ],
  }
  const raw = await buildPptx({ deck })
  const { buffer, patched, slides, clickSteps } = await addSlideAnimations(raw, {
    slides: deck.slides.map((slide) => ({ title: slide.title, subtitle: slide.subtitle, eyebrow: slide.eyebrow, takeaway: slide.takeaway })),
  })
  assert.equal(slides, 4)
  assert.ok(patched >= 3, `most slides get animations (${patched}/4)`)
  assert.ok(clickSteps >= 6, `several click steps (${clickSteps})`)

  const entries = readZipEntries(buffer)
  for (let index = 1; index <= 4; index += 1) {
    auditSlide(entries[`ppt/slides/slide${index}.xml`].toString('utf8'), { label: `slide${index}` })
  }
})

test('re-running the injector over a patched deck is stable', async () => {
  const deck = { title: 'T', slides: [{ index: 0, type: 'bullets', title: '要点', bullets: ['一', '二'], takeaway: '结语' }] }
  const raw = await buildPptx({ deck })
  const meta = { slides: [{ title: '要点', takeaway: '结语' }] }
  const first = await addSlideAnimations(raw, meta)
  const second = await addSlideAnimations(first.buffer, meta)
  assert.equal(second.patched, 0, 'a patched deck is not patched twice')
  assert.equal(
    readZipEntries(second.buffer)['ppt/slides/slide1.xml'].toString('utf8'),
    readZipEntries(first.buffer)['ppt/slides/slide1.xml'].toString('utf8'),
  )
})

test('buildTimingXml stays well-formed at its edges', () => {
  const steps = [[{ spid: 4, paragraph: null }], [{ spid: 4, paragraph: 1 }]]
  const timing = buildTimingXml(steps, { prefix: 'p', buildSpids: [4] })
  assert.equal(
    XMLValidator.validate(`<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">${timing}</p:sld>`),
    true,
  )
  assert.ok(timing.includes('<p:bldP spid="4" grpId="0" uiExpand="1" build="p"/>'))
  assert.equal(buildTimingXml([]), buildTimingXml([], { prefix: 'p' }))
  assert.equal(buildTimingXml([], { prefix: 'pm' }).startsWith('<pm:timing>'), true)
  assert.equal(buildTimingXml([], { prefix: '' }).startsWith('<timing>'), true)
})
