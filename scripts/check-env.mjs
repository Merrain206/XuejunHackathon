#!/usr/bin/env node
// Environment self-check. Prints what is installed, what is configured and what is
// missing, with the exact command to fix each problem. Exit code 1 = not runnable.
//
//   node scripts/check-env.mjs            (human readable)
//   node scripts/check-env.mjs --json     (machine readable)
//   node scripts/check-env.mjs --no-port  (skip the port probe, e.g. while serving)
import process from 'node:process'

import { ENV_FILE, ROOT, configProblems, envFileFor, loadConfig, loadEnvFile } from '../server/config.js'
import { FAIL, PASS, WARN, runSelfCheck } from '../server/selfcheck.js'

const args = new Set(process.argv.slice(2))
const asJson = args.has('--json')
const checkPort = !args.has('--no-port')

const env = loadEnvFile(envFileFor())
const config = loadConfig(process.env, { envFileLoaded: env.loaded })

const report = await runSelfCheck({
  config,
  root: ROOT,
  envFileLoaded: env.loaded,
  envFile: env.loaded ? env.file : ENV_FILE,
  checkPort,
})

if (asJson) {
  process.stdout.write(`${JSON.stringify({ ...report, problems: configProblems(config) }, null, 2)}\n`)
  process.exit(report.ok ? 0 : 1)
}

const ICON = { [PASS]: '  ✓', [WARN]: '  !', [FAIL]: '  ✕' }
const color = process.stdout.isTTY
  ? { pass: '\u001b[32m', warn: '\u001b[33m', fail: '\u001b[31m', dim: '\u001b[2m', reset: '\u001b[0m' }
  : { pass: '', warn: '', fail: '', dim: '', reset: '' }

console.log('')
console.log('  环境自检 · AI 网页 PPT 生成器')
console.log(`  运行环境 ${report.platform} · Node v${report.node}`)
console.log('')
for (const item of report.checks) {
  const paint = color[item.status] ?? ''
  console.log(`${paint}${ICON[item.status]} ${item.label}${color.reset}`)
  console.log(`${color.dim}      ${item.detail}${color.reset}`)
  if (item.fix && item.status !== PASS) console.log(`${color.warn}      修复：${item.fix}${color.reset}`)
}
console.log('')
if (report.ok) {
  console.log(`  ${color.pass}结论：可以启动。${color.reset}` + (report.warned ? `（${report.warned} 项提醒）` : ''))
  console.log('  下一步：npm run start:prod   然后浏览器打开 http://' + config.host + ':' + config.port)
  console.log('')
  process.exit(0)
}

console.log(`  ${color.fail}结论：还不能启动，请先修复上面 ${report.failed} 项标 ✕ 的问题。${color.reset}`)
console.log('')
process.exit(1)
