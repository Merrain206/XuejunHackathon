// Level-based logger. Replaces bare console.log so LOG_LEVEL works and so a
// production run can keep a rotating log file next to its output directory.
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const LEVELS = { error: 0, warn: 1, info: 2, debug: 3 }

export function normalizeLevel(value, fallback = 'info') {
  const level = String(value || '').trim().toLowerCase()
  return level in LEVELS ? level : fallback
}

function format(value) {
  if (value instanceof Error) return value.stack || value.message
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/**
 * @param {{level?: string, file?: string, maxBytes?: number}} options
 */
export function createLogger({ level = 'info', file = '', maxBytes = 5 * 1024 * 1024 } = {}) {
  const threshold = LEVELS[normalizeLevel(level)] ?? LEVELS.info
  let stream = null

  if (file) {
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true })
      // One-step rotation: the previous log is kept as <file>.1
      if (fs.existsSync(file) && fs.statSync(file).size > maxBytes) {
        fs.renameSync(file, `${file}.1`)
      }
      stream = fs.createWriteStream(file, { flags: 'a' })
      stream.on('error', () => {
        stream = null // never let logging take the server down
      })
    } catch {
      stream = null
    }
  }

  const emit = (name, args) => {
    if (LEVELS[name] > threshold) return
    const line = `${new Date().toISOString()} ${name.toUpperCase().padEnd(5)} ${args.map(format).join(' ')}`
    if (name === 'error' || name === 'warn') process.stderr.write(`${line}\n`)
    else process.stdout.write(`${line}\n`)
    if (stream) stream.write(`${line}\n`)
  }

  const logger = {
    level: normalizeLevel(level),
    error: (...args) => emit('error', args),
    warn: (...args) => emit('warn', args),
    info: (...args) => emit('info', args),
    debug: (...args) => emit('debug', args),
    close: () => {
      try {
        stream?.end()
      } catch {
        /* ignore */
      }
    },
  }
  // `log` is kept as an alias because some call sites use logger.log
  logger.log = logger.info
  return logger
}

/** A message that is safe to show a browser: never a stack trace. */
export function publicErrorMessage(error, fallback = '服务器内部错误，请查看服务端日志。') {
  if (!error) return fallback
  const message = typeof error === 'string' ? error : error.message
  if (!message) return fallback
  // Strip anything that looks like a filesystem path or a stack frame.
  const cleaned = String(message)
    .split('\n')[0]
    .replace(/([A-Za-z]:\\[^\s]+|\/(?:home|Users|root)\/[^\s]+)/g, '<path>')
    .slice(0, 300)
  return cleaned || fallback
}
