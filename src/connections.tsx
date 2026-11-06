import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Cable, ExternalLink, LoaderCircle, RefreshCw, Unplug, Download } from 'lucide-react'
import { api, ApiError, type SentryConnection, type SentryImport, type SentryWorkspace } from './api'
import { CollectionView, type ViewProps } from './imports'
import { FullTime } from './reports'
import { useWritePermission } from './account'

const active = ['queued', 'running', 'rate_limited']
const incomplete = ['failed', 'partial', 'interrupted', 'cancelled']
const localTime = (date: Date) => new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)

export function ConnectionRegion({ region }: { region: SentryConnection['region'] }) {
  return <p>Connection region: {region === 'eu' ? 'European Union (EU)' : 'United States (US)'}.</p>
}

export function SentrySetup({ configured, publicUrl }: { configured: boolean; publicUrl: string }) {
  return <section className="import-panel sentry-setup">
    <div className="section-heading"><h2><Cable size={18} />Connect your application through Sentry</h2><span>Read access</span></div>
    <ol><li>Add Sentry to your application and send errors, logs and traces to your own project.</li><li>Connect Sentry here, approve access and select your projects.</li><li>Import a time window, review the evidence and click Investigate.</li></ol>
    <p>A website URL alone cannot provide backend logs. Your application's Sentry DSN sends telemetry; the authorization here lets TraceDesk read it.</p>
    <a href="https://docs.sentry.io/platforms/" target="_blank" rel="noreferrer">Application SDK setup <ExternalLink size={13} /></a>
    {!configured && <p className="import-guidance" role="status">Sentry connection setup is pending on this server. The operator must configure the public integration, HTTPS address and encrypted credential storage. Saved collections remain available.</p>}
    {publicUrl && globalThis.location?.origin !== publicUrl && <p className="import-guidance">For authorization, <a href={publicUrl}>open the configured HTTPS workspace</a> and sign in there.</p>}
    <p className="report-note">Sentry's approval is required for organizations outside the integration developer's organization. Connecting or importing never starts model analysis.</p>
  </section>
}

export function SentryImportCard({ item, busy, onAction, onOpen }: { item: SentryImport; busy: boolean; onAction: (action: string) => void; onOpen: () => void }) {
  const canWrite = useWritePermission()
  return <article className="sentry-import-card">
    <div><strong>{item.parameters.title}</strong><span className="status-pill">{item.state.replaceAll('_', ' ')}</span></div>
    <p>{item.progress.observations || 0} observations retained / <FullTime value={item.created_at} /></p>
    {item.error && <p className="import-error" role="alert">{item.error}</p>}
    {item.progress.message && <p role="status">{item.progress.message}</p>}
    {item.progress.retry_at && <p>Next retry: <FullTime value={new Date(item.progress.retry_at * 1000).toISOString()} /></p>}
    <div className="sentry-actions"><button className="button secondary" onClick={onOpen}>Review evidence</button>
      {active.includes(item.state) && <button className="button secondary" disabled={busy} onClick={() => onAction('cancel')}>Cancel import</button>}
      {incomplete.includes(item.state) && <button className="button secondary" disabled={!canWrite || busy} onClick={() => onAction('retry')}>Retry as new snapshot</button>}
    </div>
  </article>
}

function ImportForm({ connection, disabled, onSubmit }: { connection: SentryConnection; disabled: boolean; onSubmit: (data: unknown) => Promise<void> }) {
  const [projects, setProjects] = useState<string[]>([])
  const [datasets, setDatasets] = useState(['errors', 'logs', 'spans'])
  const [start, setStart] = useState(() => localTime(new Date(Date.now() - 3600000)))
  const [end, setEnd] = useState(() => localTime(new Date()))
  const [title, setTitle] = useState('Sentry evidence')
  const [symptom, setSymptom] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    await onSubmit({ projects, datasets, start: new Date(start).toISOString(), end: new Date(end).toISOString(), title, symptom })
  }
  const toggle = (values: string[], id: string) => values.includes(id) ? values.filter(v => v !== id) : [...values, id]
  return <form className="sentry-import-form" onSubmit={e => void submit(e)}>
    <fieldset disabled={disabled}><legend>Projects</legend>{connection.projects.map(project => <label className="sentry-checkbox" key={project.id}><input type="checkbox" checked={projects.includes(project.id)} onChange={() => setProjects(toggle(projects, project.id))} />{project.name} <small>{project.slug}</small></label>)}</fieldset>
    {!connection.projects.length && <p>No authorized projects found. Create a project in Sentry, then check access.</p>}
    <fieldset disabled={disabled}><legend>Evidence to import</legend>{['errors', 'logs', 'spans'].map(dataset => <label className="sentry-checkbox" key={dataset}><input type="checkbox" checked={datasets.includes(dataset)} onChange={() => setDatasets(toggle(datasets, dataset))} />{dataset}<small>{connection.capabilities[dataset]?.state || 'Access not checked'}</small></label>)}</fieldset>
    {Object.values(connection.capabilities).some(c => c.project) && <p className="report-note">Access check samples the first authorized project. Each selected project is checked again during import; an empty dataset may receive evidence later.</p>}
    <div className="import-fields"><label>Start (your local time)<input type="datetime-local" required value={start} onChange={e => setStart(e.target.value)} disabled={disabled} /></label><label>End (your local time)<input type="datetime-local" required value={end} onChange={e => setEnd(e.target.value)} disabled={disabled} /></label></div>
    <label>Collection title<input required maxLength={120} value={title} onChange={e => setTitle(e.target.value)} disabled={disabled} /></label>
    <label>What did you observe? (optional)<textarea maxLength={2000} rows={2} value={symptom} onChange={e => setSymptom(e.target.value)} disabled={disabled} /></label>
    <p className="report-note">Up to 24 hours and 5,000 observations per snapshot. API availability, retention, sampling and import limits are shown with the saved evidence. Imports use your Sentry allowance.</p>
    <button className="button primary" disabled={disabled || !projects.length || !datasets.length}><Download size={16} />Import evidence</button>
  </form>
}

export function ConnectionsWorkspace(props: ViewProps) {
  const canWrite = useWritePermission()
  const [data, setData] = useState<SentryWorkspace | null>(null)
  const [selected, setSelected] = useState('')
  const [collection, setCollection] = useState('')
  const [region, setRegion] = useState('us')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(false)
  const refresh = useCallback(async () => {
    const value = await api<SentryWorkspace>('/connections/sentry')
    if (mounted.current) { setData(value); setSelected(id => id || value.connections[0]?.id || '') }
  }, [])
  const fail = useCallback((e: unknown) => {
    if (!mounted.current) return
    setError(e instanceof Error ? e.message : 'Sentry request failed.')
    if (e instanceof ApiError && e.status === 401) props.onError(e)
  }, [props.onError])
  useEffect(() => {
    mounted.current = true
    void refresh().catch(fail)
    const timer = setInterval(() => void refresh().catch(fail), 2000)
    return () => { mounted.current = false; clearInterval(timer) }
  }, [refresh, fail])
  async function action(path: string, body: unknown = {}) {
    setBusy(true); setError('')
    try { await api(path, body); await refresh() } catch (e) { fail(e) } finally { if (mounted.current) setBusy(false) }
  }
  async function connect() {
    setBusy(true); setError('')
    try {
      const value = await api<{ url: string }>('/connections/sentry/start', { region })
      if (mounted.current) window.location.assign(value.url)
    } catch (e) { fail(e) } finally { if (mounted.current) setBusy(false) }
  }
  const current = data?.connections.find(c => c.id === selected)
  const activeImport = data?.imports.some(item => active.includes(item.state))
  return <div className="connections-workspace">
    <SentrySetup configured={data?.configured || false} publicUrl={data?.public_url || ''} />
    {error && <p className="import-error" role="alert">{error}</p>}
    {!data && !error && <p role="status">Loading Sentry connections...</p>}
    <section className="import-panel"><div className="sentry-actions"><label>Region for new authorization<select value={region} onChange={e => setRegion(e.target.value)} disabled={busy}><option value="us">United States</option><option value="eu">European Union</option></select></label><button className="button primary" onClick={() => void connect()} disabled={!canWrite || busy || !data?.configured}>{busy ? <LoaderCircle size={16} className="spin" /> : <Cable size={16} />}Connect Sentry</button></div>
      {data && !data.connections.length && <p>No Sentry connections yet. Choose the region shown in your Sentry organization settings, then authorize access.</p>}
      {!!data?.connections.length && <label>Connection<select value={selected} onChange={e => setSelected(e.target.value)}>{data.connections.map(c => <option key={c.id} value={c.id}>{c.organization} / {c.region.toUpperCase()} / {c.state}</option>)}</select></label>}
      {current && <><div className="section-heading"><h2>{current.organization}</h2><span>{current.state.replaceAll('_', ' ')}</span></div>
        <ConnectionRegion region={current.region} />
        {current.error && <p className="import-error">{current.error}</p>}
        {current.checked_at && <p>Last checked <FullTime value={current.checked_at} /></p>}
        <div className="sentry-actions"><button className="button secondary" disabled={!canWrite || busy || current.state !== 'connected'} onClick={() => void action(`/connections/sentry/${current.id}/check`)}><RefreshCw size={15} />Check access</button><button className="button secondary" disabled={busy || current.state === 'disconnected'} onClick={() => void action(`/connections/sentry/${current.id}/disconnect`)}><Unplug size={15} />Disconnect</button></div>
        {current.state !== 'connected' ? <p>Use Connect Sentry to authorize this organization again. Saved snapshots remain readable. Local disconnect does not uninstall the integration in Sentry.</p> : <ImportForm key={current.id} connection={current} disabled={!canWrite || busy || !!activeImport} onSubmit={body => action(`/connections/sentry/${current.id}/imports`, body)} />}
      </>}
    </section>
    <section className="import-panel"><div className="section-heading"><h2>Recent imports</h2><span>Saved snapshots</span></div>{data?.imports.map(item => <SentryImportCard key={item.id} item={item} busy={busy} onOpen={() => setCollection(item.collection_id)} onAction={kind => void action(`/sentry-imports/${item.id}/${kind}`)} />)}{data && !data.imports.length && <p>Your imports will appear here. Analysis starts only when you click Investigate on saved evidence.</p>}</section>
    {collection && <CollectionView key={collection} id={collection} {...props} />}
  </div>
}
