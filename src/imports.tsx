import { useEffect, useRef, useState, type FormEvent } from 'react'
import { FileText, LoaderCircle, Upload } from 'lucide-react'
import { api, ApiError, type Collection } from './api'
import { FullTime } from './reports'
import { PageHeading, Notice } from './components/common'
import { navigate } from './routes'
import { useWritePermission } from './account'

export const IMPORT_BYTES = 1024 * 1024

export function ImportForm({ onImported, onError }: { onImported: (value: Collection) => void; onError: (error: unknown) => void }) {
  const canWrite = useWritePermission()
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
      <p className="report-note">Up to 1 MiB, 5,000 observations and 32 services per import. UTF-8 text only. Common credentials are redacted; review saved content before sending excerpts for analysis. Importing does not start analysis.</p>
      {error && <p className="import-error" role="alert">{error}</p>}
      <button className="button primary" disabled={!canWrite || busy || !content.trim()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}{busy ? 'Importing...' : 'Save logs'}</button>
    </form>
  </section>
}

export function ImportCoverage({ collection }: { collection: Collection }) {
  const summary = collection.summary
  return <section className="import-coverage" aria-label="Import coverage">
    <h3>{collection.title}</h3>
    <p>Imported <FullTime value={collection.created_at} /> from {collection.source_type === 'sentry' ? 'Sentry / ' + collection.summary.source?.organization : collection.filename || 'pasted text'}. This is import activity, not a telemetry timestamp.</p>
    {summary.source && <p>Selected window: <FullTime value={summary.source.start} /> to <FullTime value={summary.source.end} />. Region: {summary.source.region.toUpperCase()}.</p>}
    {collection.source_type === 'sentry' && <p>Sentry projects identify where evidence was stored. Service names identify the application that emitted it; missing service names remain unknown.</p>}
    {collection.source_type === 'sentry' && (summary.normalization_version ?? 1) < 2 && <p className="import-guidance" role="status">Older Sentry import: create a fresh import for corrected service names, exception handling and HTTP response metadata. This snapshot and its saved reports are unchanged.</p>}
    {summary.datasets && <ul className="dataset-coverage">{Object.entries(summary.datasets).map(([name, value]) => <li key={name}><strong>{name}</strong>: {value.state}{value.received !== undefined && ` / ${value.received} received`}{value.reason && ` / ${value.reason}`}</li>)}</ul>}
    {collection.symptom && <p><strong>Reported symptom:</strong> {collection.symptom}</p>}
    <dl>{[
      ['Saved observations', summary.observations], ['Source lines', summary.input_lines], ['Duplicate IDs skipped', summary.duplicates],
      ['Conflicting IDs retained', summary.conflicting_ids], [collection.source_type === 'sentry' ? 'Malformed records skipped' : 'Malformed records kept as text', summary.malformed_records],
      ['Redactions', summary.redactions], ['Missing/invalid timestamps', summary.missing_timestamps],
      ['Unknown services', summary.missing_services], ['Invalid normalized fields', summary.invalid_fields],
    ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p>{summary.coverage} Available kinds: {summary.kinds.join(', ') || 'none'}.
      {' '}{summary.kinds.includes('span') ? 'Recorded spans are available; missing trace links remain unknown.' : 'No spans were supplied.'}
      {' '}{summary.kinds.includes('metric') ? 'Recorded metric samples are available.' : 'No metrics were supplied.'} Missing timestamps remain unknown.</p>
    <p>{summary.redaction_note}</p>
  </section>
}

export function ImportWorkspace() {
  return <div className="imports-workspace"><PageHeading eyebrow="IMPORT LOGS" title="Import logs">Save a snapshot of application logs. Review its coverage before starting an investigation.</PageHeading><div className="import-layout"><ImportForm onError={() => {}} onImported={collection => navigate('collections', collection.id)} /><aside className="import-help"><span className="row-symbol"><FileText size={22} /></span><h2>A useful starting point</h2><p>Include the error and the observations around it. Trace or request IDs help connect context across services.</p><ol><li>Choose a UTF-8 log file or paste its content.</li><li>Give the collection a name and describe the symptom.</li><li>Inspect your saved evidence, then choose Investigate.</li></ol><Notice>Importing saves evidence. Analysis starts only when you choose Investigate.</Notice><a className="text-link" href="#/connections">Import from Sentry instead</a></aside></div></div>
}
