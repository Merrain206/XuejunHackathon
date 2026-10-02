// Shared fixtures and the ECMA-376 checker used by tests/unit/pptxAnimationXml.test.js.
//
// The checker is a miniature XSD for the injected `<p:timing>` tree: it encodes
// the child sequences and attribute sets the PresentationML schema defines, so a
// regression that reorders or over-decorates the tree fails loudly here instead
// of surfacing as a repaired file in PowerPoint.
import assert from 'node:assert/strict'
import { XMLValidator } from 'fast-xml-parser'

const EMU = 914400
export const emu = (inches) => Math.round(inches * EMU)

/* ------------------------------------------------------------- fixtures -- */

export const rect = (x, y, w, h) =>
  `<p:spPr><a:xfrm><a:off x="${emu(x)}" y="${emu(y)}"/><a:ext cx="${emu(w)}" cy="${emu(h)}"/></a:xfrm></p:spPr>`

export function textShape(id, paragraphs, { x = 1, y = 1, w = 4, h = 0.4, size = 1800, name = 'Text' } = {}) {
  const runs = paragraphs.map((text) => `<a:p><a:r><a:rPr sz="${size}"/><a:t>${text}</a:t></a:r></a:p>`).join('')
  return (
    `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="${name} ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>` +
    `${rect(x, y, w, h)}<p:txBody><a:bodyPr/>${runs}</p:txBody></p:sp>`
  )
}

export function decoration(id, { x = 1, y = 1, w = 4, h = 0.1 } = {}) {
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="Decoration ${id}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>${rect(x, y, w, h)}</p:sp>`
}

export function picture(id, { x = 6, y = 1, w = 3, h = 3 } = {}) {
  return (
    `<p:pic><p:nvPicPr><p:cNvPr id="${id}" name="Image ${id}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>` +
    `<p:blipFill/><p:spPr><a:xfrm><a:off x="${emu(x)}" y="${emu(y)}"/><a:ext cx="${emu(w)}" cy="${emu(h)}"/></a:xfrm></p:spPr></p:pic>`
  )
}

export function graphicFrame(id, { x = 1, y = 2, w = 6, h = 2 } = {}) {
  return (
    `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${id}" name="Chart ${id}"/><p:nvPr/></p:nvGraphicFramePr>` +
    `<p:xfrm><a:off x="${emu(x)}" y="${emu(y)}"/><a:ext cx="${emu(w)}" cy="${emu(h)}"/></p:xfrm><p:graphic/></p:graphicFrame>`
  )
}

const PRESENTATIONML_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'

/** A slide body wrapped in the preamble pptxgenjs itself writes. */
export function slideXml(body, { prefix = 'p', attrs = '', tail = '' } = {}) {
  const ns = prefix ? `xmlns:${prefix}="${PRESENTATIONML_NS}"` : `xmlns="${PRESENTATIONML_NS}"`
  const q = (name) => (prefix ? `${prefix}:${name}` : name)
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
    `<${q('sld')} xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ${ns}${attrs}>` +
    `<${q('cSld')}><${q('spTree')}><${q('nvGrpSpPr')}><${q('cNvPr')} id="1" name=""/><${q('cNvGrpSpPr')}/><${q('nvPr')}/></${q('nvGrpSpPr')}><${q('grpSpPr')}/>${body}</${q('spTree')}></${q('cSld')}>` +
    `<${q('clrMapOvr')}><a:masterClrMapping/></${q('clrMapOvr')}>${tail}</${q('sld')}>`
  )
}

export const META = { title: '本季度三条判断', subtitle: '副标题', eyebrow: '01 结论', takeaway: '效率提升来自流程改造' }

/** decoration + eyebrow + title + subtitle + 3 bullets + chart + picture + takeaway + furniture */
export const RICH_SLIDE = slideXml(
  [
    decoration(2, { x: 0.56, y: 0.56, w: 0.62, h: 0.06 }),
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

/* ------------------------------------------------- a mini XSD for timing -- */

/**
 * ECMA-376 sequences the injected tree must satisfy. Required elements come
 * first, optional ones in parentheses after them — exactly the `<xsd:sequence>`
 * order of the corresponding complexType.
 */
export const CHILD_SEQUENCES = {
  timing: ['tnLst', '(bldLst)', '(extLst)'],
  tnLst: ['par'],
  bldLst: ['bldP'],
  par: ['cTn'],
  cTn: ['(stCondLst)', '(endCondLst)', '(endSync)', '(iterate)', '(childTnLst)', '(subTnLst)'],
  seq: ['cTn', '(prevCondLst)', '(nextCondLst)'],
  stCondLst: ['cond'],
  prevCondLst: ['cond'],
  nextCondLst: ['cond'],
  cond: ['tgtEl', 'tn', 'rtn'],
  childTnLst: ['par', 'seq', 'set', 'animEffect', 'anim', 'animClr', 'animMotion', 'animRot', 'animScale', 'cmd', 'audio', 'video', 'excl'],
  tgtEl: ['spTgt', 'sldTgt', 'sndTgt', 'inkTgt'],
  tn: [],
  rtn: [],
  spTgt: ['(txEl)'],
  txEl: ['(charRg)', '(pRg)'],
  pRg: [],
  cBhvr: ['cTn', '(tgtEl)', '(attrNameLst)'],
  attrNameLst: ['attrName'],
  attrName: [],
  set: ['cBhvr', 'to'],
  to: ['strVal', 'boolVal', 'intVal', 'fltVal', 'clrVal'],
  strVal: [],
  animEffect: ['cBhvr', '(progress)'],
  sldTgt: [],
  bldP: ['(tmplLst)'],
  extLst: [],
}

/** Attributes ECMA-376 allows on each element; anything else is a smil-ism. */
export const ALLOWED_ATTRS = {
  timing: [],
  tnLst: [],
  bldLst: [],
  bldP: ['spid', 'grpId', 'build', 'uiExpand', 'bldLvl', 'animBg', 'autoUpdateAnimBg', 'rev', 'advAuto'],
  par: [],
  cTn: ['id', 'presetID', 'presetClass', 'presetSubtype', 'dur', 'fill', 'nodeType', 'grpId', 'restart', 'accel', 'decel', 'autoRev', 'spd', 'repeatCount', 'repeatDur', 'syncBehavior', 'tmFilter', 'evtFilter', 'display', 'masterRel', 'bldLvl', 'afterEffect', 'nodePh'],
  seq: ['concurrent', 'nextAc', 'prevAc'],
  stCondLst: [],
  prevCondLst: [],
  nextCondLst: [],
  endCondLst: [],
  cond: ['evt', 'delay'],
  childTnLst: [],
  tgtEl: [],
  spTgt: ['spid', 'elemType'],
  sldTgt: [],
  txEl: [],
  pRg: ['st', 'end'],
  cBhvr: ['additive'],
  attrNameLst: [],
  attrName: [],
  set: [],
  to: [],
  strVal: ['val'],
  animEffect: ['transition', 'filter', 'prLst'],
  extLst: ['uri'],
  tn: ['val'],
  rtn: ['val'],
}

/**
 * Elements whose children are an `xsd:choice` rather than a sequence: one legal
 * child in any order, so the required/order rules do not apply to them.
 */
export const CHOICE_ELEMENTS = new Set(['tgtEl', 'to', 'cond'])

/** CT_TimeNodeList and CT_BuildList are repeated choices (minOccurs="0"). */
export const REPEATED = new Set(['childTnLst', 'bldLst'])

const TAG_RE = /<(\/?)([\w.-]+(?::[\w.-]+)?)((?:\s+[\w.:-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/?)>/g

/** Every element of the timing tree, in document order, with its child list. */
export function timingTree(timing, prefixes = ['p', 'pm']) {
  const stack = []
  const elements = []
  let match
  while ((match = TAG_RE.exec(timing)) !== null) {
    const closing = match[1] === '/'
    const qname = match[2]
    const colon = qname.indexOf(':')
    const prefix = colon === -1 ? 'p' : qname.slice(0, colon)
    const local = colon === -1 ? qname : qname.slice(colon + 1)
    if (!prefixes.includes(prefix) || !CHILD_SEQUENCES[local]) continue
    if (closing) {
      stack.pop()
      continue
    }
    const attributes = [...match[3].matchAll(/([\w.:-]+)\s*=/g)].map((item) => item[1])
    const element = { local, attributes, children: [] }
    if (stack.length) stack.at(-1).children.push(local)
    elements.push(element)
    if (match[4] !== '/') stack.push(element)
  }
  return elements
}

export function assertTimingSchema(timing, label) {
  for (const element of timingTree(timing)) {
    const sequence = CHILD_SEQUENCES[element.local]
    const required = sequence.filter((name) => !name.startsWith('('))
    const optional = sequence.filter((name) => name.startsWith('(')).map((name) => name.slice(1, -1))
    const repeated = REPEATED.has(element.local)
    const choice = CHOICE_ELEMENTS.has(element.local)
    for (const child of element.children) {
      assert.ok(
        sequence.includes(child) || optional.includes(child),
        `${label}: <p:${child}> is not a legal child of <p:${element.local}>`,
      )
    }
    if (!repeated && !choice) {
      for (const name of required) {
        assert.ok(element.children.includes(name), `${label}: <p:${element.local}> requires <p:${name}>`)
      }
      const seen = new Set()
      for (const child of element.children) {
        assert.equal(seen.has(child), false, `${label}: <p:${child}> may not repeat inside <p:${element.local}>`)
        seen.add(child)
      }
      let cursor = 0
      for (const child of element.children) {
        const requiredAt = required.indexOf(child)
        const position = requiredAt >= 0 ? requiredAt : required.length + optional.indexOf(child)
        assert.ok(position >= cursor, `${label}: <p:${child}> out of schema order inside <p:${element.local}>`)
        cursor = position
      }
    } else if (choice) {
      assert.equal(element.children.length <= 1, true, `${label}: <p:${element.local}> allows one child`)
    }
    const allowed = ALLOWED_ATTRS[element.local]
    for (const attribute of element.attributes) {
      assert.ok(allowed.includes(attribute), `${label}: attribute ${attribute} is not defined on <p:${element.local}>`)
    }
  }
}

/**
 * Every structural promise the injector makes about a whole slide: CT_Slide
 * child order, the timing schema, id rules, click-only triggers, fade-only
 * effects, existing targets and build-list consistency.
 */
export function auditSlide(xml, { label = 'slide', prefix = 'p' } = {}) {
  const validation = XMLValidator.validate(xml)
  assert.equal(validation, true, `${label}: XML must stay well-formed (${JSON.stringify(validation)})`)

  const q = (name) => (prefix ? `${prefix}:${name}` : name)
  const open = `<${q('timing')}>`
  const close = `</${q('timing')}>`
  const start = xml.indexOf(open)
  const end = xml.indexOf(close)
  assert.ok(start !== -1 && end > start, `${label}: a complete timing tree is required`)
  const timing = xml.slice(start, end + close.length)

  // CT_Slide child order: cSld → clrMapOvr? → transition? → timing? → extLst?
  assert.ok(xml.indexOf(`<${q('cSld')}>`) < start, `${label}: timing comes after cSld`)
  assert.ok(xml.indexOf(`<${q('clrMapOvr')}>`) < start, `${label}: timing comes after clrMapOvr`)
  const transition = xml.indexOf(`<${q('transition')}`)
  if (transition !== -1) assert.ok(transition < start, `${label}: timing comes after transition`)
  const slideEnd = xml.lastIndexOf(`</${q('sld')}>`)
  assert.ok(end < slideEnd, `${label}: timing comes before the slide close tag`)
  const extLst = xml.indexOf(`<${q('extLst')}>`)
  if (extLst !== -1) assert.ok(end < extLst, `${label}: timing comes before the slide-level extLst`)
  assert.equal((xml.match(new RegExp(`<${q('timing')}>`, 'g')) || []).length, 1, `${label}: exactly one timing tree`)

  assertTimingSchema(timing, label)

  const ids = [...timing.matchAll(new RegExp(`<${q('cTn')} id="(\\d+)"`, 'g'))].map((match) => Number(match[1]))
  assert.equal(new Set(ids).size, ids.length, `${label}: p:cTn ids must be unique`)
  assert.equal(ids[0], 1, `${label}: the tmRoot cTn is id 1`)
  assert.equal(ids[1], 2, `${label}: the mainSeq cTn is id 2`)
  ids.forEach((id, index) => {
    assert.equal(id, index + 1, `${label}: p:cTn ids must be contiguous from 1 (found ${ids.join(',')})`)
  })

  // Every trigger is a click: nothing auto-plays.
  const nodeTypes = [...timing.matchAll(/nodeType="([a-zA-Z]+)"/g)].map((match) => match[1])
  assert.deepEqual(
    [...new Set(nodeTypes)].sort(),
    ['clickEffect', 'mainSeq', 'tmRoot', 'withEffect'].filter((type) => nodeTypes.includes(type)).sort(),
    `${label}: only click-triggered nodes`,
  )
  assert.equal(/nodeType="(afterEffect|interactiveSeq)"/.test(timing), false, `${label}: no automatic sequence`)

  // One visible effect per animation block: fade, entrance, 350 ms, nothing else.
  const animEffect = new RegExp(`<${q('animEffect')}[\\s/>]`, 'g')
  assert.equal((timing.match(animEffect) || []).length, (timing.match(/presetClass="entr"/g) || []).length)
  assert.equal(/presetID="(?!10)\d+"/.test(timing), false, `${label}: no effect other than Fade`)
  assert.equal(/presetClass="(?!entr)/.test(timing), false, `${label}: entrance effects only`)
  assert.equal(/filter="(?!fade)/.test(timing), false, `${label}: the fade filter only`)
  assert.equal(/transition="(?!in)/.test(timing), false, `${label}: entrance transitions only`)
  const durations = new Set([...timing.matchAll(/dur="([^"]*)"/g)].map((match) => match[1]))
  assert.deepEqual(
    [...durations].sort(),
    ['1', '350', 'indefinite'],
    `${label}: only the effect duration, the 1 ms visibility set and the container nodes`,
  )
  const animBehaviours = new RegExp(`<${q('anim')}(Motion|Rot|Scale|Clr)[\\s/>]`)
  assert.equal(animBehaviours.test(timing), false, `${label}: no extra behaviours`)

  // Targets exist on the slide, and static shapes are never targeted.
  const cNvPr = new RegExp(`<${q('cNvPr')}\\b[^>]*\\bid=["'](\\d+)["']`, 'g')
  const shapeIds = new Set([...xml.matchAll(cNvPr)].map((match) => Number(match[1])))
  const spTgt = new RegExp(`<${q('spTgt')} spid="(\\d+)"`, 'g')
  const targeted = new Set([...timing.matchAll(spTgt)].map((match) => Number(match[1])))
  for (const spid of targeted) assert.ok(shapeIds.has(spid), `${label}: spid=${spid} must exist on the slide`)

  // Build entries: one per animated shape, at the shape's first group (0).
  const builds = new Map()
  const bldP = new RegExp(`<${q('bldP')} spid="(\\d+)" grpId="(\\d+)"`, 'g')
  for (const match of timing.matchAll(bldP)) builds.set(Number(match[1]), Number(match[2]))
  for (const spid of targeted) {
    assert.ok(builds.has(spid), `${label}: animated shape ${spid} needs a <p:bldP> entry`)
    assert.equal(builds.get(spid), 0, `${label}: build entry grpId must be the shape's first group (0)`)
  }
  for (const spid of builds.keys()) assert.ok(targeted.has(spid), `${label}: build entry ${spid} must be animated`)

  return timing
}
