// Image acquisition for the browser.
//
// Pictures are always fetched through our own `/api/image` proxy: it runs the
// server-side provider chain (configured API → keyless CC0 search → Pollinations
// with a token) and keeps watermarked anonymous URLs and cross-origin surprises
// out of the page. When everything fails the renderer degrades to a CSS gradient
// decoration — silently.
function isHttpUrl(value) {
  return typeof value === 'string' && /^https?:\/\//i.test(value)
}

export function proxyImageUrl(prompt, { width = 1024, height = 720 } = {}) {
  const keywords = String(prompt || '').replace(/\s+/g, ' ').trim()
  if (!keywords) return ''
  return `/api/image?prompt=${encodeURIComponent(keywords)}&w=${width}&h=${height}`
}

export function imageCandidates(image, { width = 1024, height = 720 } = {}) {
  if (!image) return []
  const candidates = []
  if (isHttpUrl(image.url)) candidates.push(image.url)
  if (image.prompt) {
    const proxied = proxyImageUrl(image.prompt, { width, height })
    if (proxied) candidates.push(proxied)
  }
  return [...new Set(candidates)]
}
