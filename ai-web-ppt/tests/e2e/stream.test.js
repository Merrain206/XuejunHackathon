import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

import {
  FABRICATED_SOURCE,
  LONG_PAGE,
  MOCK_DESIGN,
  MOCK_DESIGN_WARM,
  MOCK_SEARCH_RESULT,
  MOCK_SLIDES,
  startMockUpstream,
} from '../helpers/mock-upstream.mjs'
import { postGenerate, readSse, startApp } from '../helpers/app.mjs'
import { zipEntryText } from '../helpers/zip.mjs'

const DOCUMENT = [
  '2026 年第三季度产品与运营复盘：本季度把交付周期从 5 天压到 2.9 天，成本节省 26%。',
  '订阅转化率只有 2.3%，未达到 3% 的目标，下一季度要接入可溯源的资料库。',
].join('\n')

function eventsByName(events) {
  return events.reduce((acc, event) => {
    acc[event.event] = (acc[event.event] ?? 0) + 1
    return acc
  }, {})
}

async function generate(app, payload) {
  const response = await postGenerate(app.baseUrl, { content: DOCUMENT, ...payload })
  assert.equal(response.status, 200)
  assert.match(response.headers.get('content-type') ?? '', /text\/event-stream/)
  return readSse(response)
}

test('流式生成：design 先行、逐页进度、最终结构完整', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const events = await generate(app, { stylePreference: '', showCitations: false })
  const names = events.map((event) => event.event)

  assert.equal(names[0], 'status')
  assert.equal(names.at(-1), 'done')
  assert.ok(names.indexOf('design') < names.indexOf('slide'), 'design 事件必须先于页面')
  assert.ok(names.indexOf('search') < names.indexOf('design'), 'search 事件先于设计')

  const design = events.find((event) => event.event === 'design').data
  // no style preference in this request → the model derives one by itself
  assert.equal(design.styleName, MOCK_DESIGN_WARM.styleName)
  assert.equal(design.palette.accent, MOCK_DESIGN_WARM.palette.accent)

  const done = events.at(-1).data
  const types = done.slides.map((slide) => slide.type)
  for (const type of ['cover', 'stats', 'chart', 'compare', 'timeline', 'table', 'quote', 'closing']) {
    assert.ok(types.includes(type), `最终结构应包含 ${type} 页`)
  }
  assert.equal(done.design.styleName, MOCK_DESIGN_WARM.styleName)
  assert.equal(done.count, done.slides.length)
  assert.equal(done.showCitations, false)

  const progress = events.filter((event) => event.event === 'progress').map((event) => event.data)
  assert.equal(progress.filter((item) => /正在生成第 \d+ 页…/.test(item.message)).length, MOCK_SLIDES.length + 1)
  assert.ok(progress.some((item) => item.stage === 'search' || item.stage === 'design'))
})

test('风格偏好与来源开关分别送入提示词，且不污染文档正文', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({
    env: { BASE_URL: upstream.baseUrl, SEARCH_PROVIDER: 'custom', SEARCH_API_URL: 'http://placeholder/search' },
  })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  await generate(app, { stylePreference: '主色用深蓝；要有高级感', showCitations: true })
  const sent = upstream.lastRequest
  const [system, user] = sent.body.messages
  assert.match(system.content, /风格约束区/)
  assert.match(system.content, /主色用深蓝；要有高级感/)
  assert.match(system.content, /硬约束/)
  assert.match(system.content, /软参考/)
  assert.match(system.content, /不得改变文档本身的信息/)
  assert.match(system.content, /来源与角标（本轮开启）/)
  assert.equal(user.content.includes('主色用深蓝'), false, '风格描述不能拼进文档内容')
  assert.ok(user.content.includes('2026 年第三季度产品与运营复盘'))
})

test('开启来源标注：真实来源保留并重新编号，编造来源被丢弃，末尾生成来源页', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({
    env: { BASE_URL: upstream.baseUrl, SEARCH_PROVIDER: 'custom', SEARCH_API_URL: upstream.searchUrl },
  })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const events = await generate(app, { showCitations: true })
  const search = events.find((event) => event.event === 'search').data
  assert.equal(search.enabled, true)
  assert.equal(search.provider, 'custom')
  assert.ok(search.queries.length >= 1, '应自动生成检索关键词')
  assert.equal(search.results[0].url, MOCK_SEARCH_RESULT.url)
  assert.ok(upstream.searchRequests.length >= 1, '确实调用了检索接口')

  const done = events.at(-1).data
  const flat = JSON.stringify(done.slides)
  assert.ok(flat.includes(MOCK_SEARCH_RESULT.url), '真实来源应保留')
  assert.equal(flat.includes(FABRICATED_SOURCE.url), false, '编造来源必须被丢弃')
  assert.equal(flat.includes('[9]'), false, '被丢弃来源的角标必须移除')

  const references = done.slides.at(-1)
  assert.equal(references.type, 'references')
  assert.equal(references.title, '参考来源')
  assert.ok(references.bullets.some((line) => line.includes(MOCK_SEARCH_RESULT.url)))
  assert.equal(done.stats.droppedCitations, 1)

  const statsSlide = done.slides.find((slide) => slide.type === 'stats')
  assert.ok(statsSlide.bullets.some((line) => line.includes('[1]')))
  assert.ok(statsSlide.bullets.some((line) => line.includes('[2]')))
  assert.ok(statsSlide.citations.every((citation) => citation.url === '' || citation.url === MOCK_SEARCH_RESULT.url))
})

test('关闭来源标注：不产生角标、citations 与来源页', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const events = await generate(app, { showCitations: false })
  const done = events.at(-1).data
  const flat = JSON.stringify(done.slides)
  assert.equal(/\[\d{1,2}\]/.test(flat), false, '不应出现任何角标')
  assert.equal(flat.includes('参考来源'), false, '不应出现来源页')
  assert.ok(done.slides.every((slide) => !slide.citations?.length))
})

test('超过上限的要点会被自动拆页（可读性下限）', async (t) => {
  const upstream = await startMockUpstream({
    design: MOCK_DESIGN,
    slides: [{ type: 'bullets', title: LONG_PAGE.title, bullets: LONG_PAGE.bullets }],
  })
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl, MAX_BULLETS_PER_SLIDE: '6' } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const events = await generate(app, {})
  const done = events.at(-1).data
  assert.equal(done.slides.length, 2)
  assert.ok(done.slides.every((slide) => slide.bullets.length <= 6))
  assert.equal(done.stats.splits, 2)
})

test('请求上游时带上 .env 的模型名与流式开关', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl, MODEL_NAME: 'model-from-env' } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  await generate(app, {})
  const sent = upstream.lastRequest
  assert.equal(sent.body.model, 'model-from-env')
  assert.equal(sent.body.stream, true)
  assert.equal(sent.headers.authorization, 'Bearer test-key-not-a-real-secret')
  assert.match(sent.body.messages[0].content, /JSONL/)
})

test('达到页数上限时截断并标记', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl, MAX_SLIDES: '3' } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const events = await generate(app, {})
  const done = events.at(-1).data
  assert.ok(done.count <= 3)
  assert.equal(done.truncated, true)
})

test('文档过短 400、缺少配置 503、上游 401 与空结果都有可读反馈', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  const bare = await startApp({ env: { BASE_URL: upstream.baseUrl, DEEPSEEK_API_KEY: '' } })
  const unauthorized = await startApp({
    env: {
      BASE_URL: (
        await startMockUpstream({
          failWith: { status: 401, body: '{"error":{"message":"Authentication Fails","code":"invalid_request_error"}}' },
        })
      ).baseUrl,
    },
  })
  const empty = await startApp({ env: { BASE_URL: (await startMockUpstream({ rawText: '抱歉，我无法完成。' })).baseUrl } })
  t.after(async () => {
    await Promise.all([app.close(), bare.close(), unauthorized.close(), empty.close(), upstream.close()])
  })

  const short = await postGenerate(app.baseUrl, { content: '太短' })
  assert.equal(short.status, 400)
  assert.match((await short.json()).error, /至少需要/)

  const missing = await postGenerate(bare.baseUrl, { content: DOCUMENT })
  assert.equal(missing.status, 503)
  assert.equal((await missing.json()).code, 'config_missing')

  const authEvents = await readSse(await postGenerate(unauthorized.baseUrl, { content: DOCUMENT }))
  const authError = authEvents.find((event) => event.event === 'error')
  assert.equal(authError.data.status, 401)
  assert.match(authError.data.message, /API Key 无效或未授权/)

  const emptyEvents = await readSse(await postGenerate(empty.baseUrl, { content: DOCUMENT }))
  assert.equal(emptyEvents.find((event) => event.event === 'error').data.code, 'empty_result')
})

test('PPTX 导出：返回真实 pptx 文件，且缺失数据时报 400', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const bad = await fetch(`${app.baseUrl}/api/export/pptx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  assert.equal(bad.status, 400)

  const response = await fetch(`${app.baseUrl}/api/export/pptx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: '季度复盘', design: MOCK_DESIGN, slides: MOCK_SLIDES }),
  })
  assert.equal(response.status, 200)
  assert.match(response.headers.get('content-type') ?? '', /presentationml\.presentation/)
  assert.match(response.headers.get('content-disposition') ?? '', /attachment/)
  const buffer = Buffer.from(await response.arrayBuffer())
  assert.equal(buffer.subarray(0, 2).toString(), 'PK', 'pptx 就是 zip，应以 PK 开头')
  assert.ok(buffer.length > 10_000)
})

test('保存到项目目录：落盘到 .exports，且不带"来自 Internet"标记', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const response = await fetch(`${app.baseUrl}/api/export/pptx`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      deck: {
        title: 'MOTW 检查',
        slides: [{ type: 'bullets', title: '标题', bullets: ['第一条', '第二条'] }],
      },
      buildMode: 'animation',
      saveTo: 'project',
    }),
  })
  const payload = await response.json()
  assert.equal(response.status, 200)
  assert.equal(payload.ok, true)
  assert.ok(payload.path.includes('.exports'), `应写进项目 .exports 目录：${payload.path}`)
  assert.ok(fs.existsSync(payload.path), '文件应真实落盘')

  const buffer = fs.readFileSync(payload.path)
  assert.equal(buffer.subarray(0, 2).toString(), 'PK')
  const slideXml = zipEntryText(buffer, 'ppt/slides/slide1.xml')
  assert.ok(slideXml.includes('<p:timing>'), '落盘文件同样带入场动画')
  assert.ok(slideXml.includes('nodeType="clickEffect"'), '且是单击触发')

  // 浏览器下载会给文件附加 Zone.Identifier（ZoneId=3），PowerPoint 因此进入受保护视图并禁用动画；
  // 直接写盘的文件没有这个数据流，这正是提供该按钮的原因。
  assert.throws(
    () => fs.readFileSync(`${payload.path}:Zone.Identifier`),
    /ENOENT/,
    '写盘文件不应带有来自 Internet 的标记',
  )
  fs.rmSync(payload.path, { force: true })
})

test('图片代理只接受白名单地址', async (t) => {
  const upstream = await startMockUpstream()
  const app = await startApp({ env: { BASE_URL: upstream.baseUrl } })
  t.after(async () => {
    await app.close()
    await upstream.close()
  })

  const rejected = await fetch(`${app.baseUrl}/api/image?url=${encodeURIComponent('https://evil.example/x.png')}`)
  assert.equal(rejected.status, 400)
  const noParams = await fetch(`${app.baseUrl}/api/image`)
  assert.equal(noParams.status, 400)
})
