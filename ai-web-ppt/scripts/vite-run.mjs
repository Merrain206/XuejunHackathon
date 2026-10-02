// Vite launcher used by `npm run build` / `npm run dev:web`.
//
// Why this exists: on Windows Vite probes mapped network drives by running
// `net use` through child_process.exec. Hosts that forbid spawning processes with
// piped stdio (sandboxes, hardened CI) make that probe throw synchronously and
// abort the build with `spawn EPERM`. The probe only decides which realpath
// implementation Vite uses, so when it is unavailable we answer "no mapped
// drives" and continue. Everywhere else this is a transparent pass-through.
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const childProcess = require('node:child_process')
const originalExec = childProcess.exec

childProcess.exec = function execWithNetUseFallback(command, ...rest) {
  if (typeof command === 'string' && command.trim() === 'net use') {
    const callback = rest.find((value) => typeof value === 'function')
    try {
      return originalExec.call(this, command, ...rest)
    } catch {
      if (callback) queueMicrotask(() => callback(null, '', ''))
      return { on() {}, once() {}, kill() {} }
    }
  }
  return originalExec.call(this, command, ...rest)
}

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
// Resolved as a file path on purpose: vite's package.json does not export ./bin/vite.js.
const viteBin = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js')
if (!fs.existsSync(viteBin)) {
  console.error(`[vite-run] 找不到 Vite 可执行文件：${viteBin}\n请先执行 npm install。`)
  process.exit(1)
}
await import(pathToFileURL(viteBin).href)
