// Image acquisition chain.
//
// Providers, in the order they are tried (see `imageProviderChain`):
//   1. configured-api  — an OpenAI-compatible text-to-image API you already have
//   2. openverse       — keyless CC0 / public-domain image search (no watermark)
//   3. pollinations    — only when POLLINATIONS_TOKEN is set: anonymous calls
//                        carry a watermark even with nologo=true
// Anything that fails returns null and the renderer falls back to a CSS gradient
// decoration — failures are never surfaced in the UI.
import { imageProviderChain } from './config.js'

const IMAGE_TIMEOUT_MS = 20000
const MAX_IMAGE_BYTES = 8 * 1024 * 1024
const OPENVERSE_LICENSES = 'cc0,pdm'

/**
 * Image keywords must be usable as a query string: English, concrete, no CJK.
 * Drops anything that is not latin letters/digits/space/dash/comma and caps the
 * length. Returns '' when nothing usable is left (slide then uses the gradient).
 */
export function normalizeImagePrompt(prompt, { maxWords = 24 } = {}) {
  const words = String(prompt || '')
    .replace(/[^\x20-\x7E]+/g, ' ') // strip CJK and other non-ASCII
    .replace(/[^A-Za-z0-9\s,-]/g, ' ')
    .split(/[\s,]+/)
    .map((word) => word.trim().toLowerCase())
    .filter((word) => /^[a-z][a-z0-9-]{1,}$/.test(word) || /^\d+$/.test(word))
  const seen = new Set()
  const unique = []
  for (const word of words) {
    if (seen.has(word)) continue
    seen.add(word)
    unique.push(word)
    if (unique.length >= maxWords) break
  }
  // A usable query needs at least a couple of real words.
  return unique.length >= 2 ? unique.join(' ') : ''
}

/** Absolute allowlist for URLs that arrive from the browser or the model. */
export function isAllowedImageUrl(url, config) {
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return false
  const allowed = new Set(['image.pollinations.ai', 'api.openverse.org', 'openverse.org'])
  for (const candidate of [config?.imageApiUrl, config?.imageTemplate, config?.openverseUrl]) {
    if (!candidate) continue
    try {
      allowed.add(new URL(candidate.replace('{prompt}', 'seed')).host)
    } catch {
      /* ignore malformed config */
    }
  }
  for (const extra of String(process.env.IMAGE_PROXY_ALLOW || '').split(',')) {
    const host = extra.trim()
    if (host) allowed.add(host)
  }
  return allowed.has(parsed.host)
}

/** Build the authenticated Pollinations URL (anonymous calls are watermarked). */
export function buildImageUrl(config, prompt, { width, height } = {}) {
  const keywords = normalizeImagePrompt(prompt) || String(prompt || '').replace(/\s+/g, ' ').trim()
  if (!keywords) return ''
  const template = config?.imageTemplate || ''
  if (!template.includes('{prompt}')) return ''
  const token = config?.pollinationsToken ? `&token=${encodeURIComponent(config.pollinationsToken)}` : ''
  return template
    .replace('{prompt}', encodeURIComponent(keywords))
    .replace('{width}', String(width ?? config.imageWidth ?? 1024))
    .replace('{height}', String(height ?? config.imageHeight ?? 720))
    .replace('{token}', token)
}

async function readImageResponse(response) {
  if (!response?.ok) return null
  const mime = response.headers.get('content-type')?.split(';')[0] || 'image/png'
  if (!mime.startsWith('image/')) return null
  const declared = Number(response.headers.get('content-length') || 0)
  if (declared > MAX_IMAGE_BYTES) return null
  const buffer = Buffer.from(await response.arrayBuffer())
  if (buffer.length < 256 || buffer.length > MAX_IMAGE_BYTES) return null
  return { buffer, mime }
}

async function download(url, { fetchImpl, signal }) {
  return readImageResponse(await fetchImpl(url, { signal, redirect: 'follow' }))
}

/** Calls the configured OpenAI-compatible image API (if any). */
async function generateViaApi(config, prompt, { fetchImpl, signal }) {
  if (!config.imageApiUrl || !config.imageApiKey) return null
  const response = await fetchImpl(config.imageApiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${config.imageApiKey}` },
    body: JSON.stringify({
      model: config.imageModel || undefined,
      prompt,
      n: 1,
      size: `${config.imageWidth}x${config.imageHeight}`,
      response_format: 'b64_json',
    }),
    signal,
  })
  if (!response.ok) throw new Error(`image api HTTP ${response.status}`)
  const payload = await response.json()
  const first = payload.data?.[0] ?? payload.images?.[0]
  if (!first) return null
  if (first.b64_json) return { buffer: Buffer.from(first.b64_json, 'base64'), mime: 'image/png' }
  const url = first.url || (typeof first === 'string' ? first : '')
  if (!url) return null
  return download(url, { fetchImpl, signal })
}

/**
 * Keyless, watermark-free image search restricted to CC0 / public domain so no
 * attribution is required. Results are third-party hosts, but they come from a
 * trusted API response rather than from user input.
 */
async function searchOpenverse(config, prompt, { fetchImpl, signal }) {
  const url =
    `${config.openverseUrl}?q=${encodeURIComponent(prompt)}` +
    `&license=${encodeURIComponent(OPENVERSE_LICENSES)}&page_size=5&mature=false`
  const response = await fetchImpl(url, {
    headers: { Accept: 'application/json', 'User-Agent': 'ai-web-ppt/1.0' },
    signal,
  })
  if (!response.ok) throw new Error(`openverse HTTP ${response.status}`)
  const payload = await response.json()
  const results = Array.isArray(payload.results) ? payload.results : []
  for (const result of results) {
    const candidate = result.url || result.thumbnail
    if (!/^https?:\/\//i.test(String(candidate || ''))) continue
    try {
      const image = await download(candidate, { fetchImpl, signal })
      if (image) return image
    } catch {
      /* try the next result */
    }
  }
  return null
}

/**
 * @returns {Promise<{buffer: Buffer, mime: string, source: string}|null>}
 */
export async function resolveImage({ config, image, fetchImpl = globalThis.fetch, logger = console } = {}) {
  if (!image) return null
  const signal = AbortSignal.timeout(IMAGE_TIMEOUT_MS)

  // 1. an explicit picture URL (from the model) — allowlisted on purpose
  if (image.url) {
    if (!isAllowedImageUrl(image.url, config)) {
      logger.warn?.(`[image] 拒绝非白名单图片地址：${image.url}`)
    } else {
      try {
        const result = await download(image.url, { fetchImpl, signal })
        if (result) return { ...result, source: 'model-url' }
      } catch (error) {
        logger.warn?.(`[image] 下载失败：${error?.message || error}`)
      }
    }
  }

  const prompt = normalizeImagePrompt(image.prompt)
  if (!prompt) {
    if (image.prompt) logger.warn?.('[image] 配图关键词不是英文，跳过配图并使用渐变装饰')
    return null
  }

  for (const provider of imageProviderChain(config)) {
    try {
      if (provider === 'configured-api') {
        const generated = await generateViaApi(config, prompt, { fetchImpl, signal })
        if (generated) return { ...generated, source: 'configured-api' }
      } else if (provider === 'openverse') {
        const found = await searchOpenverse(config, prompt, { fetchImpl, signal })
        if (found) return { ...found, source: 'openverse' }
      } else if (provider === 'pollinations') {
        const url = buildImageUrl(config, prompt)
        if (url) {
          const fetched = await download(url, { fetchImpl, signal })
          if (fetched) return { ...fetched, source: 'pollinations' }
        }
      }
    } catch (error) {
      logger.warn?.(`[image] ${provider} 获取失败：${error?.message || error}`)
    }
  }

  // Caller falls back to a CSS gradient decoration.
  return null
}
