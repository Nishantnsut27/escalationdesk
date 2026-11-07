import { useEffect, useState, type FormEvent } from 'react'
import { CalendarRange, ChevronDown, Filter, Search, X } from 'lucide-react'
import type { AnalysisDetails } from './api'
import { filterKeys } from './routes'
import { sourceDate } from './lib/time'
import { Button } from './components/ui/button'
import { Input, Select } from './components/ui/fields'
import { Badge } from './components/ui/badge'

type ChangeFilters = (update: Record<string, string | number | null>) => void
const localTime = (value: string | null) => {
  const date = sourceDate(value)
  return date ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 23) : ''
}

export function LogFilters({ params, details, total, onChange }: {
  params: URLSearchParams; details: AnalysisDetails | null; total?: number; onChange: ChangeFilters
}) {
  const query = params.get('query') || ''
  const start = params.get('start_time'), end = params.get('end_time')
  const [search, setSearch] = useState(query)
  const [timeOpen, setTimeOpen] = useState(false)
  const [startDraft, setStartDraft] = useState(localTime(start)), [endDraft, setEndDraft] = useState(localTime(end))
  const [timeError, setTimeError] = useState('')
  useEffect(() => { setSearch(query) }, [query])
  useEffect(() => { setStartDraft(localTime(start)); setEndDraft(localTime(end)); setTimeError('') }, [start, end])
  const requestValue = params.get('trace_id') ? 'trace:' + params.get('trace_id') : params.get('request_id') ? 'request:' + params.get('request_id') : ''
  const active = filterKeys.filter(key => params.get(key))
  const labels: Record<string, string> = { trace_id: 'Trace', request_id: 'Request', service: 'Service', level: 'Severity', error_type: 'Error type', query: 'Search' }

  function applyTime(event: FormEvent) {
    event.preventDefault()
    const from = startDraft ? new Date(startDraft) : null, to = endDraft ? new Date(endDraft) : null
    if ((from && !Number.isFinite(from.getTime())) || (to && !Number.isFinite(to.getTime())) || (from && to && from > to)) {
      setTimeError('Enter a valid start time before the end time.')
      return
    }
    onChange({ start_time: startDraft === localTime(start) ? start : from?.toISOString() || null, end_time: endDraft === localTime(end) ? end : to?.toISOString() || null })
    setTimeError('')
    setTimeOpen(false)
  }

  return <div className="compact-filters">
    <form className="log-search" onSubmit={event => { event.preventDefault(); onChange({ query: search.trim() }) }}>
      <label htmlFor="log-search"><Search size={16} /><span className="sr-only">Search all retained content</span></label>
      <Input id="log-search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search messages, exceptions, source content…" maxLength={200} />
      {search && <Button variant="ghost" size="icon" type="button" aria-label="Clear search" onClick={() => { setSearch(''); onChange({ query: null }) }}><X size={15} /></Button>}
      <Button variant="secondary" size="sm" type="submit">Search</Button>
    </form>
    <div className="log-filters">
      <label>Request / trace<Select aria-label="Request / trace" value={requestValue} onChange={event => {
        const value = event.target.value
        onChange({ trace_id: value.startsWith('trace:') ? value.slice(6) : null, request_id: value.startsWith('request:') ? value.slice(8) : null, correlate: null })
      }}>
        <option value="">All requests and traces</option>
        <optgroup label="Trace IDs">{[...new Set([...(details?.traces || []), ...(params.get('trace_id') ? [params.get('trace_id')!] : [])])].map(id => <option key={id} value={'trace:' + id}>{id}</option>)}</optgroup>
        <optgroup label="Request IDs">{[...new Set([...(details?.requests || []), ...(params.get('request_id') ? [params.get('request_id')!] : [])])].map(id => <option key={id} value={'request:' + id}>{id}</option>)}</optgroup>
      </Select></label>
      <label>Service<Select aria-label="Service" value={params.get('service') || ''} onChange={event => onChange({ service: event.target.value })}>
        <option value="">All services</option>{[...new Set([...(details?.services || []), ...(params.get('service') ? [params.get('service')!] : [])])].map(service => <option key={service}>{service}</option>)}
      </Select></label>
      <label>Severity<Select aria-label="Severity" value={params.get('level') || ''} onChange={event => onChange({ level: event.target.value })}>
        <option value="">All severities</option>{['error', 'warning', 'info', 'debug', 'trace', 'unknown'].map(level => <option key={level}>{level}</option>)}
      </Select></label>
      <label>Error type<Select aria-label="Error type" value={params.get('error_type') || ''} onChange={event => onChange({ error_type: event.target.value })}>
        <option value="">All error types</option>{params.get('error_type') && !details?.catalogue.some(item => item.id === params.get('error_type')) && <option value={params.get('error_type')!}>{params.get('error_type')!.replaceAll('_', ' ')}</option>}{details?.catalogue.map(signature => <option key={signature.id} value={signature.id}>{signature.label}</option>)}
      </Select></label>
      <Button className="time-range-trigger" variant="secondary" size="sm" aria-expanded={timeOpen} aria-controls="time-range-fields" onClick={() => setTimeOpen(value => !value)}>
        <CalendarRange size={15} />{start || end ? 'Custom time' : 'Time range'}<ChevronDown size={14} className={timeOpen ? 'flipped' : ''} />
      </Button>
    </div>
    {timeOpen && <form id="time-range-fields" className="time-range-fields" onSubmit={applyTime}>
      <fieldset className="time-filter"><legend>Source time · your local time</legend>
        <label>Start time<Input type="datetime-local" step="0.001" aria-label="Start time" value={startDraft} onChange={event => setStartDraft(event.target.value)} aria-describedby={timeError ? 'time-range-error' : undefined} /></label>
        <span aria-hidden="true">→</span>
        <label>End time<Input type="datetime-local" step="0.001" aria-label="End time" value={endDraft} onChange={event => setEndDraft(event.target.value)} aria-describedby={timeError ? 'time-range-error' : undefined} /></label>
        <Button variant="secondary" size="sm" type="submit">Apply time range</Button>
      </fieldset>
      {timeError && <p id="time-range-error" role="alert" className="time-range-error">{timeError}</p>}
    </form>}
    {!!active.length && <div className="active-filters" aria-label="Active filters">
      {active.filter(key => !['start_time', 'end_time'].includes(key)).map(key => {
        const value = key === 'error_type' ? details?.catalogue.find(item => item.id === params.get(key))?.label || params.get(key) : params.get(key)
        return <Badge asChild variant="outline" key={key}><button title={labels[key] + ': ' + value} aria-label={'Remove ' + labels[key].toLowerCase() + ' filter'} onClick={() => onChange({ [key]: null })}><span>{labels[key]}: {value}</span><X size={12} /></button></Badge>
      })}
      {(start || end) && <Badge asChild variant="outline"><button aria-label="Clear time range" onClick={() => onChange({ start_time: null, end_time: null })}>
        <CalendarRange size={12} /><span>{start ? sourceDate(start)?.toLocaleString() : 'Any start'} – {end ? sourceDate(end)?.toLocaleString() : 'Any end'}</span><X size={12} />
      </button></Badge>}
    </div>}
    <div className="filter-summary">
      <span role="status"><Filter size={13} />{total === undefined ? 'Filtering saved observations…' : total.toLocaleString() + ' observations match'}</span>
      {!!active.length && <Button variant="ghost" size="sm" onClick={() => onChange(Object.fromEntries(filterKeys.map(key => [key, null])))}>Clear filters<X size={13} /></Button>}
      <span>Saved snapshots</span>
    </div>
  </div>
}
