import assert from 'node:assert/strict'
import test from 'node:test'

import { UpstreamError, createSseDecoder, parseSseFrame, streamChatCompletion } from '../../server/upstream.js'

function streamOf(chunks) {
  const encoder = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
}

function sseResponse(chunks, init = {}) {
  return new Response(streamOf(chunks), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
    ...init,
  })
}

async function collect(iterable) {
  const parts = []
  for await (const part of iterable) parts.push(part)
  return parts
}

test('SSE decoder splits frames across chunk boundaries', () => {
  const decoder = createSseDecoder()
  const frames = [
    ...decoder.push('data: {"a":'),
    ...decoder.push('1}\n\ndata: {"b":2}\n\n'),
    ...decoder.push('data: [DO'),
    ...decoder.flush(),
  ]
  assert.deepEqual(frames.map((frame) => parseSseFrame(frame).data), ['{"a":1}', '{"b":2}', '[DO'])
})

test('parseSseFrame ignores comments and joins multi-line data', () => {
  const { event, data } = parseSseFrame(': keep-alive\nevent: progress\ndata: line1\ndata: line2')
  assert.equal(event, 'progress')
  assert.equal(data, 'line1\nline2')
})

test('streamChatCompletion yields streamed content and usage', async () => {
  const fetchImpl = async (_url, options) => {
    const body = JSON.parse(options.body)
    assert.equal(body.stream, true)
    assert.equal(body.model, 'deepseek-flash')
    assert.equal(options.headers.Authorization, 'Bearer test-key')
    return sseResponse([
      'data: {"choices":[{"delta":{"content":"{\\"title\\":"}}]}\n\n',
      'data: {"choices":[{"delta":{"content":"\\"A\\"}"}}]}\n\n',
      'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"total_tokens":42}}\n\n',
      'data: [DONE]\n\n',
    ])
  }

  const parts = await collect(
    streamChatCompletion({
      baseUrl: 'https://example.test/v1/',
      apiKey: 'test-key',
      model: 'deepseek-flash',
      messages: [{ role: 'user', content: 'hi' }],
      fetchImpl,
    }),
  )
  const text = parts.map((part) => part.content).join('')
  assert.equal(text, '{"title":"A"}')
  assert.deepEqual(
    parts.filter((part) => part.usage).map((part) => part.usage.total_tokens),
    [42],
  )
})

test('streamChatCompletion surfaces upstream HTTP errors with status and message', async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ error: { message: 'Authentication Fails', code: 'invalid_request_error' } }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })

  await assert.rejects(
    () =>
      collect(
        streamChatCompletion({
          baseUrl: 'https://example.test/v1',
          apiKey: 'bad',
          model: 'm',
          messages: [],
          fetchImpl,
        }),
      ),
    (error) => {
      assert.ok(error instanceof UpstreamError)
      assert.equal(error.status, 401)
      assert.match(error.message, /Authentication Fails/)
      return true
    },
  )
})

test('a non-streaming 200 response is rejected instead of silently ignored', async () => {
  const fetchImpl = async () =>
    new Response(JSON.stringify({ error: { message: 'stream unsupported' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })

  await assert.rejects(
    () =>
      collect(
        streamChatCompletion({ baseUrl: 'https://x/v1', apiKey: 'k', model: 'm', messages: [], fetchImpl }),
      ),
    /stream unsupported/,
  )
})
