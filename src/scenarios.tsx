import { ArrowUpRight, ShieldCheck } from 'lucide-react'
import type { DemoCase } from './api'
import { routeHref } from './routes'
import { CollectionWorkspace, useData } from './workspace'
import { EmptyState, ErrorState, LoadingState, PageHeading, shortTime } from './components/common'

export function CaseCards({ cases }: { cases: DemoCase[]; onSelect?: (slug: string) => void }) {
  return <div className="case-grid">{cases.map((item, index) => <a className="case-card" key={item.slug} href={routeHref('demo', item.slug)}>
    <div className="case-card-top"><span className="case-number">{String(index + 1).padStart(2, '0')}</span><span className="subtle-badge">{item.category}</span><ArrowUpRight size={18} /></div>
    <h2>{item.title}</h2><p>{item.description}</p><div className="case-counts"><strong>{item.counts.log.toLocaleString()} logs</strong><span>{item.counts.span} spans · {item.counts.metric} metrics</span></div>
    <footer>Recorded {shortTime(item.recorded_at)}</footer>
  </a>)}</div>
}
export function DemoWorkspace({ slug = '' }: { slug?: string }) {
  const { value: cases, error, retry } = useData<DemoCase[]>('/demo/cases')
  if (error) return <ErrorState error={error} retry={retry} />
  if (!cases) return <LoadingState>Opening recorded demos…</LoadingState>
  const selected = cases.find(item => item.slug === slug)
  if (slug) return selected ? <CollectionWorkspace key={selected.collection_id} id={selected.collection_id} readOnly /> : <EmptyState title="This recording is unavailable"><a href="#/demo">Browse the published demo recordings.</a></EmptyState>
  return <div className="demo-workspace"><PageHeading eyebrow="RECORDED DEMONSTRATION" title="Follow a saved investigation." actions={<span className="demo-readonly"><ShieldCheck size={16} />Read-only access</span>}>Explore {cases.length} reviewed recordings of controlled failures. Open the assessment, then inspect the evidence behind it.</PageHeading><p className="demo-disclosure">These are recorded examples, not live application incidents. Browsing them makes no analysis requests.</p>{cases.length ? <CaseCards cases={cases} /> : <EmptyState title="No published recordings">Demo cases appear here after review.</EmptyState>}</div>
}
