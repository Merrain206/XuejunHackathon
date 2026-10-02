// Minimal SSE frame decoder for the browser (the server has its own copy).
export function createSseParser() {
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

export function parseFrame(frame) {
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
