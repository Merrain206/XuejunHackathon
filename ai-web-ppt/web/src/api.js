// Client for the streaming generation endpoint. The API key never reaches the
// browser: it lives only in the server process, read from .env.
import { createSseParser, parseFrame } from './shared/sse.js'

export async function fetchHealth({ signal } = {}) {
  const response = await fetch('/api/health', { signal })
  if (!response.ok) throw new Error(`健康检查失败：HTTP ${response.status}`)
  return response.json()
}

/**
 * POSTs the source document plus the two style switches and consumes the SSE stream.
 * @param {{content: string, stylePreference?: string, showCitations?: boolean,
 *   maxSlides?: number, signal?: AbortSignal}} request
 * @param {{onStatus?: Function, onSearch?: Function, onDesign?: Function,
 *   onProgress?: Function, onSlide?: Function, onDone?: Function, onError?: Function}} handlers
 */
export async function streamGenerate(request, handlers = {}) {
  const response = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      content: request.content,
      stylePreference: request.stylePreference ?? '',
      showCitations: request.showCitations === true,
      maxSlides: request.maxSlides,
    }),
    signal: request.signal,
  })

  if (!response.ok) {
    let message = `请求失败：HTTP ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.error) message = payload.error
    } catch {
      /* keep default message */
    }
    throw new Error(message)
  }
  if (!response.body) throw new Error('浏览器不支持流式响应（response.body 为空）')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parser = createSseParser()
  let done = false

  const handle = (frames) => {
    for (const frame of frames) {
      const { event, data } = parseFrame(frame)
      if (!data) continue
      let payload
      try {
        payload = JSON.parse(data)
      } catch {
        continue
      }
      if (event === 'status') handlers.onStatus?.(payload)
      else if (event === 'search') handlers.onSearch?.(payload)
      else if (event === 'design') handlers.onDesign?.(payload)
      else if (event === 'progress') handlers.onProgress?.(payload)
      else if (event === 'slide') handlers.onSlide?.(payload)
      else if (event === 'done') handlers.onDone?.(payload)
      else if (event === 'error') handlers.onError?.(payload)
    }
  }

  try {
    while (!done) {
      const chunk = await reader.read()
      done = chunk.done
      if (chunk.value) handle(parser.push(decoder.decode(chunk.value, { stream: true })))
    }
    const tail = decoder.decode()
    handle([...(tail ? parser.push(tail) : []), ...parser.flush()])
  } finally {
    try {
      await reader.cancel()
    } catch {
      /* ignore */
    }
  }
}

/** Server-side PPTX export: the server embeds backgrounds, images and citations. */
export async function buildPptxFile({ title, design, slides, meta }) {
  const response = await fetch('/api/export/pptx', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, design, slides, meta }),
  })
  if (!response.ok) {
    let message = `导出失败：HTTP ${response.status}`
    try {
      const payload = await response.json()
      if (payload?.error) message = payload.error
    } catch {
      /* ignore */
    }
    throw new Error(message)
  }
  return response.blob()
}

/**
 * Fetch every slide picture through our own origin and inline it as a data URL,
 * so the exported HTML stays offline and the live/exported look matches.
 * Bounded on purpose: a slow or unreachable image host must never make the
 * export hang — pictures that miss the budget simply fall back to the CSS
 * gradient decoration the renderer already provides.
 */
export async function inlineDeckImages(slides = [], { limit = 8, budgetMs = 12000, perImageMs = 6000 } = {}) {
  const resolved = {}
  const targets = slides
    .map((slide, index) => ({ slide, index }))
    .filter(({ slide }) => slide?.image && slide.image.placement !== 'none')
    .slice(0, limit)
  const deadline = Date.now() + budgetMs

  await Promise.all(
    targets.map(async ({ slide, index }) => {
      for (const candidate of imageCandidates(slide.image)) {
        const remaining = Math.min(perImageMs, deadline - Date.now())
        if (remaining < 500) return
        try {
          resolved[index] = await inlineImage(candidate, { timeoutMs: remaining })
          return
        } catch {
          /* try the next source */
        }
      }
    }),
  )
  return resolved
}
