// Test helpers: boot the real Express app on an ephemeral port and read SSE.
import { once } from 'node:events'

import { createApp } from '../../server/app.js'
import { loadConfig } from '../../server/config.js'

export const TEST_ENV = {
  DEEPSEEK_API_KEY: 'test-key-not-a-real-secret',
  MODEL_NAME: 'deepseek-flash',
  MAX_SLIDES: '20',
}

export async function startApp({ env = {}, fetchImpl } = {}) {
  const config = loadConfig({ ...TEST_ENV, ...env })
  const app = createApp({
    config,
    fetchImpl,
    logger: { error() {}, log() {}, warn() {} },
  })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const { port } = server.address()
  return {
    server,
    port,
    config,
    baseUrl: `http://127.0.0.1:${port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

/** Parses an SSE response body into `[{ event, data }]`. */
export async function readSse(response) {
  const events = []
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    for (;;) {
      const match = /\r?\n\r?\n/.exec(buffer)
      if (!match) break
      const frame = buffer.slice(0, match.index)
      buffer = buffer.slice(match.index + match[0].length)
      let event = 'message'
      const data = []
      for (const line of frame.split(/\r?\n/)) {
        if (!line || line.startsWith(':')) continue
        const colon = line.indexOf(':')
        const field = colon === -1 ? line : line.slice(0, colon)
        let payload = colon === -1 ? '' : line.slice(colon + 1)
        if (payload.startsWith(' ')) payload = payload.slice(1)
        if (field === 'event') event = payload
        else if (field === 'data') data.push(payload)
      }
      const joined = data.join('\n')
      let parsed = null
      try {
        parsed = joined ? JSON.parse(joined) : null
      } catch {
        parsed = null
      }
      events.push({ event, data: parsed, raw: joined })
    }
  }
  return events
}

export async function postGenerate(baseUrl, payload) {
  const response = await fetch(`${baseUrl}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return response
}
