import type { Overview, ReportEvidence, Triage } from './api'
import { Citations, FullTime } from './reports'

const categories: Record<string, string> = {
  application_exception: 'Application exceptions', database_query: 'Database and queries',
  resource_exhaustion: 'Resource exhaustion', latency_timeout: 'Latency and timeouts',
  network_connectivity: 'Network and connectivity', dependency_failure: 'Dependency failures',
  authentication_access: 'Authentication and access', configuration: 'Configuration',
  data_validation: 'Data validation and contracts', storage_filesystem: 'Storage and filesystems',
  jobs_messaging: 'Background jobs and messaging', deployment_lifecycle: 'Deployment and process lifecycle',
  rate_limit_quota: 'Rate limits and quotas', mixed: 'Mixed problems', unknown: 'Unknown or unclassified',
  no_clear_failure: 'No clear failure observed',
}

export function WorkflowLabel({ providers }: { providers?: Overview['providers'] }) {
  return <span className="workflow-label">{!providers ? 'Checking provider configuration…' : !providers.groq
    ? 'Full investigation unavailable: Groq is not configured.' : providers.jev
      ? 'Jev triage → Groq investigation' : 'Groq investigation · Jev not configured'}</span>
}

export function SourcePreviews({ ids, scope, error, onOpen }: {
  ids: string[]; scope?: ReportEvidence | null; error?: string; onOpen: (id: string) => void
}) {
  return <div className="source-previews" aria-label="Assessment evidence"><span className="tiny-label">Cited observations</span>
    {!ids.length && <p>No sources cited.</p>}
    {[...new Set(ids)].map(id => {
      const row = scope?.evidence.find(item => item.id === id)
      return <button type="button" key={id} onClick={() => onOpen(id)} aria-label={'Open evidence ' + id}>
        <strong>{row?.service || id.slice(0, 6).toUpperCase()}</strong>
        <span>{row ? row.message || row.name || 'Metric sample' : error ? 'Preview unavailable — open source' : scope ? 'Saved source unavailable' : 'Loading source preview…'}</span>
        {row && <small><FullTime value={row.timestamp} />{row.duration_ms !== undefined && ` · ${row.duration_ms} ms`}</small>}
      </button>
    })}
  </div>
}

export function TriagePanel({ triage, onOpen }: { triage?: Triage; onOpen: (id: string) => void }) {
  if (!triage) return null
  if (triage.state === 'pending' || triage.state === 'running') return <section className="triage-panel" role="status">
    <strong>Initial triage</strong><p>{triage.state === 'pending' ? 'Jev triage is queued with this investigation.' : 'Jev is classifying the observations before Groq investigates.'}</p>
    {triage.retry_at && <p>Temporary provider cooldown. Retry <FullTime value={triage.retry_at} />; triage has a 20-second total allowance.</p>}
  </section>
  if (triage.state !== 'completed') return <section className="triage-panel triage-fallback">
    <strong>{triage.state === 'interrupted' ? 'Initial triage interrupted' : 'Groq-only fallback · Jev unavailable'}</strong><p>{triage.reason}</p>
    <TriageUsage triage={triage} />
  </section>
  return <details className="triage-panel"><summary>Initial triage · {categories[triage.category || ''] || 'Unknown'}</summary>
    <p>Preliminary Jev classification, not a root-cause finding. Groq can disagree. Alternatives and lower-priority evidence remain available.</p>
    {triage.advisory_context === 'omitted_to_preserve_evidence' && <p>Jev guidance was omitted from the final request to preserve source evidence within the input limit.</p>}
    <p>Classification confidence: {triage.confidence === undefined ? 'Not returned' : triage.confidence.toFixed(2)} (0–1). This is not the probability that the assessed cause is correct.</p>
    {triage.category === 'no_clear_failure' && <p>No clear failure in this view does not establish application health.</p>}
    <details><summary>Category probabilities</summary><dl className="triage-probabilities">{Object.entries(triage.probabilities || {}).sort((a, b) => b[1] - a[1]).map(([name, value]) =>
      <div key={name}><dt>{categories[name] || name}</dt><dd>{(value * 100).toFixed(1)}%</dd></div>)}</dl></details>
    {!!triage.groups?.length && <><h4>Observation priorities</h4><p>Diagnostic usefulness from 0 to 2; not severity or proof of causation. Successful and conflicting observations can be useful.</p>
      {triage.groups.map(group => <div key={group.id}><span>Priority {group.score?.toFixed(2) ?? 'Unavailable'} / 2</span><Citations ids={group.source_ids} label="Observations considered" onOpen={onOpen} /></div>)}</>}
    {triage.coverage && <p>{triage.coverage.represented_groups} representative groups from {triage.coverage.baseline_observations} baseline observations; {triage.coverage.omitted_groups} groups omitted. {triage.coverage.note}</p>}
    <TriageUsage triage={triage} />
  </details>
}

function TriageUsage({ triage }: { triage: Triage }) {
  return <p className="usage-note">Jev via OpenRouter · {triage.returned_model || 'Model not returned'} · {triage.requests ?? 0} HTTP attempts · Input tokens: {triage.actual_usage?.input_tokens ?? 'not returned'} · Output tokens: {triage.actual_usage?.output_tokens ?? 'not returned'} · {((triage.duration_ms ?? 0) / 1000).toFixed(2)}s
    {triage.wait_seconds ? ` · ${triage.wait_seconds}s waited` : ''}{triage.cost !== undefined && ` · $${triage.cost.toFixed(6)} provider-reported cost`}. Underlying version is only known if returned by the provider.</p>
}
