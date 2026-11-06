import { useEffect, useRef, useState } from 'react'
import { api, type Evidence, type Investigation, type ReportEvidence, type Supplement } from './api'

export function FullTime({ value }: { value: string | null | undefined }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <span>Timestamp not recorded</span>
  return <time dateTime={value} title={new Date(value).toLocaleString()}>{value}</time>
}

export function Citations({ ids, label = 'Cited sources', onOpen }: { ids: string[]; label?: string; onOpen: (id: string) => void }) {
  return <div className="evidence-citations"><span>{label}</span>{[...new Set(ids)].map(id =>
    <button key={id} onClick={() => onOpen(id)} aria-label={'Open evidence ' + id}>{id.slice(0, 6).toUpperCase()} ↗</button>)}
    {!ids.length && <span>None cited</span>}</div>
}

export function useReportEvidence(investigation?: Investigation) {
  const [loaded, setLoaded] = useState<ReportEvidence | null>(null)
  const [error, setError] = useState<{ id: string; message: string } | null>(null)
  const id = investigation?.id
  const state = investigation?.state
  useEffect(() => {
    setLoaded(null); setError(null)
    if (!id) return
    let cancelled = false
    api<ReportEvidence>('/investigations/' + id + '/evidence').then(value => { if (!cancelled) setLoaded(value) })
      .catch(e => { if (!cancelled) setError({ id, message: e instanceof Error ? e.message : 'Unable to load saved evidence.' }) })
    return () => { cancelled = true }
  }, [id, state])
  return { scope: loaded?.investigation_id === id ? loaded : null, error: error && error.id === id ? error.message : '' }
}

export function orderedEvidence(rows: Evidence[]) {
  const stamp = (row: Evidence) => {
    const value = Date.parse((row.kind === 'span' && row.started_at ? row.started_at : row.timestamp) || '')
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY
  }
  return [...new Map(rows.map(row => [row.id, row])).values()].sort((a, b) =>
    stamp(a) - stamp(b) || (a.source_line ?? 0) - (b.source_line ?? 0) || a.id.localeCompare(b.id))
}

export function traceCoverage(row: Evidence, rows: Evidence[]) {
  if (row.kind === 'metric') return 'Metric sample; no trace relationship recorded.'
  if (!row.trace_id || !row.span_id) return 'Trace or span reference missing.'
  if (row.kind === 'log') return rows.some(r => r.kind === 'span' && r.trace_id === row.trace_id && r.span_id === row.span_id)
    ? 'Linked span present in this report’s evidence.' : 'Referenced span is not in this report’s evidence.'
  if (!row.parent_id) return 'No parent recorded; this observation alone does not establish an upstream relationship.'
  return rows.some(r => r.kind === 'span' && r.trace_id === row.trace_id && r.span_id === row.parent_id)
    ? 'Parent span present in this report’s evidence.' : 'Parent span is not in this report’s evidence.'
}

export function ObservationFields({ row }: { row: Evidence }) {
  const labels: Record<string, string> = { message: 'Message', name: 'Operation', level: 'Log level', status: 'Span outcome',
    started_at: 'Started at', duration_ms: 'Duration (ms)', http_status: 'HTTP response status', timeout_ms: 'Explicit timeout (ms)',
    downstream: 'Downstream service', pool_capacity: 'Pool capacity', pool_in_use: 'Connections in use',
    trace_id: 'Trace ID', span_id: 'Span ID', parent_id: 'Parent span ID', span_op: 'Span operation type',
    exception_type: 'Exception type', exception_handled: 'Exception handled',
    source_project: 'Sentry project (provenance)', source_project_id: 'Sentry project ID', source_dataset: 'Sentry dataset' }
  return <dl className="observation-fields">{Object.entries(row).filter(([key]) => !['id', 'kind', 'service', 'timestamp', 'run_id', 'collection_id', 'original'].includes(key)).map(([key, value]) =>
    <div key={key}><dt>{labels[key] || key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value ?? 'Not recorded')}</dd></div>)}</dl>
}

export function EvidenceTimeline({ scope, error, onOpen }: { scope: ReportEvidence | null; error?: string; onOpen: (id: string) => void }) {
  const rows = orderedEvidence(scope?.evidence || [])
  return <section className="evidence-timeline" aria-label="Evidence timeline"><h4>Evidence timeline</h4>
    <p className="report-note">Recorded telemetry for the selected report. Ordered by recorded span start, otherwise observation time. Untimed observations follow in source order. Chronology alone does not establish causation. Investigation activity is shown separately below.</p>
    {error ? <p role="alert">{error}</p> : !scope ? <p role="status">Loading saved report evidence…</p> : <>
      <p className="coverage-note">{scope.note} {rows.length} of {scope.source_ids.length} sources available.</p>
      {!!scope.unavailable_source_ids.length && <details className="missing-sources"><summary>{scope.unavailable_source_ids.length} saved sources unavailable</summary>{scope.unavailable_source_ids.map(id => <code key={id}>{id}</code>)}</details>}
      {!rows.length && <p>No saved observations available.</p>}
      <ol>{rows.map(row => <li key={row.id}><div className="timeline-heading"><span className="service-label">{row.service}</span><strong>{row.message || row.name || 'Metric sample'}</strong></div>
        <p>{row.kind === 'span' ? row.started_at ? 'Started ' : 'Start unavailable; completed ' : 'Observed '}<FullTime value={row.kind === 'span' && row.started_at ? row.started_at : row.timestamp} /></p>
        {row.kind === 'span' && <p>{row.duration_ms === undefined ? 'Duration unavailable' : `Duration ${row.duration_ms.toFixed(2)} ms`} · Completed <FullTime value={row.timestamp} /> · Outcome {row.status || 'not recorded'}</p>}
        {row.kind === 'metric' && <p>{Object.entries(row).filter(([, value]) => typeof value === 'number').map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value}`).join(' · ')}</p>}
        <p className="trace-coverage">{traceCoverage(row, rows)}{row.trace_id && <> Trace <code>{row.trace_id}</code></>}</p>
        <Citations ids={[row.id]} onOpen={onOpen} />
      </li>)}</ol>
    </>}
  </section>
}

export function SupplementResult({ supplement, onOpen }: { supplement: Supplement; onOpen: (id: string) => void }) {
  const [clock, setClock] = useState(Date.now())
  useEffect(() => {
    if (supplement.state !== 'quota_waiting') return
    const timer = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [supplement.state])
  const result = supplement.result
  const labels = { supports: 'Supports the original conclusion', qualifies: 'Qualifies the original conclusion', disagrees: 'Disagrees with the original conclusion', cannot_assess: 'Cannot assess the original conclusion' }
  return <article className={'supplement-result ' + (result?.relationship === 'disagrees' ? 'disagreement' : '')}>
    <div className="supplement-meta"><strong>{result ? 'Recorded supplement' : 'Further analysis'} · {supplement.state.replaceAll('_', ' ')}</strong><FullTime value={supplement.created_at} /></div>
    {supplement.state === 'queued' && <p role="status">Queued. Fresh investigations take priority; a running job finishes first.</p>}
    {supplement.state === 'running' && <p role="status">Reviewing the original report’s saved evidence through Groq…</p>}
    {supplement.state === 'quota_waiting' && <div role="status"><p>Waiting for Groq quota. {supplement.usage.wait_reason}</p>{supplement.usage.retry_at && <p>Resuming in {Math.max(0, Math.ceil((Date.parse(supplement.usage.retry_at) - clock) / 1000))}s · Expected retry <FullTime value={supplement.usage.retry_at} /></p>}</div>}
    {['failed', 'interrupted'].includes(supplement.state) && <p role="alert">{supplement.error || 'Further analysis was interrupted.'}</p>}
    {result && <><h4>{labels[result.relationship]}</h4><p>{result.summary}</p>
      {result.explanations.map((item, index) => <section className="explanation" key={index}><span className="badge neutral">{item.status}</span><p>{item.claim}</p><Citations ids={item.supporting_citations} label="Supporting sources" onOpen={onOpen} /><Citations ids={item.conflicting_citations} label="Conflicting sources" onOpen={onOpen} /></section>)}
      {!!result.questions.length && <h4>Unanswered questions</h4>}{result.questions.map((item, index) => <div className="unanswered" key={index}><strong>{item.question}</strong><p>Suggested check: {item.suggested_check}</p></div>)}
      <h4>Limitations</h4>{result.limitations.map((item, index) => <p key={index}>{item}</p>)}
    </>}
    <p className="report-note">Supplement usage: {supplement.usage.requests ?? 0}/2 HTTP attempts · {supplement.usage.total_tokens ?? 0} provider-reported tokens · {supplement.usage.quota_wait_seconds ?? 0}s waited</p>
    {supplement.usage.coverage && <p className="report-note">{supplement.source_ids.length} sources supplied from {supplement.usage.available_sources} available. {supplement.usage.omitted_sources ?? Math.max(0, (supplement.usage.available_sources ?? 0) - supplement.source_ids.length)} omitted by compaction; {supplement.usage.unavailable_source_ids?.length ?? 0} unavailable. {supplement.usage.coverage === 'legacy_citations_only' && 'Legacy coverage: cited sources only.'} Trace groups retained together.</p>}
    {supplement.completed_at && <p className="report-note">Finished <FullTime value={supplement.completed_at} /></p>}
    <details className="tool-details"><summary>Supplement sources and safe diagnostics</summary><Citations ids={supplement.source_ids} onOpen={onOpen} />{supplement.usage.diagnostics?.map(entry => <p key={entry.attempt}>Attempt {entry.attempt} · {entry.status ?? 'In progress'} · {entry.input_bytes} input bytes · {entry.duration_ms ?? 0} ms{entry.limit_category ? ` · ${entry.limit_category}` : ''}</p>)}</details>
  </article>
}

export function SupplementPanel({ investigation, onOpen, onUpdate }: { investigation: Investigation; onOpen: (id: string) => void; onUpdate?: (value: Investigation) => void }) {
  const canWrite = useWritePermission()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)
  const supplements = investigation.supplements || []
  const latest = supplements.at(-1)
  const retry = latest && ['failed', 'interrupted'].includes(latest.state)
  async function start() {
    if (submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    try {
      const value = await api<Supplement>('/investigations/' + investigation.id + '/supplements', retry ? { retry: true } : {})
      onUpdate?.({ ...investigation, supplements: [...supplements.filter(item => item.id !== value.id), value] })
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to start further analysis.') }
    finally { submitting.current = false; setBusy(false) }
  }
  return <section className="supplement-panel" aria-label="Further analysis"><h4>Further analysis</h4><p className="report-note">An optional Groq review of this report’s saved evidence. The original assessment stays above. No new telemetry is collected.</p>
    {(!latest || retry) && <button className="button primary" disabled={!canWrite || busy} onClick={start}>{busy ? 'Submitting…' : retry ? 'Retry further analysis' : 'Analyze further'}</button>}
    {retry && <p className="report-note">Explicit retry creates a new retained attempt.</p>}
    {error && <p role="alert" className="supplement-error">{error}</p>}
    {supplements.map(item => <SupplementResult key={item.id} supplement={item} onOpen={onOpen} />)}
  </section>
}
import { useWritePermission } from './account'
