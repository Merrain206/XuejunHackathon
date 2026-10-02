// 回归测试：timing 树的插入位置必须永远处于 slide 的直接子元素层
// （曾出现：表格 graphicFrame 内部嵌套的 extLst 被误判为 slide 级 extLst，
//   导致 timing 被插进 spTree 中间 → 该页动画被 WPS/PowerPoint 整页丢弃）
import assert from 'node:assert/strict'
import test from 'node:test'
import { XMLValidator } from 'fast-xml-parser'

import { injectTiming, parseSlideShapes } from '../../server/pptxAnimate.js'

const EMU = 914400
const rect = (x, y, w, h) =>
  `<p:spPr><a:xfrm><a:off x="${Math.round(x * EMU)}" y="${Math.round(y * EMU)}"/><a:ext cx="${Math.round(w * EMU)}" cy="${Math.round(h * EMU)}"/></a:xfrm></p:spPr>`

function textShape(id, paragraphs, { x = 1, y = 1, w = 5, h = 0.5, size = 1800 } = {}) {
  const runs = paragraphs.map((text) => `<a:p><a:r><a:rPr sz="${size}"/><a:t>${text}</a:t></a:r></a:p>`).join('')
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="Text ${id}"></p:cNvPr><p:cNvSpPr/><p:nvPr></p:nvPr></p:nvSpPr>${rect(x, y, w, h)}<p:txBody><a:bodyPr/>${runs}</p:txBody></p:sp>`
}

const NESTED_EXT_LST_TABLE =
  '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table 4"/>' +
  '<p:cNvGraphicFramePr/><p:nvPr><p:extLst><p:ext uri="{NESTED}"/></p:extLst></p:nvPr></p:nvGraphicFramePr>' +
  `<p:xfrm><a:off x="${EMU}" y="${EMU}"/><a:ext cx="${2 * EMU}" cy="${EMU}"/></p:xfrm><p:graphic/></p:graphicFrame>`

function slideXml(body, { slideExtLst = false } = {}) {
  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' +
    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">' +
    `<p:cSld><p:spTree>${body}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>` +
    (slideExtLst ? '<p:extLst><p:ext uri="{SLIDE}"/></p:extLst>' : '') +
    '</p:sld>'
  )
}

test('表格页：timing 落在 slide 层，而不是表格内部的 extLst 之前', () => {
  const xml = slideXml(NESTED_EXT_LST_TABLE + textShape(3, ['表格页标题'], { y: 0.6, size: 2800 }))
  const shapes = parseSlideShapes(xml)
  assert.ok(shapes.some((shape) => shape.kind === 'graphic'), '表格应被解析为 graphic 形状')

  const patched = injectTiming(xml, { title: '表格页标题' })
  const timingAt = patched.indexOf('<p:timing>')
  assert.ok(timingAt !== -1, '表格页也必须有动画')
  assert.ok(patched.indexOf('</p:cSld>') < timingAt, 'timing 必须在 </p:cSld> 之后（slide 直接子元素）')
  assert.ok(patched.indexOf('</p:graphicFrame>') < timingAt, 'timing 不能落在 graphicFrame 内部')
  assert.ok(patched.indexOf('{NESTED}') < timingAt, '嵌套 extLst 属于表格，位置不变')
  assert.ok(timingAt < patched.indexOf('</p:sld>'))
  assert.equal(XMLValidator.validate(patched), true, 'XML 必须良构')
  assert.equal((patched.match(/nodeType="clickEffect"/g) || []).length, 1, '表格本身是一次点击')
})

test('slide 级 extLst 仍然排在 timing 之后（CT_Slide 顺序）', () => {
  const xml = slideXml(NESTED_EXT_LST_TABLE + textShape(3, ['表格页标题'], { y: 0.6, size: 2800 }), {
    slideExtLst: true,
  })
  const patched = injectTiming(xml, { title: '表格页标题' })
  const timingAt = patched.indexOf('<p:timing>')
  const slideExtAt = patched.indexOf('<p:extLst><p:ext uri="{SLIDE}"/>')
  assert.ok(timingAt !== -1 && slideExtAt !== -1)
  assert.ok(timingAt < slideExtAt, 'cSld → clrMapOvr → timing → extLst')
  assert.equal(XMLValidator.validate(patched), true)
})
