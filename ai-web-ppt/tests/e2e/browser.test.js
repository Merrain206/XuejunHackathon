import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

import { FABRICATED_SOURCE, MOCK_SEARCH_RESULT, startMockUpstream } from '../helpers/mock-upstream.mjs'
import { startApp } from '../helpers/app.mjs'
import {
  clickElement,
  evaluate,
  findBrowserExecutable,
  launchBrowser,
  navigate,
  pressKey,
  screenshot,
  waitFor,
} from '../helpers/browser.mjs'
import { readZipEntries, zipEntryText } from '../helpers/zip.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const DIST_INDEX = path.join(ROOT, 'web', 'dist', 'index.html')
const TMP = path.join(ROOT, '.test-tmp')
const DOWNLOADS = path.join(TMP, 'downloads')
const SHOTS = path.join(TMP, 'shots')

const PLACEHOLDERS = ['封面页', '收尾页', '封面主图：', '配图：', '图片：', '此处插入', '谢谢观看', 'Slide 1']

const skipReason = !findBrowserExecutable()
  ? '本机没有 Edge/Chrome'
  : !fs.existsSync(DIST_INDEX)
    ? '前端未构建（先执行 npm run build）'
    : process.env.SKIP_BROWSER_E2E === '1'
      ? 'SKIP_BROWSER_E2E=1'
      : false

async function setTextarea(page, testid, value) {
  await evaluate(
    page,
    `(() => {
      const el = document.querySelector('[data-testid="${testid}"]');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
      setter.call(el, ${JSON.stringify(value)});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return el.value.length;
    })()`,
  )
}

async function readDeck(page) {
  return evaluate(
    page,
    `(() => {
      const surface = document.querySelector('.deck-surface');
      const frame = document.querySelector('.slide-frame');
      const style = getComputedStyle(surface);
      const bodySelector = '.slide .slide-bullets li, .slide .data-table td, .slide .stat-label, .slide .timeline-text, .slide .reference-item, .slide .slide-subtitle';
      const bullets = document.querySelector(bodySelector);
      return {
        styleName: surface.dataset.styleName,
        accent: style.getPropertyValue('--s-accent').trim(),
        bg: style.getPropertyValue('--s-bg').trim(),
        headingFont: style.getPropertyValue('--s-heading-font').trim(),
        radius: style.getPropertyValue('--s-radius').trim(),
        citationsAttr: surface.dataset.citations,
        slideTypes: [...document.querySelectorAll('.slide')].map((el) => el.dataset.type),
        titles: [...document.querySelectorAll('.slide .slide-title')].map((el) => el.textContent.trim()),
        citationMarkers: document.querySelectorAll('.cite-mark').length,
        citationFooters: document.querySelectorAll('.slide-citations').length,
        referenceLinks: [...document.querySelectorAll('.reference-url')].map((el) => el.textContent.trim()),
        bodyFontPx: bullets ? parseFloat(getComputedStyle(bullets).fontSize) : null,
        padTopPx: parseFloat(getComputedStyle(document.querySelector('.slide.is-active')).paddingTop),
        frameHeight: frame.getBoundingClientRect().height,
        text: document.body.innerText,
      };
    })()`,
  )
}

async function waitForDownload(extension, attempts = 220) {
  let previous = null
  let stable = 0
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const files = fs
      .readdirSync(DOWNLOADS)
      .filter((name) => name.endsWith(extension) && !name.endsWith('.crdownload'))
      .map((name) => path.join(DOWNLOADS, name))
    if (files.length) {
      const stats = fs.statSync(files[0])
      // A finished download stops growing
      if (stats.size > 0 && stats.size === previous) {
        stable += 1
        if (stable >= 3) return files[0]
      } else {
        stable = 0
      }
      previous = stats.size
    }
    await delay(150)
  }
  return null
}

/** Waits until every started download reported `completed`, then settles briefly. */
async function settleDownloads(browser, { timeoutMs = 15000 } = {}) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const begins = browser.downloads.filter((item) => item.event === 'begin')
    const completed = new Set(
      browser.downloads.filter((item) => item.event === 'progress' && item.state === 'completed').map((item) => item.guid),
    )
    if (begins.length && begins.every((item) => completed.has(item.guid))) break
    if (Date.now() > deadline) break
    await delay(150)
  }
  await delay(600)
}

/**
 * Clicks an export button and waits for the file. Chrome drops a download that
 * starts while the previous one is still finishing, so we settle between
 * attempts and click again if nothing arrived — exactly what a user would do.
 */
async function clickAndDownload(page, browser, selector, extension, { attempts = 3 } = {}) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await clickElement(page, selector)
    const file = await waitForDownload(extension, 40)
    if (file) return file
    await settleDownloads(browser)
  }
  return null
}

test('浏览器验收：① 风格偏好+来源标注 ② 全部留空，两条路径都可用', { skip: skipReason }, async (t) => {
  const upstream = await startMockUpstream({ chunkDelayMs: 3 })
  const app = await startApp({
    env: {
      BASE_URL: upstream.baseUrl,
      SEARCH_PROVIDER: 'custom',
      SEARCH_API_URL: upstream.searchUrl,
      // Deterministic + offline: every picture silently degrades to the CSS
      // gradient decoration, which is exactly the fallback the UI must show
      // (unit tests cover the acquisition chain itself).
      IMAGE_PROVIDER: 'none',
    },
  })
  fs.rmSync(DOWNLOADS, { recursive: true, force: true })
  fs.mkdirSync(DOWNLOADS, { recursive: true })

  const browser = await launchBrowser({ downloadDir: DOWNLOADS }).catch(async (error) => {
    await app.close()
    await upstream.close()
    t.skip(`无法启动无头浏览器：${error.message}`)
    return null
  })
  if (!browser) return
  t.after(async () => {
    await browser.close()
    await app.close()
    await upstream.close()
  })

  const page = browser.page
  const pageErrors = []
  page.on('Runtime.exceptionThrown', (params) => {
    pageErrors.push(params?.exceptionDetails?.exception?.description || params?.exceptionDetails?.text || 'unknown')
  })

  /* ---------------- 场景 ① 风格偏好 + 来源标注 ---------------- */
  await navigate(page, app.baseUrl)
  await waitFor(page, 'Boolean(document.querySelector(\'[data-testid="source-text"]\'))', { label: '编辑器就绪' })

  // 问题①：界面上不得出现「未配置 / 检索未配置 / pollinations」这类错误或实现细节文案
  const editorText = await evaluate(page, 'document.body.innerText')
  for (const needle of ['未配置', 'pollinations', '检索', '能力检查', '水印']) {
    assert.equal(editorText.includes(needle), false, `界面不应出现「${needle}」这类文案`)
  }
  assert.equal(
    await evaluate(page, 'Boolean(document.querySelector(\'[data-testid="capability-badge"]\'))'),
    false,
    '不应再有能力/提供商徽章',
  )

  await evaluate(page, 'document.querySelector(\'[data-testid="sample"]\').click(); true')
  await waitFor(page, 'document.querySelector(\'[data-testid="source-text"]\').value.length > 100', {
    label: '示例文档已填入',
  })
  await setTextarea(page, 'style-preference', '面向投资人的年度复盘，主色用深蓝，冷静克制，信息密度高一些')
  assert.equal(
    await evaluate(page, 'document.querySelector(\'[data-testid="citations-toggle"]\').getAttribute("aria-checked")'),
    'false',
    '来源标注默认应为关闭',
  )
  await evaluate(page, 'document.querySelector(\'[data-testid="citations-toggle"]\').click(); true')
  await waitFor(page, 'document.querySelector(\'[data-testid="citations-toggle"]\').getAttribute("aria-checked") === "true"', {
    label: '来源标注已开启',
  })

  await evaluate(page, 'document.querySelector(\'[data-testid="generate"]\').click(); true')
  await waitFor(page, 'Boolean(document.querySelector(\'[data-testid="deck"]\'))', {
    label: '场景①生成完成',
    timeout: 60000,
  })
  await delay(400)

  const first = await readDeck(page)
  assert.equal(first.citationsAttr, 'on')
  assert.ok(first.styleName && first.styleName.length > 0, 'AI 应给出风格名')
  assert.ok(first.slideTypes.includes('references'), '开启来源标注必须生成来源页')
  assert.ok(first.slideTypes.includes('chart') && first.slideTypes.includes('stats'), '数据应转为可视化页')

  // 问题④：版式必须有变化、有洞察条、有卡片/图表等支撑元素，而不是纯文字堆叠
  const composition = await evaluate(
    page,
    `(() => ({
      layouts: [...document.querySelectorAll('.slide')].map((el) => el.dataset.layout),
      takeaways: document.querySelectorAll('.takeaway-bar').length,
      cards: document.querySelectorAll('.info-card').length,
      charts: document.querySelectorAll('.chart-svg').length,
      kpis: document.querySelectorAll('.kpi, .stat-card').length,
      images: document.querySelectorAll('.slide-image').length,
      decors: document.querySelectorAll('.decor').length,
    }))()`,
  )
  assert.ok(new Set(composition.layouts).size >= 4, `整份 PPT 至少 4 种版式，实际 ${composition.layouts.join(',')}`)
  assert.ok(composition.takeaways >= 3, `应有若干「一句话结论」强调条，实际 ${composition.takeaways}`)
  assert.ok(composition.cards >= 3, `应有卡片分区，实际 ${composition.cards}`)
  assert.ok(composition.charts >= 1, '应有图表')
  assert.ok(composition.kpis >= 3, '应有强调数字')
  // 问题①②：配图不可用时静默降级为渐变装饰，页面上没有任何错误文案
  assert.ok(composition.decors >= 1, '配图不可用时应出现 CSS 渐变装饰')
  assert.equal(composition.images, 0, 'IMAGE_PROVIDER=none 时不应有真实图片')
  const deckText = await evaluate(page, 'document.body.innerText')
  for (const needle of ['未配置', 'pollinations', '图片获取失败', '错误']) {
    assert.equal(deckText.includes(needle), false, `演示界面不应出现「${needle}」`)
  }

  assert.ok(first.citationMarkers > 0, '应有 [n] 角标')
  assert.ok(first.citationFooters > 0, '每页底部应有来源标注')
  assert.ok(
    first.referenceLinks.some((url) => url === MOCK_SEARCH_RESULT.url),
    `来源页应包含检索到的真实链接，实际：${first.referenceLinks.join(', ')}`,
  )
  assert.equal(first.text.includes(FABRICATED_SOURCE.url), false, '编造来源不得出现')
  for (const placeholder of PLACEHOLDERS) {
    assert.equal(first.text.includes(placeholder), false, `正文不得出现占位符「${placeholder}」`)
  }
  assert.ok(first.bodyFontPx >= 18, `正文字号必须 ≥18px，实际 ${first.bodyFontPx}px`)
  assert.ok(
    first.padTopPx >= first.frameHeight * 0.1 - 1,
    `四周留白必须 ≥10% 页高，实际 ${first.padTopPx}px / ${Math.round(first.frameHeight)}px`,
  )
  await screenshot(page, path.join(SHOTS, 'rebuild-scenario1-deck.png'))

  // 键盘翻页仍然可用
  await pressKey(page, 'ArrowRight')
  await waitFor(page, 'document.querySelector(\'[data-testid="counter"]\').textContent.trim().startsWith("2 /")', {
    label: '方向键翻页',
  })

  // 导出 PPTX 并校验内容真的进去了（仍在演示视图中，原生表单下载）
  const pptxFile = await clickAndDownload(page, browser, '[data-testid="export-pptx"]', '.pptx')
  assert.ok(
    pptxFile,
    `应下载 PPTX（下载事件：${JSON.stringify(browser.downloads.filter((item) => item.event === 'begin').map((item) => item.suggestedFilename))}）`,
  )
  const pptxBuffer = fs.readFileSync(pptxFile)
  assert.equal(pptxBuffer.subarray(0, 2).toString(), 'PK', 'pptx 应为有效 zip 容器')
  const slideXml = zipEntryText(pptxBuffer, 'ppt/slides/slide1.xml')
  assert.ok(slideXml.includes(first.titles[0]), 'PPTX 第一页应包含封面标题')
  const allXml = ['ppt/slides/slide1.xml', 'ppt/slides/slide2.xml', 'ppt/slides/slide3.xml', 'ppt/slides/slide4.xml']
    .map((name) => zipEntryText(pptxBuffer, name))
    .join('\n')
  assert.ok(
    allXml.includes(MOCK_SEARCH_RESULT.url) || allXml.includes('search.example.com'),
    'PPTX 应带上来源信息',
  )

  // 问题⑤：每页都要有进入动画，且正文按段落逐条出现（标题→正文逐条→配图）
  const animatedSlides = ['ppt/slides/slide1.xml', 'ppt/slides/slide2.xml', 'ppt/slides/slide3.xml']
  for (const name of animatedSlides) {
    const xml = zipEntryText(pptxBuffer, name)
    assert.ok(xml.includes('<p:timing>'), `${name} 应包含动画时间轴`)
    assert.ok(xml.includes('<p:seq '), `${name} 应包含动画序列`)
    assert.ok(xml.includes('presetClass="entr"'), `${name} 应包含进入效果`)
  }
  // 逐条出现：定位含「要点清单」的那一页（有段落列表），它必须有段落级构建
  const slideNames = Object.keys(readZipEntries(pptxBuffer)).filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
  const listSlide = slideNames
    .map((name) => zipEntryText(pptxBuffer, name))
    .find((xml) => xml.includes('要点清单'))
  assert.ok(listSlide, '应能定位到要点清单页')
  assert.ok(listSlide.includes('<p:timing>'), '要点页应有动画时间轴')
  assert.ok(listSlide.includes('build="p"'), '正文应按段落逐条出现（p:bldP build="p"）')
  const firstTiming = listSlide.slice(listSlide.indexOf('<p:timing>'))
  assert.ok(firstTiming.includes('pRg st="0"'), '第一段应作为第一个动画步骤')
  if (firstTiming.includes('pRg st="1"')) {
    assert.ok(firstTiming.indexOf('pRg st="0"') < firstTiming.indexOf('pRg st="1"'), '段落顺序递增')
  }

  // 导出离线 HTML 并在 file:// 下验证
  await settleDownloads(browser)
  const htmlFile = await clickAndDownload(page, browser, '[data-testid="export-html"]', '.html')
  assert.ok(
    htmlFile,
    `应下载离线 HTML（下载事件：${JSON.stringify(browser.downloads.filter((item) => item.event === 'begin').map((item) => item.suggestedFilename))}；日志：${browser.logs.slice(-6).join(' | ')}）`,
  )
  const exportedHtml = fs.readFileSync(htmlFile, 'utf8')
  assert.match(exportedHtml, /class="slide[^"]*slide--cover[^"]*is-active"/)
  assert.doesNotMatch(exportedHtml, /https?:\/\/(?!search\.example\.com)/i, '导出文件不得引用外部资源')
  for (const placeholder of PLACEHOLDERS) {
    assert.equal(exportedHtml.includes(placeholder), false, `导出文件不得出现占位符「${placeholder}」`)
  }
  assert.ok(exportedHtml.includes('slide-citations'), '导出文件应保留每页来源标注')

  const offlineRequests = []
  page.on('Network.requestWillBeSent', (params) => offlineRequests.push(params?.request?.url ?? ''))
  await navigate(page, `file:///${htmlFile.replace(/\\/g, '/')}`)
  await waitFor(page, 'Boolean(document.querySelector(".slide.is-active"))', { label: '离线文件渲染' })
  await pressKey(page, 'ArrowRight')
  await waitFor(page, 'document.querySelector(".slide.is-active").dataset.index === "1"', { label: '离线翻页' })
  await screenshot(page, path.join(SHOTS, 'rebuild-offline.png'))
  const external = offlineRequests.filter((url) => /^https?:/i.test(url))
  assert.deepEqual(external, [], `离线演示不应发起网络请求：${external.join(', ')}`)

  /* ---------------- 场景 ② 全部留空 ---------------- */
  await navigate(page, app.baseUrl)
  await waitFor(page, 'Boolean(document.querySelector(\'[data-testid="source-text"]\'))', { label: '回到编辑器' })
  await evaluate(page, 'document.querySelector(\'[data-testid="sample"]\').click(); true')
  await waitFor(page, 'document.querySelector(\'[data-testid="source-text"]\').value.length > 100', {
    label: '示例文档已填入',
  })
  await setTextarea(page, 'style-preference', '')
  await evaluate(page, 'document.querySelector(\'[data-testid="generate"]\').click(); true')
  await waitFor(page, 'Boolean(document.querySelector(\'[data-testid="deck"]\'))', {
    label: '场景②生成完成',
    timeout: 60000,
  })
  await delay(400)

  const second = await readDeck(page)
  assert.equal(second.citationsAttr, 'off')
  assert.equal(second.citationMarkers, 0, '关闭后不应有角标')
  assert.equal(second.citationFooters, 0, '关闭后不应有每页来源')
  assert.equal(second.slideTypes.includes('references'), false, '关闭后不应有来源页')
  assert.equal(/\[\d{1,2}\]/.test(second.text), false, '关闭后正文不应出现角标')

  // 视觉必须不同：配色 / 风格名 / 字体 / 圆角
  assert.notEqual(second.styleName, first.styleName, '两次的 AI 风格应有差异')
  assert.notEqual(second.accent, first.accent, '两次的强调色应有差异')
  assert.notEqual(second.bg, first.bg, '两次的背景色应有差异')

  // 内容逻辑一致（去掉场景①独有的来源页）
  const contentTitles = (deck) => deck.titles.filter((title) => title !== '参考来源')
  assert.deepEqual(contentTitles(second), contentTitles(first), '两份 PPT 的内容逻辑应一致')
  assert.deepEqual(
    second.slideTypes.filter((type) => type !== 'references'),
    first.slideTypes.filter((type) => type !== 'references'),
    '页面类型序列应一致',
  )
  await screenshot(page, path.join(SHOTS, 'rebuild-scenario2-deck.png'))

  /* ---------------- 手机视口 ---------------- */
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 2,
    mobile: true,
  })
  await delay(400)
  const mobile = await evaluate(
    page,
    `(() => {
      const body = document.querySelector('.slide .slide-bullets li, .slide .data-table td, .slide .stat-label, .slide .timeline-text, .slide .slide-subtitle')
        || document.querySelector('.slide.is-active');
      return {
        overflowX: document.documentElement.scrollWidth - window.innerWidth,
        frameHeight: Math.round(document.querySelector('.slide-frame').getBoundingClientRect().height),
        bodyFontPx: parseFloat(getComputedStyle(body).fontSize),
      };
    })()`,
  )
  assert.ok(mobile.overflowX <= 1, `手机视口不得横向溢出（${mobile.overflowX}px）`)
  assert.ok(mobile.frameHeight > 200, `手机视口幻灯片高度异常（${mobile.frameHeight}px）`)
  assert.ok(mobile.bodyFontPx >= 18, `手机视口正文字号必须 ≥18px，实际 ${mobile.bodyFontPx}px`)
  await screenshot(page, path.join(SHOTS, 'rebuild-mobile.png'))

  assert.deepEqual(pageErrors, [], `页面不应有未捕获异常：${pageErrors.join(' | ')}`)
})
