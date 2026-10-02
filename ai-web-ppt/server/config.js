// Configuration loading. Every secret and every environment-specific value comes
// from the environment / project .env file — nothing is hardcoded in source.
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

export const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
export const ENV_FILE = path.join(ROOT, '.env')

// Non-secret defaults. Kept here so the app is usable with a bare .env, but
// every one of them can be overridden from the environment.
export const FALLBACKS = {
  BASE_URL: 'https://api.deepseek.com/v1',
  HOST: '127.0.0.1',
  PORT: '8787',
  LOG_LEVEL: 'info',
  MAX_UPLOAD_MB: '4',
  MAX_SLIDES: '20',
  REQUEST_TIMEOUT_MS: '180000',
  MAX_BULLETS_PER_SLIDE: '6',
  SEARCH_PROVIDER: 'none',
  SEARCH_MAX_RESULTS: '4',
  IMAGE_PROVIDER: 'auto',
  IMAGE_WIDTH: '1024',
  IMAGE_HEIGHT: '720',
  OPENVERSE_URL: 'https://api.openverse.org/v1/images/',
  PPTX_BUILD_MODE: 'animation',
}

/** Keyless Pollinations template. Anonymous calls now stamp a watermark, so this
 *  is only used when POLLINATIONS_TOKEN is configured (which removes it). */
export const POLLINATIONS_TEMPLATE =
  'https://image.pollinations.ai/prompt/{prompt}?width={width}&height={height}&model=flux&nologo=true&safe=false{token}'

/**
 * Minimal .env reader: `KEY=value` per line, `#` comments, optional quotes.
 * Existing environment variables always win, so `FOO=bar node server/index.js`
 * and test harnesses can override the file without editing it.
 */
export function parseEnv(text) {
  const out = {}
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq <= 0) continue
    const key = line.slice(0, eq).trim().replace(/^export\s+/, '')
    let value = line.slice(eq + 1).trim()
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
      (value.startsWith("'") && value.endsWith("'") && value.length > 1)
    ) {
      value = value.slice(1, -1)
    }
    out[key] = value
  }
  return out
}

/** The .env file this process should read (ENV_FILE lets a deployment point elsewhere). */
export function envFileFor(env = process.env) {
  const custom = typeof env.ENV_FILE === 'string' ? env.ENV_FILE.trim() : ''
  return custom || ENV_FILE
}

/** Load .env into process.env without clobbering real environment variables. */
export function loadEnvFile(file = ENV_FILE, env = process.env) {  let text
  try {
    text = fs.readFileSync(file, 'utf8')
  } catch {
    return { loaded: false, file, reason: 'not-found' }
  }
  const parsed = parseEnv(text)
  for (const [key, value] of Object.entries(parsed)) {
    if (env[key] === undefined || env[key] === '') env[key] = value
  }
  return { loaded: true, file, keys: Object.keys(parsed) }
}

function pick(env, key) {
  const value = env[key]
  return typeof value === 'string' ? value.trim() : ''
}

function pickInt(env, key, fallback, { min = 1 } = {}) {
  const parsed = Number.parseInt(pick(env, key) || fallback, 10)
  return Number.isFinite(parsed) && parsed >= min ? parsed : Number(fallback)
}

/**
 * Serve settings that must not silently fall back to a developer machine:
 * a missing/blank required value makes the process exit with a fix list unless
 * ALLOW_INCOMPLETE_CONFIG=1 is set explicitly (that escape hatch only exists so
 * the UI can be inspected before a key is available).
 */
export function loadServeConfig(env = process.env) {
  const originRaw = pick(env, 'ALLOWED_ORIGINS')
  return {
    nodeEnv: pick(env, 'NODE_ENV') || 'production',
    host: pick(env, 'HOST') || FALLBACKS.HOST,
    logLevel: pick(env, 'LOG_LEVEL') || FALLBACKS.LOG_LEVEL,
    logFile: pick(env, 'LOG_FILE'),
    outputDir: pick(env, 'OUTPUT_DIR'),
    allowedOrigins: originRaw
      ? originRaw
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
      : [], // empty = same-origin only, which is what the bundled front end needs
    maxUploadMb: pickInt(env, 'MAX_UPLOAD_MB', FALLBACKS.MAX_UPLOAD_MB),
    workers: pickInt(env, 'WORKERS', '0', { min: 0 }),
    trustProxy: ['1', 'true', 'yes'].includes(pick(env, 'TRUST_PROXY').toLowerCase()),
    allowIncompleteConfig: ['1', 'true', 'yes'].includes(pick(env, 'ALLOW_INCOMPLETE_CONFIG').toLowerCase()),
  }
}

export function loadConfig(env = process.env, { envFileLoaded = false, root = ROOT } = {}) {
  const apiKey = pick(env, 'DEEPSEEK_API_KEY')
  const searchProviderRaw = pick(env, 'SEARCH_PROVIDER').toLowerCase() || FALLBACKS.SEARCH_PROVIDER
  const serve = loadServeConfig(env)
  return {
    ...serve,
    isProduction: serve.nodeEnv !== 'development',
    outputDir: serve.outputDir ? path.resolve(serve.outputDir) : path.join(root, '.exports'),
    // --- model ---
    apiKey,
    hasApiKey: apiKey.length > 0,
    baseUrl: (pick(env, 'BASE_URL') || FALLBACKS.BASE_URL).replace(/\/+$/, ''),
    model: pick(env, 'MODEL_NAME'), // deliberately no hardcoded model name
    port: pickInt(env, 'PORT', FALLBACKS.PORT),
    maxSlides: pickInt(env, 'MAX_SLIDES', FALLBACKS.MAX_SLIDES),
    requestTimeoutMs: pickInt(env, 'REQUEST_TIMEOUT_MS', FALLBACKS.REQUEST_TIMEOUT_MS),
    maxBulletsPerSlide: pickInt(env, 'MAX_BULLETS_PER_SLIDE', FALLBACKS.MAX_BULLETS_PER_SLIDE),

    // --- image generation (optional; keyless CC0 search is the default) ---
    imageProvider: ['auto', 'api', 'openverse', 'pollinations', 'none'].includes(
      pick(env, 'IMAGE_PROVIDER').toLowerCase(),
    )
      ? pick(env, 'IMAGE_PROVIDER').toLowerCase()
      : FALLBACKS.IMAGE_PROVIDER,
    imageTemplate: pick(env, 'IMAGE_URL_TEMPLATE') || POLLINATIONS_TEMPLATE,
    imageWidth: pickInt(env, 'IMAGE_WIDTH', FALLBACKS.IMAGE_WIDTH),
    imageHeight: pickInt(env, 'IMAGE_HEIGHT', FALLBACKS.IMAGE_HEIGHT),
    // Any already-configured text-to-image API (OpenAI-compatible /images/generations).
    imageApiUrl: pick(env, 'IMAGE_API_URL'),
    imageApiKey: pick(env, 'IMAGE_API_KEY'),
    imageModel: pick(env, 'IMAGE_MODEL'),
    // Pollinations only renders without its watermark for authenticated calls.
    pollinationsToken: pick(env, 'POLLINATIONS_TOKEN'),
    openverseUrl: pick(env, 'OPENVERSE_URL') || FALLBACKS.OPENVERSE_URL,

    // How the PPTX reveals elements: native entrance animations, cumulative
    // build slides, or both.
    pptxBuildMode: ['animation', 'steps', 'both'].includes(pick(env, 'PPTX_BUILD_MODE').toLowerCase())
      ? pick(env, 'PPTX_BUILD_MODE').toLowerCase()
      : FALLBACKS.PPTX_BUILD_MODE,

    // --- web search (optional; powers the citation switch) ---
    searchProvider: ['none', 'tavily', 'serper', 'duckduckgo', 'custom'].includes(searchProviderRaw)
      ? searchProviderRaw
      : 'none',
    searchApiKey: pick(env, 'SEARCH_API_KEY'),
    searchApiUrl: pick(env, 'SEARCH_API_URL'),
    searchMaxResults: pickInt(env, 'SEARCH_MAX_RESULTS', FALLBACKS.SEARCH_MAX_RESULTS),

    envFileLoaded,
    envFilePath: ENV_FILE,
  }
}

/** Human-readable preconditions, surfaced by /api/health and the UI. */
export function configProblems(config) {
  const problems = []
  if (!config.hasApiKey) {
    problems.push('DEEPSEEK_API_KEY 未配置：请在项目根目录 .env 中填写后重启服务。')
  }
  if (!config.model) {
    problems.push('MODEL_NAME 未配置：请在项目根目录 .env 中设置模型名（例如 MODEL_NAME=deepseek-flash）。')
  }
  return problems
}

/**
 * Fail-fast guard for production starts. Returns the problems; callers decide,
 * but `assertConfig` is what `scripts/serve.mjs` and the start scripts use so a
 * misconfigured deployment never comes up half-working.
 */
export function assertConfig(config, { allowIncomplete = config.allowIncompleteConfig } = {}) {
  const problems = configProblems(config)
  if (!problems.length || allowIncomplete) return problems
  const error = new Error(
    [
      '配置不完整，服务未启动：',
      ...problems.map((problem) => `  - ${problem}`),
      '',
      '修法：复制 .env.example 为 .env 并填写；或设置同名环境变量。',
      '（如只想先看界面：ALLOW_INCOMPLETE_CONFIG=1，此时生成功能不可用）',
    ].join('\n'),
  )
  error.code = 'CONFIG_INCOMPLETE'
  error.problems = problems
  throw error
}

/** Capability report shown in the UI so the user knows what is actually wired. */
export function capabilities(config) {
  const providers = imageProviderChain(config)
  return {
    image: {
      // First provider that can actually run, used for the badge/hint only.
      provider: providers[0] ?? 'none',
      chain: providers,
      watermarkFree: !providers.includes('pollinations') || Boolean(config.pollinationsToken),
      fallback: 'css-gradient',
      size: { width: config.imageWidth, height: config.imageHeight },
    },
    search: {
      provider: config.searchProvider,
      enabled: config.searchProvider !== 'none' && (config.searchProvider === 'duckduckgo' || Boolean(config.searchApiKey)),
      maxResults: config.searchMaxResults,
    },
  }
}

/**
 * Ordered image providers that are actually usable with the current config.
 * Anonymous Pollinations is deliberately excluded: it stamps a watermark.
 */
export function imageProviderChain(config) {
  const chain = []
  const wanted = config.imageProvider || 'auto'
  const hasApi = Boolean(config.imageApiUrl && config.imageApiKey)
  if (wanted === 'none') return chain
  if ((wanted === 'auto' || wanted === 'api') && hasApi) chain.push('configured-api')
  if (wanted === 'auto' || wanted === 'openverse') chain.push('openverse')
  if ((wanted === 'auto' || wanted === 'pollinations') && config.pollinationsToken) chain.push('pollinations')
  return chain
}
