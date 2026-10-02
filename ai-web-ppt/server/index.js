// Server entry point (production-safe). `npm start` runs scripts/serve.mjs, which
// forks this file once per worker; running it directly starts a single process.
import process from 'node:process'

import { createApp } from './app.js'
import { ENV_FILE, configProblems, envFileFor, loadConfig, loadEnvFile } from './config.js'
import { createLogger } from './logger.js'

const env = loadEnvFile(envFileFor())
const config = loadConfig(process.env, { envFileLoaded: env.loaded })
const logger = createLogger({
  level: config.logLevel,
  file: config.logFile || '',
})

const problems = configProblems(config)
if (problems.length && !config.allowIncompleteConfig) {
  // Fail loudly instead of serving a half-working app on a strange machine.
  logger.error('配置不完整，服务未启动：')
  for (const problem of problems) logger.error(`  - ${problem}`)
  logger.error('修法：复制 .env.example 为 .env 并填写；或设置同名环境变量。')
  logger.error('（如只想先看界面：ALLOW_INCOMPLETE_CONFIG=1，此时生成功能不可用）')
  logger.close()
  process.exit(2)
}
if (problems.length) {
  logger.warn('配置不完整，但 ALLOW_INCOMPLETE_CONFIG=1：界面可用，生成功能会失败。')
  for (const problem of problems) logger.warn(`  - ${problem}`)
}

const app = createApp({ config, logger })
const server = app.listen(config.port, config.host, () => {
  const shown = config.host === '0.0.0.0' || config.host === '::' ? 'localhost' : config.host
  logger.info(`AI 网页 PPT 生成器已启动：http://${shown}:${config.port}`)
  logger.info(`  环境      ${config.nodeEnv}${config.isProduction ? '' : '（开发模式）'}`)
  logger.info(`  模型      ${config.model || '(未配置 MODEL_NAME)'}`)
  logger.info(`  接口      ${config.baseUrl}`)
  logger.info(`  .env      ${env.loaded ? env.file : `${ENV_FILE}（未找到）`}`)
  logger.info(`  API Key   ${config.hasApiKey ? '已配置 ✓' : '未配置 ✗'}`)
  logger.info(`  输出目录  ${config.outputDir}`)
  logger.info(`  自检页    http://${shown}:${config.port}/health`)
  logger.info(`  日志级别  ${config.logLevel}`)
})

server.on('error', (error) => {
  if (error.code === 'EADDRINUSE') {
    logger.error(
      `端口 ${config.port} 已被占用。改 .env 里的 PORT，或结束占用该端口的进程` +
        `（Windows: netstat -ano | findstr :${config.port}；macOS/Linux: lsof -i :${config.port}）`,
    )
  } else {
    logger.error('HTTP 服务启动失败：', error)
  }
  logger.close()
  process.exit(1)
})

let shuttingDown = false
function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true
  logger.info(`收到 ${signal}，正在停止（等待进行中的请求结束，最多 10 秒）…`)
  const force = setTimeout(() => {
    logger.warn('超时，强制退出。')
    logger.close()
    process.exit(0)
  }, 10_000)
  force.unref()
  server.close(() => {
    clearTimeout(force)
    logger.info('已停止。')
    logger.close()
    process.exit(0)
  })
}

process.on('SIGINT', () => shutdown('SIGINT'))
process.on('SIGTERM', () => shutdown('SIGTERM'))
process.on('unhandledRejection', (reason) => logger.error('未处理的 Promise 拒绝：', reason))
process.on('uncaughtException', (error) => {
  logger.error('未捕获异常：', error)
  shutdown('uncaughtException')
})

export { server, config }
