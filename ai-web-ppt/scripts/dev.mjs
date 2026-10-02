// Starts the API server and the Vite dev server together: `npm run dev`.
// Deliberately dependency-free so the app has no orchestration toolchain.
import { spawn } from 'node:child_process'
import process from 'node:process'

const isWindows = process.platform === 'win32'
const npmCmd = isWindows ? 'npm.cmd' : 'npm'

const children = []
let shuttingDown = false

function run(label, args, color) {
  const options = { stdio: ['ignore', 'pipe', 'pipe'], shell: isWindows }
  let child
  try {
    child = spawn(npmCmd, args, options)
  } catch {
    // Some sandboxes forbid spawning with piped stdio (EPERM). Fall back to
    // inheriting our own stdio: we lose the [api]/[web] prefixes, nothing else.
    child = spawn(npmCmd, args, { stdio: 'inherit', shell: isWindows })
  }
  children.push(child)
  const tag = `\u001b[${color}m[${label}]\u001b[0m `
  const pipe = (stream, out) => {
    if (!stream) return
    stream.setEncoding('utf8')
    let buffer = ''
    stream.on('data', (chunk) => {
      buffer += chunk
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) out.write(tag + line + '\n')
    })
  }
  pipe(child.stdout, process.stdout)
  pipe(child.stderr, process.stderr)
  child.on('exit', (code) => {
    if (!shuttingDown) {
      process.stdout.write(`${tag}exited with code ${code} —— 正在停止另一个进程\n`)
      shutdown(code ?? 0)
    }
  })
  return child
}

function shutdown(code = 0) {
  if (shuttingDown) return
  shuttingDown = true
  for (const child of children) {
    try {
      child.kill()
    } catch {
      /* ignore */
    }
  }
  setTimeout(() => process.exit(code), 150)
}

process.on('SIGINT', () => shutdown(0))
process.on('SIGTERM', () => shutdown(0))

run('api', ['run', 'dev:api'], '36')
run('web', ['run', 'dev:web'], '35')
