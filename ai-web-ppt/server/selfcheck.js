// One self-check implementation, used by three callers:
//   * scripts/check-env.mjs   (CLI, run by start.bat / start.sh / CI)
//   * GET /health             (HTML page for a human on a strange machine)
//   * GET /api/health         (JSON, already existed — now reports the same facts)
import fs from 'node:fs'
import net from 'node:net'
import path from 'node:path'
import process from 'node:process'

export const MIN_NODE = [20, 12, 0]

const PASS = 'pass'
const WARN = 'warn'
const FAIL = 'fail'

function check(id, label, status, detail, fix = '') {
  return { id, label, status, detail, fix }
}

export function nodeVersionCheck(version = process.versions.node) {
  const parts = String(version).split('.').map((part) => Number.parseInt(part, 10) || 0)
  const ok =
    parts[0] > MIN_NODE[0] ||
    (parts[0] === MIN_NODE[0] && parts[1] > MIN_NODE[1]) ||
    (parts[0] === MIN_NODE[0] && parts[1] === MIN_NODE[1] && parts[2] >= MIN_NODE[2])
  return check(
    'node',
    `Node.js 版本（需要 >= ${MIN_NODE.join('.')}）`,
    ok ? PASS : FAIL,
    ok ? `v${version}` : `v${version} 太旧`,
    '安装 Node.js 20.12+ / 22 LTS：https://nodejs.org/en/download（或 nvm install 22）',
  )
}

export async function portCheck(host, port, timeoutMs = 800) {
  return new Promise((resolve) => {
    const server = net.createServer()
    const done = (result) => {
      try {
        server.close()
      } catch {
        /* ignore */
      }
      resolve(result)
    }
    server.once('error', (error) => {
      if (error.code === 'EADDRINUSE') {
        done(
          check(
            'port',
            `端口 ${port} 可用`,
            FAIL,
            `已被占用（${host}:${port}）`,
            `改 .env 里的 PORT，或结束占用该端口的进程（Windows: netstat -ano | findstr :${port}；macOS/Linux: lsof -i :${port}）`,
          ),
        )
      } else {
        done(check('port', `端口 ${port} 可用`, WARN, `无法检测：${error.code || error.message}`, ''))
      }
    })
    const timer = setTimeout(() => done(check('port', `端口 ${port} 可用`, WARN, '检测超时', '')), timeoutMs)
    server.listen(port, host, () => {
      clearTimeout(timer)
      done(check('port', `端口 ${port} 可用`, PASS, '空闲', ''))
    })
  })
}

export function writableDirCheck(dir, label = '输出目录可写') {
  try {
    fs.mkdirSync(dir, { recursive: true })
    const probe = path.join(dir, `.write-probe-${process.pid}`)
    fs.writeFileSync(probe, 'ok')
    fs.rmSync(probe, { force: true })
    return check('output', label, PASS, dir, '')
  } catch (error) {
    return check(
      'output',
      label,
      FAIL,
      `${dir} 不可写：${error.code || error.message}`,
      '检查目录权限，或在 .env 里把 OUTPUT_DIR 指到一个可写目录',
    )
  }
}

export function distCheck(root) {
  const indexHtml = path.join(root, 'web', 'dist', 'index.html')
  if (fs.existsSync(indexHtml)) return check('dist', '前端已构建（web/dist）', PASS, indexHtml, '')
  return check(
    'dist',
    '前端已构建（web/dist）',
    FAIL,
    '找不到 web/dist/index.html',
    '运行 npm run build',
  )
}

export function envChecks(config, { envFileLoaded = false, envFile = '' } = {}) {
  const results = []
  results.push(
    check(
      'env-file',
      '.env 配置文件',
      envFileLoaded ? PASS : config.hasApiKey ? WARN : FAIL,
      envFileLoaded ? envFile : '未找到 .env（只读环境变量）',
      '复制 .env.example 为 .env 并填写，或在系统里设置同名环境变量',
    ),
  )
  results.push(
    config.hasApiKey
      ? check('api-key', 'DEEPSEEK_API_KEY 已配置', PASS, '已配置（值不会显示）', '')
      : check(
          'api-key',
          'DEEPSEEK_API_KEY 已配置',
          FAIL,
          '未配置或为空',
          '在 .env 里填写 DEEPSEEK_API_KEY=<你的 Key>（DeepSeek 控制台 → API Keys）',
        ),
  )
  results.push(
    config.model
      ? check('model', 'MODEL_NAME 已配置', PASS, config.model, '')
      : check('model', 'MODEL_NAME 已配置', FAIL, '未配置', '在 .env 里填写 MODEL_NAME=deepseek-flash（或你的模型名）'),
  )
  results.push(
    check(
      'base-url',
      'BASE_URL（OpenAI 兼容接口）',
      config.baseUrl ? PASS : FAIL,
      config.baseUrl || '空',
      '在 .env 里填写 BASE_URL=https://api.deepseek.com/v1',
    ),
  )
  return results
}

export async function upstreamCheck(config, { fetchImpl = globalThis.fetch, timeoutMs = 8000 } = {}) {
  if (!config.hasApiKey || !config.baseUrl) {
    return check('upstream', '模型接口可达', WARN, '缺少 Key 或 BASE_URL，跳过', '')
  }
  const url = `${String(config.baseUrl).replace(/\/+$/, '')}/models`
  try {
    const response = await fetchImpl(url, {
      headers: { Authorization: `Bearer ${config.apiKey}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (response.ok) return check('upstream', '模型接口可达', PASS, `${url}（HTTP ${response.status}）`, '')
    if (response.status === 401 || response.status === 403) {
      return check(
        'upstream',
        '模型接口可达',
        FAIL,
        `${url} 返回 HTTP ${response.status}：Key 无效或被拒`,
        '检查 .env 里的 DEEPSEEK_API_KEY 是否正确、是否过期',
      )
    }
    return check('upstream', '模型接口可达', WARN, `${url} 返回 HTTP ${response.status}`, '确认 BASE_URL 与网络代理设置')
  } catch (error) {
    const reason = error?.cause?.code || error?.code || error?.name || error?.message || 'unknown'
    return check(
      'upstream',
      '模型接口可达',
      WARN,
      `${url} 连接失败（${reason}）`,
      '检查网络/代理；若用公司网络需放行 api.deepseek.com',
    )
  }
}

/**
 * @param {{config: object, root: string, envFileLoaded?: boolean, envFile?: string,
 *   checkPort?: boolean, probeUpstream?: boolean, fetchImpl?: Function}} options
 */
export async function runSelfCheck({
  config,
  root,
  envFileLoaded = false,
  envFile = '',
  checkPort = true,
  probeUpstream = true,
  fetchImpl,
} = {}) {
  const checks = [nodeVersionCheck(), ...envChecks(config, { envFileLoaded, envFile })]
  checks.push(distCheck(root))
  checks.push(writableDirCheck(config.outputDir || path.join(root, '.exports')))
  if (checkPort) checks.push(await portCheck(config.host, config.port))
  if (probeUpstream) checks.push(await upstreamCheck(config, { fetchImpl }))

  const failed = checks.filter((item) => item.status === FAIL)
  const warned = checks.filter((item) => item.status === WARN)
  return {
    ok: failed.length === 0,
    checks,
    failed: failed.length,
    warned: warned.length,
    generatedAt: new Date().toISOString(),
    node: process.versions.node,
    platform: `${process.platform} ${process.arch}`,
  }
}

export { PASS, WARN, FAIL }
