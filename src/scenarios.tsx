import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowUpRight, Play, ShieldCheck, Square } from 'lucide-react'
import { api, ApiError, type CollectionDetail, type DemoCase, type Evidence, type EvidencePage, type Run, type Scenario, type Detail, type Incident } from './api'
import { ReportView, EvidenceTable, ServiceMap } from './App'
import { FullTime, useReportEvidence } from './reports'
import { poll } from './poll'

const active = ['starting', 'running', 'resetting']
const working = ['queued', 'running', 'quota_waiting']
type Props = { onOpen: (id: string) => void; onError: (error: unknown) => void }

export function CaseCards({ cases, onSelect }: { cases: DemoCase[]; onSelect: (slug: string) => void }) {
  return <div className="case-grid">{cases.map((item, index) => <button className="case-card" key={item.slug} onClick={() => onSelect(item.slug)}>
    <div className="case-card-top"><span className="case-number">{String(index + 1).padStart(2, '0')}</span><span className="badge neutral">{item.category}</span><ArrowUpRight size={18} /></div>
    <h2>{item.title}</h2><p>{item.description}</p><div className="case-counts"><strong>{item.counts.log.toLocaleString()} logs</strong><span>{item.counts.span} spans · {item.counts.metric} metrics</span></div>
    <footer>Recorded <FullTime value={item.recorded_at} /></footer>
  </button>)}</div>
}

export function CollectionAssessment({ id, readOnly = false, ...props }: Props & { id: string; readOnly?: boolean }) {
  const [detail, setDetail] = useState<CollectionDetail | null>(null)
  const [selected, setSelected] = useState('')
  const [tab, setTab] = useState('assessment')
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<EvidencePage | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const update = async () => {
      try {
        const next = await api<CollectionDetail>('/collections/' + id)
        if (cancelled) return
        setDetail(next)
        if (!readOnly && next.investigations.some(item => working.includes(item.state))) timer = setTimeout(update, 2000)
      } catch (e) { if (!cancelled) { setError(e instanceof Error ? e.message : 'Unable to load recording.'); props.onError(e) } }
    }
    void update()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [id, readOnly, busy])
  useEffect(() => {
    if (tab === 'assessment') return
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams({ kind: tab, limit: '100', offset: String(offset) })
    if (query.trim()) params.set('query', query.trim())
    api<EvidencePage>('/collections/' + id + '/evidence?' + params).then(next => {
      if (!cancelled) setPage(previous => ({ ...next, evidence: offset ? [...(previous?.evidence || []), ...next.evidence] : next.evidence }))
    }).catch(e => { if (!cancelled) { setError(e.message); props.onError(e) } }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [id, tab, query, offset])
  const investigation = detail?.investigations.find(item => item.id === selected) || detail?.investigations[0]
  const { scope, error: evidenceError } = useReportEvidence(investigation)
  async function investigate() {
    setBusy(true); setError('')
    try {
      const next = await api<{ id: string }>('/collections/' + id + '/investigations', { engine: 'groq', triage: true })
      if (mounted.current) { setSelected(next.id); setTab('assessment') }
    } catch (e) { if (mounted.current) { setError(e instanceof Error ? e.message : 'Unable to investigate.'); props.onError(e) } }
    finally { if (mounted.current) setBusy(false) }
  }
  if (!detail) return <p role={error ? 'alert' : 'status'}>{error || 'Loading recorded evidence…'}</p>
  return <section className="detail-panel import-detail case-detail">
    <div className="import-coverage"><span className="eyebrow">{readOnly ? 'RECORDED DEMO · READ-ONLY' : 'SAVED SCENARIO EVIDENCE'}</span><h2>{detail.collection.title}</h2><p>{detail.collection.summary.coverage}</p><p>{detail.collection.summary.input_lines} log records · {detail.collection.summary.observations} total observations</p>
      {!readOnly && <button className="button primary" onClick={investigate} disabled={busy || detail.investigations.some(item => working.includes(item.state)) || !detail.collection.summary.observations}>{busy ? 'Submitting…' : 'Investigate'}<ArrowUpRight size={16} /></button>}
      {!readOnly && <p>Investigate explicitly runs Jev triage, then Groq. Saved evidence remains unchanged.</p>}
    </div>
    {error && <p className="import-error" role="alert">{error}</p>}
    <ServiceMap overview={null} services={detail.collection.summary.services} evidence={scope?.evidence || []} report={investigation?.report} evidenceReady={!!scope} />
    <div className="detail-tabs">{['assessment', 'log', 'span', 'metric'].map(kind => <button key={kind} className={tab === kind ? 'active' : ''} onClick={() => { setTab(kind); setOffset(0); setQuery(''); setPage(null) }}>{({ assessment: 'Assessment', log: 'Logs', span: 'Traces', metric: 'Metrics' })[kind]}</button>)}</div>
    {tab === 'assessment' ? <>
      {!readOnly && !!detail.investigations.length && <div className="history-picker"><label htmlFor="case-report">Saved investigation</label><select id="case-report" value={investigation?.id || ''} onChange={e => setSelected(e.target.value)}>{detail.investigations.map(item => <option key={item.id} value={item.id}>{item.created_at} · {item.state}</option>)}</select></div>}
      <ReportView readOnly={readOnly} investigation={investigation} onOpen={props.onOpen} scope={scope} evidenceError={evidenceError} onUpdate={value => setDetail(current => current ? { ...current, investigations: current.investigations.map(item => item.id === value.id ? value : item) } : current)} />
    </> : <>
      <label className="collection-search">Search all messages in this recording<input value={query} onChange={e => { setQuery(e.target.value); setOffset(0); setPage(null) }} maxLength={200} placeholder="For example: certificate or query" /></label>
      <EvidenceTable evidence={page?.evidence || []} kind={tab} onOpen={props.onOpen} note="Full recorded collection, paged separately from the sources used by the model. The filter below searches loaded rows." />
      {loading && <p className="import-guidance" role="status">Loading observations…</p>}
      {!loading && page?.next_offset != null && <button className="button secondary load-more" onClick={() => setOffset(page.next_offset!)}>Load more observations</button>}
    </>}
  </section>
}

export function DemoWorkspace(props: Props) {
  const [cases, setCases] = useState<DemoCase[] | null>(null)
  const [slug, setSlug] = useState(location.hash.replace(/^#\/demo\/?/, ''))
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    api<DemoCase[]>('/demo/cases').then(value => { if (!cancelled) setCases(value) }).catch(e => { if (!cancelled) { setError(e.message); props.onError(e) } })
    const hash = () => setSlug(location.hash.replace(/^#\/demo\/?/, ''))
    window.addEventListener('hashchange', hash)
    return () => { cancelled = true; window.removeEventListener('hashchange', hash) }
  }, [])
  const selected = cases?.find(item => item.slug === slug)
  return <div className="demo-workspace"><div className="recorded-banner"><ShieldCheck size={18} /><span>Recorded demo · Read-only · No model requests while browsing</span></div>
    {error && <p role="alert" className="import-error">{error}</p>}
    {!cases ? <p role="status">Loading saved cases…</p> : selected ? <><button className="text-button gallery-back" onClick={() => { location.hash = '/demo' }}><ArrowLeft size={16} />All {cases.length} cases</button><CollectionAssessment key={selected.slug} id={selected.collection_id} readOnly {...props} /></> : <><p className="gallery-intro">Explore {cases.length} reviewed recordings of controlled failures. Every published case includes at least 500 log records, retained source evidence and a saved Jev → Groq investigation. Further recordings are published after review.</p><CaseCards cases={cases} onSelect={value => { location.hash = '/demo/' + value }} /></>}
  </div>
}

function LegacyAssessment({ run, ...props }: Props & { run: Run }) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const incidents = await api<Incident[]>('/incidents')
        const incident = incidents.find(item => item.run_id === run.id)
        if (!incident) return
        const next = await api<Detail>('/incidents/' + incident.id)
        if (cancelled) return
        setDetail(next)
        if (next.investigations.some(item => working.includes(item.state))) timer = setTimeout(refresh, 2000)
      } catch (e) { if (!cancelled) { setError(e instanceof Error ? e.message : 'Unable to load incident.'); props.onError(e) } }
    }
    void refresh()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [run.id, busy])
  const investigation = detail?.investigations[0]
  const { scope, error: evidenceError } = useReportEvidence(investigation)
  return <section className="detail-panel case-detail"><div className="import-coverage"><h2>Investigate this recording</h2><p>Jev triage → Groq investigation. This is an explicit model request.</p>{error && <p role="alert">{error}</p>}
    {detail && <button className="button primary" disabled={busy || working.includes(investigation?.state || '')} onClick={async () => {
      setBusy(true)
      try { await api('/incidents/' + detail.incident.id + '/investigations', { engine: 'groq', triage: true }) }
      catch (e) { setError(e instanceof Error ? e.message : 'Unable to investigate.'); props.onError(e) }
      finally { setBusy(false) }
    }}>Investigate</button>}</div><ReportView investigation={investigation} scope={scope} evidenceError={evidenceError} onOpen={props.onOpen} /></section>
}

export function ScenarioWorkspace(props: Props) {
  const [catalog, setCatalog] = useState<Scenario[]>([])
  const [choice, setChoice] = useState('rollout')
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState('')
  const [rows, setRows] = useState<Evidence[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('log')
  useEffect(() => {
    let cancelled = false
    api<Scenario[]>('/scenarios').then(value => { if (!cancelled) setCatalog(value) }).catch(props.onError)
    const refresh = () => api<Run[]>('/runs').then(value => { if (!cancelled) { setRuns(value); setSelected(current => current || value[0]?.id || '') } }).catch(e => { if (!cancelled) { setError(e.message); props.onError(e) } })
    const stop = poll(refresh, 2000)
    return () => { cancelled = true; stop() }
  }, [])
  const run = runs.find(item => item.id === selected)
  const running = !!run && active.includes(run.state)
  useEffect(() => {
    if (!selected) return
    let cancelled = false, cursor = 0
    let timer: ReturnType<typeof setTimeout>
    setRows([])
    const poll = async () => {
      try {
        const page = await api<{ evidence: Evidence[]; next_cursor: number; has_more: boolean }>('/runs/' + selected + '/evidence?after=' + cursor)
        if (cancelled) return
        cursor = page.next_cursor
        setRows(previous => Array.from(new Map([...previous, ...page.evidence].map(row => [row.id, row])).values()))
        if (page.has_more || running) timer = setTimeout(poll, page.has_more ? 0 : 2000)
      } catch (e) { if (!cancelled) { setError(e instanceof Error ? e.message : 'Unable to load evidence.'); props.onError(e) } }
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [selected, running])
  const chosen = catalog.find(item => item.id === choice)
  return <div className="live-workspace"><section className="lab-intro"><span className="eyebrow mint">CONTROLLED LOCAL EXECUTION</span><h2>Watch an incident unfold.</h2><p>Real requests, real failures, retained evidence. Choose a scenario to observe its baseline, failure and recovery.</p>
    <div className="lab-controls"><label>Scenario<select value={choice} onChange={e => setChoice(e.target.value)}>{catalog.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><button className="button mint-button" disabled={busy || runs.some(item => active.includes(item.state))} onClick={async () => {
      setBusy(true); setError('')
      try { const value = await api<{ id: string }>('/scenarios/' + choice + '/start', {}); setSelected(value.id); setRuns(await api<Run[]>('/runs')) }
      catch (e) { setError(e instanceof Error ? e.message : 'Unable to start.'); props.onError(e) }
      finally { setBusy(false) }
    }}><Play size={16} />{busy ? 'Starting…' : 'Run scenario'}</button></div><p>{chosen?.description}</p><p>Short, bounded runs. Model analysis starts only when you click Investigate after collection finishes.</p></section>
    {error && <p role="alert" className="import-error">{error}</p>}
    {!!runs.length && <div className="history-picker"><label htmlFor="lab-run">Saved run</label><select id="lab-run" value={selected} onChange={e => setSelected(e.target.value)}>{runs.map(item => <option key={item.id} value={item.id}>{catalog.find(meta => meta.id === item.scenario)?.title || item.scenario} · {item.created_at} · {item.state}</option>)}</select></div>}
    {run && <><div className="run-progress" role="status"><div><strong>{running ? 'Live capture' : 'Recorded execution'} · {run.stage || run.state}</strong><p>{run.requests} requests · {run.failures} failed responses · {rows.length} observations loaded</p>{run.error && <p>{run.error}</p>}</div>{running && <button className="text-button" onClick={async () => { try { await api('/scenarios/reset', {}) } catch (e) { props.onError(e) } }}><Square size={14} />Stop & reset</button>}</div>
      <section className="detail-panel"><div className="detail-tabs">{['log', 'span', 'metric'].map(kind => <button key={kind} className={tab === kind ? 'active' : ''} onClick={() => setTab(kind)}>{({ log: 'Logs', span: 'Traces', metric: 'Metrics' })[kind]}</button>)}</div><EvidenceTable evidence={rows} kind={tab} onOpen={props.onOpen} note="Captured observations arrive every two seconds during execution. Late observations retain their original timestamps." /></section>
      {!running && (run.collection_id ? <CollectionAssessment key={run.collection_id} id={run.collection_id} {...props} /> : <LegacyAssessment key={run.id} run={run} {...props} />)}
    </>}
  </div>
}
