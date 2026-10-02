// A fake OpenAI-compatible endpoint plus a fake search provider. Used by the
// integration and browser tests so the whole pipeline (design line → pages →
// citation verification) can be exercised without calling the real API.
import http from 'node:http'

export const MOCK_SEARCH_RESULT = {
  title: '行业报告：2026 交付效率基准',
  url: 'https://search.example.com/report-2026',
  snippet: '样本覆盖 240 个团队，平均交付周期 3.1 天，工具化核查可再降 30%。',
}

export const FABRICATED_SOURCE = {
  title: '看起来很像真的来源',
  url: 'https://fabricated.example.com/not-real',
}

export const MOCK_DESIGN = {
  styleName: '深海金融',
  rationale: '面向投资人的年度复盘：冷色底、高对比、克制的图形语言',
  palette: {
    bg: '#08111f',
    bgAlt: '#0f2036',
    fg: '#eaf2ff',
    muted: '#93a8c6',
    accent: '#4fc3f7',
    accent2: '#ffb74d',
    surface: 'rgba(255,255,255,0.06)',
    border: 'rgba(255,255,255,0.16)',
  },
  typography: { heading: 'Noto Sans SC', body: 'system-ui', scale: 1, headingWeight: 800, letterSpacing: 0 },
  decor: { style: 'technical', imageStyle: 'clean 3d render, deep blue palette, soft rim light', radius: 16, pattern: 'grid' },
}

/** Used when the request carries no style preference — visually very different. */
export const MOCK_DESIGN_WARM = {
  styleName: '暖调编辑',
  rationale: '没有风格约束，按内容语义选择亲和、留白充足的编辑感',
  palette: {
    bg: '#fdf6ec',
    bgAlt: '#ffffff',
    fg: '#2b2118',
    muted: '#7a6a58',
    accent: '#c2410c',
    accent2: '#0f766e',
    surface: 'rgba(43,33,24,0.05)',
    border: 'rgba(43,33,24,0.16)',
  },
  typography: { heading: 'Georgia', body: 'system-ui', scale: 1.05, headingWeight: 700, letterSpacing: 0 },
  decor: { style: 'editorial', imageStyle: 'warm paper texture, soft natural light, muted tones', radius: 6, pattern: 'dots' },
}

/** Emulates the model reacting to the style-constraint zone in the prompt. */
export function designForPrompt(systemPrompt) {
  return /主色用深蓝|深蓝/.test(String(systemPrompt || '')) ? MOCK_DESIGN : MOCK_DESIGN_WARM
}

export const MOCK_SLIDES = [
  {
    type: 'cover',
    layout: 'cover-image',
    title: '2026 年第三季度复盘',
    subtitle: 'AI 辅助内容生产的效率验证',
    image: { prompt: 'modern newsroom desk with glowing charts, cool blue tones, wide shot', placement: 'background', alt: '抽象数据流' },
  },
  {
    type: 'stats',
    layout: 'kpi-strip',
    eyebrow: '01 结果',
    title: '三个关键结果与实际达成',
    takeaway: '效率目标基本达成，转化率仍是短板',
    stats: [
      { value: '2.9', unit: '天', label: '平均交付周期', delta: '-42%' },
      { value: '12', unit: '篇', label: '月度深度文章' },
      { value: '2.3', unit: '%', label: '注册转化率', delta: '+0.5pt' },
      { value: '26', unit: '%', label: '综合成本节省' },
    ],
    bullets: ['交付周期缩短到 2.9 天[1]', '工具化核查可再降 30%[2]', '订阅转化率仍低于目标[9]'],
    citations: [
      { n: 1, title: '用户提供的文档（本文档）', url: '' },
      { n: 2, ...MOCK_SEARCH_RESULT, publisher: 'search.example.com' },
      { n: 9, ...FABRICATED_SOURCE, publisher: 'fabricated.example.com' },
    ],
  },
  {
    type: 'chart',
    layout: 'chart-split',
    eyebrow: '02 趋势',
    title: '交付周期与成本趋势',
    takeaway: '周期下降主要发生在工具化之后',
    chart: {
      kind: 'line',
      title: '季度对比',
      unit: '天',
      series: [{ name: '交付周期', data: [{ label: 'Q1', value: 5 }, { label: 'Q2', value: 3.8 }, { label: 'Q3', value: 2.9 }] }],
    },
    bullets: ['Q1 到 Q3 缩短 42%', '工具化后斜率明显变陡'],
  },
  {
    type: 'compare',
    layout: 'compare-columns',
    eyebrow: '03 对比',
    title: '改造前后',
    takeaway: '真正省下的是核查之外的时间',
    compare: { left: { title: '改造前', items: ['人工逐条核查', '平均 5 天'] }, right: { title: '改造后', items: ['工具化核查', '平均 2.9 天'] } },
  },
  {
    type: 'bullets',
    layout: 'bullets-list',
    eyebrow: '04 清单',
    title: '要点清单',
    takeaway: '三条要点都要落到执行动作上',
    bullets: ['把核查流程工具化', 'A/B 测试订阅引导位置', '沉淀模板给新编辑'],
  },
  {
    type: 'timeline',
    layout: 'timeline-horizontal',
    eyebrow: '05 计划',
    title: '下一季度推进节奏',
    timeline: [
      { label: 'Q1', title: '核查工具化', text: '接入可溯源资料库' },
      { label: 'Q2', title: '订阅引导 A/B', text: '三种方案并行' },
    ],
  },
  {
    type: 'table',
    layout: 'table-split',
    eyebrow: '05 成本',
    title: '成本拆解',
    takeaway: 'AI 支出只占四成，人力仍是主要成本',
    table: { headers: ['项目', '金额', '占比'], rows: [['AI 调用', '3860 元', '43%'], ['人工审核', '5120 元', '57%']] },
    bullets: ['单篇综合成本 890 元', '比外包低 26%'],
  },
  {
    type: 'quote',
    title: '结论',
    quote: { text: '效率提升来自流程改造，而不是换一个更快的模型', attribution: '编辑负责人' },
  },
  {
    type: 'bullets',
    layout: 'bullets-cards',
    eyebrow: '06 风险',
    title: '风险与待办',
    takeaway: '三个待办都必须在下季度闭环',
    cards: [
      { title: '采访细节不足', text: 'AI 初稿偏泛化，需要补一手采访' },
      { title: '跨部门同步遗漏', text: '3 个依赖项因无人当面提出而延期' },
      { title: '订阅引导未优化', text: '停留时长涨了，转化没跟上' },
    ],
    notes: '这里可以补充两个未达标案例的细节。',
  },
  {
    type: 'closing',
    layout: 'closing-cards',
    title: '下一步',
    eyebrow: '07 行动',
    cards: [
      { title: '核查工具化', text: '目标：核查耗时 -30%' },
      { title: '订阅 A/B 测试', text: '目标：转化率 ≥2.8%' },
      { title: '沉淀模板', text: '新编辑一周内独立产出' },
    ],
  },
]

const LONG_PAGE = {
  type: 'bullets',
  title: '一页塞太多要点',
  bullets: Array.from({ length: 9 }, (_, index) => `要点 ${index + 1}`),
}

export function slidesToJsonl({
  design = MOCK_DESIGN,
  slides = MOCK_SLIDES,
} = {}) {
  return [JSON.stringify({ design }), ...slides.map((slide) => JSON.stringify(slide))].join('\n')
}

/**
 * @param {{design?: object, slides?: Array, rawText?: string, chunkSize?: number,
 *   chunkDelayMs?: number, failWith?: {status:number, body:string}|null,
 *   searchResults?: Array}} options
 */
export async function startMockUpstream(options = {}) {
  const {
    slides = MOCK_SLIDES,
    chunkSize = 48,
    chunkDelayMs = 5,
    failWith = null,
    searchResults = [MOCK_SEARCH_RESULT],
  } = options
  const fixedDesign = options.design ?? null
  const fixedRawText = options.rawText ?? null
  const requests = []
  const searchRequests = []

  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => {
      body += chunk
    })
    req.on('end', async () => {
      const path = req.url.replace(/\?.*$/, '')

      if (req.method === 'POST' && path.endsWith('/chat/completions')) {
        let parsed = null
        try {
          parsed = JSON.parse(body)
        } catch {
          /* ignore */
        }
        requests.push({ url: req.url, headers: req.headers, body: parsed })

        if (failWith) {
          res.writeHead(failWith.status, { 'Content-Type': 'application/json' })
          res.end(failWith.body)
          return
        }

        // React to the style-constraint zone exactly like a real model would.
        const systemPrompt = parsed?.messages?.[0]?.content ?? ''
        const design = fixedDesign ?? designForPrompt(systemPrompt)
        const rawText = fixedRawText ?? slidesToJsonl({ design, slides })

        res.writeHead(200, {
          'Content-Type': 'text/event-stream; charset=utf-8',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        })
        res.write(': keep-alive\n\n')

        const chunks = []
        for (let i = 0; i < rawText.length; i += chunkSize) chunks.push(rawText.slice(i, i + chunkSize))

        for (const chunk of chunks) {
          res.write(
            `data: ${JSON.stringify({
              id: 'chatcmpl-mock',
              object: 'chat.completion.chunk',
              model: 'deepseek-flash',
              choices: [{ index: 0, delta: { content: chunk }, finish_reason: null }],
            })}\n\n`,
          )
          if (chunkDelayMs) await new Promise((resolve) => setTimeout(resolve, chunkDelayMs))
        }
        res.write(
          `data: ${JSON.stringify({
            choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
            usage: { prompt_tokens: 100, completion_tokens: 200, total_tokens: 300 },
          })}\n\n`,
        )
        res.write('data: [DONE]\n\n')
        res.end()
        return
      }

      // fake search provider (SEARCH_PROVIDER=custom)
      if (req.method === 'POST' && path.endsWith('/search')) {
        let parsed = null
        try {
          parsed = JSON.parse(body)
        } catch {
          /* ignore */
        }
        searchRequests.push(parsed)
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ results: searchResults }))
        return
      }

      if (req.method === 'GET' && path.endsWith('/models')) {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ object: 'list', data: [{ id: 'deepseek-flash' }] }))
        return
      }

      res.writeHead(404, { 'Content-Type': 'application/json' })
      res.end('{"error":{"message":"not found"}}')
    })
  })

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    server,
    port,
    baseUrl: `http://127.0.0.1:${port}/v1`,
    searchUrl: `http://127.0.0.1:${port}/search`,
    requests,
    searchRequests,
    get lastRequest() {
      return requests.at(-1)
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

export { LONG_PAGE }
