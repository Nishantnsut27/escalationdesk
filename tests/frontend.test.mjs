import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
import { readFileSync } from 'node:fs'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRequire } from 'node:module'

test('waiting and failed investigations retain activity, evidence and safe diagnostics', async () => {
  const bundle = await build({ entryPoints: ['src/App.tsx'], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external' })
  const output = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), output, output.exports)
  const investigation = { id: 'fixture', engine: 'groq', state: 'quota_waiting', created_at: new Date().toISOString(), report: null,
    activity: [{ tool: 'search_logs', timestamp: new Date().toISOString(), count: 1, evidence_ids: ['abc123source'] }],
    usage: { retry_at: new Date(Date.now() + 30000).toISOString(), wait_reason: 'Waiting for tokens', requests: 1,
      diagnostics: [{ attempt: 1, phase: 'tools', status: 429, input_bytes: 1234, estimated_input_tokens: 681 }] } }
  const render = value => renderToStaticMarkup(createElement(output.exports.ReportView, { investigation: value, onOpen() {} }))
  const waiting = render(investigation)
  assert.match(waiting, /Waiting for Groq quota/)
  assert.match(waiting, /Resuming in \d+s/)
  assert.match(waiting, /resumes automatically/)
  assert.match(waiting, /Open collected evidence ABC123/)
  assert.match(waiting, /Completed tool activity/)
  assert.match(waiting, /429/)
  const failed = render({ ...investigation, state: 'failed', error: 'Daily quota exhausted.' })
  assert.match(failed, /Daily quota exhausted/)
  assert.match(failed, /Recorded tool activity/)
  assert.match(failed, /Open collected evidence ABC123/)
  assert.doesNotMatch(failed, /Resuming in/)
  assert.match(failed, /Retry creates a new investigation/)
})

test('TypeScript UI has no compile diagnostics', () => {
  const config = ts.readConfigFile('tsconfig.json', ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, '.')
  const program = ts.createProgram(parsed.fileNames, parsed.options)
  const diagnostics = ts.getPreEmitDiagnostics(program)
  assert.equal(diagnostics.length, 0, ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: file => file, getCurrentDirectory: () => process.cwd(), getNewLine: () => '\n',
  }))
})
test('API client rejects non-success responses and sends CSRF on writes', async () => {
  const source = readFileSync('src/api.ts', 'utf8')
  const compiled = ts.transpile(source, { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 })
  const module = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))
  const originalFetch = globalThis.fetch
  let captured
  try {
    globalThis.fetch = async (path, options) => {
      captured = { path, options }
      return { ok: false, status: 403, json: async () => ({ error: 'Access denied' }) }
    }
    module.setCsrf('test-csrf')
    await assert.rejects(module.api('/scenarios/pool/start', {}), error => error.status === 403 && error.message === 'Access denied')
    assert.equal(captured.path, '/api/scenarios/pool/start')
    assert.equal(captured.options.headers['X-CSRF-Token'], 'test-csrf')
    assert.equal(captured.options.credentials, 'same-origin')
  } finally { globalThis.fetch = originalFetch }
})

