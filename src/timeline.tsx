import { useEffect, useRef } from 'react'
import { ArrowLeft, ArrowRight, Clock3 } from 'lucide-react'
import type { Evidence, LogPage } from './api'
import { Button } from './components/ui/button'
import { EmptyState, LoadingState, Severity, StatusBadge } from './components/common'
import { sourceDate, sourceTime } from './lib/time'
import { Card } from './components/ui/card'
import { Badge } from './components/ui/badge'

function TimelineRows({ rows, selected, onSelect }: { rows: Evidence[]; selected?: string; onSelect: (id: string) => void }) {
  const first = rows.map(row => sourceDate(row.timestamp)?.getTime()).find(time => time !== undefined)
  return <ol className="timeline-list">{rows.map(row => {
    const date = sourceDate(row.timestamp), elapsed = date && first !== undefined ? date.getTime() - first : null
    return <li key={row.id} className={(selected === row.id ? 'timeline-selected ' : '') + (row.level === 'error' || row.status === 'error' ? 'timeline-error' : '')}>
      <div className="timeline-time"><time dateTime={date ? row.timestamp! : undefined}>{sourceTime(row.timestamp)}</time><small>{date?.toLocaleDateString() || 'No source timestamp'}</small>{elapsed !== null && <span>+{elapsed < 1000 ? elapsed.toLocaleString() + ' ms' : (elapsed / 1000).toLocaleString(undefined, { maximumFractionDigits: 3 }) + ' s'}</span>}</div>
      <Card className="timeline-event" aria-current={selected === row.id ? 'true' : undefined}>
        <div className="timeline-event-meta"><strong>{row.service}</strong>{row.kind === 'span' ? <StatusBadge state={row.status || 'unknown'} /> : <Severity level={row.level} />}<Badge variant="outline">{row.kind}</Badge>{row.duration_ms != null && <span className="timeline-duration">{row.duration_ms.toLocaleString()} ms recorded</span>}</div>
        {(row.message || row.name || '').length > 240 ? <details className="timeline-content"><summary>{(row.message || row.name || '').slice(0, 160)}… <span>Expand message</span></summary><pre tabIndex={0}>{row.message || row.name}</pre></details> : <p className="timeline-message">{row.message || row.name || 'Metric sample'}</p>}
        <Button className="timeline-inspect" variant="ghost" size="sm" aria-label={'Inspect timeline observation ' + row.id} onClick={() => onSelect(row.id)}>Inspect source</Button>
      </Card>
    </li>
  })}</ol>
}

export function EvidenceTimeline({ data, selected, onSelect, onPage }: {
  data: LogPage | null; selected?: string; onSelect: (id: string) => void; onPage: (offset: number) => void
}) {
  const heading = useRef<HTMLDivElement>(null), scroll = useRef<HTMLDivElement>(null), requestedPage = useRef(false)
  useEffect(() => {
    if (!data || !requestedPage.current) return
    requestedPage.current = false
    scroll.current?.scrollTo({ top: 0, behavior: 'instant' })
    heading.current?.focus({ preventScroll: true })
  }, [data])
  function page(offset: number) { requestedPage.current = true; onPage(offset) }
  return <div className="evidence-timeline">
    <div className="timeline-heading" ref={heading} tabIndex={-1}>
      <span><Clock3 size={15} />{data ? data.total.toLocaleString() + ' matching observations' : 'Reading source time…'}</span>
      {data && <span>{data.unknown_timestamps.toLocaleString()} undated in this scope</span>}
    </div>
    <p className="context-note">Source time order. Offsets start at the first timed event on each page. Undated records follow; order alone does not establish causation.</p>
    {!data ? <LoadingState>Loading timeline observations…</LoadingState> : !data.evidence.length ? <EmptyState title="No events in this timeline">Clear a filter or widen the source time range.</EmptyState> : <>
      <div className="timeline-scroll" ref={scroll} role="region" aria-label="Chronological observations" tabIndex={0}><TimelineRows rows={data.evidence} selected={selected} onSelect={onSelect} /></div>
      <div className="timeline-pagination">
        <span role="status">{data.offset + 1}–{data.offset + data.evidence.length} of {data.total.toLocaleString()}</span>
        <div><Button variant="secondary" size="sm" disabled={data.offset === 0} onClick={() => page(Math.max(0, data.offset - 100))}><ArrowLeft size={14} />Previous timeline page</Button>
          <Button variant="secondary" size="sm" disabled={data.next_offset == null} onClick={() => page(data.next_offset!)}>Next timeline page<ArrowRight size={14} /></Button></div>
      </div>
    </>}
  </div>
}

export function RequestEvents({ rows, selected, onSelect }: { rows: Evidence[]; selected?: string; onSelect: (id: string) => void }) {
  return rows.length ? <div className="timeline-scroll"><TimelineRows rows={rows} selected={selected} onSelect={onSelect} /></div> : <EmptyState title="No recorded events for this request">Choose another request or trace.</EmptyState>
}
