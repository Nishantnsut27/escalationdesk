import { useEffect, useRef, useState, type FormEvent } from 'react'
import { FileText, Fingerprint, LoaderCircle, Upload } from 'lucide-react'
import { api, ApiError, type Collection, type CollectionDetail, type EvidencePage, type Overview } from './api'
import { FullTime, useReportEvidence } from './reports'
import { WorkflowLabel } from './triage'
import type { ReportView, ServiceMap, EvidenceTable } from './App'

const active = ['queued', 'running', 'quota_waiting']
export const IMPORT_BYTES = 1024 * 1024

export function ImportForm({ onImported, onError }: { onImported: (value: Collection) => void; onError: (error: unknown) => void }) {
  const [content, setContent] = useState('')
  const [filename, setFilename] = useState<string | undefined>()
  const [title, setTitle] = useState('')
  const [symptom, setSymptom] = useState('')
  const [service, setService] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  const fileRead = useRef(0)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; fileRead.current++ } }, [])
  async function chooseFile(file?: File) {
    const revision = ++fileRead.current
    if (!file) return
    setError('')
    try {
      if (!/\.(log|txt|jsonl)$/i.test(file.name)) throw new Error('Choose a .log, .txt or .jsonl file.')
      if (file.size > IMPORT_BYTES) throw new Error('Import at most 1 MiB of log text.')
      const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
      if (!mounted.current || revision !== fileRead.current) return
      setContent(text); setFilename(file.name)
      if (!title) setTitle(file.name)
    } catch (e) { if (mounted.current && revision === fileRead.current) setError(e instanceof Error ? e.message : 'Unable to read UTF-8 text.') }
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      if (new TextEncoder().encode(content).length > IMPORT_BYTES) throw new Error('Import at most 1 MiB of log text.')
      const collection = await api<Collection>('/imports', { content, title: title.trim() || filename || 'Imported logs',
        symptom, filename, service: service.trim() || 'unknown' })
      if (mounted.current) { onImported(collection); setContent(''); setFilename(undefined); setTitle(''); setSymptom('') }
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof Error ? e.message : 'Unable to import logs.')
        if (e instanceof ApiError && e.status === 401) onError(e)
      }
    } finally { if (mounted.current) setBusy(false) }
  }
  return <section className="import-panel" aria-label="Import application logs">
    <div className="section-heading"><h2><Upload size={17} />Import application logs</h2><span>Saved to your account</span></div>
    <form onSubmit={submit}>
      <div className="import-fields"><label>Collection title<input value={title} onChange={e => setTitle(e.target.value)} maxLength={120} placeholder="Checkout errors, November 3" disabled={busy} /></label>
        <label>Default service (optional)<input value={service} onChange={e => setService(e.target.value)} maxLength={80} placeholder="Used when a log has no service" disabled={busy} /></label></div>
      <label>Log file (optional)<input type="file" accept=".log,.txt,.jsonl" disabled={busy} onChange={e => void chooseFile(e.target.files?.[0])} /></label>
      <label>Log content<textarea value={content} onChange={e => { fileRead.current++; setContent(e.target.value) }} placeholder="Paste JSONL, plain logs or a multiline exception..." rows={7} required disabled={busy} spellCheck={false} /></label>
      <label>What did you observe? (optional)<textarea value={symptom} onChange={e => setSymptom(e.target.value)} maxLength={2000} rows={2} placeholder="For example: sign-in requests started returning 500." disabled={busy} /></label>
      <p className="report-note">Up to 1 MiB, 5,000 observations and 32 services per import. UTF-8 text only. Common credentials are redacted; review saved content before investigating with Jev and Groq. Importing does not start analysis.</p>
      {error && <p className="import-error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy || !content.trim()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}{busy ? 'Importing...' : 'Save logs'}</button>
    </form>
  </section>
}

export function ImportCoverage({ collection }: { collection: Collection }) {
  const summary = collection.summary
  return <section className="import-coverage" aria-label="Import coverage">
    <h3>{collection.title}</h3>
    <p>Imported <FullTime value={collection.created_at} /> from {collection.filename || 'pasted text'}. This is import activity, not a telemetry timestamp.</p>
    {collection.symptom && <p><strong>Reported symptom:</strong> {collection.symptom}</p>}
    <dl>{[
      ['Saved observations', summary.observations], ['Source lines', summary.input_lines], ['Duplicate IDs skipped', summary.duplicates],
      ['Conflicting IDs retained', summary.conflicting_ids], ['Malformed records kept as text', summary.malformed_records],
      ['Redactions', summary.redactions], ['Missing/invalid timestamps', summary.missing_timestamps],
      ['Unknown services', summary.missing_services], ['Invalid normalized fields', summary.invalid_fields],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p>{summary.coverage} Available kinds: {summary.kinds.join(', ')}. Metrics and traces are unavailable unless supplied. Missing timestamps remain unknown.</p>
    <p>{summary.redaction_note}</p>
  </section>
}

type ViewProps = {
  overview: Overview | null; onOpen: (id: string) => void; onError: (error: unknown) => void
  ReportComponent: typeof ReportView; MapComponent: typeof ServiceMap; TableComponent: typeof EvidenceTable
}

function CollectionView({ id, overview, onOpen, onError, ReportComponent, MapComponent, TableComponent }: ViewProps & { id: string }) {
  const [detail, setDetail] = useState<CollectionDetail | null>(null)
  const [historyId, setHistoryId] = useState('')
  const [tab, setTab] = useState('assessment')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<EvidencePage | null>(null)
  const [loading, setLoading] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let cancelled = false
    const update = () => api<CollectionDetail>('/collections/' + id).then(value => {
      if (!cancelled) setDetail(value)
    }).catch(e => { if (!cancelled) { setError(e.message); if (e instanceof ApiError && e.status === 401) onError(e) } })
    void update()
    const interval = setInterval(update, 1500)
    return () => { cancelled = true; clearInterval(interval) }
  }, [id, onError])
  useEffect(() => {
    if (tab === 'assessment') return
    let cancelled = false
    setLoading(true); setError('')
    if (offset === 0) setPage(null)
    const params = new URLSearchParams({ kind: tab, limit: '100', offset: String(offset) })
    if (query.trim()) params.set('query', query.trim())
    api<EvidencePage>('/collections/' + id + '/evidence?' + params).then(value => {
      if (!cancelled) setPage(previous => ({ ...value, evidence: offset ? [...(previous?.evidence || []), ...value.evidence] : value.evidence }))
    }).catch(e => { if (!cancelled) { setError(e.message); if (e instanceof ApiError && e.status === 401) onError(e) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id, tab, offset, query, onError])
  const investigation = detail?.investigations.find(item => item.id === historyId) || detail?.investigations[0]
  const { scope, error: evidenceError } = useReportEvidence(investigation)
  const investigating = detail?.investigations.some(item => active.includes(item.state))
  async function investigate() {
    setBusy(true); setError('')
    try {
      const started = await api<{ id: string }>('/collections/' + id + '/investigations', { engine: 'groq', triage: true })
      const updated = await api<CollectionDetail>('/collections/' + id)
      if (mounted.current) { setDetail(updated); setHistoryId(started.id); setTab('assessment') }
    } catch (e) { if (mounted.current) { setError(e instanceof Error ? e.message : 'Unable to investigate.'); if (e instanceof ApiError && e.status === 401) onError(e) } }
    finally { if (mounted.current) setBusy(false) }
  }
  if (!detail) return <p role={error ? 'alert' : 'status'}>{error || 'Loading saved collection...'}</p>
  return <section className="detail-panel import-detail">
    <ImportCoverage collection={detail.collection} />
    <div className="section-heading"><h2>Services in this collection</h2><span>No live reachability or configured links supplied</span></div>
    <MapComponent overview={null} services={detail.collection.summary.services} evidence={scope?.evidence || []} report={investigation?.report} evidenceReady={!!scope} />
    <div className="detail-heading"><h2>Review the evidence</h2><div className="investigate-controls">
      <WorkflowLabel providers={overview?.providers} />
      <button className="button primary" onClick={investigate} disabled={!overview?.providers.groq || busy || investigating}>{busy || investigating ? <LoaderCircle size={16} className="spin" /> : <Fingerprint size={16} />}{investigating ? 'Investigating' : 'Investigate'}</button>
    </div></div>
    <p className="import-guidance">Investigate sends selected redacted observations to OpenRouter Jev for initial triage, then Groq for an assessment. Missing evidence remains unknown; review the cited sources and limitations.</p>
    {error && <p className="import-error" role="alert">{error}</p>}
    <div className="detail-tabs">{['assessment', 'log', 'span', 'metric'].map(kind => <button key={kind} className={tab === kind ? 'active' : ''} onClick={() => { setTab(kind); setOffset(0); setPage(null); setQuery('') }}>{({ assessment: 'Assessment', log: 'Logs', span: 'Traces', metric: 'Metrics' })[kind]}</button>)}</div>
    {tab === 'assessment' ? <>
      {!!detail.investigations.length && <div className="history-picker"><label htmlFor="import-history">Saved investigation</label><select id="import-history" value={investigation?.id || ''} onChange={e => setHistoryId(e.target.value)}>{detail.investigations.map(item => <option key={item.id} value={item.id}>{item.created_at} · {item.engine} · {item.state}</option>)}</select></div>}
      <ReportComponent investigation={investigation} onOpen={onOpen} scope={scope} evidenceError={evidenceError} onUpdate={value => setDetail(current => current ? { ...current, investigations: current.investigations.map(item => item.id === value.id ? value : item) } : current)} />
    </> : <>
      {tab === 'log' && <label className="collection-search">Search messages across this collection<input value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} maxLength={200} placeholder="For example: TypeError" /></label>}
      <TableComponent key={tab + query} kind={tab} evidence={page?.evidence || []} onOpen={onOpen} note="Raw imported evidence. Paged separately from sources used by the selected report. Filtering below searches loaded rows." />
      {loading && <p className="import-guidance" role="status">Loading evidence...</p>}
      {!loading && page?.next_offset != null && <button className="button secondary load-more" onClick={() => setOffset(page.next_offset!)}>Load more observations</button>}
    </>}
  </section>
}

export function ImportWorkspace(props: ViewProps) {
  const [collections, setCollections] = useState<Collection[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    api<Collection[]>('/collections').then(rows => { if (!cancelled) { setCollections(rows); setSelected(value => value || rows[0]?.id || '') } })
      .catch(e => { if (!cancelled) { setError(e.message); if (e instanceof ApiError && e.status === 401) props.onError(e) } })
    return () => { cancelled = true }
  }, [props.onError])
  return <div className="imports-workspace">
    <ImportForm onError={props.onError} onImported={value => { setCollections(rows => [value, ...rows.filter(row => row.id !== value.id)]); setSelected(value.id) }} />
    {error && <p className="import-error" role="alert">{error}</p>}
    <div className="investigation-workspace">
      <section className="incident-panel"><div className="section-heading"><h2><FileText size={16} />Saved collections</h2><span>{collections.length}</span></div>
        <div className="incident-list">{collections.map(item => <button className={'incident-item ' + (item.id === selected ? 'selected' : '')} key={item.id} onClick={() => setSelected(item.id)}><h3>{item.title}</h3><p>{item.summary.observations} observations · {item.source_type}</p><FullTime value={item.created_at} /></button>)}</div>
        {!collections.length && <p className="import-guidance">Save logs above to begin. Importing creates no incident or model request.</p>}
      </section>
      {selected ? <CollectionView key={selected} id={selected} {...props} /> : <section className="detail-panel"><div className="report-empty"><FileText size={28} /><h3>Your application, your evidence.</h3><p>Import logs without knowing the root cause. Review the saved observations, then explicitly investigate.</p></div></section>}
    </div>
  </div>
}
