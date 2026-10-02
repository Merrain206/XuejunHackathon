import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'

import { FALLBACKS, configProblems, loadConfig, loadEnvFile, parseEnv } from '../../server/config.js'

test('parseEnv handles comments, quotes, blank lines and export prefixes', () => {
  const parsed = parseEnv(
    ['# comment', '', 'A=1', 'B = "two"', "C='three'", 'export D=4', 'NOEQUALS', 'E='].join('\n'),
  )
  assert.deepEqual(parsed, { A: '1', B: 'two', C: 'three', D: '4', E: '' })
})

test('the model name is never hardcoded: missing MODEL_NAME is reported', () => {
  const config = loadConfig({ DEEPSEEK_API_KEY: 'k' })
  assert.equal(config.model, '')
  const problems = configProblems(config)
  assert.equal(problems.length, 1)
  assert.match(problems[0], /MODEL_NAME/)
})

test('missing API key is reported, and never echoed', () => {
  const config = loadConfig({ MODEL_NAME: 'deepseek-flash' })
  assert.equal(config.hasApiKey, false)
  assert.match(configProblems(config).join(' '), /DEEPSEEK_API_KEY/)
})

test('loadConfig reads base url, port and caps from the environment', () => {
  const config = loadConfig({
    DEEPSEEK_API_KEY: 'secret-value',
    MODEL_NAME: 'deepseek-flash',
    BASE_URL: 'https://api.deepseek.com/v1/',
    PORT: '9001',
    MAX_SLIDES: '7',
    REQUEST_TIMEOUT_MS: '5000',
  })
  assert.equal(config.baseUrl, 'https://api.deepseek.com/v1', 'trailing slash trimmed')
  assert.equal(config.port, 9001)
  assert.equal(config.maxSlides, 7)
  assert.equal(config.requestTimeoutMs, 5000)
  assert.equal(configProblems(config).length, 0)
})

test('defaults are used when optional values are absent', () => {
  const config = loadConfig({ DEEPSEEK_API_KEY: 'k', MODEL_NAME: 'm', PORT: 'not-a-number' })
  assert.equal(config.baseUrl, FALLBACKS.BASE_URL)
  assert.equal(config.port, Number(FALLBACKS.PORT))
  assert.equal(config.maxSlides, Number(FALLBACKS.MAX_SLIDES))
})

test('loadEnvFile fills missing variables but never overrides the real environment', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ppt-env-'))
  const file = path.join(dir, '.env')
  fs.writeFileSync(file, 'DEEPSEEK_API_KEY=from-file\nMODEL_NAME=model-from-file\n', 'utf8')
  const env = { MODEL_NAME: 'from-real-env' }
  const result = loadEnvFile(file, env)
  assert.equal(result.loaded, true)
  assert.equal(env.DEEPSEEK_API_KEY, 'from-file')
  assert.equal(env.MODEL_NAME, 'from-real-env')
  fs.rmSync(dir, { recursive: true, force: true })
})

test('loadEnvFile reports a missing file instead of throwing', () => {
  const result = loadEnvFile(path.join(os.tmpdir(), 'definitely-missing-ppt-env-file'))
  assert.equal(result.loaded, false)
  assert.equal(result.reason, 'not-found')
})
