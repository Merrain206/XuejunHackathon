import assert from 'node:assert/strict'
import test from 'node:test'

import { loadConfig } from '../../server/config.js'
import { createSearchClient } from '../../server/search.js'

function configWith(env) {
  return loadConfig({ MODEL_NAME: 'm', DEEPSEEK_API_KEY: 'k', ...env })
}

const silent = { warn() {}, error() {}, log() {} }

test('provider "none" leaves search disabled and returns no results', async () => {
  const client = createSearchClient(configWith({ SEARCH_PROVIDER: 'none' }), { logger: silent })
  assert.equal(client.enabled, false)
  assert.deepEqual(await client.search(['任何关键词']), [])
})

test('tavily results are normalized, de-duplicated and capped', async () => {
  const client = createSearchClient(
    configWith({ SEARCH_PROVIDER: 'tavily', SEARCH_API_KEY: 'tv', SEARCH_MAX_RESULTS: '2' }),
    {
      logger: silent,
      fetchImpl: async (url, options) => {
        assert.match(String(url), /api\.tavily\.com/)
        assert.match(options.body, /"api_key":"tv"/)
        return new Response(
          JSON.stringify({
            results: [
              { title: 'A', url: 'https://a.example/1', content: 'snippet a' },
              { title: 'A duplicate', url: 'https://a.example/1', content: 'dup' },
              { title: 'B', url: 'https://b.example/2', content: 'snippet b' },
              { title: 'C', url: 'https://c.example/3', content: 'snippet c' },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      },
    },
  )
  const results = await client.search(['topic'])
  assert.equal(results.length, 2, 'capped at maxResults and de-duplicated')
  assert.deepEqual(results.map((item) => item.url), ['https://a.example/1', 'https://b.example/2'])
  assert.equal(results[0].publisher, 'a.example')
})

test('serper sends its API key header and maps organic results', async () => {
  const client = createSearchClient(configWith({ SEARCH_PROVIDER: 'serper', SEARCH_API_KEY: 'sr' }), {
    logger: silent,
    fetchImpl: async (url, options) => {
      assert.match(String(url), /google\.serper\.dev/)
      assert.equal(options.headers['X-API-KEY'], 'sr')
      return new Response(JSON.stringify({ organic: [{ title: 'T', link: 'https://t.example/x', snippet: 'S' }] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    },
  })
  const results = await client.search(['q'])
  assert.equal(results.length, 1)
  assert.equal(results[0].url, 'https://t.example/x')
})

test('custom provider accepts several payload shapes', async () => {
  for (const payload of [
    { results: [{ title: 'A', url: 'https://a.example/1' }] },
    { data: [{ name: 'A', link: 'https://a.example/1' }] },
    [{ title: 'A', url: 'https://a.example/1' }],
  ]) {
    const client = createSearchClient(
      configWith({ SEARCH_PROVIDER: 'custom', SEARCH_API_URL: 'https://search.internal/api' }),
      {
        logger: silent,
        fetchImpl: async () => new Response(JSON.stringify(payload), { status: 200 }),
      },
    )
    const results = await client.search(['q'])
    assert.equal(results.length, 1)
    assert.equal(results[0].url, 'https://a.example/1')
  }
})

test('duckduckgo HTML is parsed, including its redirect links', async () => {
  const html = `
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnews.example%2Fstory&amp;rut=abc">Story &amp; Analysis</a>
      <a class="result__snippet" href="x">A short <b>snippet</b> here</a>
    </div>`
  const client = createSearchClient(configWith({ SEARCH_PROVIDER: 'duckduckgo' }), {
    logger: silent,
    fetchImpl: async () => new Response(html, { status: 200, headers: { 'Content-Type': 'text/html' } }),
  })
  const results = await client.search(['q'])
  assert.equal(results.length, 1)
  assert.equal(results[0].url, 'https://news.example/story')
  assert.equal(results[0].title, 'Story & Analysis')
  assert.equal(results[0].snippet, 'A short snippet here')
})

test('a failing provider degrades to no results instead of breaking generation', async () => {
  const logs = []
  const client = createSearchClient(configWith({ SEARCH_PROVIDER: 'tavily', SEARCH_API_KEY: 'tv' }), {
    logger: { warn: (message) => logs.push(message) },
    fetchImpl: async () => {
      throw new Error('network down')
    },
  })
  assert.deepEqual(await client.search(['q']), [])
  assert.ok(logs.some((line) => /检索失败/.test(line)))

  const failing = createSearchClient(configWith({ SEARCH_PROVIDER: 'tavily', SEARCH_API_KEY: 'tv' }), {
    logger: silent,
    fetchImpl: async () => new Response('{"error":"rate limited"}', { status: 429 }),
  })
  assert.deepEqual(await failing.search(['q']), [])
})

test('results without a title or with a non-http url are discarded', async () => {
  const client = createSearchClient(configWith({ SEARCH_PROVIDER: 'custom', SEARCH_API_URL: 'https://s/api' }), {
    logger: silent,
    fetchImpl: async () =>
      new Response(
        JSON.stringify([
          { title: '', url: 'https://a.example/1' },
          { title: 'B', url: 'javascript:alert(1)' },
          { title: 'C', url: 'https://c.example/3' },
        ]),
        { status: 200 },
      ),
  })
  const results = await client.search(['q'])
  assert.deepEqual(results.map((item) => item.url), ['https://c.example/3'])
})
