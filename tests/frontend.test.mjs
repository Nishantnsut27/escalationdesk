import test from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
import { readFileSync } from 'node:fs'
import { build } from 'esbuild'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createRequire } from 'node:module'

async function components(entry) {
  const bundle = await build({ entryPoints: [entry], bundle: true, write: false, format: 'cjs', platform: 'node', packages: 'external' })
  const output = { exports: {} }
  new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), output, output.exports)
  return output.exports
}

const reportFixture = { assessment: 'inconclusive', origin: 'payment', summary: 'The source is uncertain.', affected_services: ['order'], evidence_ids: ['source-a'],
  hypotheses: [{ claim: 'Processing may be delayed.', evidence_ids: ['source-b'] }], recommendations: ['Inspect linked spans.'], limitations: ['Deeper cause unknown.'] }
const investigationFixture = { id: 'saved', engine: 'groq', state: 'completed', created_at: '2026-11-02T00:00:05Z', completed_at: '2026-11-02T00:00:09Z', report: reportFixture, activity: [], usage: { requests: 2, total_tokens: 200 }, supplements: [] }

test('Jev triage stays advisory and separate from the assessed cause and Groq usage', async () => {
  const { ReportView } = await components('src/App.tsx')
  const triage = { requested: true, state: 'completed', category: 'resource_exhaustion', confidence: .6,
    probabilities: { resource_exhaustion: .7, latency_timeout: .3 }, requests: 1,
    returned_model: 'typesafe/jev-1.14', actual_usage: { input_tokens: 123, output_tokens: 45 },
    groups: [{ id: 'g0', source_ids: ['source-a'], score: 1.5 }] }
  const investigation = { ...investigationFixture, usage: { ...investigationFixture.usage, triage } }
  const html = renderToStaticMarkup(createElement(ReportView, { investigation, onOpen() {} }))
  assert.match(html, /Possible cause — limited evidence/)
  assert.match(html, /Initial triage · Resource exhaustion/)
  assert.match(html, /not a root-cause finding/)
  assert.match(html, /Groq can disagree/)
  assert.match(html, /Input tokens: 123/)
  assert.match(html, /Original usage: 2 HTTP attempts/)
  assert.match(html, /Priority 1.50 \/ 2/)
  assert.doesNotMatch(html, /Initial triage[^>]*open=/)
})

test('triage pending, fallback, unknown and missing usage have truthful display', async () => {
  const { TriagePanel, WorkflowLabel } = await components('src/triage.tsx')
  for (const state of ['pending', 'running', 'fallback', 'interrupted']) {
    const html = renderToStaticMarkup(createElement(TriagePanel, { triage: { requested: true, state, reason: 'Provider unavailable' }, onOpen() {} }))
    assert.match(html, state === 'fallback' ? /Groq-only fallback/ : /Initial triage/)
    assert.doesNotMatch(html, /Assessed cause/)
  }
  const html = renderToStaticMarkup(createElement(TriagePanel, { triage: { requested: true, state: 'completed', category: 'no_clear_failure', probabilities: {}, groups: [] }, onOpen() {} }))
  assert.match(html, /does not establish application health/)
  assert.match(html, /Input tokens: not returned/)
  assert.match(renderToStaticMarkup(createElement(WorkflowLabel, { providers: { groq: false, jev: true } })), /Full investigation unavailable/)
  assert.match(renderToStaticMarkup(createElement(WorkflowLabel, { providers: { groq: true, jev: true } })), /Jev triage → Groq investigation/)
})

test('source previews show actual observations, missing timestamps and unavailable sources', async () => {
  const { SourcePreviews } = await components('src/triage.tsx')
  const scope = { evidence: [{ id: 'a', service: 'catalog', kind: 'log', message: 'Actual TypeError', timestamp: null }] }
  const html = renderToStaticMarkup(createElement(SourcePreviews, { ids: ['a', 'a', 'missing'], scope, onOpen() {} }))
  assert.match(html, /Actual TypeError/)
  assert.match(html, /Timestamp not recorded/)
  assert.match(html, /Saved source unavailable/)
  assert.equal((html.match(/Open evidence a/g) || []).length, 1)
})

test('saved reports expose individual hypotheses, neutral assessment, timeline and explicit further analysis', async () => {
  const { ReportView } = await components('src/App.tsx')
  const html = renderToStaticMarkup(createElement(ReportView, { investigation: investigationFixture, onOpen() {} }))
  assert.match(html, /Possible cause — limited evidence/)
  assert.doesNotMatch(html, /failure originates/)
  assert.match(html, /Processing may be delayed/)
  assert.match(html, /Open evidence source-b/)
  assert.match(html, /Evidence timeline/)
  assert.match(html, /Analyze further/)
  assert.match(html, /Original usage: 2 HTTP attempts/)
  assert.match(html, /2026-11-02T00:00:09Z/)
  const rules = renderToStaticMarkup(createElement(ReportView, { investigation: { ...investigationFixture, engine: 'rules' }, onOpen() {} }))
  assert.match(rules, /Reported hypotheses/)
  assert.doesNotMatch(rules, /Analyze further/)
})

test('timeline deduplicates by ID, orders span starts and reveals missing relationships and legacy coverage', async () => {
  const { orderedEvidence, EvidenceTimeline, traceCoverage } = await components('src/reports.tsx')
  const late = { id: 'late', kind: 'span', service: 'payment', name: 'payment.process', duration_ms: 4000, status: 'ok', timestamp: '2026-11-02T00:00:04Z', started_at: '2026-11-02T00:00:00Z', trace_id: 'trace', span_id: 'work', parent_id: 'missing' }
  const log = { id: 'timeout', kind: 'log', service: 'order', message: 'Timed out', timestamp: '2026-11-02T00:00:02Z' }
  assert.deepEqual(orderedEvidence([log, late, late]).map(row => row.id), ['late', 'timeout'])
  assert.match(traceCoverage(late, [late, log]), /Parent span is not/)
  const scope = { investigation_id: 'saved', evidence: [log, late, late], source_ids: ['late', 'timeout', 'unavailable'], unavailable_source_ids: ['unavailable'], coverage: 'legacy_citations_only', note: 'Legacy report: only cited sources.' }
  const html = renderToStaticMarkup(createElement(EvidenceTimeline, { scope, onOpen() {} }))
  assert.match(html, /Legacy report/)
  assert.match(html, /saved sources unavailable/)
  assert.match(html, /Duration 4000.00 ms/)
  assert.match(html, /Completed/)
  assert.match(html, /Chronology alone does not establish causation/)
  assert.match(html, /Trace or span reference missing/)
  assert.equal((html.match(/payment.process/g) || []).length, 1)
  assert.ok(html.indexOf('payment.process') < html.indexOf('Timed out'))
})

test('service map preserves Payment origin despite late successful work and current recovery', async () => {
  const { ServiceMap } = await components('src/App.tsx')
  const html = renderToStaticMarkup(createElement(ServiceMap, { report: { ...reportFixture, assessment: 'supported' }, overview: { services: ['payment', 'order', 'gateway'].map(name => ({ name, status: 'healthy' })) }, evidence: [{ id: 'late', service: 'payment', kind: 'span', status: 'ok', duration_ms: 4000 }] }))
  assert.match(html, /Reported origin: yes/)
  assert.match(html, /Reported affected: not listed/)
  assert.match(html, /Observed errors: 0 in this evidence scope/)
  assert.match(html, /Current reachability: reachable/)
  assert.doesNotMatch(html, /unaffected/)
})

test('all supplement states leave original report readable and display disagreement and separate usage', async () => {
  const { ReportView } = await components('src/App.tsx')
  for (const state of ['queued', 'running', 'quota_waiting', 'completed', 'failed', 'interrupted']) {
    const supplement = { id: 'supplement', investigation_id: 'saved', state, created_at: '2026-11-02T00:01:00Z', source_ids: ['source-a'], error: 'Retryable test failure.',
      usage: { requests: 1, total_tokens: 50, retry_at: new Date(Date.now() + 15000).toISOString() },
      result: state === 'completed' ? { relationship: 'disagrees', summary: 'The original conclusion is unsupported.', explanations: [{ claim: 'Delay remains unresolved.', status: 'unresolved', supporting_citations: [], conflicting_citations: ['source-a'] }], questions: [], limitations: ['No new evidence.'] } : null }
    const html = renderToStaticMarkup(createElement(ReportView, { investigation: { ...investigationFixture, supplements: [supplement] }, onOpen() {} }))
    assert.match(html, /The source is uncertain/)
    assert.match(html, /Supplement usage: 1\/2 HTTP attempts/)
    assert.match(html, /Original usage: 2 HTTP attempts/)
    assert.doesNotMatch(html, />Analyze further</)
    if (state === 'completed') { assert.match(html, /Disagrees with the original conclusion/); assert.match(html, /disagreement/); assert.match(html, /Conflicting sources/) }
    if (state === 'quota_waiting') assert.match(html, /Resuming in \d+s/)
    if (['failed', 'interrupted'].includes(state)) assert.match(html, /Retry further analysis/)
  }
})

test('observation inspection has readable measured fields and source punctuation is clean', async () => {
  const { ObservationFields } = await components('src/reports.tsx')
  const html = renderToStaticMarkup(createElement(ObservationFields, { row: { id: 'x', kind: 'span', service: 'payment', timestamp: '2026-11-02T00:00:04Z', duration_ms: 4000, timeout_ms: 2000, parent_id: 'caller' } }))
  assert.match(html, /Duration \(ms\)/)
  assert.match(html, /Explicit timeout \(ms\)/)
  assert.match(html, /Parent span ID/)
  for (const file of ['src/App.tsx', 'src/reports.tsx']) assert.doesNotMatch(readFileSync(file, 'utf8'), /\uFFFD|\u00c2\u00b7|\u00e2\u20ac/)
})

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

test('import form and coverage make explicit analysis and missing context clear', async () => {
  const { ImportForm, ImportCoverage } = await components('src/imports.tsx')
  const form = renderToStaticMarkup(createElement(ImportForm, { onImported() {}, onError() {} }))
  assert.match(form, /Save logs/)
  assert.match(form, /Importing does not start analysis/)
  assert.match(form, /\.log,\.txt,\.jsonl/)
  assert.match(form, /Default service/)
  assert.match(form, /1 MiB/)
  const collection = { id: 'collection', title: 'Application logs', created_at: '2026-11-03T00:00:00Z', source_type: 'paste', filename: null, symptom: '500 errors',
    summary: { observations: 3, input_lines: 5, duplicates: 1, conflicting_ids: 1, malformed_records: 1, redactions: 2, missing_timestamps: 1, missing_services: 1,
      invalid_fields: 0, kinds: ['log'], services: ['billing'], coverage: 'Only supplied text.', redaction_note: 'Review saved content.' } }
  const coverage = renderToStaticMarkup(createElement(ImportCoverage, { collection }))
  assert.match(coverage, /not a telemetry timestamp/)
  assert.match(coverage, /Conflicting IDs retained/)
  assert.match(coverage, /Malformed records kept as text/)
  assert.match(coverage, /Missing\/invalid timestamps/)
  assert.match(coverage, /Metrics and traces are unavailable unless supplied/)
})

test('imported service maps and timelines do not invent topology, reachability or timestamps', async () => {
  const { ServiceMap } = await components('src/App.tsx')
  const html = renderToStaticMarkup(createElement(ServiceMap, { overview: null, services: ['billing', 'catalog'], evidence: [] }))
  assert.match(html, /billing/)
  assert.match(html, /not measured for imported logs/)
  assert.doesNotMatch(html, /gateway|payment|class="connector"/)
  const { orderedEvidence, FullTime } = await components('src/reports.tsx')
  const rows = [{ id: 'untimed-last', kind: 'log', timestamp: null, source_line: 6 },
    { id: 'dated', kind: 'log', timestamp: '2026-11-03T00:00:00Z', source_line: 5 },
    { id: 'untimed-first', kind: 'log', timestamp: null, source_line: 2 }]
  assert.deepEqual(orderedEvidence(rows).map(row => row.id), ['dated', 'untimed-first', 'untimed-last'])
  assert.match(renderToStaticMarkup(createElement(FullTime, { value: null })), /Timestamp not recorded/)
})

