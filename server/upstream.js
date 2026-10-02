// OpenAI-compatible chat-completions client with streaming (SSE) support.

export class UpstreamError extends Error {
  constructor(message, { status = null, code = null, body = '' } = {}) {
    super(message)
    this.name = 'UpstreamError'
    this.status = status
    this.code = code
    this.body = body
  }
}

/** Incremental SSE frame splitter (frames are separated by a blank line). */
export function createSseDecoder() {
  let buffer = ''
  return {
    push(chunk) {
      buffer += chunk
      const frames = []
      for (;;) {
        const match = /\r?\n\r?\n/.exec(buffer)
        if (!match) break
        frames.push(buffer.slice(0, match.index))
        buffer = buffer.slice(match.index + match[0].length)
      }
      return frames
    },
    flush() {
      const rest = buffer
      buffer = ''
      return rest.trim() ? [rest] : []
    },
  }
}

/** `event:` / `data:` fields of one SSE frame. Comment lines are ignored. */
export function parseSseFrame(frame) {
  let event = 'message'
  const data = []
  for (const line of String(frame).split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'event') event = value
    else if (field === 'data') data.push(value)
  }
  return { event, data: data.join('\n') }
}

function extractError(payload) {
  try {
    const json = JSON.parse(payload)
    return {
      code: json?.error?.code ?? json?.code ?? null,
      message: json?.error?.message ?? json?.message ?? '',
    }
  } catch {
    return { code: null, message: String(payload || '').slice(0, 300) }
  }
}

/**
 * Calls `POST {baseUrl}/chat/completions` with `stream: true` and yields
 * `{ content, reasoning, usage }` parts as they arrive.
 */
export async function* streamChatCompletion({
  baseUrl,
  apiKey,
  model,
  messages,
  signal,
  fetchImpl = globalThis.fetch,
  temperature = 0.4,
}) {
  const url = `${String(baseUrl).replace(/\/+$/, '')}/chat/completions`
  const response = await fetchImpl(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      Authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({ model, messages, stream: true, temperature }),
    signal,
  })

  if (!response.ok) {
    let body = ''
    try {
      body = await response.text()
    } catch {
      /* ignore */
    }
    const { code, message } = extractError(body)
    throw new UpstreamError(message || `上游返回 HTTP ${response.status}`, {
      status: response.status,
      code,
      body: body.slice(0, 800),
    })
  }

  const contentType = response.headers?.get?.('content-type') ?? ''
  if (contentType.includes('application/json')) {
    // Some gateways answer a streaming request with a single JSON document.
    const body = await response.text()
    const { code, message } = extractError(body)
    throw new UpstreamError(message || '上游没有返回流式响应', { status: response.status, code, body: body.slice(0, 800) })
  }
  if (!response.body) throw new UpstreamError('上游响应没有可读的流', { status: response.status })

  const decoder = createSseDecoder()
  const textDecoder = new TextDecoder()
  const reader = response.body.getReader()

  const handle = function* (frames) {
    for (const frame of frames) {
      const { data } = parseSseFrame(frame)
      if (!data || data === '[DONE]') {
        if (data === '[DONE]') return true
        continue
      }
      let json
      try {
        json = JSON.parse(data)
      } catch {
        continue
      }
      const choice = json?.choices?.[0]
      const content = typeof choice?.delta?.content === 'string' ? choice.delta.content : ''
      const reasoning =
        typeof choice?.delta?.reasoning_content === 'string' ? choice.delta.reasoning_content : ''
      if (json?.usage) yield { content: '', reasoning: '', usage: json.usage }
      if (content || reasoning) yield { content, reasoning, usage: null }
    }
    return false
  }

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      const frames = decoder.push(textDecoder.decode(value, { stream: true }))
      if (yield* handle(frames)) return
    }
    const tail = textDecoder.decode()
    const frames = tail ? [...decoder.push(tail), ...decoder.flush()] : decoder.flush()
    yield* handle(frames)
  } finally {
    try {
      await reader.cancel()
    } catch {
      /* ignore */
    }
  }
}
