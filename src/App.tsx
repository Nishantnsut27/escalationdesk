import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Activity, ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronRight,
  CircleDot, Database, FileText, Fingerprint, GitBranch, Layers3, LoaderCircle, LockKeyhole,
  LogOut, Play, Radio, Search, Server, ShieldCheck, Square, Terminal, TriangleAlert, X,
} from 'lucide-react'
import { api, ApiError, setCsrf, type Detail, type Evidence, type Incident, type Investigation,
  type Overview, type Run, type Session, type User } from './api'

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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const data = Object.fromEntries(new FormData(event.currentTarget))
    try { onSession(await api<Session>(signup ? '/auth/signup' : '/auth/login', data)) }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to sign in.') }
    finally { setBusy(false) }
  }
  return <div className="auth-page">
    <section className="auth-story">
      <Brand light />
      <div className="auth-message"><span className="eyebrow mint"><span className="live-dot" /> FOLLOW THE EVIDENCE</span>
        <h1>Understand the<br />failure.<br /><em>Find the cause.</em></h1>
        <p>A clear path from service symptoms to an evidence-backed explanation.</p>
        <div className="auth-flow">{['Gateway', 'Order', 'Payment'].map((name, i) => <div key={name}><span><Server size={17} />{name}</span>{i < 2 && <ArrowRight size={17} />}</div>)}</div>
        <div className="story-note"><GitBranch size={18} /><span>Real service requests. Connected traces.<br />Every conclusion has a source.</span></div>
      </div>
      <span className="auth-foot">ESCALATION DESK <span>LOCAL WORKSPACE / 01</span></span>
    </section>
    <section className="auth-form-side">
      <div className="auth-form-wrap">
        <span className="tiny-label">YOUR INVESTIGATION WORKSPACE</span>
        <h2>{signup ? 'Start with clarity.' : 'Welcome back.'}</h2>
        <p>{signup ? 'Create your account and follow your first incident.' : 'Sign in to your incidents, evidence and investigations.'}</p>
        <div className="auth-toggle"><button className={!signup ? 'selected' : ''} onClick={() => { setSignup(false); setError('') }}>Sign in</button><button className={signup ? 'selected' : ''} onClick={() => { setSignup(true); setError('') }}>Create account</button></div>
        <form onSubmit={submit}>
          {signup && <label>Your name<input name="name" autoComplete="name" placeholder="Alex Morgan" required maxLength={60} /></label>}
          <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required maxLength={200} /></label>
          <label>Password<input name="password" type="password" autoComplete={signup ? 'new-password' : 'current-password'} placeholder={signup ? 'At least 10 characters' : 'Enter your password'} required minLength={signup ? 10 : 1} maxLength={128} /></label>
          {error && <div className="error-banner" role="alert"><TriangleAlert size={16} />{error}</div>}
          <button className="button primary full" disabled={busy}>{busy ? <Spinner /> : signup ? 'Create account' : 'Sign in'}{!busy && <ArrowRight size={16} />}</button>
        </form>
        <div className="privacy"><ShieldCheck size={17} /><span>Your investigations stay in your account.<br />Passwords are securely hashed.</span></div>
      </div>
      <div className="auth-local"><span className="live-dot" /> Running on your local machine</div>
    </section>
  </div>
}

function ServiceMap({ overview, evidence }: { overview: Overview | null; evidence: Evidence[] }) {
  return <div className="service-map">
    {['gateway', 'order', 'payment'].map((service, i) => {
      const errors = evidence.filter(row => row.service === service && row.kind === 'span' && row.status === 'error').length
      const up = overview?.services.find(item => item.name === service)?.status === 'healthy'
      return <div className="service-node-wrap" key={service}>
        <div className="service-node"><span className={'node-icon ' + (errors ? 'node-error' : '')}>{service === 'payment' ? <Database size={20} /> : <Server size={20} />}</span>
          <div><strong>{service}</strong><span>{errors ? errors + ' error spans in run' : 'Independent service'}</span></div>
          <span className={'service-dot ' + (up ? 'up' : '')} title={up ? 'Service reachable now' : 'Service unreachable'} />
        </div>{i < 2 && <ArrowRight className="connector" size={18} />}
      </div>
    })}
  </div>
}

function EvidenceTable({ evidence, kind, onOpen }: { evidence: Evidence[]; kind: string; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState('')
  const rows = evidence.filter(row => row.kind === kind && (!query || JSON.stringify(row).toLowerCase().includes(query.toLowerCase())))
  return <div className="evidence-section">
    <div className="evidence-toolbar"><span>{rows.length} {kind === 'span' ? 'measured spans' : kind === 'metric' ? 'metric samples' : 'log entries'}</span><label className="search"><Search size={15} /><input aria-label="Filter evidence" placeholder="Filter evidence…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
    {rows.length ? <div className="table-scroll"><table><thead><tr><th>Time</th><th>Service</th><th>{kind === 'span' ? 'Operation' : kind === 'metric' ? 'Observation' : 'Message'}</th><th>Source</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}><td className="mono">{time(row.timestamp)}</td><td><span className="service-label">{row.service}</span></td>
        <td>{kind === 'log' ? <><span className={'log-level ' + row.level}>{row.level}</span>{row.message}</> : kind === 'span' ? <div className="span-cell"><span>{row.name}</span><span className={row.status === 'error' ? 'red-text mono' : 'mono'}>{row.duration_ms?.toFixed(1)} ms</span></div> : <div className="metric-values">{Object.entries(row).filter(([key, value]) => typeof value === 'number' && !['duration_ms'].includes(key)).map(([key, value]) => <span key={key}>{key.replaceAll('_', ' ')} <b>{String(value)}</b></span>)}</div>}</td>
        <td><button className="source-button" onClick={() => onOpen(row.id)} aria-label={'Open evidence ' + shortId(row.id)}>{shortId(row.id)}<ArrowUpRight size={12} /></button></td></tr>)}</tbody></table></div> : <div className="small-empty">No matching evidence in this view.</div>}
  </div>
}

function InvestigationActivity({ investigation, onOpen }: { investigation: Investigation; onOpen: (id: string) => void }) {
  return <div className="investigation-activity">
    {!!investigation.activity.length && <details className="tool-details" open={investigationActiveStates.includes(investigation.state)}><summary>{investigationActiveStates.includes(investigation.state) ? 'Completed tool activity' : 'Recorded tool activity'} <span>{investigation.activity.length} tool calls</span></summary>{investigation.activity.map((entry, i) => <div className="activity-entry" key={i}><Terminal size={14} /><code>{entry.tool}</code><span>{entry.count} sources{entry.omitted_groups ? ` · ${entry.omitted_groups} groups omitted` : ''}{entry.query_limit_reached ? ' · query limit reached' : ''}</span><time>{time(entry.timestamp)}</time><div className="evidence-citations">{entry.evidence_ids?.map(id => <button key={id} onClick={() => onOpen(id)} aria-label={'Open collected evidence ' + shortId(id)}>{shortId(id)}<ArrowUpRight size={12} /></button>)}</div></div>)}</details>}
    {!!investigation.usage.diagnostics?.length && <details className="tool-details"><summary>Provider diagnostics <span>{investigation.usage.requests} HTTP attempts</span></summary><p className="usage-note">{investigation.usage.total_tokens ?? 0} provider-reported tokens · {investigation.usage.estimated_input_tokens ?? 0} estimated input tokens for scheduling · {investigation.usage.quota_wait_seconds ?? 0}s waited</p>{investigation.usage.diagnostics.map(entry => <div key={entry.attempt}><span>#{entry.attempt} {entry.phase}</span><span>{entry.status ?? 'In progress'}{entry.limit_category ? ' · ' + entry.limit_category : ''}</span><span>{entry.input_bytes} bytes · {entry.duration_ms ?? 0}ms</span>{entry.retry_at && <span>Retry {time(entry.retry_at)}</span>}</div>)}</details>}
  </div>
}

export function ReportView({ investigation, onOpen }: { investigation: Investigation | undefined; onOpen: (id: string) => void }) {
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
    const heading = waiting ? 'Waiting for Groq quota' : queued ? 'Investigation queued' : reporting ? 'Writing the assessment' : 'Reviewing the evidence'
    const description = waiting ? investigation.usage.wait_reason : queued ? 'Your investigation will start automatically.' : reporting ? 'Preparing the report from the collected evidence.' : 'Reviewing logs, metrics and traces for this incident.'
    return <><div className="report-empty report-progress" role="status">
      <div className="progress-indicator"><Spinner /><StateBadge state={investigation.state} /></div>
      <h3>{heading}</h3><p>{description}</p>
      {waiting && <p>{seconds !== null ? `Resuming in ${seconds}s · Expected retry ${time(investigation.usage.retry_at!)}` : 'Waiting for provider retry timing.'}<br />Collected evidence is preserved. This investigation resumes automatically.</p>}
    </div><InvestigationActivity investigation={investigation} onOpen={onOpen} /></>
  }
  if (!investigation.report) return <><div className="report-empty" role="alert"><TriangleAlert size={28} /><h3>Investigation {investigation.state}</h3><p>{investigation.error || 'Start a new investigation to try again.'}</p><p>Retry creates a new investigation and keeps this attempt in saved history.</p></div><InvestigationActivity investigation={investigation} onOpen={onOpen} /></>
  const report = investigation.report
  return <div className="report-content">
    <div className="report-meta"><span className="eyebrow"><Fingerprint size={14} /> {investigation.engine === 'rules' ? 'DETERMINISTIC EVIDENCE ANALYSIS' : 'RECORDED GROQ INVESTIGATION'}</span><StateBadge state={report.assessment} /></div>
    <h3>{report.origin === 'unknown' ? 'More evidence is needed.' : 'The failure originates in ' + report.origin.charAt(0).toUpperCase() + report.origin.slice(1) + '.'}</h3>
    <p className="report-summary">{report.summary}</p>
    <div className="evidence-citations"><span>Supporting evidence</span>{report.evidence_ids.map(id => <button onClick={() => onOpen(id)} key={id}><FileText size={12} />{shortId(id)}<ArrowUpRight size={12} /></button>)}</div>
    <div className="recommendations"><span className="tiny-label">RECOMMENDED NEXT STEPS</span>{report.recommendations.map((text, i) => <div key={text}><span className="step-number">{String(i + 1).padStart(2, '0')}</span><p>{text}</p></div>)}</div>
    <div className="limitation"><CircleDot size={17} /><div><strong>What the evidence does not establish</strong>{report.limitations.map(item => <p key={item}>{item}</p>)}</div></div>
    <div className="report-footer"><span>{investigation.engine === 'rules' ? 'Evidence rules v1 · No model call' : investigation.usage.model}</span><span>{((investigation.usage.duration_ms || 0) / 1000).toFixed(2)}s · Saved {time(investigation.completed_at || investigation.created_at)}</span></div>
    <InvestigationActivity investigation={investigation} onOpen={onOpen} />
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
  const [nav, setNav] = useState('overview')
  const [scenario, setScenario] = useState('pool')
  const [engine, setEngine] = useState('rules')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [openedEvidence, setOpenedEvidence] = useState<Evidence | null>(null)
  const [historyId, setHistoryId] = useState('')
  const modalClose = useRef<HTMLButtonElement>(null)
  const accountEpoch = useRef(0)
  const pendingRun = useRef('')

  const session = (value: Session) => { accountEpoch.current++; setUser(value.user); setCsrf(value.csrf); setError(''); setOverview(null); setRuns([]); setIncidents([]); setSelected(''); setDetail(null); setSelectedRun(''); setRunEvidence([]); pendingRun.current = '' }
  const failure = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) { accountEpoch.current++; setUser(null); setCsrf(''); setSelected(''); setDetail(null) }
    setError(e instanceof Error ? e.message : 'Something went wrong. Try again.')
  }, [])
  useEffect(() => { api<Session>('/auth/me').then(session).catch(e => { if (!(e instanceof ApiError && e.status === 401)) failure(e) }).finally(() => setInitializing(false)) }, [failure])

  const refresh = useCallback(async () => {
    const epoch = accountEpoch.current
    const [nextOverview, nextRuns, nextIncidents] = await Promise.all([api<Overview>('/overview'), api<Run[]>('/runs'), api<Incident[]>('/incidents')])
    if (epoch !== accountEpoch.current) return
    setOverview(nextOverview); setRuns(nextRuns); setIncidents(nextIncidents)
    const newIncident = nextIncidents.find(item => item.run_id === pendingRun.current)
    if (newIncident) { setSelected(newIncident.id); setDetail(null); setHistoryId(''); setTab('assessment'); pendingRun.current = '' }
    else setSelected(value => value || nextIncidents[0]?.id || '')
  }, [])
  useEffect(() => {
    if (!user) return
    refresh().catch(failure)
    const id = setInterval(() => refresh().catch(failure), 2000)
    return () => clearInterval(id)
  }, [user, refresh, failure])
  useEffect(() => {
    if (!user || !selected) { setDetail(null); return }
    let cancelled = false
    const update = () => api<Detail>('/incidents/' + selected).then(value => { if (!cancelled) setDetail(value) }).catch(e => { if (!cancelled) failure(e) })
    update()
    const id = setInterval(update, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [user, selected, failure])
  const latestRunId = selectedRun || runs[0]?.id
  useEffect(() => {
    if (!user || !latestRunId) { setRunEvidence([]); return }
    let cancelled = false
    const update = () => api<{ evidence: Evidence[] }>('/runs/' + latestRunId).then(value => { if (!cancelled) setRunEvidence(value.evidence) }).catch(e => { if (!cancelled) failure(e) })
    update()
    const id = setInterval(update, 2000)
    return () => { cancelled = true; clearInterval(id) }
  }, [user, latestRunId, failure])
  useEffect(() => {
    if (!openedEvidence) return
    const previous = document.activeElement as HTMLElement | null
    modalClose.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenedEvidence(null)
      if (e.key === 'Tab') { e.preventDefault(); modalClose.current?.focus() }
    }
    document.addEventListener('keydown', handler)
    return () => { document.removeEventListener('keydown', handler); previous?.focus() }
  }, [openedEvidence])

  const active = runs.find(run => activeStates.includes(run.state))
  const selectedIncidentRun = runs.find(run => run.id === detail?.incident.run_id)
  const evidence = selectedRun || !detail ? runEvidence : detail.evidence
  const investigation = detail?.investigations.find(item => item.id === historyId) || detail?.investigations[0]
  const investigating = detail?.investigations.some(item => investigationActiveStates.includes(item.state))
  const allServicesUp = overview?.services.every(item => item.status === 'healthy')
  async function startScenario() {
    setBusy('scenario'); setError(''); setSelectedRun(''); setNav('overview')
    try {
      const started = await api<{ id: string }>('/scenarios/' + scenario + '/start', {})
      pendingRun.current = started.id
      await refresh()
    } catch (e) { failure(e) } finally { setBusy('') }
  }
  async function resetScenario() {
    setBusy('reset'); setError('')
    try { await api('/scenarios/reset', {}); await refresh() } catch (e) { failure(e) } finally { setBusy('') }
  }
  async function investigate() {
    if (!selected) return
    setBusy('investigate'); setError(''); setHistoryId(''); setTab('assessment'); setSelectedRun('')
    try {
      await api('/incidents/' + selected + '/investigations', { engine })
      setDetail(await api<Detail>('/incidents/' + selected))
    } catch (e) { failure(e) } finally { setBusy('') }
  }
  async function openEvidence(id: string) {
    try { setOpenedEvidence(await api<Evidence>('/evidence/' + id)) } catch (e) { failure(e) }
  }
  async function logout() {
    try { await api('/auth/logout', {}); accountEpoch.current++; setUser(null); setCsrf(''); setDetail(null); setSelected(''); setRuns([]); setIncidents([]); setOverview(null); setSelectedRun(''); setRunEvidence([]); pendingRun.current = '' } catch (e) { failure(e) }
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
      <div className="workspace-switch"><span className="workspace-icon"><Layers3 size={18} /></span><div><strong>Personal workspace</strong><span>Local environment</span></div><LockKeyhole size={13} /></div>
      <span className="nav-label">WORKSPACE</span>
      <nav>
        {[{ key: 'overview', label: 'Overview', icon: Layers3 }, { key: 'investigations', label: 'Investigations', icon: Fingerprint }, { key: 'evidence', label: 'Evidence explorer', icon: FileText }, { key: 'map', label: 'Service map', icon: GitBranch }].map(({ key, label, icon: Icon }) => <button key={key} className={nav === key ? 'active' : ''} onClick={() => navigate(key)}><Icon size={18} />{label}{key === 'overview' && <span className="nav-count">{incidents.length}</span>}</button>)}
      </nav>
      <div className="sidebar-runtime"><span className="nav-label">RUNTIME</span>{overview?.services.map(service => <div key={service.name}><span className={'service-dot ' + (service.status === 'healthy' ? 'up' : '')} />{service.name}<span>{service.status === 'healthy' ? 'Online' : 'Offline'}</span></div>)}</div>
      <div className="sidebar-bottom"><div className="local-card"><Radio size={18} /><div><strong>Connected locally</strong><p>Evidence stays on this machine.</p></div></div><div className="user-row"><span className="avatar">{user.name.charAt(0).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.email}</span></div><button onClick={logout} title="Sign out" aria-label="Sign out"><LogOut size={17} /></button></div></div>
    </aside>

    <div className="main-shell">
      <header className="topbar"><div><span>Workspace</span><ChevronRight size={13} /><strong>{nav === 'overview' ? 'Overview' : nav === 'investigations' ? 'Investigations' : nav === 'evidence' ? 'Evidence explorer' : 'Service map'}</strong></div><span className={'connection-pill ' + (!allServicesUp ? 'offline' : '')}><i />{allServicesUp ? 'All services connected' : 'Checking service connections'}</span></header>
      <main>
        <div className="page-heading"><div><span className="eyebrow">OBSERVE. INVESTIGATE. UNDERSTAND.</span><h1>{nav === 'overview' ? 'Incident overview' : nav === 'investigations' ? 'Your investigations' : nav === 'evidence' ? 'Evidence explorer' : 'Connected services'}</h1><p>A connected view of your services, failures and the evidence behind them.</p></div><span className="date-label">{new Date().toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span></div>

        {error && <div className="error-banner global-error" role="alert"><TriangleAlert size={17} /><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16} /></button></div>}

        <section className="scenario-panel">
          <div className="scenario-copy"><span className="scenario-icon"><Activity size={23} /></span><div><span className="eyebrow mint">LIVE SERVICE SCENARIOS</span><h2>See what happens when a service fails.</h2><p>Three real requests. Captured evidence. Automatic recovery.</p></div></div>
          <div className="scenario-controls"><label className="sr-only" htmlFor="scenario">Failure scenario</label><select id="scenario" value={scenario} onChange={e => setScenario(e.target.value)} disabled={!!busy || overview?.runtime_busy}><option value="pool">Connection pool exhaustion</option><option value="slow">Slow Payment processing</option><option value="healthy">Healthy checkout</option></select><button className="button mint-button" disabled={!!busy || overview?.runtime_busy || !allServicesUp} onClick={startScenario}>{busy === 'scenario' ? <Spinner /> : <Play size={15} fill="currentColor" />}Run scenario</button></div>
        </section>

        {active && <div className="run-progress" role="status"><Spinner /><div><strong>{active.state === 'resetting' ? 'Restoring healthy service flow' : 'Capturing ' + scenarioNames[active.scenario].toLowerCase()}</strong><span>{active.requests}/3 requests observed · {active.failures} failures · Waiting for complete downstream evidence</span></div><button className="text-button" disabled={!!busy} onClick={resetScenario}><Square size={12} />{busy === 'reset' ? 'Resetting…' : 'Stop & reset'}</button></div>}

        <div className="stats-grid">
          {[{ label: 'Requests observed', value: overview?.counts.requests || 0, sub: 'Across your scenario runs', icon: Activity, color: 'teal' },
            { label: 'Failed requests', value: overview?.counts.failures || 0, sub: 'Actual checkout responses', icon: TriangleAlert, color: 'orange' },
            { label: 'Incidents detected', value: overview?.counts.incidents || 0, sub: 'Correlated service failures', icon: GitBranch, color: 'purple' },
            { label: 'Evidence captured', value: overview?.counts.evidence || 0, sub: 'Logs, spans & metric samples', icon: FileText, color: 'blue' }].map(stat => <div className="stat-card" key={stat.label}><div><span>{stat.label}</span><span className={'stat-icon ' + stat.color}><stat.icon size={17} /></span></div><strong>{stat.value.toLocaleString()}</strong><p>{stat.sub}</p></div>)}
        </div>

        <section className="topology-panel"><div className="section-heading"><h2><GitBranch size={16} />Service topology</h2><span>Configured dependencies <span className="dot-divider">·</span> Live reachability</span></div><ServiceMap overview={overview} evidence={evidence} /></section>

        <div className="investigation-workspace">
          <section className="incident-panel">
            <div className="section-heading"><h2>Incidents <span className="count">{incidents.length}</span></h2><span className="live-label"><i />LIVE</span></div>
            <div className="incident-list">
              {incidents.length ? incidents.map(incident => {
                const run = runs.find(item => item.id === incident.run_id)
                return <button key={incident.id} className={'incident-item ' + (selected === incident.id && !selectedRun ? 'selected' : '')} onClick={() => { setSelected(incident.id); setDetail(null); setSelectedRun(''); setHistoryId(''); setTab(nav === 'evidence' ? 'log' : 'assessment') }}>
                  <div><span className="incident-id">INC-{shortId(incident.id)}</span><StateBadge state={incident.state} /></div><h3>{incident.title}</h3><div className="incident-tags"><span>Gateway</span><ArrowRight size={11} /><span>Order</span><ArrowRight size={11} /><span>Payment</span></div><footer><span>{time(incident.created_at)}</span><span>{run?.failures || 3} failed requests<ChevronRight size={13} /></span></footer>
                </button>
              }) : <div className="incident-empty"><CheckCircle2 size={26} /><strong>No incidents yet</strong><p>Run a failure scenario to see a correlated incident here.</p></div>}
            </div>
            <div className="runs-heading"><h3>Recent runs</h3><span>{runs.length}</span></div>
            <div className="recent-runs">{runs.length ? runs.slice(0, 6).map(run => <button className={selectedRun === run.id ? 'selected' : ''} key={run.id} onClick={() => { setSelectedRun(run.id); setTab('log') }}><span className={'run-indicator ' + (run.failures ? 'warn' : '')}>{run.failures ? <Activity size={14} /> : <Check size={14} />}</span><div><strong>{scenarioNames[run.scenario]}</strong><span>{time(run.created_at)} · {run.state}</span></div><ChevronRight size={13} /></button>) : <p className="run-placeholder">Your scenario history will appear here.</p>}</div>
          </section>

          <section className="detail-panel">
            <div className="detail-heading"><div><span className="tiny-label">{selectedRun ? 'RUN-' + shortId(selectedRun) : selected ? 'INC-' + shortId(selected) : 'INVESTIGATION WORKSPACE'}</span><h2>{selectedRun ? scenarioNames[runs.find(run => run.id === selectedRun)?.scenario || 'healthy'] : selected ? 'From signals to a clear assessment.' : 'Every incident has a story.'}</h2></div>
              {selected && !selectedRun && <div className="investigate-controls"><label className="sr-only" htmlFor="engine">Investigation engine</label><select id="engine" value={engine} onChange={e => setEngine(e.target.value)}><option value="rules">Evidence rules</option><option value="groq" disabled={!overview?.providers.groq}>Groq{!overview?.providers.groq ? ' · not configured' : ''}</option></select><button className="button primary" onClick={investigate} disabled={!!busy || investigating || activeStates.includes(selectedIncidentRun?.state || '')}>{investigating || busy === 'investigate' ? <Spinner /> : <Fingerprint size={15} />}{investigating ? 'Investigating' : investigation && ['failed', 'interrupted'].includes(investigation.state) ? 'Retry investigation' : 'Investigate'}</button></div>}
            </div>
            {(selected || runs.length > 0) ? <>
              <div className="detail-tabs">{(selectedRun || !selected ? [{ key: 'log', name: 'Logs' }, { key: 'span', name: 'Traces' }, { key: 'metric', name: 'Metrics' }] : [{ key: 'assessment', name: 'Assessment' }, { key: 'log', name: 'Logs' }, { key: 'span', name: 'Traces' }, { key: 'metric', name: 'Metrics' }]).map(item => <button className={tab === item.key ? 'active' : ''} key={item.key} onClick={() => setTab(item.key)}>{item.name}{item.key !== 'assessment' && <span>{evidence.filter(row => row.kind === item.key).length}</span>}</button>)}</div>
              {tab === 'assessment' && !selectedRun ? <>
                {detail && detail.investigations.length > 1 && <div className="history-picker"><label htmlFor="history">Saved investigation</label><select id="history" value={investigation?.id || ''} onChange={e => setHistoryId(e.target.value)}>{detail.investigations.map(item => <option key={item.id} value={item.id}>{time(item.created_at)} · {item.engine} · {item.state}</option>)}</select></div>}
                <ReportView investigation={investigation} onOpen={openEvidence} />
              </> : <EvidenceTable key={tab + latestRunId + selected} evidence={evidence} kind={tab === 'assessment' ? 'log' : tab} onOpen={openEvidence} />}
            </> : <div className="workspace-empty"><div className="empty-orbit"><Search size={29} /><span><FileText size={14} /></span></div><h3>Your workspace is ready.</h3><p>Start a service scenario above. We'll capture the requests,<br />connect the symptoms and keep the evidence in one place.</p><div className="empty-steps"><span><b>01</b> Run scenario</span><ArrowRight size={13} /><span><b>02</b> Inspect evidence</span><ArrowRight size={13} /><span><b>03</b> Investigate</span></div></div>}
          </section>
        </div>
        <footer className="page-footer"><span><ShieldCheck size={13} />Account-scoped evidence · Stored locally</span><span>Escalation Desk <span className="footer-slash">/</span> Proof of concept</span></footer>
      </main>
    </div>
    {openedEvidence && <div className="modal-backdrop" onClick={() => setOpenedEvidence(null)}><section className="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="evidence-title" onClick={e => e.stopPropagation()}><header><div><span className="tiny-label">PRESERVED SOURCE OBSERVATION</span><h2 id="evidence-title">Evidence {shortId(openedEvidence.id)}</h2></div><button ref={modalClose} aria-label="Close evidence" onClick={() => setOpenedEvidence(null)}><X size={20} /></button></header><div className="modal-meta"><span className="service-label">{openedEvidence.service}</span><span>{openedEvidence.kind}</span><time>{new Date(openedEvidence.timestamp).toLocaleString()}</time></div><pre>{JSON.stringify(openedEvidence, null, 2)}</pre><footer><LockKeyhole size={13} />Stored observation from your service run.</footer></section></div>}
  </div>
}

