import { useEffect, useRef, useState, type Ref } from 'react'
import { ArrowLeft, ArrowRight, Check, ChevronRight, Copy, FileCode2, GitBranch, History, Layers3, X } from 'lucide-react'
import { api, userMessage, type AnalysisDetails, type Evidence, type FlowData, type HistogramData, type Investigation, type LogPage, type SuppliedExcerpt } from './api'
import { useRoute, evidenceFilters, filterKeys, navigate, routeHref } from './routes'
import { EmptyState, ErrorState, LoadingState, Notice, PageHeading, Severity } from './components/common'
import { Button } from './components/ui/button'
import { Histogram } from './charts'
import { RequestFlow } from './request-flow'
import { ImportCoverage } from './imports'
import { FullTime, ObservationFields } from './reports'
import { sourceDate } from './lib/time'
import { LogFilters } from './log-filters'
import { EvidenceTimeline } from './timeline'

function Highlight({ value, query }: { value: string; query: string }) {
  if (!query) return <>{value}</>
  const start = value.toLowerCase().indexOf(query.toLowerCase())
  return start < 0 ? <>{value}</> : <>{value.slice(0, start)}<mark>{value.slice(start, start + query.length)}</mark>{value.slice(start + query.length)}</>
}
export function LogRows({ rows, selected, query = '', onSelect }: { rows: Evidence[]; selected?: string; query?: string; onSelect: (id: string) => void }) {
  return <div className="table-scroll log-table"><table><caption className="sr-only">Saved observations. Select a row to inspect the retained source.</caption><thead><tr><th>Timestamp</th><th>Severity</th><th>Service</th><th>Observation</th><th><span className="sr-only">Source details</span></th></tr></thead><tbody>{rows.map(row => <tr key={row.id} id={'observation-' + row.id} className={(selected === row.id ? 'selected-row ' : '') + (row.level === 'error' ? 'error-row' : '')} aria-selected={selected === row.id}><td><time dateTime={sourceDate(row.timestamp) ? row.timestamp! : undefined}>{sourceDate(row.timestamp)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, fractionalSecondDigits: 3 }) || 'Not recorded'}</time>{sourceDate(row.timestamp) && <small>{sourceDate(row.timestamp)!.toLocaleDateString()}</small>}</td><td><Severity level={row.level} /><small>{row.kind !== 'log' ? row.kind : ''}</small></td><td><code className="service-label">{row.service}</code></td><td><button className="log-message" onClick={() => onSelect(row.id)} aria-label={'Inspect observation ' + row.id}><Highlight value={row.message || row.name || 'Metric sample'} query={query} /></button>{row.error_tags?.filter(tag => tag !== 'unknown').length ? <div className="row-tags">{row.error_tags.filter(tag => tag !== 'unknown' && tag !== 'mixed').slice(0, 3).map(tag => <span key={tag}>{tag.replaceAll('_', ' ')}</span>)}</div> : null}</td><td><button className="icon-button" onClick={() => onSelect(row.id)} aria-label={'Open details for ' + row.id}><ChevronRight size={16} /></button></td></tr>)}</tbody></table></div>
}
export function RecordDetails({ row, excerpts, query, onClose, focusRef }: { row: Evidence; excerpts: SuppliedExcerpt[]; query: string; onClose: () => void; focusRef?: Ref<HTMLElement> }) {
  return <section ref={focusRef} tabIndex={-1} className="record-details" data-evidence-id={row.id} aria-labelledby="record-title"><div className="panel-heading"><div><span className="eyebrow">SELECTED OBSERVATION</span><h3 id="record-title">{row.service} <code>{row.id.slice(0, 8)}</code></h3></div><button className="icon-button" onClick={onClose} aria-label="Close selected observation"><X size={17} /></button></div><div className="record-meta"><Severity level={row.level} /><span>{row.kind}</span><FullTime value={row.timestamp} /></div>
    {!!excerpts.length && <div className="supplied-excerpts"><h4>Excerpt supplied to this report</h4><p>The exact selected content is retained below. The full redacted observation remains available for comparison.</p>{excerpts.map((excerpt, index) => <div key={index}><pre tabIndex={0}><Highlight value={JSON.stringify(excerpt.record, null, 2)} query={query} /></pre><p>Source {excerpt.selection.source_line ? `lines ${excerpt.selection.source_line}–${excerpt.selection.source_end_line || excerpt.selection.source_line}` : 'line range unavailable'} · {excerpt.selection.occurrences || 1} recorded occurrence(s).</p>{Object.entries(excerpt.fields).map(([field, value]) => <p key={field}>{field}: characters {value.ranges.map(range => `${range[0]}–${range[1]}`).join(', ')} of {value.source_chars} (Unicode code points; end exclusive).</p>)}<span className="detail-muted">Selection: {excerpt.selection.selection?.map(value => value.reason.replaceAll('_', ' ')).join('; ') || 'saved evidence'}</span></div>)}</div>}
    <div className="record-message"><h4>Recorded message</h4><pre tabIndex={0}><Highlight value={row.message || row.name || 'No message recorded'} query={query} /></pre></div>
    {row.stack_trace && <details className="stack-trace" open={Boolean(query && row.stack_trace.toLowerCase().includes(query.toLowerCase()))}><summary>Stack trace</summary><pre tabIndex={0}><Highlight value={row.stack_trace} query={query} /></pre></details>}
    <details><summary>Observation fields and correlation</summary><ObservationFields row={row} /></details>
    {row.original && <details open={Boolean(query && row.original.toLowerCase().includes(query.toLowerCase()))}><summary>Retained redacted source content</summary><pre tabIndex={0}><Highlight value={row.original} query={query} /></pre></details>}
    <details><summary>Recorded JSON</summary><pre tabIndex={0}>{JSON.stringify(row, null, 2)}</pre></details>
  </section>
}
export default function LogsWorkspace() {
  const route = useRoute(), params = route.params
  const evidenceId = params.get('evidence') || '', investigationId = params.get('investigation') || ''
  const collectionId = params.get('collection') || '', runId = params.get('run') || ''
  const scopePath = collectionId ? '/collections/' + encodeURIComponent(collectionId) : runId ? '/runs/' + encodeURIComponent(runId) : ''
  const filters = evidenceFilters(params).toString()
  const [details, setDetails] = useState<AnalysisDetails | null>(null), [page, setPage] = useState<LogPage | null>(null)
  const [chart, setChart] = useState<HistogramData | null>(null), [flow, setFlow] = useState<FlowData | null>(null), [timeline, setTimeline] = useState<LogPage | null>(null)
  const [selected, setSelected] = useState<Evidence | null>(null), [report, setReport] = useState<Investigation | null>(null)
  const [error, setError] = useState(''), [loading, setLoading] = useState(false), [retry, setRetry] = useState(0), [copied, setCopied] = useState(false)
  const generation = useRef(0)
  const selectedRecord = useRef<HTMLElement>(null), revealedSelection = useRef('')
  function revealSelectedRecord() {
    selectedRecord.current?.focus({ preventScroll: true })
    selectedRecord.current?.scrollIntoView({ behavior: 'instant', block: 'start' })
  }
  function change(update: Record<string, string | number | null>, reset = true, replace = false) {
    const next = new URLSearchParams(window.location.hash.split('?')[1] || '')
    if (reset) { next.delete('offset'); next.delete('timeline_offset') }
    for (const [name, value] of Object.entries(update)) value == null || value === '' ? next.delete(name) : next.set(name, String(value))
    navigate('logs', '', Object.fromEntries(next), replace)
  }
  useEffect(() => {
    if (!evidenceId) { setSelected(null); return }
    let alive = true
    setSelected(null)
    api<Evidence>('/evidence/' + encodeURIComponent(evidenceId)).then(row => {
      if (!alive) return
      setSelected(row)
      if (!scopePath || params.get('correlate') === '1') {
        const update: Record<string, string | null> = { collection: row.collection_id as string || null, run: row.run_id as string || null, correlate: null, trace_id: null, request_id: null }
        if (row.trace_id) { update.trace_id = row.trace_id; update.request_id = null }
        else if (row.request_id) update.request_id = row.request_id
        change(update, true, true)
      }
    }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [evidenceId, scopePath, params.get('correlate'), retry])
  useEffect(() => {
    if (!investigationId) { setReport(null); return }
    let alive = true
    api<Investigation>('/investigations/' + encodeURIComponent(investigationId)).then(value => { if (alive) setReport(value) }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [investigationId, retry])
  useEffect(() => {
    if (!scopePath) return
    let alive = true
    setDetails(null)
    api<AnalysisDetails>(scopePath + '/analysis').then(value => { if (alive) setDetails(value) }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [scopePath, retry])
  useEffect(() => {
    if (!scopePath) return
    const current = ++generation.current
    setLoading(true); setError(''); setPage(null)
    const query = new URLSearchParams(filters)
    query.set('limit', '50')
    if (params.has('offset')) query.set('offset', params.get('offset')!)
    if (evidenceId) query.set('selected', evidenceId)
    api<LogPage>(scopePath + '/logs?' + query).then(rows => {
      if (current !== generation.current) return
      setPage(rows)
    }).catch(e => { if (current === generation.current) setError(userMessage(e)) }).finally(() => { if (current === generation.current) setLoading(false) })
    return () => { generation.current++ }
  }, [scopePath, filters, params.get('offset'), evidenceId, retry])
  useEffect(() => {
    if (!scopePath) return
    let alive = true
    setChart(null)
    api<HistogramData>(scopePath + '/histogram?' + filters).then(value => { if (alive) setChart(value) }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [scopePath, filters, retry])
  const flowOpen = params.get('flow') === '1', timelineOpen = params.get('timeline') === '1'
  useEffect(() => {
    if (!scopePath || !flowOpen) return
    let alive = true
    setFlow(null)
    api<FlowData>(scopePath + '/flow?' + filters).then(value => { if (alive) setFlow(value) }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [scopePath, filters, flowOpen, retry])
  useEffect(() => {
    if (!scopePath || !timelineOpen) return
    let alive = true
    setTimeline(null)
    api<LogPage>(scopePath + '/timeline?' + filters + '&limit=100&offset=' + (params.get('timeline_offset') || '0')).then(value => { if (alive) setTimeline(value) }).catch(e => { if (alive) setError(userMessage(e)) })
    return () => { alive = false }
  }, [scopePath, filters, timelineOpen, params.get('timeline_offset'), retry])
  const supplementId = params.get('supplement') || ''
  const finalRequest = supplementId ? report?.supplements?.find(item => item.id === supplementId)?.supplied_excerpts?.at(-1) : report?.supplied_excerpts?.filter(item => item.phase === 'report').at(-1)
  const excerpts = finalRequest?.excerpts.filter(item => item.evidence_id === evidenceId) || []
  useEffect(() => {
    if (!evidenceId) { revealedSelection.current = ''; return }
    const key = [scopePath, investigationId, supplementId, evidenceId].join(':')
    if (revealedSelection.current === key || selected?.id !== evidenceId || params.get('correlate') === '1') return
    // Wait for the stacked views above the source to settle before jumping to a citation.
    if (loading || !page || !details || !chart || (investigationId && !report) || (flowOpen && !flow) || (timelineOpen && !timeline)) return
    const frame = requestAnimationFrame(() => {
      if (!selectedRecord.current) return
      revealSelectedRecord()
      revealedSelection.current = key
    })
    return () => cancelAnimationFrame(frame)
  }, [evidenceId, selected, scopePath, investigationId, supplementId, params.get('correlate'), loading, page, details, chart, report, flowOpen, flow, timelineOpen, timeline])
  function select(id: string) {
    if (id === evidenceId && selectedRecord.current) revealSelectedRecord()
    else change({ evidence: id, offset: null }, false)
  }
  if (!scopePath && !evidenceId) return <EmptyState title="Choose saved evidence"><a href="#/collections">Open a collection</a> to search its observations, or follow a report citation.</EmptyState>
  return <div className="logs-workspace"><PageHeading eyebrow="EVIDENCE WORKSPACE" title={details?.collection?.title || (runId ? 'Recorded run logs' : 'Inspect the evidence')} actions={<><Button variant="secondary" onClick={async () => { try { await navigator.clipboard.writeText(window.location.href); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { setError('Copy the address from your browser to share this view.') } }}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? 'Link copied' : 'Copy view link'}</Button>{investigationId && <Button asChild variant="secondary"><a href={routeHref('investigations', investigationId)}><ArrowLeft size={15} />Back to report</a></Button>}</>}>Search the saved observations. Follow recorded relationships. Keep the source in view.</PageHeading>
    {error && <ErrorState error={error} retry={() => setRetry(value => value + 1)} />}
    <section className="panel log-overview"><div className="coverage-strip"><span><Layers3 size={15} /><strong>{details?.counts.observations.toLocaleString() ?? '—'}</strong> saved observations</span><span><strong>{details?.counts.services ?? '—'}</strong> observed services</span><span className={details?.counts.unknown_time_in_scope ? 'coverage-gap' : ''}><History size={14} />{details?.counts.unknown_time_in_scope ?? '—'} timestamps missing</span></div>
      {details?.collection && <details className="coverage-details"><summary>Collection details and coverage<ChevronRight size={14} /></summary><ImportCoverage collection={details.collection} /></details>}
      <LogFilters params={params} details={details} total={page?.total} onChange={change} />
    </section>
    <section className="panel"><div className="panel-heading"><h2>Log volume</h2><span>By recorded timestamp</span></div>{chart ? <Histogram data={chart} onBucket={bucket => change({ start_time: bucket.start, end_time: bucket.end })} /> : <LoadingState>Loading complete log counts…</LoadingState>}</section>
    <section className="panel expandable"><button className="expander" onClick={() => change({ flow: flowOpen ? null : '1' }, false)} aria-expanded={flowOpen} aria-controls="request-flow"><span><GitBranch size={17} />Request flow<small>Full request context</small></span><ChevronRight className={flowOpen ? 'turned' : ''} size={17} /></button>{flowOpen && <div id="request-flow">{flow ? <RequestFlow data={flow} selected={evidenceId} onSelect={select} /> : <LoadingState>Reading recorded relationships…</LoadingState>}</div>}</section>
    <section className="panel expandable"><button className="expander" onClick={() => change({ timeline: timelineOpen ? null : '1' }, false)} aria-expanded={timelineOpen} aria-controls="chronological-timeline"><span><History size={17} />Chronological timeline<small>Current filters</small></span><ChevronRight className={timelineOpen ? 'turned' : ''} size={17} /></button>{timelineOpen && <div id="chronological-timeline" className="panel-content"><EvidenceTimeline data={timeline} selected={evidenceId} onSelect={select} onPage={offset => change({ timeline_offset: offset }, false)} /></div>}</section>
    <section className="panel"><div className="panel-heading"><h2><FileCode2 size={17} />Observations</h2><span>{page ? `${page.offset + (page.evidence.length ? 1 : 0)}–${page.offset + page.evidence.length} of ${page.total.toLocaleString()}` : 'Saved source records'}</span></div>{loading ? <LoadingState /> : page?.evidence.length ? <LogRows rows={page.evidence} selected={evidenceId} query={params.get('query') || ''} onSelect={select} /> : <EmptyState title="No observations match">Try a wider time range or clear a filter. Missing timestamps cannot fall within a time range.</EmptyState>}
      {page && <div className="pagination"><span>50 observations per page · {page.unknown_timestamps} undated in this scope</span><div><Button variant="secondary" size="sm" disabled={page.offset === 0 || loading} onClick={() => change({ offset: Math.max(0, page.offset - 50) }, false)}><ArrowLeft size={14} />Previous</Button><Button variant="secondary" size="sm" disabled={page.next_offset == null || loading} onClick={() => change({ offset: page.next_offset! }, false)}>Next<ArrowRight size={14} /></Button></div></div>}
      {selected && <>{page?.selected_in_filter === false && <Notice kind="warning">The selected source is outside the current filters. <button className="text-button" onClick={() => change({ ...Object.fromEntries(filterKeys.map(key => [key, null])), offset: null })}>Show surrounding evidence</button></Notice>}{investigationId && !excerpts.length && report && <Notice>Exact excerpt metadata was not recorded for this observation in the selected report. The retained source is shown below.</Notice>}<RecordDetails focusRef={selectedRecord} row={selected} excerpts={excerpts} query={params.get('query') || ''} onClose={() => change({ evidence: null }, false)} /></>}
    </section>
  </div>
}
