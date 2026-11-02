import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
import { readFileSync } from 'node:fs'

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

