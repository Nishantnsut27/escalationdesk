import { AlertCircle, CheckCircle2, CircleDot, Clock3, LoaderCircle, Terminal, TriangleAlert } from 'lucide-react'
import type { ReactNode } from 'react'
import { userMessage } from '../api'
import { sourceDate } from '../lib/time'
export function Brand() { return <a className="brand" href="#/" aria-label="TraceDesk home"><span className="brand-symbol"><Terminal size={21} /></span><span>TraceDesk</span></a> }
export function Spinner() { return <LoaderCircle size={16} aria-hidden="true" className="spin" /> }
export function StatusBadge({ state }: { state: string }) {
  const label = state.replaceAll('_', ' ')
  const good = ['completed', 'supported', 'connected', 'ok'].includes(state)
  const bad = ['failed', 'error', 'interrupted', 'disconnected'].includes(state)
  const Icon = good ? CheckCircle2 : bad ? AlertCircle : state === 'inconclusive' ? CircleDot : Clock3
  return <span className={'badge ' + (good ? 'good' : bad ? 'bad' : 'neutral')}><Icon size={12} aria-hidden="true" />{label}</span>
}
export function Severity({ level = 'unknown' }: { level?: string }) { return <span className={'severity severity-' + level}>{level === 'error' ? <AlertCircle size={12} aria-hidden="true" /> : level === 'warning' ? <TriangleAlert size={12} aria-hidden="true" /> : <CircleDot size={11} aria-hidden="true" />}{level}</span> }
export function Notice({ children, kind = 'info' }: { children: ReactNode; kind?: 'info' | 'error' | 'warning' }) { return <div className={'notice notice-' + kind} role={kind === 'error' ? 'alert' : 'status'}><TriangleAlert size={16} aria-hidden="true" /><div>{children}</div></div> }
export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) { return <Notice kind="error"><p>{userMessage(error)}</p>{retry && <button className="text-button" onClick={retry}>Try again</button>}</Notice> }
export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) { return <div className="empty-state"><h3>{title}</h3><div className="empty-description">{children}</div>{action}</div> }
export function LoadingState({ children = 'Loading saved evidence…' }: { children?: ReactNode }) { return <div className="loading-state" role="status"><Spinner />{children}</div> }
export function PageHeading({ eyebrow, title, children, actions }: { eyebrow?: string; title: string; children?: ReactNode; actions?: ReactNode }) { return <div className="page-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1>{children && <p>{children}</p>}</div>{actions && <div className="heading-actions">{actions}</div>}</div> }
export function shortTime(value?: string | null) { return sourceDate(value)?.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) || 'Time not recorded' }
