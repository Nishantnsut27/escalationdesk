import { useEffect, useRef, useState } from 'react'
import { ArrowRight, ArrowUpRight, Fingerprint, Layers3, Plus, Search } from 'lucide-react'
import { api, userMessage, type Collection, type CollectionDetail, type Investigation, type InvestigationItem } from './api'
import { useRoute, navigate, routeHref, logHref } from './routes'
import { Button } from './components/ui/button'
import { Card } from './components/ui/card'
import { EmptyState, ErrorState, LoadingState, PageHeading, StatusBadge, shortTime } from './components/common'
import { ReportView } from './assessments'
import { ImportCoverage } from './imports'
import { useWritePermission } from './account'

export function useData<T>(path: string, refreshWhile?: (value: T) => boolean) {
  const [value, setValue] = useState<T | null>(null), [error, setError] = useState(''), [attempt, setAttempt] = useState(0)
  const refresh = useRef(refreshWhile)
  refresh.current = refreshWhile
  useEffect(() => {
    let alive = true, timer: ReturnType<typeof setTimeout>
    setValue(null); setError('')
    async function read() {
      try {
        const result = await api<T>(path)
        if (!alive) return
        setValue(result); setError('')
        if (refresh.current?.(result)) timer = setTimeout(read, 2500)
      } catch (e) { if (alive) setError(userMessage(e)) }
    }
    void read()
    return () => { alive = false; clearTimeout(timer) }
  }, [path, attempt])
  return { value, error, retry: () => setAttempt(value => value + 1) }
}
export function InvestigationList({ items }: { items: InvestigationItem[] }) {
  if (!items.length) return <EmptyState title="No investigations yet">Select saved evidence and choose Investigate to create your first assessment.</EmptyState>
  return <div className="investigation-list">{items.map(item => <a key={item.id} className="investigation-row" href={routeHref('investigations', item.id)}><span className="row-symbol"><Fingerprint size={18} /></span><div><strong>{item.title || 'Recorded investigation'}</strong><p>{item.summary || 'Open the saved attempt to see progress and collected evidence.'}</p><span>{shortTime(item.created_at)} · {item.id.slice(0, 8)}</span></div><div className="investigation-state"><StatusBadge state={item.assessment === 'inconclusive' ? 'inconclusive' : item.state} /><ArrowUpRight size={15} /></div></a>)}</div>
}
export function Collections({ readOnly = false }: { readOnly?: boolean }) {
  const { value: collections, error, retry } = useData<Collection[]>('/collections'), [query, setQuery] = useState('')
  const rows = collections?.filter(item => (item.title + ' ' + item.summary.services.join(' ')).toLowerCase().includes(query.toLowerCase()))
  return <><PageHeading eyebrow={readOnly ? 'RECORDED DEMO · READ-ONLY' : 'SAVED EVIDENCE'} title="Saved evidence" actions={!readOnly && <Button asChild><a href="#/import"><Plus size={16} />Import logs</a></Button>}>Choose a snapshot to investigate or open its observations.</PageHeading>{error && <ErrorState error={error} retry={retry} />}<label className="collection-search"><Search size={16} /><span className="sr-only">Search saved collections</span><input placeholder="Find a collection or service…" value={query} onChange={event => setQuery(event.target.value)} /></label>{!collections ? <LoadingState /> : rows?.length ? <div className="collection-grid">{rows.map(collection => <Card className="collection-card" key={collection.id}><div className="collection-card-top"><span className="row-symbol"><Layers3 size={20} /></span><span className="subtle-badge">{collection.source_type === 'sentry' ? 'Sentry snapshot' : readOnly ? 'Recorded demo' : 'Imported logs'}</span></div><a href={routeHref('collections', collection.id)}><h2>{collection.title}</h2></a><p>{collection.symptom || 'Saved observations, ready for inspection.'}</p><div className="collection-services">{collection.summary.services.slice(0, 4).map(service => <code key={service}>{service}</code>)}{collection.summary.services.length > 4 && <span>+{collection.summary.services.length - 4}</span>}</div><div className="collection-card-meta"><strong>{collection.summary.observations.toLocaleString()} observations</strong><span>{shortTime(collection.created_at)}</span></div><div className="collection-card-actions"><a href={routeHref('collections', collection.id)}>Open collection<ArrowRight size={14} /></a><a href={logHref({ collection: collection.id })} target="_blank" rel="noopener noreferrer">Logs<ArrowUpRight size={14} /></a></div></Card>)}</div> : <Card><EmptyState title={query ? 'No matching collections' : 'No evidence saved yet'}>{query ? 'Try another collection name or service.' : 'Import logs or connect Sentry to begin.'}</EmptyState></Card>}</>
}
export function CollectionWorkspace({ id, readOnly = false }: { id: string; readOnly?: boolean }) {
  const { value: detail, error, retry } = useData<CollectionDetail>('/collections/' + encodeURIComponent(id),
    value => value.investigations.some(item => ['queued', 'running', 'quota_waiting'].includes(item.state)) || ['queued', 'running', 'rate_limited'].includes(value.import_state || ''))
  const canWrite = useWritePermission(), [busy, setBusy] = useState(false), [actionError, setActionError] = useState('')
  const route = useRoute(), investigation = detail?.investigations.find(item => item.id === route.params.get('report')) || detail?.investigations[0]
  const working = detail?.investigations.some(item => ['queued','running','quota_waiting'].includes(item.state))
  async function investigate() { setBusy(true); setActionError(''); try { const result = await api<{ id: string }>('/collections/' + id + '/investigations', { engine: 'groq', triage: true }); navigate('investigations', result.id) } catch (e) { setActionError(userMessage(e)) } finally { setBusy(false) } }
  if (error) return <ErrorState error={error} retry={retry} />
  if (!detail) return <LoadingState />
  return <><a className="back-link" href={readOnly ? '#/demo' : '#/collections'}>← {readOnly ? 'Recorded demos' : 'Saved evidence'}</a><PageHeading eyebrow={readOnly ? 'RECORDED DEMO · READ-ONLY' : 'EVIDENCE COLLECTION'} title={detail.collection.title} actions={<><Button asChild variant="secondary"><a href={logHref({ collection: id })} target="_blank" rel="noopener noreferrer">Open logs<ArrowUpRight size={15} /></a></Button>{!readOnly && <Button disabled={!canWrite || busy || working || ['queued','running','rate_limited'].includes(detail.import_state || '') || !detail.collection.summary.observations} onClick={investigate}><Fingerprint size={16} />{busy ? 'Submitting…' : working ? 'Investigation active' : 'Investigate'}</Button>}</>}>{detail.collection.symptom || 'Inspect the saved evidence, then investigate the observed failure.'}</PageHeading>{actionError && <ErrorState error={actionError} />}<div className="collection-context"><span><b>{detail.collection.summary.observations.toLocaleString()}</b> saved observations</span><span><b>{detail.collection.summary.services.length}</b> service labels</span><span><b>{detail.collection.summary.missing_timestamps || 0}</b> missing timestamps</span></div><details className="panel collection-coverage"><summary>Collection coverage and provenance</summary><ImportCoverage collection={detail.collection} /></details><div className="collection-services">{detail.collection.summary.services.map(service => <code key={service}>{service}</code>)}</div>
    {!!detail.investigations.length && <div className="report-picker"><label>Saved investigation<select value={investigation?.id || ''} onChange={event => navigate('collections', id, { report: event.target.value })}>{detail.investigations.map(item => <option key={item.id} value={item.id}>{shortTime(item.created_at)} · {item.state}</option>)}</select></label><a className="text-link" href={routeHref('investigations', investigation?.id)}>Open report<ArrowUpRight size={14} /></a></div>}
    <Card><ReportView investigation={investigation} readOnly={readOnly} onUpdate={retry} /></Card>
  </>
}
export function Investigations({ id, readOnly = false }: { id?: string; readOnly?: boolean }) {
  const route = useRoute(), offset = Math.max(0, Number(route.params.get('offset')) || 0)
  const path = id ? '/investigations/' + encodeURIComponent(id) : '/investigations?limit=51&offset=' + offset + (route.params.get('collection_id') ? '&collection_id=' + encodeURIComponent(route.params.get('collection_id')!) : '')
  const { value, error, retry } = useData<Investigation | InvestigationItem[]>(path)
  const canWrite = useWritePermission(), [busy, setBusy] = useState(false), [actionError, setActionError] = useState('')
  const [updated, setUpdated] = useState<Investigation | null>(null)
  useEffect(() => { setUpdated(null) }, [id, value])
  const investigation = !Array.isArray(value) ? updated || value : null
  useEffect(() => { if (!investigation || !['queued','running','quota_waiting'].includes(investigation.state)) return; let alive = true; const timer = setTimeout(() => api<Investigation>(path).then(item => { if (alive) setUpdated(item) }).catch(e => { if (alive) setActionError(userMessage(e)) }), 2500); return () => { alive = false; clearTimeout(timer) } }, [investigation, path])
  async function again() { if (!investigation) return; setBusy(true); try { const result = await api<{ id: string }>(investigation.collection_id ? '/collections/' + investigation.collection_id + '/investigations' : '/incidents/' + investigation.incident_id + '/investigations', { engine: 'groq', triage: true }); navigate('investigations', result.id) } catch (e) { setActionError(userMessage(e)) } finally { setBusy(false) } }
  return <><PageHeading eyebrow={readOnly ? 'RECORDED DEMO · READ-ONLY' : 'INVESTIGATIONS'} title={id ? 'Investigation report' : 'Investigations'} actions={id && investigation && !readOnly && !['queued','running','quota_waiting'].includes(investigation.state) && <Button disabled={!canWrite || busy} onClick={again}><Fingerprint size={15} />{busy ? 'Submitting…' : ['failed','interrupted'].includes(investigation.state) ? 'Retry investigation' : 'New investigation'}</Button>}>{id ? 'A saved assessment, its supporting observations and what remains unknown.' : 'Reopen an assessment or follow an active investigation.'}</PageHeading>{error && <ErrorState error={error} retry={retry} />}{actionError && <ErrorState error={actionError} />}{!value ? !error && <LoadingState /> : <Card>{Array.isArray(value) ? <><InvestigationList items={value.slice(0, 50)} /><div className="pagination"><span>Investigations {offset + (value.length ? 1 : 0)}–{offset + Math.min(50, value.length)}</span><div><Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => navigate('investigations', '', { collection_id: route.params.get('collection_id'), offset: Math.max(0, offset - 50) })}>Previous</Button><Button variant="secondary" size="sm" disabled={value.length <= 50} onClick={() => navigate('investigations', '', { collection_id: route.params.get('collection_id'), offset: offset + 50 })}>Next</Button></div></div></> : <ReportView investigation={investigation || undefined} readOnly={readOnly} onUpdate={setUpdated} />}</Card>}</>
}
