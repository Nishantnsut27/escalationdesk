import { useContext } from 'react'
import type { ReportEvidence, Triage } from './api'
import { Citations, EvidenceLinks, FullTime } from './reports'
import { logHref } from './routes'
import { ArrowUpRight } from 'lucide-react'
export const categories: Record<string, string> = { application_exception: 'Application exceptions', database_query: 'Database and queries', resource_exhaustion: 'Resource exhaustion', latency_timeout: 'Latency and timeouts', network_connectivity: 'Network and connectivity', dependency_failure: 'Dependency failures', authentication_access: 'Authentication and access', configuration: 'Configuration', data_validation: 'Data validation', storage_filesystem: 'Storage and filesystems', jobs_messaging: 'Jobs and messaging', deployment_lifecycle: 'Deployment and lifecycle', rate_limit_quota: 'Rate limits and quotas', mixed: 'Mixed problems', unknown: 'Unclassified', no_clear_failure: 'No clear failure observed' }
export function SourcePreviews({ ids, scope, error }: { ids: string[]; scope?: ReportEvidence | null; error?: string; onOpen?: (id: string) => void }) {
  const link = useContext(EvidenceLinks)
  return <div className="source-previews" aria-label="Assessment evidence">{!ids.length && <p>No sources cited.</p>}{[...new Set(ids)].map(id => {
    const row = scope?.evidence.find(item => item.id === id)
    return <a key={id} href={logHref({ collection: link.collection, run: link.run, investigation: link.investigation, evidence: id, row })} target="_blank" rel="noopener noreferrer" aria-label={'Open evidence ' + id + ' in a new tab'}><div><code>{id.slice(0, 8)}</code><ArrowUpRight size={14} /></div><strong>{row?.service || 'Saved source'}</strong><span>{row ? row.message || row.name || 'Metric sample' : error ? 'Preview unavailable — open source' : scope ? 'Saved source unavailable' : 'Open retained observation'}</span>{row && <small><FullTime value={row.timestamp} /></small>}</a>
  })}</div>
}
export function TriagePanel({ triage, continuing = false }: { triage?: Triage; continuing?: boolean; onOpen?: (id: string) => void }) {
  if (!triage) return null
  if (triage.state === 'pending' || triage.state === 'running') return <div className="triage-panel" role="status"><strong>Evidence review</strong><p>{triage.state === 'pending' ? 'Evidence review is queued.' : 'Reviewing and prioritizing recorded observations.'}</p></div>
  if (triage.state !== 'completed') return <div className="triage-panel"><strong>{triage.state === 'interrupted' ? 'Evidence review interrupted' : 'Preliminary review unavailable ? Groq-only fallback'}</strong><p>{continuing ? 'Preliminary review unavailable; investigation continuing with the available evidence.' : triage.state === 'interrupted' ? 'Retry explicitly to create a new investigation.' : 'This attempt used the available evidence without the preliminary classification.'}</p></div>
  return <details className="triage-panel"><summary>Evidence review · {categories[triage.category || ''] || 'Unclassified'}</summary><p>Preliminary classification, not a root-cause finding. The assessment can disagree. Successful and conflicting observations remain relevant.</p>{triage.category === 'no_clear_failure' && <p>No clear failure in this view does not establish application health.</p>}{triage.groups?.map(group => <Citations key={group.id} ids={group.source_ids} label="Reviewed observations" />)}{triage.coverage && <p>{triage.coverage.represented_groups} groups reviewed; {triage.coverage.omitted_groups} groups omitted from this bounded review.</p>}</details>
}
