// Save generated files (offline HTML / PPTX) from the browser.
export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export function downloadHtml(filename, html) {
  downloadBlob(filename, new Blob([html], { type: 'text/html;charset=utf-8' }))
}

export function safeFilename(title, extension = '.html') {
  const base = String(title || '')
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '')
    .replace(/\s+/g, '-')
    .slice(0, 60)
    .trim()
  return `${base || 'presentation'}${extension}`
}

/** Fetch an image through our own origin and turn it into a data URL. */
export async function inlineImage(url, { timeoutMs = 25000 } = {}) {
  const response = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const blob = await response.blob()
  if (!blob.type.startsWith('image/')) throw new Error(`不是图片：${blob.type}`)
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(blob)
  })
}

/**
 * Downloads a server-generated file through a real form POST.
 *
 * A Blob download triggered after an `await` loses the user-activation window and
 * browsers silently block it; submitting a form lets the browser handle the
 * response itself, which needs no gesture and also streams large files properly.
 */
export function submitDownloadForm(action, payload, { field = 'payload' } = {}) {
  let frame = document.getElementById('dsh-download-frame')
  if (!frame) {
    frame = document.createElement('iframe')
    frame.id = 'dsh-download-frame'
    frame.name = 'dsh-download-frame'
    frame.style.display = 'none'
    frame.setAttribute('aria-hidden', 'true')
    document.body.appendChild(frame)
  }
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = action
  form.target = frame.name
  form.style.display = 'none'
  const input = document.createElement('input')
  input.type = 'hidden'
  input.name = field
  input.value = JSON.stringify(payload)
  form.appendChild(input)
  document.body.appendChild(form)
  form.submit()
  form.remove()
}
