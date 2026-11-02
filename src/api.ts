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
  id: string; kind: 'log' | 'span' | 'metric'; service: string; timestamp: string
  message?: string; level?: string; name?: string; trace_id?: string; span_id?: string; parent_id?: string
  duration_ms?: number; status?: string; pool_capacity?: number; pool_in_use?: number
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
  report: Report | null; activity: { tool: string; timestamp: string; count: number }[]
  usage: { model?: string; duration_ms?: number; total_tokens?: number; requests?: number }
}
export type Detail = { incident: Incident; evidence: Evidence[]; investigations: Investigation[] }
export type Overview = {
  services: { name: string; status: string }[]
  counts: { requests: number; failures: number; incidents: number; evidence: number; total: number }
  providers: { groq: boolean; jev: boolean }; runtime_busy: boolean
}

