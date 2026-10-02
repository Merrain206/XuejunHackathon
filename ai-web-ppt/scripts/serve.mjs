#!/usr/bin/env node
// Production launcher: runs the API server with a small process pool and restarts
// a worker that dies. `npm start` uses this; `npm run start:single` runs one process
// directly (useful when a supervisor such as systemd/pm2/Docker owns the lifecycle).
//
//   WORKERS=0            auto: min(4, cpus - 1)
//   WORKERS=1            single worker (no cluster)
//   WORKERS=8            pin the pool size
import cluster from 'node:cluster'
import os from 'node:os'
import process from 'node:process'

import { ROOT, assertConfig, envFileFor, loadConfig, loadEnvFile } from '../server/config.js'
import { createLogger } from '../server/logger.js'

const env = loadEnvFile(envFileFor())
const config = loadConfig(process.env, { envFileLoaded: env.loaded })

const logger = createLogger({ level: config.logLevel, file: config.logFile || '' })

// Fail fast: a half-configured deployment must not come up at all.
try {
  assertConfig(config)
} catch (error) {
  for (const line of String(error.message).split('\n')) logger.error(line)
  logger.error(`提示：也可以先运行 node scripts/check-env.mjs 看完整自检。`)
  logger.error(`项目目录：${ROOT}`)
  logger.close()
  process.exit(2)
}

function resolveWorkers() {
  if (config.workers === 1) return 1
  if (config.workers > 1) return config.workers
  const cpus = typeof os.availableParallelism === 'function' ? os.availableParallelism() : os.cpus().length
  // The work here is I/O bound (one upstream SSE stream per request): a few
  // workers is plenty, and leaving one core free keeps the box responsive.
  return Math.max(1, Math.min(4, cpus - 1))
}

const workers = resolveWorkers()

if (workers === 1) {
  logger.info('以单进程模式启动（WORKERS=1）。')
  await import('../server/index.js')
} else {
  logger.info(`以 ${workers} 个 worker 启动（可用 WORKERS 覆盖）。`)

  let shuttingDown = false
  const restarts = new Map()

  function fork() {
    const worker = cluster.fork()
    const startedAt = Date.now()
    worker.on('exit', (code, signal) => {
      if (shuttingDown) return
      const alive = Date.now() - startedAt
      const count = (restarts.get(worker.id) || 0) + 1
      restarts.set(worker.id, count)
      // Crash loop guard: back off when a worker dies immediately and repeatedly.
      const delay = alive < 5_000 ? Math.min(30_000, 1_000 * count) : 250
      logger.warn(`worker ${worker.process.pid} 退出（code=${code} signal=${signal}），${delay}ms 后重启。`)
      setTimeout(fork, delay).unref()
    })
  }

  for (let index = 0; index < workers; index += 1) fork()

  const shutdown = (signal) => {
    if (shuttingDown) return
    shuttingDown = true
    logger.info(`收到 ${signal}，正在停止 ${Object.keys(cluster.workers || {}).length} 个 worker…`)
    for (const worker of Object.values(cluster.workers || {})) {
      try {
        worker.process.kill('SIGTERM')
      } catch {
        /* ignore */
      }
    }
    const force = setTimeout(() => {
      logger.warn('超时，强制退出。')
      logger.close()
      process.exit(0)
    }, 12_000)
    force.unref()
    cluster.on('exit', () => {
      if (Object.keys(cluster.workers || {}).length === 0) {
        clearTimeout(force)
        logger.info('全部 worker 已停止。')
        logger.close()
        process.exit(0)
      }
    })
  }

  process.on('SIGINT', () => shutdown('SIGINT'))
  process.on('SIGTERM', () => shutdown('SIGTERM'))
}
