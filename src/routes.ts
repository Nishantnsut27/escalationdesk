import { useEffect, useState } from 'react'
import type { Evidence } from './api'

export type Route = { page: string; id: string; params: URLSearchParams }
export function isProtectedRoute(hash: string) {
  return ['dashboard', 'collections', 'errors', 'investigations', 'connections', 'import', 'logs', 'demo'].includes(parseRoute(hash).page)
}
export function destinationAfterAuth(hash: string, remembered = '', readOnly = false) {
  return isProtectedRoute(hash) ? hash : isProtectedRoute(remembered) ? remembered : readOnly ? '#/demo' : '#/dashboard'
}
export function parseRoute(hash: string): Route {
  if (hash.startsWith('#auth/')) return { page: 'auth-link', id: '', params: new URLSearchParams() }
  const clean = hash.replace(/^#\/?/, '')
  const [path, query = ''] = clean.split('?', 2)
  const [page = '', encodedId = ''] = path.split('/')
  let id = ''
  try { id = decodeURIComponent(encodedId) } catch { return { page: 'not-found', id: '', params: new URLSearchParams() } }
  const aliases: Record<string, string> = { overview: 'dashboard', history: 'investigations', evidence: 'collections', imports: 'import' }
  return { page: aliases[page] || page, id, params: new URLSearchParams(query) }
}
export function routeHref(page: string, id = '', parameters: Record<string, string | number | undefined | null> = {}) {
  const params = new URLSearchParams()
  Object.entries(parameters).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') params.set(key, String(value)) })
  return '#/' + page + (id ? '/' + encodeURIComponent(id) : '') + (params.size ? '?' + params.toString() : '')
}
export function navigate(page: string, id = '', parameters: Record<string, string | number | undefined | null> = {}, replace = false) {
  const href = routeHref(page, id, parameters)
  if (replace) { history.replaceState(null, '', href); window.dispatchEvent(new HashChangeEvent('hashchange')) }
  else window.location.hash = href.slice(1)
}
export function useRoute() {
  const [hash, setHash] = useState(() => typeof window === 'undefined' ? '' : window.location.hash)
  useEffect(() => { const update = () => setHash(window.location.hash); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update) }, [])
  return parseRoute(hash)
}
export function logHref({ evidence, collection, run, investigation, row, ...filters }: {
  evidence?: string; collection?: string | null; run?: string | null; investigation?: string; row?: Evidence
  [key: string]: string | Evidence | undefined | null
}) {
  const correlation = row?.trace_id ? { trace_id: row.trace_id, request_id: null } : row?.request_id ? { request_id: row.request_id, trace_id: null } : row ? { trace_id: null, request_id: null } : {}
  return routeHref('logs', '', { ...filters as Record<string, string | undefined>, collection, run, investigation, evidence: evidence || row?.id, correlate: evidence && !row ? '1' : undefined, ...correlation })
}
export const filterKeys = ['trace_id', 'request_id', 'service', 'level', 'start_time', 'end_time', 'error_type', 'query'] as const
export function evidenceFilters(params: URLSearchParams) {
  const result = new URLSearchParams()
  for (const key of filterKeys) if (params.get(key)) result.set(key, params.get(key)!)
  return result
}
