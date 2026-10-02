// Pluggable web search. The citation switch needs REAL sources, so this module is
// the only place URLs can enter a deck: whatever it returns is what the model is
// allowed to cite. Unconfigured or failing providers degrade to "no results"
// rather than breaking generation.
const DEFAULT_URLS = {
  tavily: 'https://api.tavily.com/search',
  serper: 'https://google.serper.dev/search',
  duckduckgo: 'https://html.duckduckgo.com/html/',
}

const TIMEOUT_MS = 9000

function decodeEntities(text) {
  return String(text || '')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_match, code) => String.fromCharCode(Number(code)))
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function hostOf(url) {
  try {
    return new URL(url).host
  } catch {
    return ''
  }
}

function isEnabled(config) {
  if (config.searchProvider === 'none') return false
  if (config.searchProvider === 'duckduckgo') return true
  if (config.searchProvider === 'custom') return Boolean(config.searchApiUrl)
  return Boolean(config.searchApiKey)
}

async function searchTavily(config, query, fetchImpl, signal) {
  const response = await fetchImpl(config.searchApiUrl || DEFAULT_URLS.tavily, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      api_key: config.searchApiKey,
      query,
      max_results: config.searchMaxResults,
      search_depth: 'basic',
      include_answer: false,
    }),
    signal,
  })
  if (!response.ok) throw new Error(`tavily HTTP ${response.status}`)
  const payload = await response.json()
  return (payload.results ?? []).map((item) => ({
    title: item.title,
    url: item.url,
    snippet: item.content ?? item.snippet ?? '',
  }))
}

async function searchSerper(config, query, fetchImpl, signal) {
  const response = await fetchImpl(config.searchApiUrl || DEFAULT_URLS.serper, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-API-KEY': config.searchApiKey },
    body: JSON.stringify({ q: query, num: config.searchMaxResults }),
    signal,
  })
  if (!response.ok) throw new Error(`serper HTTP ${response.status}`)
  const payload = await response.json()
  return (payload.organic ?? []).map((item) => ({
    title: item.title,
    url: item.link,
    snippet: item.snippet ?? '',
  }))
}

async function searchDuckDuckGo(config, query, fetchImpl, signal) {
  const url = `${config.searchApiUrl || DEFAULT_URLS.duckduckgo}?q=${encodeURIComponent(query)}`
  const response = await fetchImpl(url, {
    headers: {
      'User-Agent':
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
      'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    },
    signal,
  })
  if (!response.ok) throw new Error(`duckduckgo HTTP ${response.status}`)
  const html = await response.text()
  const results = []
  const linkRe = /<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi
  const snippetRe = /class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)<\/a>/gi
  const snippets = [...html.matchAll(snippetRe)].map((match) => decodeEntities(match[1]))
  let match
  let index = 0
  while ((match = linkRe.exec(html)) !== null && results.length < config.searchMaxResults) {
    let href = match[1]
    const redirect = /uddg=([^&]+)/.exec(href)
    if (redirect) href = decodeURIComponent(redirect[1])
    if (!/^https?:\/\//i.test(href)) {
      index += 1
      continue
    }
    results.push({ title: decodeEntities(match[2]), url: href, snippet: snippets[index] ?? '' })
    index += 1
  }
  return results
}

async function searchCustom(config, query, fetchImpl, signal) {
  const response = await fetchImpl(config.searchApiUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(config.searchApiKey ? { Authorization: `Bearer ${config.searchApiKey}` } : {}),
    },
    body: JSON.stringify({ query, q: query, max_results: config.searchMaxResults }),
    signal,
  })
  if (!response.ok) throw new Error(`custom search HTTP ${response.status}`)
  const payload = await response.json()
  const list = Array.isArray(payload) ? payload : (payload.results ?? payload.data ?? payload.items ?? [])
  return list.map((item) => ({
    title: item.title ?? item.name ?? '',
    url: item.url ?? item.link ?? '',
    snippet: item.snippet ?? item.content ?? item.description ?? '',
  }))
}

const PROVIDERS = {
  tavily: searchTavily,
  serper: searchSerper,
  duckduckgo: searchDuckDuckGo,
  custom: searchCustom,
}

export function createSearchClient(config, { fetchImpl = globalThis.fetch, logger = console } = {}) {
  const enabled = isEnabled(config)
  return {
    enabled,
    provider: config.searchProvider,
    /**
     * @param {string[]} queries
     * @returns {Promise<Array<{title:string,url:string,snippet:string,publisher:string,query:string}>>}
     */
    async search(queries = []) {
      if (!enabled || !queries.length) return []
      const provider = PROVIDERS[config.searchProvider]
      if (!provider) return []
      const signal = AbortSignal.timeout(TIMEOUT_MS)
      const batches = await Promise.all(
        queries.map(async (query) => {
          try {
            const raw = await provider(config, query, fetchImpl, signal)
            return raw.map((item) => ({ ...item, query }))
          } catch (error) {
            logger.warn?.(`[search] ${config.searchProvider} 检索失败（${query}）：${error?.message || error}`)
            return []
          }
        }),
      )
      const seen = new Set()
      const merged = []
      for (const item of batches.flat()) {
        const url = String(item.url || '').trim()
        if (!/^https?:\/\//i.test(url) || seen.has(url)) continue
        const title = String(item.title || '').trim()
        if (!title) continue
        seen.add(url)
        merged.push({
          title: title.slice(0, 110),
          url: url.slice(0, 300),
          snippet: String(item.snippet || '').replace(/\s+/g, ' ').trim().slice(0, 320),
          publisher: hostOf(url),
          query: item.query,
        })
        if (merged.length >= config.searchMaxResults) break
      }
      return merged
    },
  }
}
