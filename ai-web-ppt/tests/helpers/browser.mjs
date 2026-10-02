// Minimal Chrome DevTools Protocol driver (no Playwright/Puppeteer dependency).
// Speaks HTTP + WebSocket directly, so it works with an already-installed Edge.
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'

const CANDIDATES = [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
]

export function findBrowserExecutable() {
  return CANDIDATES.find((candidate) => fs.existsSync(candidate)) ?? null
}

export async function connectCdp(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', () => reject(new Error(`无法连接 CDP: ${webSocketUrl}`)), { once: true })
  })

  let nextId = 0
  const pending = new Map()
  const listeners = new Map()

  socket.addEventListener('message', (event) => {
    const text = typeof event.data === 'string' ? event.data : Buffer.from(event.data).toString('utf8')
    let message
    try {
      message = JSON.parse(text)
    } catch {
      return
    }
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) reject(new Error(`${message.error.message} (${message.error.code})`))
      else resolve(message.result)
      return
    }
    if (message.method) {
      for (const handler of listeners.get(message.method) ?? []) handler(message.params)
    }
  })

  return {
    send(method, params = {}) {
      const id = ++nextId
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method, params }))
      })
    },
    on(method, handler) {
      const list = listeners.get(method) ?? []
      list.push(handler)
      listeners.set(method, list)
    },
    close() {
      try {
        socket.close()
      } catch {
        /* ignore */
      }
    },
  }
}

export async function evaluate(cdp, expression) {
  const result = await cdp.send('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (result.exceptionDetails) {
    const description =
      result.exceptionDetails.exception?.description || result.exceptionDetails.text || 'unknown page error'
    throw new Error(`页面执行出错: ${description}`)
  }
  return result.result?.value
}

export async function waitFor(cdp, expression, { timeout = 20000, interval = 120, label } = {}) {
  const deadline = Date.now() + timeout
  for (;;) {
    const value = await evaluate(cdp, expression)
    if (value) return value
    if (Date.now() > deadline) throw new Error(`等待超时: ${label || expression}`)
    await delay(interval)
  }
}

export async function navigate(cdp, url) {
  await cdp.send('Page.navigate', { url })
  await waitFor(cdp, 'document.readyState === "complete"', { label: `加载 ${url}` })
}

export async function screenshot(cdp, file) {
  const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, Buffer.from(data, 'base64'))
  return file
}

const KEYS = {
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', windowsVirtualKeyCode: 39, nativeVirtualKeyCode: 39 },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', windowsVirtualKeyCode: 37, nativeVirtualKeyCode: 37 },
  Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 },
}

export async function pressKey(cdp, name) {
  const info = KEYS[name]
  if (!info) throw new Error(`未定义的按键: ${name}`)
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', ...info })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', ...info })
}

/**
 * A real mouse click at the element's centre.
 *
 * `element.click()` fires a synthetic event with no user activation, which makes
 * Chrome treat any download it triggers as "automatic" and block the second one.
 * Real input mirrors what a user does and keeps downloads allowed.
 */
export async function clickElement(cdp, selector) {
  const box = await evaluate(
    cdp,
    `(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const rect = el.getBoundingClientRect();
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    })()`,
  )
  if (!box) throw new Error(`找不到元素：${selector}`)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: box.x, y: box.y })
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mousePressed',
    x: box.x,
    y: box.y,
    button: 'left',
    clickCount: 1,
  })
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased',
    x: box.x,
    y: box.y,
    button: 'left',
    clickCount: 1,
  })
}

/** Kills a whole process tree (Edge spawns many children). */
function killTree(pid) {
  return new Promise((resolve) => {
    if (!pid) return resolve()
    try {
      const killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' })
      killer.on('exit', () => resolve())
      killer.on('error', () => resolve())
    } catch {
      resolve()
    }
  })
}

/** Launches headless Edge/Chrome and returns handles for the page + browser. */
export async function launchBrowser({ downloadDir } = {}) {
  const executable = findBrowserExecutable()
  if (!executable) throw new Error('没有找到 Edge/Chrome 可执行文件')

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'ppt-e2e-'))
  if (downloadDir) fs.mkdirSync(downloadDir, { recursive: true })
  const stderrLog = path.join(userDataDir, 'browser-stderr.log')
  const stderrFd = fs.openSync(stderrLog, 'w')

  let child
  try {
    child = spawn(
      executable,
      [
        '--headless=new',
        '--disable-gpu',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-extensions',
        '--disable-background-networking',
        '--remote-debugging-port=0',
        `--user-data-dir=${userDataDir}`,
        'about:blank',
      ],
      // stdio must not be piped: sandboxes that forbid piped stdio fail the spawn
      // with EPERM. stderr goes to a file we can quote if the launch fails.
      { stdio: ['ignore', 'ignore', stderrFd] },
    )
  } catch (error) {
    throw new Error(`无法启动浏览器进程: ${error.message}`)
  }

  const portFile = path.join(userDataDir, 'DevToolsActivePort')
  let port = null
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null) break
    if (fs.existsSync(portFile)) {
      const [first] = fs.readFileSync(portFile, 'utf8').split('\n')
      port = Number.parseInt(first, 10)
      if (port) break
    }
    await delay(100)
  }
  if (!port) {
    await killTree(child.pid)
    let detail = ''
    try {
      detail = fs
        .readFileSync(stderrLog, 'utf8')
        .split('\n')
        .filter(Boolean)
        .slice(0, 3)
        .join(' | ')
    } catch {
      /* ignore */
    }
    throw new Error(
      `浏览器没有暴露调试端口（exitCode=${child.exitCode}）。常见原因：运行环境禁止命名管道/子进程。${detail ? ` 浏览器输出: ${detail}` : ''}`,
    )
  }

  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()
  const browser = await connectCdp(version.webSocketDebuggerUrl)

  const downloads = []
  browser.on('Browser.downloadWillBegin', (params) => downloads.push({ event: 'begin', ...params }))
  browser.on('Browser.downloadProgress', (params) => downloads.push({ event: 'progress', ...params }))

  if (downloadDir) {
    await browser.send('Browser.setDownloadBehavior', {
      behavior: 'allow',
      downloadPath: downloadDir,
      eventsEnabled: true,
    })
  }

  const target = await (
    await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })
  ).json()
  const page = await connectCdp(target.webSocketDebuggerUrl)
  await page.send('Page.enable')
  await page.send('Runtime.enable')
  await page.send('Network.enable')
  const logs = []
  await page.send('Log.enable').catch(() => {})
  page.on('Log.entryAdded', (params) => {
    if (params?.entry) logs.push(`[${params.entry.level}] ${params.entry.text}`)
  })
  page.on('Runtime.consoleAPICalled', (params) => {
    const text = (params.args ?? []).map((arg) => arg.value ?? arg.description ?? '').join(' ')
    if (text) logs.push(`[console.${params.type}] ${text}`)
  })

  return {
    port,
    child,
    page,
    browser,
    userDataDir,
    downloads,
    logs,
    async close() {
      page.close()
      browser.close()
      await killTree(child.pid)
      await delay(150)
      fs.rmSync(userDataDir, { recursive: true, force: true })
    },
  }
}
