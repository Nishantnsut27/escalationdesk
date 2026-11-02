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
  const data = await response.json()
  if (!response.ok) throw new ApiError(data.error || 'The request could not be completed.', response.status)
  return data as T
}
export type User = { id: string; name: string; email: string }
export type Session = { user: User; csrf: string }
export type Evidence = {
  id: string; kind: 'log' | 'span' | 'metric'; service: string; timestamp: string | null
  message?: string; level?: string; name?: string; trace_id?: string; span_id?: string; parent_id?: string
  duration_ms?: number; status?: string; pool_capacity?: number; pool_in_use?: number
  started_at?: string; http_status?: number; timeout_ms?: number
  source_line?: number; source_id?: string; original?: string; request_id?: string
  requests?: number; failures?: number; last_latency_ms?: number
  [key: string]: unknown
}
export type Run = {
  id: string; scenario: string; state: string; created_at: string; ended_at?: string
  requests: number; failures: number; error?: string
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
    progress?: string; retry_at?: string; wait_reason?: string; quota_wait_seconds?: number
    estimated_input_tokens?: number; supplied_evidence_ids?: string[]; coverage?: string; available_sources?: number
    omitted_sources?: number; unavailable_source_ids?: string[]
    diagnostics?: { attempt: number; phase: string; status?: number | string; input_bytes: number
      estimated_input_tokens: number; duration_ms?: number; limit_category?: string; retry_at?: string
      actual_usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }[] }
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
  id: string; title: string; symptom: string; filename: string | null; source_type: 'file' | 'paste'; created_at: string
  summary: { observations: number; input_bytes: number; input_lines: number; duplicates: number; conflicting_ids: number
    malformed_records: number; redactions: number; missing_timestamps: number; missing_services: number; invalid_fields: number
    services: string[]; kinds: string[]; coverage: string; redaction_note: string }
}
export type CollectionDetail = { collection: Collection; investigations: Investigation[] }
export type EvidencePage = { collection_id: string; evidence: Evidence[]; next_offset: number | null }

