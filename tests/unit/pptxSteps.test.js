import assert from 'node:assert/strict'
import test from 'node:test'

import { buildPptx } from '../../server/pptx.js'
import { addSlideAnimations } from '../../server/pptxAnimate.js'
import { expandBuildSteps } from '../../server/pptxSteps.js'
import { readZipEntries, zipEntryText } from '../helpers/zip.mjs'

const DECK = {
  title: '步骤演示',
  design: { palette: { bg: '#08111f', bgAlt: '#0f2036', fg: '#eaf2ff', muted: '#93a8c6', accent: '#4fc3f7', accent2: '#ffb74d' } },
  slides: [
    { index: 0, type: 'cover', title: '封面', subtitle: '副标题' },
    {
      index: 1,
      type: 'bullets',
      layout: 'bullets-list',
      title: '三条要点',
      bullets: ['第一条', '第二条', '第三条'],
      takeaway: '结论只出现在最后一屏',
      citations: [{ n: 1, title: '来源', url: 'https://example.com/a', publisher: 'example.com' }],
      image: { prompt: 'modern office', placement: 'side' },
    },
    { index: 2, type: 'quote', title: '观点', quote: { text: '一句话', attribution: '某人' } },
  ],
}

test('a page is expanded into cumulative reveal steps', () => {
  const { slides, expanded, added } = expandBuildSteps(DECK.slides)
  assert.equal(expanded, 1, '只有要点页需要拆分')
  assert.equal(added, 2, '3 条要点 → 3 步，多出 2 页')
  const steps = slides.filter((slide) => slide.buildTotal)
  assert.deepEqual(steps.map((slide) => slide.buildStep), [1, 2, 3])
  assert.deepEqual(steps.map((slide) => slide.bullets.length), [1, 2, 3])
  // the conclusion, the picture and the sources only land on the last step
  assert.deepEqual(steps.slice(0, 2).map((slide) => Boolean(slide.takeaway)), [false, false])
  assert.equal(steps.at(-1).takeaway, '结论只出现在最后一屏')
  assert.deepEqual(steps.slice(0, 2).map((slide) => slide.image), [null, null])
  assert.equal(steps.at(-1).image.prompt, 'modern office')
  assert.deepEqual(steps.slice(0, 2).map((slide) => slide.citations.length), [0, 0])
  assert.equal(steps.at(-1).citations.length, 1)
  // single-focus pages are never split, and indexes stay continuous
  assert.equal(slides.filter((slide) => slide.type === 'cover').length, 1)
  assert.equal(slides.filter((slide) => slide.type === 'quote').length, 1)
  assert.deepEqual(slides.map((slide) => slide.index), slides.map((_, index) => index))
})

test('step expansion respects the per-slide step ceiling', () => {
  const slide = {
    index: 0,
    type: 'bullets',
    title: '很多条',
    bullets: Array.from({ length: 20 }, (_, index) => `要点${index + 1}`),
  }
  const { slides } = expandBuildSteps([slide])
  assert.ok(slides.length <= 5, '最多 5 步，避免页数爆炸')
  assert.equal(slides.at(-1).bullets.length, slides.length, '最后一步展示与步数相同数量的要点')
})

test('stats / chart / timeline pages are expanded too, tables and quotes are not', () => {
  const slides = [
    { index: 0, type: 'stats', title: '指标', stats: [{ value: '1', label: 'a' }, { value: '2', label: 'b' }, { value: '3', label: 'c' }] },
    { index: 1, type: 'timeline', title: '节奏', timeline: [{ title: 'a' }, { title: 'b' }] },
    { index: 2, type: 'quote', title: '观点', quote: { text: 'x' } },
  ]
  const { slides: out } = expandBuildSteps(slides)
  assert.equal(out.filter((slide) => slide.type === 'stats').length, 3)
  assert.equal(out.filter((slide) => slide.type === 'timeline').length, 2)
  assert.equal(out.filter((slide) => slide.type === 'quote').length, 1, '单焦点页面不拆')
})

test('a steps-mode pptx has one slide per reveal and no timing tree', async () => {
  const { slides } = expandBuildSteps(DECK.slides)
  const buffer = await buildPptx({ deck: { ...DECK, slides } })
  const entries = readZipEntries(buffer)
  const slideNames = Object.keys(entries).filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
  assert.equal(slideNames.length, slides.length)

  // 顺序：封面(1) → 要点三步(2,3,4) → 观点(5)
  assert.equal(slideNames.length, 5)
  const last = zipEntryText(buffer, 'ppt/slides/slide4.xml')
  assert.ok(last.includes('第三条'), '最后一屏包含全部要点')
  const first = zipEntryText(buffer, 'ppt/slides/slide2.xml')
  assert.equal(first.includes('第三条'), false, '第一屏只出现第一条要点')
  assert.equal(first.includes('<p:timing>'), false, 'steps 模式不注入时间轴')
})

test('both mode combines cumulative slides with entrance animations', async () => {
  const { slides } = expandBuildSteps(DECK.slides)
  const raw = await buildPptx({ deck: { ...DECK, slides } })
  const { buffer, patched } = await addSlideAnimations(raw, { titles: slides.map((slide) => slide.title) })
  assert.equal(patched, slides.length)
  for (const name of Object.keys(readZipEntries(buffer)).filter((entry) => /^ppt\/slides\/slide\d+\.xml$/.test(entry))) {
    assert.ok(zipEntryText(buffer, name).includes('<p:timing>'), `${name} 也带入场动画`)
  }
})
