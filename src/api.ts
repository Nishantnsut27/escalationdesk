export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}
let csrf = ''
export function setCsrf(value: string) { csrf = value }
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, {
    credentials: 'same-origin',
    ...(body !== undefined ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) } : {}),
  })
  let data
  try { data = await response.json() }
  catch { throw new ApiError('The server could not complete this request. Please try again shortly.', response.status) }
  if (!response.ok) throw new ApiError(data.error || 'The request could not be completed.', response.status)
  return data as T
}
export type User = { id: string; name: string; email: string; role: 'regular' | 'demo'; read_only: boolean; email_verified: boolean }
export type Session = { user: User; csrf: string }
export type Evidence = {
  id: string; kind: 'log' | 'span' | 'metric'; service: string; timestamp: string | null
  message?: string; level?: string; name?: string; trace_id?: string; span_id?: string; parent_id?: string
  duration_ms?: number; status?: string; pool_capacity?: number; pool_in_use?: number
  started_at?: string; http_status?: number; timeout_ms?: number
  span_op?: string; exception_type?: string; exception_handled?: boolean
  source_project?: string; source_dataset?: string
  source_line?: number; source_id?: string; original?: string; request_id?: string
  requests?: number; failures?: number; last_latency_ms?: number
  [key: string]: unknown
}
export type Run = {
  id: string; scenario: string; state: string; created_at: string; ended_at?: string
  requests: number; failures: number; error?: string
  collection_id?: string | null; stage?: string | null
}
export type Incident = { id: string; run_id: string; title: string; state: string; created_at: string }
export type Report = {
  assessment: string; summary: string; origin: string; affected_services: string[]
  evidence_ids: string[]; hypotheses: { claim: string; evidence_ids: string[] }[]
  recommendations: string[]; limitations: string[]
}
export type Investigation = {
  id: string; engine: string; state: string; created_at: string; completed_at?: string; error?: string
  report: Report | null; activity: { tool: string; timestamp: string; count: number; evidence_ids?: string[]; omitted_groups?: number; query_limit_reached?: boolean }[]
  supplements?: Supplement[]
  usage: { model?: string; duration_ms?: number; total_tokens?: number; requests?: number
    triage?: Triage
    progress?: string; retry_at?: string; wait_reason?: string; quota_wait_seconds?: number
    estimated_input_tokens?: number; supplied_evidence_ids?: string[]; coverage?: string; available_sources?: number
    omitted_sources?: number; unavailable_source_ids?: string[]
    diagnostics?: { attempt: number; phase: string; status?: number | string; input_bytes: number
      estimated_input_tokens: number; duration_ms?: number; limit_category?: string; retry_at?: string
      actual_usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }[] }
}
export type Triage = {
  requested: boolean; state: 'pending' | 'running' | 'completed' | 'fallback' | 'interrupted'
  reason?: string; requested_model?: string; returned_model?: string; taxonomy_version?: string
  category?: string; probabilities?: Record<string, number>; confidence?: number
  requests?: number; duration_ms?: number; wait_seconds?: number; retry_at?: string
  actual_usage?: { input_tokens?: number; output_tokens?: number }; cost?: number; provider?: string
  source_ids?: string[]; input_bytes?: number; started_at?: string; completed_at?: string
  advisory_context?: string
  coverage?: { baseline_observations: number; represented_groups: number; omitted_groups: number; note: string }
  groups?: { id: string; source_ids: string[]; score?: number; confidence?: number }[]
}
export type ReportEvidence = {
  investigation_id: string; evidence: Evidence[]; source_ids: string[]; unavailable_source_ids: string[]
  coverage: 'recorded_supplied' | 'legacy_citations_only'; note: string
}
export type Supplement = {
  id: string; investigation_id: string; state: string; created_at: string; started_at?: string; completed_at?: string
  source_ids: string[]; usage: Investigation['usage']; error?: string
  result: null | {
    summary: string; relationship: 'supports' | 'qualifies' | 'disagrees' | 'cannot_assess'
    explanations: { claim: string; status: 'supported' | 'weakened' | 'unresolved'; supporting_citations: string[]; conflicting_citations: string[] }[]
    questions: { question: string; suggested_check: string }[]; limitations: string[]
  }
}
export type Detail = { incident: Incident; evidence: Evidence[]; investigations: Investigation[] }
export type Overview = {
  services: { name: string; status: string }[]
  counts: { requests: number; failures: number; incidents: number; evidence: number; total: number }
  providers: { groq: boolean; jev: boolean }; runtime_busy: boolean
}


export type Collection = {
  id: string; title: string; symptom: string; filename: string | null; source_type: 'file' | 'paste' | 'live_scenario' | 'recorded_demo' | 'sentry'; created_at: string
  summary: { observations: number; input_bytes: number; input_lines: number; duplicates: number; conflicting_ids: number
    malformed_records: number; redactions: number; missing_timestamps: number; missing_services: number; invalid_fields: number
    services: string[]; kinds: string[]; coverage: string; redaction_note: string
    source?: { organization: string; region: string; projects: string[]; start: string; end: string }
    datasets?: Record<string, DatasetCoverage>; truncated_records?: number; out_of_window?: number; normalization_version?: number }
}
export type CollectionDetail = { collection: Collection; investigations: Investigation[]; import_state?: string | null }
export type DatasetCoverage = { state: string; reason?: string; received?: number; pages?: number; project?: string }
export type SentryConnection = { id: string; organization: string; region: 'us' | 'eu'; state: string; created_at: string; checked_at: string | null; error: string | null; projects: { id: string; slug: string; name: string }[]; capabilities: Record<string, DatasetCoverage> }
export type SentryImport = { id: string; connection_id: string; collection_id: string; state: string; created_at: string; completed_at: string | null; error: string | null; retry_of: string | null; parameters: { title: string; projects: string[]; datasets: string[]; start: string; end: string }; progress: { observations?: number; datasets?: Record<string, DatasetCoverage>; message?: string; retry_at?: number } }
export type SentryWorkspace = { configured: boolean; public_url: string; connections: SentryConnection[]; imports: SentryImport[] }
export type EvidencePage = { collection_id: string; evidence: Evidence[]; next_offset: number | null }
export type Scenario = { id: string; title: string; description: string; category: string }
export type DemoCase = Omit<Scenario, 'id'> & { slug: string; version: string; recorded_at: string; collection_id: string; investigation_id: string; counts: Record<'log' | 'span' | 'metric', number> }

