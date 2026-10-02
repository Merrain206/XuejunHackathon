import assert from 'node:assert/strict'
import test from 'node:test'

import { imageProviderChain, loadConfig } from '../../server/config.js'
import { buildImageUrl, isAllowedImageUrl, normalizeImagePrompt, resolveImage } from '../../server/images.js'

const PNG = Buffer.concat([
  Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AL+2gAAAABJRU5ErkJggg==',
    'base64',
  ),
  Buffer.alloc(300, 7),
])

const imageResponse = () => new Response(PNG, { status: 200, headers: { 'Content-Type': 'image/png' } })
const configWith = (env = {}) => loadConfig({ MODEL_NAME: 'm', DEEPSEEK_API_KEY: 'k', ...env })

const OPENVERSE_PAYLOAD = {
  results: [
    { title: 'Modern office', url: 'https://images.example.org/office.jpg', license: 'cc0' },
    { title: 'Fallback', url: 'https://images2.example.org/other.jpg', license: 'pdm' },
  ],
}

/* ------------------------------------------------------- prompt hygiene -- */

test('image keywords are normalized to usable English', () => {
  assert.equal(normalizeImagePrompt('现代办公室 团队 数据看板'), '', '中文关键词不可用')
  assert.equal(
    normalizeImagePrompt('Modern Office, team reviewing charts; warm light!'),
    'modern office team reviewing charts warm light',
  )
  assert.equal(normalizeImagePrompt('success growth future innovation'), 'success growth future innovation')
  assert.equal(normalizeImagePrompt('a'), '', '单词不足以构成检索短语')
  assert.equal(normalizeImagePrompt('data-flow 2026 dashboard'), 'data-flow 2026 dashboard')
  assert.equal(normalizeImagePrompt('team team team office'), 'team office', '去重')
  const long = normalizeImagePrompt(Array.from({ length: 40 }, (_, i) => `word${i}`).join(' '), { maxWords: 10 })
  assert.equal(long.split(' ').length, 10, '词数上限')
})

test('the model prompt is dropped when it is not usable English', () => {
  assert.equal(normalizeImagePrompt('封面主图：深蓝色数据流'), '')
  assert.equal(normalizeImagePrompt(''), '')
})

/* ------------------------------------------------------------- encoding -- */

test('the image URL is built with the keywords percent-encoded', () => {
  const config = configWith({ POLLINATIONS_TOKEN: 'tok 123' })
  const url = buildImageUrl(config, 'modern office, warm light')
  assert.ok(url.includes(encodeURIComponent('modern office warm light')))
  assert.ok(!url.includes(' '), 'URL 中不能有未编码空格')
  assert.ok(url.includes(`&token=${encodeURIComponent('tok 123')}`), 'token 必须附加并编码')
  assert.match(url, /nologo=true/)
  assert.match(url, /width=1024&height=720/)
})

test('without a token the Pollinations URL is never used at all', () => {
  const config = configWith({})
  assert.deepEqual(imageProviderChain(config), ['openverse'], '匿名调用有 watermark，必须排除')
  assert.equal(imageProviderChain(configWith({ IMAGE_PROVIDER: 'pollinations' })).includes('pollinations'), false)
  assert.deepEqual(
    imageProviderChain(configWith({ POLLINATIONS_TOKEN: 't' })),
    ['openverse', 'pollinations'],
  )
})

/* --------------------------------------------------------------- chain --- */

test('the keyless provider searches CC0/public-domain images and downloads one', async () => {
  const config = configWith({})
  const calls = []
  const fetchImpl = async (url) => {
    calls.push(String(url))
    if (String(url).includes('openverse')) {
      return new Response(JSON.stringify(OPENVERSE_PAYLOAD), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    return imageResponse()
  }

  const result = await resolveImage({ config, image: { prompt: 'modern office team' }, fetchImpl })
  assert.equal(result.source, 'openverse')
  assert.equal(result.mime, 'image/png')
  assert.match(calls[0], /api\.openverse\.org\/v1\/images\/\?q=modern(%20|\+)office(%20|\+)team/)
  assert.match(calls[0], /license=cc0%2Cpdm/, '只取 CC0 / 公有领域，避免署名义务')
  assert.equal(calls.some((url) => url.includes('pollinations')), false, '不得回落到有水印的匿名接口')
})

test('an unusable (non-English) keyword skips the whole chain', async () => {
  const config = configWith({})
  const logs = []
  let called = 0
  const result = await resolveImage({
    config,
    image: { prompt: '深蓝色数据流封面' },
    fetchImpl: async () => {
      called += 1
      return imageResponse()
    },
    logger: { warn: (message) => logs.push(message) },
  })
  assert.equal(result, null)
  assert.equal(called, 0, '不应发起任何请求')
  assert.ok(logs.some((line) => /不是英文/.test(line)))
})

test('a configured text-to-image API still wins over the search provider', async () => {
  const config = configWith({
    IMAGE_API_URL: 'https://images.internal.test/v1/generations',
    IMAGE_API_KEY: 'img-key',
    IMAGE_MODEL: 'flux-pro',
  })
  assert.equal(imageProviderChain(config)[0], 'configured-api')
  const calls = []
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url: String(url), body: options.body })
    return new Response(JSON.stringify({ data: [{ b64_json: PNG.toString('base64') }] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const result = await resolveImage({ config, image: { prompt: 'modern office' }, fetchImpl })
  assert.equal(result.source, 'configured-api')
  assert.equal(calls.length, 1)
  assert.match(calls[0].body, /flux-pro/)
})

test('every failing provider degrades to null so the renderer shows a gradient', async () => {
  const config = configWith({})
  const logs = []
  const result = await resolveImage({
    config,
    image: { prompt: 'modern office', url: 'https://evil.example/x.png' },
    fetchImpl: async () => new Response('nope', { status: 500 }),
    logger: { warn: (message) => logs.push(message) },
  })
  assert.equal(result, null)
  assert.ok(logs.some((line) => /拒绝非白名单/.test(line)))
})

test('the browser-facing allowlist stays narrow', () => {
  const config = configWith({})
  assert.equal(isAllowedImageUrl('https://image.pollinations.ai/prompt/x', config), true)
  assert.equal(isAllowedImageUrl('https://api.openverse.org/v1/images/', config), true)
  assert.equal(isAllowedImageUrl('https://evil.example/x.png', config), false)
  assert.equal(isAllowedImageUrl('file:///etc/passwd', config), false)
  assert.equal(isAllowedImageUrl('not a url', config), false)
})
