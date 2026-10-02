// Express application: streaming generation, image proxy, PPTX export, health.
import fs from 'node:fs'
import path from 'node:path'
import express from 'express'

import { ROOT, capabilities, configProblems } from './config.js'
import { createDeckParser, finalizeDeck } from './deck.js'
import { isAllowedImageUrl, resolveImage } from './images.js'
import { renderHealthPage } from './healthPage.js'
import { publicErrorMessage } from './logger.js'
import { buildMessages, buildSearchQueries } from './prompt.js'
import { createSearchClient } from './search.js'
import { runSelfCheck } from './selfcheck.js'
import { UpstreamError, streamChatCompletion } from './upstream.js'

export const MIN_CONTENT_CHARS = 20
export const MAX_CONTENT_CHARS = 40000
/** Default location for "save to project folder"; overridden by OUTPUT_DIR. */
export const EXPORT_DIR = path.join(ROOT, '.exports')
const HEARTBEAT_MS = 15000
const SELFCHECK_CACHE_MS = 5000
const PROGRESS_MESSAGES = {
  searching: '正在检索相关资料…',
  designing: '正在设计视觉风格…',
  slide: (index) => `正在生成第 ${index} 页…`,
}

function describeUpstreamError(error, config) {
  if (error?.name === 'AbortError' || error?.code === 'ABORT_ERR') {
    return '请求已中断或超时（可调大 .env 中的 REQUEST_TIMEOUT_MS）。'
  }
  if (error instanceof UpstreamError) {
    const status = error.status
    if (status === 401 || status === 403) {
      return 'API Key 无效或未授权：请检查项目根目录 .env 中的 DEEPSEEK_API_KEY。'
    }
    if (status === 404) return `接口路径不存在（${config.baseUrl}）：请检查 .env 中的 BASE_URL。`
    if (status === 429) return '上游限流（429）：请稍后重试，或降低请求频率。'
    if (status === 400 && /model/i.test(error.message || '')) {
      return `模型名可能不正确（当前 MODEL_NAME=${config.model}）：请核对 .env。上游说：${error.message}`
    }
    return `上游错误（HTTP ${status ?? '?'}）：${error.message}`
  }
  return `生成失败：${publicErrorMessage(error, '请查看服务端日志。')}`
}

/**
 * A download name that is safe on every filesystem: ASCII only, no spaces, no
 * reserved characters. Chinese (or any non-ASCII) titles fall back to a stable
 * transliteration-free slug plus a short hash so names stay unique and readable.
 */
export function safeFilename(title, extension, { fallback = 'presentation' } = {}) {
  const raw = String(title || '')
  const ascii = raw
    .normalize('NFKD')
    .replace(/[^\x20-\x7E]/g, '') // drop non-ASCII (CJK etc.)
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^[-.]+|[-.]+$/g, '')
    .slice(0, 60)
  if (ascii) return `${ascii}${extension}`
  // Pure CJK title: keep a short deterministic suffix so two decks never collide.
  let hash = 0
  for (const char of raw) hash = (hash * 31 + char.codePointAt(0)) >>> 0
  return `${fallback}-${hash.toString(16).slice(0, 6)}${extension}`
}

function pickContent(body) {
  // `text` is accepted for backwards compatibility with the first version.
  const raw = typeof body.content === 'string' ? body.content : typeof body.text === 'string' ? body.text : ''
  return raw.trim()
}

export function createApp({ config, fetchImpl = globalThis.fetch, logger = console, now = () => Date.now() }) {
  const app = express()
  app.disable('x-powered-by')
  if (config.trustProxy) app.set('trust proxy', true)

  const bodyLimit = `${Math.max(1, Number(config.maxUploadMb) || 4)}mb`
  app.use(express.json({ limit: bodyLimit }))
  // The browser posts the PPTX payload as a plain form so the download is handled
  // natively (no user-gesture window required after async work).
  app.use(express.urlencoded({ extended: false, limit: bodyLimit }))
  const searchClient = createSearchClient(config, { fetchImpl, logger })

  // --- security headers + CORS (configurable; same-origin by default) --------
  const allowedOrigins = Array.isArray(config.allowedOrigins) ? config.allowedOrigins : []
  app.use((req, res, next) => {
    res.setHeader('X-Content-Type-Options', 'nosniff')
    res.setHeader('Referrer-Policy', 'no-referrer')
    res.setHeader('X-Frame-Options', 'SAMEORIGIN')
    // The app is a single page with inline design tokens, so styles stay inline;
    // scripts and connections are restricted to this origin.
    res.setHeader(
      'Content-Security-Policy',
      [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "connect-src 'self'",
        "font-src 'self' data:",
        "base-uri 'none'",
        "form-action 'self'",
      ].join('; '),
    )

    const origin = req.headers.origin
    if (allowedOrigins.length && origin && allowedOrigins.includes(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin)
      res.setHeader('Vary', 'Origin')
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept')
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    } else if (allowedOrigins.length && req.method === 'OPTIONS') {
      return res.status(403).end()
    }
    if (req.method === 'OPTIONS') return res.status(204).end()
    next()
  })

  if (logger.debug) {
    app.use((req, res, next) => {
      const startedAt = now()
      res.on('finish', () => {
        logger.debug(`${req.method} ${req.originalUrl} ${res.statusCode} ${now() - startedAt}ms`)
      })
      next()
    })
  }

  const outputDir = config.outputDir || EXPORT_DIR
  fs.mkdirSync(outputDir, { recursive: true })

  app.get('/api/health', (req, res) => {
    const problems = configProblems(config)
    res.setHeader('Cache-Control', 'no-store')
    res.json({
      ok: problems.length === 0,
      model: config.model || null,
      baseUrl: config.baseUrl,
      hasApiKey: config.hasApiKey,
      maxSlides: config.maxSlides,
      maxBulletsPerSlide: config.maxBulletsPerSlide,
      pptxBuildMode: config.pptxBuildMode,
      // Absolute paths are a deployment detail: expose them only outside production.
      exportDir: config.isProduction ? undefined : outputDir,
      maxUploadMb: config.maxUploadMb,
      workers: config.workers || null,
      capabilities: capabilities(config),
      envFileLoaded: Boolean(config.envFileLoaded),
      problems,
    })
  })

  // Human-facing self-check page: open this first on a strange machine.
  let selfCheckCache = { at: 0, report: null }
  app.get('/health', async (req, res) => {
    const nowMs = now()
    if (!selfCheckCache.report || nowMs - selfCheckCache.at > SELFCHECK_CACHE_MS) {
      selfCheckCache = {
        at: nowMs,
        report: await runSelfCheck({
          config,
          root: ROOT,
          envFileLoaded: Boolean(config.envFileLoaded),
          envFile: config.envFilePath || '',
          // The process already holds the port, so probing it would always fail.
          checkPort: false,
          probeUpstream: false,
          fetchImpl,
        }),
      }
    }
    res.setHeader('Cache-Control', 'no-store')
    res.type('text/html; charset=utf-8').send(
      renderHealthPage(selfCheckCache.report, {
        config,
        envFileLoaded: Boolean(config.envFileLoaded),
      }),
    )
  })

  app.post('/api/generate', async (req, res) => {
    const body = req.body && typeof req.body === 'object' ? req.body : {}
    const content = pickContent(body)
    const stylePreference = typeof body.stylePreference === 'string' ? body.stylePreference.trim().slice(0, 2000) : ''
    const showCitations = body.showCitations === true

    if (content.length < MIN_CONTENT_CHARS) {
      return res.status(400).json({ error: `文档内容太短了：至少需要 ${MIN_CONTENT_CHARS} 个字符。` })
    }
    if (content.length > MAX_CONTENT_CHARS) {
      return res
        .status(400)
        .json({ error: `文档内容太长了（${content.length} 字符）：请控制在 ${MAX_CONTENT_CHARS} 字符以内。` })
    }
    const problems = configProblems(config)
    if (problems.length) {
      return res.status(503).json({ error: problems.join(' '), code: 'config_missing' })
    }

    const requested = Number.parseInt(body.maxSlides, 10)
    const maxSlides = Math.min(
      Math.max(Number.isFinite(requested) ? requested : config.maxSlides, 3),
      config.maxSlides,
    )

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    })
    if (typeof res.flushHeaders === 'function') res.flushHeaders()

    let clientGone = false
    const send = (event, data) => {
      if (clientGone || res.writableEnded) return
      res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    }

    const controller = new AbortController()
    // NOTE: since Node 16 `req` emits 'close' as soon as the request body has been
    // consumed, so it cannot be used to detect a disconnect. `res` emits 'close'
    // both when the response finished normally and when the socket died — the
    // difference is whether the response had already ended.
    const onClose = () => {
      if (res.writableEnded) return
      clientGone = true
      controller.abort()
    }
    res.on('close', onClose)

    const heartbeat = setInterval(() => {
      if (!clientGone && !res.writableEnded) res.write(': ping\n\n')
    }, HEARTBEAT_MS)
    const timeout = setTimeout(() => controller.abort(), config.requestTimeoutMs)
    const startedAt = now()

    try {
      send('status', {
        stage: 'connecting',
        model: config.model,
        message: '已连接服务，正在请求模型…',
        showCitations,
        stylePreference: stylePreference ? 'custom' : 'auto',
      })

      // --- 1. retrieval (optional, never fatal) ------------------------------
      const searchQueries = showCitations || searchClient.enabled ? buildSearchQueries(content) : []
      let searchResults = []
      if (searchClient.enabled && searchQueries.length) {
        send('progress', { stage: 'search', index: 0, done: 0, message: PROGRESS_MESSAGES.searching })
        searchResults = await searchClient.search(searchQueries)
        send('search', {
          enabled: true,
          provider: searchClient.provider,
          queries: searchQueries,
          results: searchResults.map((item) => ({ title: item.title, url: item.url, publisher: item.publisher })),
        })
      } else {
        send('search', { enabled: false, provider: searchClient.provider, queries: [], results: [] })
      }
      if (clientGone) return

      send('progress', {
        stage: 'design',
        index: 0,
        done: 0,
        message: showCitations ? PROGRESS_MESSAGES.designing : '正在推导视觉风格…',
      })

      // --- 2. streaming generation ------------------------------------------
      const parser = createDeckParser({ maxBulletsPerSlide: config.maxBulletsPerSlide })
      const rawSlides = []
      let design = null
      let usage = null
      let truncated = false

      const upstream = streamChatCompletion({
        baseUrl: config.baseUrl,
        apiKey: config.apiKey,
        model: config.model,
        messages: buildMessages({
          content,
          stylePreference,
          showCitations,
          searchResults,
          searchQueries,
          maxSlides,
          maxBulletsPerSlide: config.maxBulletsPerSlide,
        }),
        signal: controller.signal,
        fetchImpl,
      })

      for await (const part of upstream) {
        if (clientGone) break
        if (part.usage) usage = part.usage
        if (!part.content) continue
        for (const event of parser.push(part.content)) {
          if (event.kind === 'design') {
            design = event.value
            send('design', event.value)
            send('progress', {
              stage: 'slide',
              index: 1,
              done: 0,
              message: PROGRESS_MESSAGES.slide(1),
            })
            continue
          }
          rawSlides.push(event.value)
          send('slide', { index: event.value.index, slide: event.value, total: rawSlides.length })
          if (rawSlides.length >= maxSlides) {
            truncated = true
            break
          }
          send('progress', {
            stage: 'slide',
            index: rawSlides.length + 1,
            done: rawSlides.length,
            message: PROGRESS_MESSAGES.slide(rawSlides.length + 1),
          })
        }
        if (truncated) break
      }

      const flushed = parser.flush()
      for (const event of flushed.events) {
        if (event.kind === 'design') {
          design = event.value
          send('design', event.value)
        } else {
          rawSlides.push(event.value)
          send('slide', { index: event.value.index, slide: event.value, total: rawSlides.length })
        }
      }

      if (clientGone) return
      if (!rawSlides.length) {
        send('error', {
          code: 'empty_result',
          message: '模型没有返回可解析的 PPT 结构，请重试，或换一段更完整的文档。',
        })
        return
      }

      // --- 3. post-processing (cleanup, splitting, citation verification) ----
      const documentTitle = rawSlides[0]?.title || content.split('\n')[0]?.slice(0, 40) || '演示文稿'
      const deck = finalizeDeck({
        slides: rawSlides.slice(0, maxSlides),
        design,
        showCitations,
        allowedUrls: searchResults.map((item) => item.url),
        maxBulletsPerSlide: config.maxBulletsPerSlide,
        documentTitle,
      })

      send('done', {
        count: deck.slides.length,
        slides: deck.slides,
        design: deck.design,
        title: documentTitle,
        showCitations,
        stylePreference,
        search: {
          enabled: searchClient.enabled,
          provider: searchClient.provider,
          queries: searchQueries,
          results: searchResults,
        },
        stats: deck.stats,
        usage,
        truncated,
        droppedPartial: flushed.partial ? 1 : 0,
        elapsedMs: now() - startedAt,
        model: config.model,
      })
    } catch (error) {
      if (clientGone) return
      logger.error?.('[generate] ', error?.stack || error?.message || error)
      send('error', {
        code: error?.code || 'upstream_error',
        status: error?.status ?? null,
        message: describeUpstreamError(error, config),
      })
    } finally {
      clearInterval(heartbeat)
      clearTimeout(timeout)
      res.off('close', onClose)
      if (!res.writableEnded) res.end()
    }
  })

  // --- image proxy ----------------------------------------------------------
  // The browser uses this so images can be embedded into the offline export
  // without CORS problems, and so the provider chain stays on the server.
  // Allowlisted on purpose: never an open proxy.
  app.get('/api/image', async (req, res) => {
    const source = typeof req.query.url === 'string' ? req.query.url : ''
    const prompt = typeof req.query.prompt === 'string' ? req.query.prompt : ''
    let image = null
    if (source) {
      if (!isAllowedImageUrl(source, config)) return res.status(400).json({ error: '不允许的图片地址。' })
      image = await resolveImage({ config, image: { url: source, prompt: '' }, fetchImpl, logger })
    } else if (prompt) {
      image = await resolveImage({
        config,
        image: {
          prompt,
          width: Number.parseInt(req.query.w, 10) || undefined,
          height: Number.parseInt(req.query.h, 10) || undefined,
        },
        fetchImpl,
        logger,
      })
    } else {
      return res.status(400).json({ error: '缺少图片参数。' })
    }
    if (!image) return res.status(502).end()
    res.setHeader('Content-Type', image.mime)
    res.setHeader('Cache-Control', 'public, max-age=86400')
    res.end(image.buffer)
  })

  // --- pptx export ----------------------------------------------------------
  app.post('/api/export/pptx', async (req, res) => {
    let deck = null
    let requestedMode = ''
    let saveTo = ''
    if (req.body && typeof req.body === 'object' && !Buffer.isBuffer(req.body)) {
      if (typeof req.body.payload === 'string') {
        try {
          const parsed = JSON.parse(req.body.payload)
          deck = parsed?.deck ?? parsed
          requestedMode = String(parsed?.buildMode || '')
          saveTo = String(parsed?.saveTo || '')
        } catch {
          deck = null
        }
      } else {
        // JSON callers may post the deck directly or wrapped as { deck, buildMode, saveTo }
        deck = req.body.deck && typeof req.body.deck === 'object' ? req.body.deck : req.body
        requestedMode = String(req.body.buildMode || '')
        saveTo = String(req.body.saveTo || '')
      }
    }
    if (!deck || !Array.isArray(deck.slides) || !deck.slides.length) {
      return res.status(400).json({ error: '缺少可导出的演示文稿数据。' })
    }

    // `animation` injects real PowerPoint entrance effects; `steps` turns the
    // reveal into cumulative slides, which works in every reader.
    const buildMode = ['animation', 'steps', 'both'].includes(requestedMode || config.pptxBuildMode)
      ? requestedMode || config.pptxBuildMode
      : 'animation'
    try {
      const { buildPptx } = await import('./pptx.js')
      const { addSlideAnimations } = await import('./pptxAnimate.js')
      const { expandBuildSteps } = await import('./pptxSteps.js')

      // Reveal strategy: real entrance animations, cumulative build slides, or both.
      const expanded = buildMode === 'animation' ? { slides: deck.slides, expanded: 0, added: 0 } : expandBuildSteps(deck.slides)
      const exportDeck = expanded.slides.length === deck.slides.length ? deck : { ...deck, slides: expanded.slides }

      const raw = await buildPptx({
        deck: exportDeck,
        generatedAt: new Date().toLocaleString('zh-CN'),
        fetchImage: async (url) => {
          const image = await resolveImage({ config, image: { url, prompt: '' }, fetchImpl, logger })
          return image ? { buffer: image.buffer, mime: image.mime } : null
        },
      })
      // pptxgenjs cannot express animations; inject the entrance sequence into
      // each slide's XML (title → body paragraphs → graphics → pictures).
      const animated =
        buildMode === 'steps'
          ? { buffer: raw }
          : await addSlideAnimations(raw, {
              slides: exportDeck.slides.map((slide) => ({
                title: slide?.title || '',
                subtitle: slide?.subtitle || '',
                eyebrow: slide?.eyebrow || '',
                takeaway: slide?.takeaway || '',
              })),
            })
      const buffer = animated.buffer
      logger.log?.(
        `[pptx] 导出 ${exportDeck.slides.length} 页（原始 ${deck.slides.length} 页，展开 ${expanded.expanded} 页，模式 ${buildMode}）`,
      )
      const filename = safeFilename(deck.title || 'presentation', '.pptx')

      // Saving into the project folder matters for animations: a file that comes
      // out of a browser download carries a "mark of the web", and PowerPoint then
      // opens it in Protected View, where animations do not play. A file written
      // straight to disk has no such mark.
      if (saveTo === 'project') {
        const target = path.join(outputDir, filename)
        fs.writeFileSync(target, buffer)
        logger.info?.(`[pptx] 已保存到 ${target}`)
        return res.json({
          ok: true,
          path: target,
          bytes: buffer.length,
          slides: exportDeck.slides.length,
          buildMode,
        })
      }
      res.setHeader(
        'Content-Type',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      )
      res.setHeader('Content-Disposition', `attachment; filename*=UTF-8''${encodeURIComponent(filename)}`)
      res.end(buffer)
    } catch (error) {
      logger.error?.('[pptx] ', error?.stack || error?.message || error)
      res.status(500).json({ error: `导出 PPTX 失败：${publicErrorMessage(error)}` })
    }
  })

  // Production: serve the built front end (npm run build). Hashed assets are
  // immutable; index.html is never cached so a redeploy is picked up immediately.
  const distDir = path.join(ROOT, 'web', 'dist')
  const indexHtml = path.join(distDir, 'index.html')
  if (fs.existsSync(indexHtml)) {
    app.use(
      '/assets',
      express.static(path.join(distDir, 'assets'), {
        immutable: true,
        maxAge: '365d',
        fallthrough: true,
      }),
    )
    app.use(express.static(distDir, { index: false, maxAge: '1h', etag: true }))
    app.use((req, res, next) => {
      if (req.method !== 'GET' && req.method !== 'HEAD') return next()
      if (req.path.startsWith('/api/') || req.path === '/health') return next()
      res.setHeader('Cache-Control', 'no-cache')
      res.sendFile(indexHtml)
    })
    logger.info?.(`静态资源目录：${distDir}`)
  } else {
    app.get('/', (req, res) => {
      res
        .status(503)
        .type('text/plain; charset=utf-8')
        .send(
          '前端未构建：找不到 web/dist/index.html。\n' +
            '请在项目目录执行 npm run build（或运行 start.bat / start.sh，它会自动构建）。',
        )
    })
    logger.warn?.('未找到 web/dist/index.html —— 请先运行 npm run build。')
  }

  // Last-resort handler: never leak a stack trace to the browser.
  app.use((error, req, res, next) => {
    logger.error?.('[http] ', error?.stack || error?.message || error)
    if (res.headersSent) return next(error)
    const status = error?.status || error?.statusCode || 500
    res.status(status).json({
      error: status === 413 ? '请求体过大：请减小上传内容或调大 .env 中的 MAX_UPLOAD_MB。' : publicErrorMessage(error),
    })
  })

  return app
}
