import { createContext, useContext, useEffect, useState } from 'react'
import { ArrowUpRight, RotateCcw } from 'lucide-react'
import { api, userMessage, type Evidence, type Investigation, type ReportEvidence, type Supplement } from './api'
import { logHref } from './routes'
import { Button } from './components/ui/button'
import { Notice, Spinner, StatusBadge } from './components/common'
import { useWritePermission } from './account'
import { sourceDate } from './lib/time'

export function FullTime({ value }: { value: string | null | undefined }) {
  const date = sourceDate(value)
  if (!date) return <span title={value || undefined}>{value ? 'Timestamp unavailable (timezone missing or invalid)' : 'Timestamp not recorded'}</span>
  return <time dateTime={value!} title={date.toLocaleString()}>{value}</time>
}
export const EvidenceLinks = createContext<{ collection?: string | null; run?: string | null; investigation?: string; supplement?: string; rows?: Evidence[] }>({})
export function Citations({ ids, label = 'Cited sources' }: { ids: string[]; label?: string; onOpen?: (id: string) => void }) {
  const scope = useContext(EvidenceLinks)
  return <div className="evidence-citations"><span>{label}</span>{[...new Set(ids)].map(id => <a key={id} href={logHref({ evidence: id, collection: scope.collection, run: scope.run, investigation: scope.investigation, supplement: scope.supplement, row: scope.rows?.find(row => row.id === id) })} target="_blank" rel="noopener noreferrer" aria-label={'Open evidence ' + id + ' in a new tab'}>{id.slice(0, 8)}<ArrowUpRight size={12} aria-hidden="true" /></a>)}{!ids.length && <span>None cited</span>}</div>
}
export function useReportEvidence(investigation?: Investigation) {
  const [loaded, setLoaded] = useState<ReportEvidence | null>(null), [error, setError] = useState('')
  const id = investigation?.id, state = investigation?.state
  useEffect(() => {
    setLoaded(null); setError('')
    if (!id) return
    let cancelled = false
    api<ReportEvidence>('/investigations/' + id + '/evidence').then(value => { if (!cancelled) setLoaded(value) }).catch(e => { if (!cancelled) setError(userMessage(e)) })
    return () => { cancelled = true }
  }, [id, state])
  return { scope: loaded?.investigation_id === id ? loaded : null, error }
}
export function orderedEvidence(rows: Evidence[]) {
  const stamp = (row: Evidence) => sourceDate(row.kind === 'span' && row.started_at ? row.started_at : row.timestamp)?.getTime() ?? Number.POSITIVE_INFINITY
  return [...new Map(rows.map(row => [row.id, row])).values()].sort((a, b) => stamp(a) - stamp(b) || (a.source_line ?? 0) - (b.source_line ?? 0) || a.id.localeCompare(b.id))
}
export function traceCoverage(row: Evidence, rows: Evidence[]) {
  if (row.kind === 'metric') return 'Metric sample; no trace relationship recorded.'
  if (!row.trace_id || !row.span_id) return 'Trace or span reference missing.'
  if (row.kind === 'log') return rows.some(r => r.kind === 'span' && r.trace_id === row.trace_id && r.span_id === row.span_id) ? 'Linked span present in this report’s evidence.' : 'Referenced span is not in this report’s evidence.'
  if (!row.parent_id) return 'No parent recorded; this observation alone does not establish an upstream relationship.'
  return rows.some(r => r.kind === 'span' && r.trace_id === row.trace_id && r.span_id === row.parent_id) ? 'Parent span present in this report’s evidence.' : 'Parent span is not in this report’s evidence.'
}
export function ObservationFields({ row }: { row: Evidence }) {
  const labels: Record<string, string> = { message: 'Message', name: 'Operation', level: 'Log level', status: 'Span outcome', started_at: 'Started at', duration_ms: 'Duration (ms)', http_status: 'HTTP response status', timeout_ms: 'Explicit timeout (ms)', downstream: 'Downstream service', pool_capacity: 'Pool capacity', pool_in_use: 'Connections in use', trace_id: 'Trace ID', request_id: 'Request ID', span_id: 'Span ID', parent_id: 'Parent span ID', span_op: 'Span operation type', exception_type: 'Exception type', exception_handled: 'Exception handled', source_project: 'Sentry project (provenance)', source_project_id: 'Sentry project ID', source_dataset: 'Sentry dataset' }
  return <dl className="observation-fields">{Object.entries(row).filter(([key]) => !['id', 'kind', 'service', 'timestamp', 'run_id', 'collection_id', 'original'].includes(key)).map(([key, value]) => <div key={key}><dt>{labels[key] || key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value ?? 'Not recorded')}</dd></div>)}</dl>
}
export function SupplementResult({ supplement }: { supplement: Supplement; onOpen?: (id: string) => void }) {
  const parentScope = useContext(EvidenceLinks)
  const result = supplement.result
  if (!result) return <Notice kind={['failed','interrupted'].includes(supplement.state) ? 'error' : 'info'}>{supplement.state === 'failed' ? userMessage(supplement.error, 'Further analysis failed. Retry explicitly.') : supplement.state === 'interrupted' ? 'Further analysis was interrupted. Retry explicitly to create a new saved attempt.' : supplement.state === 'quota_waiting' ? 'Waiting for analysis capacity. This attempt resumes automatically within its allowance.' : 'Reviewing the saved assessment…'}</Notice>
  return <EvidenceLinks.Provider value={{ ...parentScope, investigation: supplement.investigation_id, supplement: supplement.id }}><article className="supplement-result"><div className="report-section-heading"><h3>Further analysis</h3><StatusBadge state={result.relationship} /></div><p>{result.summary}</p>{result.explanations.map((item, index) => <div key={index} className="finding"><p>{item.claim}</p><span className="badge neutral">{item.status}</span><Citations ids={item.supporting_citations} label="Supporting sources" />{!!item.conflicting_citations.length && <Citations ids={item.conflicting_citations} label="Conflicting sources" />}</div>)}{!!result.questions.length && <><h4>Next questions</h4><ul>{result.questions.map((item, index) => <li key={index}><strong>{item.question}</strong><p>{item.suggested_check}</p></li>)}</ul></>}{!!result.limitations.length && <><h4>Limitations</h4><ul>{result.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></>}<p className="report-note">Saved <FullTime value={supplement.completed_at} />. Uses the original saved evidence.</p></article></EvidenceLinks.Provider>
}
export function SupplementPanel({ investigation, onUpdate }: { investigation: Investigation; onOpen?: (id: string) => void; onUpdate?: (value: Investigation) => void }) {
  const canWrite = useWritePermission(), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const supplements = investigation.supplements || [], latest = supplements.at(-1)
  const running = latest && ['queued','running','quota_waiting'].includes(latest.state)
  const retry = latest && ['failed','interrupted'].includes(latest.state)
  useEffect(() => {
    if (!running) return
    let alive = true, timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try { const value = await api<Investigation>('/investigations/' + investigation.id); if (alive) { onUpdate?.(value); timer = setTimeout(refresh, 2500) } }
      catch (e) { if (alive) setError(userMessage(e)) }
    }
    timer = setTimeout(refresh, 2500)
    return () => { alive = false; clearTimeout(timer) }
  }, [running, investigation.id, onUpdate])
  async function analyze() {
    setBusy(true); setError('')
    try { const value = await api<Supplement>('/investigations/' + investigation.id + '/supplements', retry ? { retry: true } : {}); onUpdate?.({ ...investigation, supplements: [...supplements.filter(item => item.id !== value.id), value] }) }
    catch (e) { setError(userMessage(e)) } finally { setBusy(false) }
  }
  return <section className="supplement-panel"><div><h3>Take another look</h3><p>Review this assessment against its saved evidence. New evidence needs a new investigation.</p></div>{(!latest || retry) && <Button variant="secondary" disabled={!canWrite || busy} onClick={analyze}>{busy ? <Spinner /> : <RotateCcw size={14} />}{retry ? 'Retry further analysis' : 'Analyze further'}</Button>}{error && <Notice kind="error">{error}</Notice>}{latest && <SupplementResult supplement={latest} />}{supplements.length > 1 && <details><summary>Previous review attempts ({supplements.length - 1})</summary>{supplements.slice(0, -1).map(item => <SupplementResult key={item.id} supplement={item} />)}</details>}</section>
}
