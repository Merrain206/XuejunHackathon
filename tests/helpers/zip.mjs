// Minimal ZIP reader (stored/deflated entries) so tests can inspect the XML
// inside a .pptx without adding a dependency.
import zlib from 'node:zlib'

function findEndOfCentralDirectory(buffer) {
  const minimum = Math.max(0, buffer.length - 66_000)
  for (let i = buffer.length - 22; i >= minimum; i -= 1) {
    if (buffer.readUInt32LE(i) === 0x06054b50) return i
  }
  return -1
}

/** @returns {Record<string, Buffer>} entry name -> decompressed bytes */
export function readZipEntries(buffer) {
  const eocd = findEndOfCentralDirectory(buffer)
  if (eocd === -1) throw new Error('不是有效的 zip：找不到中央目录')
  const entryCount = buffer.readUInt16LE(eocd + 10)
  let offset = buffer.readUInt32LE(eocd + 16)
  const entries = {}

  for (let index = 0; index < entryCount; index += 1) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error('中央目录项损坏')
    const method = buffer.readUInt16LE(offset + 10)
    const compressedSize = buffer.readUInt32LE(offset + 20)
    const nameLength = buffer.readUInt16LE(offset + 28)
    const extraLength = buffer.readUInt16LE(offset + 30)
    const commentLength = buffer.readUInt16LE(offset + 32)
    const localOffset = buffer.readUInt32LE(offset + 42)
    const name = buffer.subarray(offset + 46, offset + 46 + nameLength).toString('utf8')
    offset += 46 + nameLength + extraLength + commentLength

    if (name.endsWith('/')) continue
    const localNameLength = buffer.readUInt16LE(localOffset + 26)
    const localExtraLength = buffer.readUInt16LE(localOffset + 28)
    const dataStart = localOffset + 30 + localNameLength + localExtraLength
    const raw = buffer.subarray(dataStart, dataStart + compressedSize)
    entries[name] = method === 0 ? Buffer.from(raw) : zlib.inflateRawSync(raw)
  }
  return entries
}

export function zipEntryText(buffer, name) {
  const entries = readZipEntries(buffer)
  const entry = entries[name]
  return entry ? entry.toString('utf8') : ''
}
