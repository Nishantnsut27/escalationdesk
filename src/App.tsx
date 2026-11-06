import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Activity, ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronRight,
  CircleDot, Database, FileText, Fingerprint, GitBranch, Layers3, LoaderCircle, LockKeyhole,
  LogOut, Play, Radio, Search, Server, ShieldCheck, Square, Terminal, TriangleAlert, X,
} from 'lucide-react'
import { api, ApiError, setCsrf, type Detail, type Evidence, type Incident, type Investigation,
  type Overview, type Run, type Session, type User, type Report, type ReportEvidence } from './api'
import { ImportWorkspace } from './imports'
import { DemoWorkspace } from './scenarios'
import { ConnectionsWorkspace } from './connections'
import { SourcePreviews, TriagePanel, WorkflowLabel } from './triage'
import { Citations, EvidenceTimeline, FullTime, ObservationFields, SupplementPanel, useReportEvidence } from './reports'

const scenarioNames: Record<string, string> = { pool: 'Connection pool exhaustion', slow: 'Slow Payment processing', healthy: 'Healthy checkout' }
const activeStates = ['starting', 'running', 'resetting']
const investigationActiveStates = ['queued', 'running', 'quota_waiting']
const time = (value: string) => new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const shortId = (value: string) => value.slice(0, 6).toUpperCase()
function Spinner() { return <LoaderCircle size={16} className="spin" /> }
function Brand({ light = false }: { light?: boolean }) {
  return <div className={'brand ' + (light ? 'brand-light' : '')}><span className="brand-symbol"><Activity size={23} /></span><span>escalation<span className="brand-desk">desk</span></span></div>
}
function StateBadge({ state }: { state: string }) {
  const good = ['healthy', 'completed', 'resolved', 'supported'].includes(state)
  const bad = ['failed', 'open', 'error', 'interrupted'].includes(state)
  return <span className={'badge ' + (good ? 'good' : bad ? 'bad' : 'neutral')}><i />{state.replaceAll('_', ' ')}</span>
}

function Auth({ onSession }: { onSession: (session: Session) => void }) {
  const [signup, setSignup] = useState(false)
  const [demo, setDemo] = useState(false)
  const [demoAvailable, setDemoAvailable] = useState<boolean | null>(null)
  const [demoCount, setDemoCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!demo) return
    let cancelled = false
    setDemoAvailable(null)
    api<{ demo_available: boolean; demo_case_count: number }>('/health').then(value => { if (!cancelled) { setDemoAvailable(value.demo_available); setDemoCount(value.demo_case_count) } })
      .catch(() => { if (!cancelled) setError('Unable to check recorded-demo availability. Try again.') })
    return () => { cancelled = true }
  }, [demo])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const data = Object.fromEntries(new FormData(event.currentTarget))
    try { onSession(await api<Session>(demo ? '/auth/demo-login' : signup ? '/auth/signup' : '/auth/login', data)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in.') }
    finally { setBusy(false) }
  }
  return <div className="auth-page">
    <section className="auth-story">
      <Brand light />
      <div className="auth-message"><span className="eyebrow mint"><span className="live-dot" /> FOLLOW THE EVIDENCE</span>
        <h1>Understand the<br />failure.<br /><em>Find the cause.</em></h1>
        <p>A clear path from service symptoms to an evidence-backed explanation.</p>
        <div className="auth-flow">{['Connect', 'Collect', 'Investigate'].map((name, i) => <div key={name}><span><Server size={17} />{name}</span>{i < 2 && <ArrowRight size={17} />}</div>)}</div>
        <div className="story-note"><GitBranch size={18} /><span>Your application logs and traces.<br />Every conclusion has a source.</span></div>
      </div>
      <span className="auth-foot">ESCALATION DESK <span>LOCAL WORKSPACE / 01</span></span>
    </section>
    <section className="auth-form-side">
      <div className="auth-form-wrap">
        <span className="tiny-label">YOUR INVESTIGATION WORKSPACE</span>
        <h2>{demo ? 'Explore the evidence.' : signup ? 'Start with clarity.' : 'Welcome back.'}</h2>
        <p>{signup ? 'Create your account and follow your first incident.' : 'Sign in to your incidents, evidence and investigations.'}</p>
        <div className="auth-toggle"><button className={!signup && !demo ? 'selected' : ''} onClick={() => { setSignup(false); setDemo(false); setError('') }}>Sign in</button><button className={signup && !demo ? 'selected' : ''} onClick={() => { setSignup(true); setDemo(false); setError('') }}>Create account</button><button className={demo ? 'selected' : ''} onClick={() => { setDemo(true); setSignup(false); setError('') }}>Demo Account</button></div>
        <form onSubmit={submit}>
          {signup && <label>Your name<input name="name" autoComplete="name" placeholder="Alex Morgan" required maxLength={60} /></label>}
          {!demo && <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={200} /></label>}
          {demo && <p className="demo-password" role="status">{demoAvailable ? <>{demoCount} reviewed recorded cases. Read-only access.<br />Public password: <strong>Demo@login</strong></> : demoAvailable === false ? 'No reviewed demo recordings are installed. Use a regular account to connect Sentry or import logs.' : 'Checking recorded-demo availability…'}</p>}
          {(!demo || demoAvailable) && <label>Password<input name="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'} placeholder={signup ? 'At least 10 characters' : 'Enter your password'} required minLength={signup ? 10 : 1} maxLength={128} /></label>}
          {error && <div className="error-banner" role="alert"><TriangleAlert size={16} />{error}</div>}
          <button className="button primary full" disabled={busy || (demo && !demoAvailable)}>{busy ? <Spinner /> : signup ? 'Create account' : 'Sign in'}{!busy && <ArrowRight size={16} />}</button>
        </form>
        <div className="privacy"><ShieldCheck size={17} /><span>Your investigations stay in your account.<br />Passwords are securely hashed.</span></div>
      </div>
      <div className="auth-local"><span className="live-dot" /> Running on your local machine</div>
    </section>
  </div>
}

export function ServiceMap({ overview, evidence, report, evidenceReady = true, services }: { overview: Overview | null; evidence: Evidence[]; report?: Report | null; evidenceReady?: boolean; services?: string[] }) {
  return <div className="service-map">
    {(services || ['gateway', 'order', 'payment']).map((service, i) => {
      const errors = evidence.filter(row => row.service === service && (row.status === 'error' || row.level === 'error' || (row.http_status ?? 0) >= 400)).length
      const up = overview?.services.find(item => item.name === service)?.status === 'healthy'
      return <div className="service-node-wrap" key={service}>
        <div className="service-node"><span className={'node-icon ' + (errors ? 'node-error' : '')}>{service === 'payment' ? <Database size={20} /> : <Server size={20} />}</span>
          <div><strong>{service}</strong>{report && <><span>Reported origin: {report.origin === service ? report.assessment === 'inconclusive' ? 'proposed; inconclusive' : 'yes' : 'not identified here'}</span><span>Reported affected: {report.affected_services.includes(service) ? 'yes' : 'not listed'}</span></>}
            <span>Observed errors: {evidenceReady ? `${errors}${!errors ? ' in this evidence scope' : ''}` : 'evidence not loaded'}</span><span>Current reachability: {services ? 'not measured in this evidence view' : up ? 'reachable' : 'unavailable'}</span></div>
          <span className={'service-dot ' + (up ? 'up' : '')} aria-hidden="true" />
        </div>{!services && i < 2 && <ArrowRight className="connector" size={18} />}
      </div>
    })}
  </div>
}

export function EvidenceTable({ evidence, kind, onOpen, note }: { evidence: Evidence[]; kind: string; onOpen: (id: string) => void; note?: string }) {
  const [query, setQuery] = useState('')
  const rows = evidence.filter(row => row.kind === kind && (!query || JSON.stringify(row).toLowerCase().includes(query.toLowerCase())))
  return <div className="evidence-section">
    <p className="raw-evidence-note">{note || "Raw run evidence · Up to 200 observations across all types. This view may include sources not used by the selected report."}</p>
    <div className="evidence-toolbar"><span>{rows.length} {kind === 'span' ? 'measured spans' : kind === 'metric' ? 'metric samples' : 'log entries'}</span><label className="search"><Search size={15} /><input aria-label="Filter evidence" placeholder="Filter evidence…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
    {rows.length ? <div className="table-scroll"><table><thead><tr><th>Time</th><th>Service</th><th>{kind === 'span' ? 'Operation' : kind === 'metric' ? 'Observation' : 'Message'}</th><th>Source</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}><td className="mono"><FullTime value={row.timestamp} /></td><td><span className="service-label">{row.service}</span></td>
        <td>{kind === 'log' ? <><span className={'log-level ' + row.level}>{row.level}</span>{row.message}</> : kind === 'span' ? <div className="span-cell"><span>{row.name}</span><span className={row.status === 'error' ? 'red-text mono' : 'mono'}>{row.duration_ms?.toFixed(1)} ms</span></div> : <div className="metric-values">{Object.entries(row).filter(([key, value]) => typeof value === 'number' && !['duration_ms'].includes(key)).map(([key, value]) => <span key={key}>{key.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div>}</td>
        <td><button className="source-button" onClick={() => onOpen(row.id)} aria-label={'Open evidence ' + shortId(row.id)}>{shortId(row.id)}<ArrowUpRight size={12} /></button></td></tr>)}</tbody></table></div> : <div className="small-empty">No matching evidence in this view.</div>}
  </div>
}

function InvestigationActivity({ investigation, onOpen }: { investigation: Investigation; onOpen: (id: string) => void }) {
  return <div className="investigation-activity">
    {!!investigation.activity.length && <details className="tool-details" open={investigationActiveStates.includes(investigation.state)}><summary>{investigationActiveStates.includes(investigation.state) ? 'Completed tool activity' : 'Recorded tool activity'} <span>{investigation.activity.length} tool calls</span></summary>{investigation.activity.map((entry, i) => <div className="activity-entry" key={i}><Terminal size={14} /><code>{entry.tool}</code><span>{entry.count} sources{entry.omitted_groups ? ` · ${entry.omitted_groups} groups omitted` : ''}{entry.query_limit_reached ? ' · query limit reached' : ''}</span><FullTime value={entry.timestamp} /><div className="evidence-citations">{entry.evidence_ids?.map(id => <button key={id} onClick={() => onOpen(id)} aria-label={'Open collected evidence ' + shortId(id)}>{shortId(id)}<ArrowUpRight size={12} /></button>)}</div></div>)}</details>}
    {!!investigation.usage.diagnostics?.length && <details className="tool-details"><summary>Provider diagnostics <span>{investigation.usage.requests} HTTP attempts</span></summary><p className="usage-note">{investigation.usage.total_tokens ?? 0} provider-reported tokens · {investigation.usage.estimated_input_tokens ?? 0} estimated input tokens for scheduling · {investigation.usage.quota_wait_seconds ?? 0}s waited</p>{investigation.usage.diagnostics.map(entry => <div key={entry.attempt}><span>#{entry.attempt} {entry.phase}</span><span>{entry.status ?? 'In progress'}{entry.limit_category ? ' · ' + entry.limit_category : ''}</span><span>{entry.input_bytes} bytes · {entry.duration_ms ?? 0}ms</span>{entry.retry_at && <span>Retry {time(entry.retry_at)}</span>}</div>)}</details>}
  </div>
}

export function ReportView({ investigation, onOpen, scope = null, evidenceError, onUpdate, readOnly = false }: { investigation: Investigation | undefined; onOpen: (id: string) => void; scope?: ReportEvidence | null; evidenceError?: string; onUpdate?: (value: Investigation) => void; readOnly?: boolean }) {
  const [clock, setClock] = useState(Date.now())
  useEffect(() => {
    if (investigation?.state !== 'quota_waiting') return
    setClock(Date.now())
    const id = setInterval(() => setClock(Date.now()), 1000)
    return () => clearInterval(id)
  }, [investigation?.state])
  if (!investigation) return <div className="report-empty"><span className="empty-icon"><Fingerprint size={30} /></span><h3>Let the evidence tell the story.</h3><p>Start an investigation to connect the failure signals<br />and get a source-linked assessment.</p></div>
  if (investigationActiveStates.includes(investigation.state)) {
    const waiting = investigation.state === 'quota_waiting'
    const queued = investigation.state === 'queued'
    const reporting = investigation.usage.progress === 'reporting'
    const seconds = investigation.usage.retry_at ? Math.max(0, Math.ceil((Date.parse(investigation.usage.retry_at) - clock) / 1000)) : null
    const triaging = investigation.usage.progress === 'triaging'
    const heading = waiting ? 'Waiting for Groq quota' : queued ? 'Investigation queued' : triaging ? 'Initial triage' : reporting ? 'Writing the assessment' : 'Investigating evidence'
    const description = waiting ? investigation.usage.wait_reason : queued ? 'Your investigation will start automatically.' : triaging ? 'Jev is classifying the observations and their diagnostic usefulness.' : reporting ? 'Preparing the report from the collected evidence.' : 'Reviewing logs, metrics and traces for this incident.'
    return <><div className="report-empty report-progress" role="status">
      <div className="progress-indicator"><Spinner /><StateBadge state={investigation.state} /></div>
      <h3>{heading}</h3><p>{description}</p>
      {waiting && <p>{seconds !== null ? `Resuming in ${seconds}s · Expected retry ${time(investigation.usage.retry_at!)}` : 'Waiting for provider retry timing.'}<br />Collected evidence is preserved. This investigation resumes automatically.</p>}
    </div><TriagePanel triage={investigation.usage.triage} onOpen={onOpen} /><InvestigationActivity investigation={investigation} onOpen={onOpen} /></>
  }
  if (!investigation.report) return <><div className="report-empty" role="alert"><TriangleAlert size={28} /><h3>Investigation {investigation.state}</h3><p>{investigation.error || 'Start a new investigation to try again.'}</p><p>Retry creates a new investigation and keeps this attempt in saved history.</p></div><TriagePanel triage={investigation.usage.triage} onOpen={onOpen} /><InvestigationActivity investigation={investigation} onOpen={onOpen} /></>
  const report = investigation.report
  return <div className="report-content">
    <div className="report-meta"><span className="eyebrow"><Fingerprint size={14} /> {investigation.engine === 'rules' ? 'RECORDED EVIDENCE RULES ANALYSIS' : 'RECORDED GROQ INVESTIGATION'}</span><StateBadge state={report.assessment} /></div>
    <h3>{report.origin === 'unknown' ? 'Cause not established' : report.assessment === 'inconclusive' ? 'Possible cause — limited evidence' : 'Assessed cause — supported by evidence'}</h3>
    {report.origin !== 'unknown' && <p className="assessment-origin">Reported origin: <strong>{report.origin}</strong></p>}
    <p className="report-summary">{report.summary}</p>
    <SourcePreviews ids={report.evidence_ids} scope={scope} error={evidenceError} onOpen={onOpen} />
    <TriagePanel triage={investigation.usage.triage} onOpen={onOpen} />
    <section className="report-hypotheses"><h4>Reported hypotheses</h4>{report.hypotheses.length ? report.hypotheses.map((item, index) => <div key={index}><p>{item.claim}</p><Citations ids={item.evidence_ids} onOpen={onOpen} /></div>) : <p>No hypotheses recorded.</p>}</section>
    <div className="recommendations"><span className="tiny-label">RECOMMENDED NEXT STEPS</span>{report.recommendations.map((text, i) => <div key={text}><span className="step-number">{String(i + 1).padStart(2, '0')}</span><p>{text}</p></div>)}</div>
    <div className="limitation"><CircleDot size={17} /><div><strong>What the evidence does not establish</strong>{report.limitations.map(item => <p key={item}>{item}</p>)}</div></div>
    <div className="report-footer"><span>{investigation.engine === 'rules' ? 'Evidence rules v1 · No model call' : investigation.usage.model} · Original usage: {investigation.usage.requests ?? 0} HTTP attempts · {investigation.usage.total_tokens ?? 0} provider-reported tokens</span><span>{((investigation.usage.duration_ms || 0) / 1000).toFixed(2)}s · Saved <FullTime value={investigation.completed_at || investigation.created_at} /></span></div>
    <EvidenceTimeline scope={scope} error={evidenceError} onOpen={onOpen} />
    <InvestigationActivity investigation={investigation} onOpen={onOpen} />
    {!readOnly && investigation.engine === 'groq' && investigation.state === 'completed' && <SupplementPanel key={investigation.id} investigation={investigation} onOpen={onOpen} onUpdate={onUpdate} />}
  </div>
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [initializing, setInitializing] = useState(true)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [runs, setRuns] = useState<Run[]>([])
  const [incidents, setIncidents] = useState<Incident[]>([])
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<Detail | null>(null)
  const [selectedRun, setSelectedRun] = useState('')
  const [runEvidence, setRunEvidence] = useState<Evidence[]>([])
  const [tab, setTab] = useState('assessment')
  const [nav, setNav] = useState(() => window.location.hash.startsWith('#connections') ? 'connections' : 'collections')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [openedEvidence, setOpenedEvidence] = useState<Evidence | null>(null)
  const [historyId, setHistoryId] = useState('')
  const modalClose = useRef<HTMLButtonElement>(null)
  const modalReturn = useRef<HTMLElement | null>(null)
  const accountEpoch = useRef(0)
  const pendingRun = useRef('')

  const session = (value: Session) => { accountEpoch.current++; setUser(value.user); setCsrf(value.csrf); setOpenedEvidence(null); setHistoryId(''); setError(''); setOverview(null); setRuns([]); setIncidents([]); setSelected(''); setDetail(null); setSelectedRun(''); setRunEvidence([]); pendingRun.current = '' }
  const failure = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) { accountEpoch.current++; setUser(null); setOpenedEvidence(null); setCsrf(''); setSelected(''); setDetail(null) }
    setError(e instanceof Error ? e.message : 'Something went wrong. Try again.')
  }, [])
  useEffect(() => { api<Session>('/auth/me').then(session).catch(e => { if (!(e instanceof ApiError && e.status === 401)) failure(e) }).finally(() => setInitializing(false)) }, [failure])

  const refresh = useCallback(async () => {
    const epoch = accountEpoch.current
    const [nextOverview, nextRuns, nextIncidents] = await Promise.all([api<Overview>('/overview'), api<Run[]>('/runs'), api<Incident[]>('/incidents')])
    if (epoch !== accountEpoch.current) return
    setOverview(nextOverview); setRuns(nextRuns.filter(run => !run.collection_id)); setIncidents(nextIncidents)
    const newIncident = nextIncidents.find(item => item.run_id === pendingRun.current)
    if (newIncident) { setSelected(newIncident.id); setDetail(null); setHistoryId(''); setTab('assessment'); pendingRun.current = '' }
    else setSelected(value => value || nextIncidents[0]?.id || '')
  }, [])
  useEffect(() => {
    if (!user || user.read_only) return
    refresh().catch(failure)
    const id = setInterval(() => refresh().catch(failure), 2000)
    return () => clearInterval(id)
  }, [user, refresh, failure])
  useEffect(() => {
    if (!user || user.read_only || !selected) { setDetail(null); return }
    let cancelled = false
    const update = () => api<Detail>('/incidents/' + selected).then(value => { if (!cancelled) setDetail(value) }).catch(e => { if (!cancelled) failure(e) })
    update()
    const id = setInterval(update, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [user, selected, failure])
  const latestRunId = selectedRun || runs[0]?.id
  useEffect(() => {
    if (!user || user.read_only || !latestRunId) { setRunEvidence([]); return }
    let cancelled = false
    const update = () => api<{ evidence: Evidence[] }>('/runs/' + latestRunId).then(value => { if (!cancelled) setRunEvidence(value.evidence) }).catch(e => { if (!cancelled) failure(e) })
    update()
    const id = setInterval(update, 2000)
    return () => { cancelled = true; clearInterval(id) }
  }, [user, latestRunId, failure])
  useEffect(() => {
    if (!openedEvidence) return
    modalClose.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenedEvidence(null)
      if (e.key === 'Tab') {
        const focusable = Array.from(modalClose.current?.closest('section')?.querySelectorAll<HTMLElement>('button, summary, [tabindex="0"]') || [])
        const first = focusable[0], last = focusable.at(-1)
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', handler)
    return () => { document.removeEventListener('keydown', handler); modalReturn.current?.focus() }
  }, [openedEvidence])

  const selectedIncidentRun = runs.find(run => run.id === detail?.incident.run_id)
  const evidence = selectedRun || !detail ? runEvidence : detail.evidence
  const investigation = detail?.investigations.find(item => item.id === historyId) || detail?.investigations[0]
  const { scope, error: evidenceError } = useReportEvidence(selectedRun ? undefined : investigation)
  const investigating = detail?.investigations.some(item => investigationActiveStates.includes(item.state))
  async function investigate() {
    if (!selected) return
    setBusy('investigate'); setError(''); setHistoryId(''); setTab('assessment'); setSelectedRun('')
    try {
      await api('/incidents/' + selected + '/investigations', { engine: 'groq', triage: true })
      setDetail(await api<Detail>('/incidents/' + selected))
    } catch (e) { failure(e) } finally { setBusy('') }
  }
  async function openEvidence(id: string) {
    modalReturn.current = document.activeElement as HTMLElement | null
    const epoch = accountEpoch.current
    try { const value = await api<Evidence>('/evidence/' + id); if (epoch === accountEpoch.current) setOpenedEvidence(value) } catch (e) { if (epoch === accountEpoch.current) failure(e) }
  }
  async function logout() {
    try { await api('/auth/logout', {}); accountEpoch.current++; setUser(null); setOpenedEvidence(null); setCsrf(''); setDetail(null); setSelected(''); setRuns([]); setIncidents([]); setOverview(null); setSelectedRun(''); setRunEvidence([]); pendingRun.current = '' } catch (e) { failure(e) }
  }
  function navigate(value: string) {
    setNav(value); setSelectedRun(''); setHistoryId('')
    if (value === 'evidence') setTab('log')
    else if (value === 'map') setTab('span')
    else setTab('assessment')
  }
  if (initializing) return <div className="initializing"><Brand /><Spinner /><span>Opening your workspace…</span></div>
  if (!user) return <Auth onSession={session} />

  return <div className="app-shell">
    <aside className="sidebar">
      <Brand light />
      <div className="workspace-switch"><span className="workspace-icon"><Layers3 size={18} /></span><div><strong>{user.read_only ? 'Demo Account' : 'Personal workspace'}</strong><span>Local environment</span></div><LockKeyhole size={13} /></div>
      <span className="nav-label">WORKSPACE</span>
      <nav>
        {(user.read_only ? [{ key: 'demo', label: 'Recorded cases', icon: Layers3 }] : [{ key: 'collections', label: 'Saved evidence', icon: Layers3 }, { key: 'connections', label: 'Connections', icon: GitBranch }, { key: 'imports', label: 'Import logs', icon: FileText }, { key: 'history', label: 'Recorded history', icon: Fingerprint }]).map(({ key, label, icon: Icon }) => <button key={key} className={nav === key ? 'active' : ''} onClick={() => navigate(key)}><Icon size={18} />{label}{key === 'overview' && <span className="nav-count">{incidents.length}</span>}</button>)}
      </nav>

      <div className="sidebar-bottom"><div className="local-card"><Radio size={18} /><div><strong>Connected locally</strong><p>Saved locally. Groq uses selected evidence.</p></div></div><div className="user-row"><span className="avatar">{user.name.charAt(0).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.email}</span></div><button onClick={logout} title="Sign out" aria-label="Sign out"><LogOut size={17} /></button></div></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div><span>Workspace</span><ChevronRight size={13} /><strong>{user.read_only ? 'Recorded demo' : nav === 'connections' ? 'Connections' : nav === 'imports' ? 'Import logs' : nav === 'history' ? 'Recorded history' : 'Saved evidence'}</strong></div><span className="connection-pill"><i />{user.read_only ? 'Recorded / Read-only' : 'Account-owned evidence'}</span></header>
      <main>
        <div className="page-heading"><div><span className="eyebrow">OBSERVE. INVESTIGATE. UNDERSTAND.</span><h1>{user.read_only ? 'Follow the recorded evidence.' : nav === 'connections' ? 'Connect your application' : nav === 'imports' ? 'Import your application logs' : nav === 'history' ? 'Your recorded history' : 'Your evidence. Your investigations.'}</h1><p>Review application observations, investigate symptoms and inspect the sources behind each conclusion.</p></div><span className="date-label">{new Date().toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span></div>

        {error && <div className="error-banner global-error" role="alert"><TriangleAlert size={17} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}

        {user.read_only ? <DemoWorkspace key={user.id} onOpen={openEvidence} onError={failure} /> : nav === 'connections' ? <ConnectionsWorkspace key={user.id} overview={overview} onOpen={openEvidence} onError={failure} ReportComponent={ReportView} MapComponent={ServiceMap} TableComponent={EvidenceTable} /> : (nav === 'imports' || nav === 'collections') ? <ImportWorkspace showImport={nav === 'imports'} key={user.id + nav} overview={overview} onOpen={openEvidence} onError={failure} ReportComponent={ReportView} MapComponent={ServiceMap} TableComponent={EvidenceTable} /> : <>
        <div className="stats-grid">
          {[{ label: 'Requests observed', value: overview?.counts.requests || 0, sub: 'Across saved runs', icon: Activity, color: 'teal' },
            { label: 'Failed requests', value: overview?.counts.failures || 0, sub: 'Recorded failed responses', icon: TriangleAlert, color: 'orange' },
            { label: 'Incidents detected', value: overview?.counts.incidents || 0, sub: 'Correlated service failures', icon: GitBranch, color: 'purple' },
            { label: 'Evidence captured', value: overview?.counts.evidence || 0, sub: 'Logs, spans & metric samples', icon: FileText, color: 'blue' }].map(stat => <div className="stat-card" key={stat.label}><div><span>{stat.label}</span><span className={'stat-icon ' + stat.color}><stat.icon size={17} /></span></div><strong>{stat.value.toLocaleString()}</strong><p>{stat.sub}</p></div>)}
        </div>

        {!!evidence.length && <section className="topology-panel"><div className="section-heading"><h2><GitBranch size={16} />Services in saved evidence</h2><span>Recorded observations</span></div><ServiceMap overview={null} services={[...new Set(evidence.map(row => row.service))]} evidence={!selectedRun && investigation?.report ? scope?.evidence || [] : evidence} report={!selectedRun ? investigation?.report : null} evidenceReady={!!selectedRun || !investigation?.report || !!scope} /></section>}

        <div className="investigation-workspace">
          <section className="incident-panel">
            <div className="section-heading"><h2>Incidents <span className="count">{incidents.length}</span></h2><span>Saved records</span></div>
            <div className="incident-list">
              {incidents.length ? incidents.map(incident => {
                const run = runs.find(item => item.id === incident.run_id)
                return <button key={incident.id} className={'incident-item ' + (selected === incident.id && !selectedRun ? 'selected' : '')} onClick={() => { setSelected(incident.id); setDetail(null); setSelectedRun(''); setHistoryId(''); setTab(nav === 'evidence' ? 'log' : 'assessment') }}>
                  <div><span className="incident-id">INC-{shortId(incident.id)}</span><StateBadge state={incident.state} /></div><h3>{incident.title}</h3><footer><span>{time(incident.created_at)}</span><span>{run?.failures || 3} failed requests<ChevronRight size={13} /></span></footer>
                </button>
              }) : <div className="incident-empty"><CheckCircle2 size={26} /><strong>No incidents yet</strong><p>Your previously recorded incidents appear here. Connect Sentry or import logs for new investigations.</p></div>}
            </div>
            <div className="runs-heading"><h3>Recent runs</h3><span>{runs.length}</span></div>
            <div className="recent-runs">{runs.length ? runs.slice(0, 6).map(run => <button className={selectedRun === run.id ? 'selected' : ''} key={run.id} onClick={() => { setSelectedRun(run.id); setTab('log') }}><span className={'run-indicator ' + (run.failures ? 'warn' : '')}>{run.failures ? <Activity size={14} /> : <Check size={14} />}</span><div><strong>{scenarioNames[run.scenario] || run.scenario}</strong><span>{time(run.created_at)} · {run.state}</span></div><ChevronRight size={13} /></button>) : <p className="run-placeholder">No previous runs in this account.</p>}</div>
          </section>

          <section className="detail-panel">
            <div className="detail-heading"><div><span className="tiny-label">{selectedRun ? 'RUN-' + shortId(selectedRun) : selected ? 'INC-' + shortId(selected) : 'INVESTIGATION WORKSPACE'}</span><h2>{selectedRun ? scenarioNames[runs.find(run => run.id === selectedRun)?.scenario || 'healthy'] : selected ? 'From signals to a clear assessment.' : 'Every incident has a story.'}</h2></div>
              {selected && !selectedRun && <div className="investigate-controls"><WorkflowLabel providers={overview?.providers} /><button className="button primary" onClick={investigate} disabled={!overview?.providers.groq || !!busy || investigating || activeStates.includes(selectedIncidentRun?.state || '')}>{investigating || busy === 'investigate' ? <Spinner /> : <Fingerprint size={15} />}{investigating ? 'Investigating' : investigation && ['failed', 'interrupted'].includes(investigation.state) ? 'Retry investigation' : 'Investigate'}</button></div>}
            </div>
            {(selected || runs.length > 0) ? <>
              <div className="detail-tabs">{(selectedRun || !selected ? [{ key: 'log', name: 'Logs' }, { key: 'span', name: 'Traces' }, { key: 'metric', name: 'Metrics' }] : [{ key: 'assessment', name: 'Assessment' }, { key: 'log', name: 'Logs' }, { key: 'span', name: 'Traces' }, { key: 'metric', name: 'Metrics' }]).map(item => <button className={tab === item.key ? 'active' : ''} key={item.key} onClick={() => setTab(item.key)}>{item.name}{item.key !== 'assessment' && <span>{evidence.filter(row => row.kind === item.key).length}</span>}</button>)}</div>
              {tab === 'assessment' && !selectedRun ? <>
                {detail && detail.investigations.length > 1 && <div className="history-picker"><label htmlFor="history">Saved investigation</label><select id="history" value={investigation?.id || ''} onChange={e => setHistoryId(e.target.value)}>{detail.investigations.map(item => <option key={item.id} value={item.id}>{item.created_at} · {item.engine} · {item.state}</option>)}</select></div>}
                <ReportView investigation={investigation} onOpen={openEvidence} scope={scope} evidenceError={evidenceError} onUpdate={value => setDetail(current => current ? { ...current, investigations: current.investigations.map(item => item.id === value.id ? { ...item, supplements: value.supplements } : item) } : current)} />
              </> : <EvidenceTable key={tab + latestRunId + selected} evidence={evidence} kind={tab === 'assessment' ? 'log' : tab} onOpen={openEvidence} />}
            </> : <div className="workspace-empty"><div className="empty-orbit"><Search size={29} /><span><FileText size={14} /></span></div><h3>Your workspace is ready.</h3><p>Connect Sentry or import application logs.<br />Review the evidence and investigate when you are ready.</p><div className="empty-steps"><span><b>01</b> Import evidence</span><ArrowRight size={13} /><span><b>02</b> Inspect evidence</span><ArrowRight size={13} /><span><b>03</b> Investigate</span></div></div>}
          </section>
        </div>
        </>}
        <footer className="page-footer"><span><ShieldCheck size={13} />Account-scoped evidence · Stored locally</span><span>Escalation Desk <span className="footer-slash">/</span> Proof of concept</span></footer>
      </main>
    </div>
    {openedEvidence && <div className="modal-backdrop" onClick={() => setOpenedEvidence(null)}><section className="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="evidence-title" onClick={e => e.stopPropagation()}><header><div><span className="tiny-label">RECORDED SOURCE OBSERVATION</span><h2 id="evidence-title">Evidence {shortId(openedEvidence.id)}</h2></div><button ref={modalClose} aria-label="Close evidence" onClick={() => setOpenedEvidence(null)}><X size={20} /></button></header><div className="modal-meta"><span className="service-label">{openedEvidence.service}</span><span>{openedEvidence.kind}</span><FullTime value={openedEvidence.timestamp} /></div><div className="modal-body"><ObservationFields row={openedEvidence} />{openedEvidence.original && <details><summary>Original imported content (redacted)</summary><pre>{openedEvidence.original}</pre></details>}<details><summary>Original recorded JSON</summary><pre>{JSON.stringify(openedEvidence, null, 2)}</pre></details></div><footer><LockKeyhole size={13} />Stored observation in your evidence run.</footer></section></div>}
  </div>
}

