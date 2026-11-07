import { useState } from 'react'
import { Pie, PieChart, Sector, type PieSectorShapeProps } from 'recharts'
import type { EvidenceSummary } from './api'
import { ChartContainer, type ChartConfig } from './components/ui/chart'
import { EmptyState } from './components/common'

const config: ChartConfig = {
  logs: { label: 'Logs', color: 'var(--chart-logs)' },
  spans: { label: 'Spans', color: 'var(--chart-spans)' },
  metrics: { label: 'Metrics', color: 'var(--chart-metrics)' },
}
const percent = (value: number, total: number) => total ? (value / total * 100).toLocaleString(undefined, { maximumFractionDigits: 1 }) + '%' : '0%'

export function observationDistribution(counts: Pick<EvidenceSummary['counts'], 'observations' | 'spans' | 'metrics'>) {
  return [
    { key: 'logs', value: counts.observations - counts.spans - counts.metrics, ...config.logs },
    { key: 'spans', value: counts.spans, ...config.spans },
    { key: 'metrics', value: counts.metrics, ...config.metrics },
  ]
}

export function ObservationDistribution({ counts }: { counts: EvidenceSummary['counts'] }) {
  const [pinned, setPinned] = useState<string | null>(null), [hovered, setHovered] = useState<string | null>(null)
  const data = observationDistribution(counts), active = hovered || pinned
  const selected = data.find(item => item.key === active)
  const present = data.filter(item => item.value > 0)
  if (!counts.observations) return <EmptyState title="No observations in this scope">Import evidence or choose a wider scope to see its composition.</EmptyState>

  return <div className="observation-distribution">
    <div className="distribution-visual">
      <ChartContainer config={config} className="distribution-chart" label="Saved observations by kind. Exact values and percentages follow.">
        <PieChart accessibilityLayer={false}>
          <Pie data={present} dataKey="value" nameKey="label" cx="50%" cy="50%" innerRadius="68%" outerRadius="94%" startAngle={90} endAngle={-270} stroke="var(--card)" strokeWidth={3} isAnimationActive={false}
            onMouseEnter={(_, index) => setHovered(present[index].key)} onMouseLeave={() => setHovered(null)}
            shape={(props: PieSectorShapeProps) => <Sector {...props} fill={present[props.index].color} className={'distribution-sector' + (active && active !== present[props.index].key ? ' is-dimmed' : '')} />} />
        </PieChart>
      </ChartContainer>
      <div className="distribution-center" aria-hidden="true"><strong>{(selected?.value ?? counts.observations).toLocaleString()}</strong><span>{selected?.label || 'observations'}</span><small>{selected ? percent(selected.value, counts.observations) + ' of saved evidence' : 'saved in this scope'}</small></div>
    </div>
    <div className="distribution-legend" aria-label="Observation types">
      {data.map(item => <button key={item.key} className="distribution-key" aria-pressed={pinned === item.key}
        onMouseEnter={() => setHovered(item.key)} onMouseLeave={() => setHovered(null)}
        onFocus={() => setHovered(item.key)} onBlur={() => setHovered(null)}
        onClick={() => setPinned(value => value === item.key ? null : item.key)}>
        <i style={{ background: item.color }} aria-hidden="true" /><span>{item.label}</span><strong>{item.value.toLocaleString()}</strong><small>{percent(item.value, counts.observations)}</small>
      </button>)}
    </div>
    <p className="small-note">Saved observations by kind. Each record is counted once.</p>
    <details className="chart-data"><summary>View distribution data</summary>
      <div className="table-scroll" tabIndex={0}><table><caption>Counts and shares of all saved observations in the selected scope.</caption>
        <thead><tr><th>Kind</th><th>Observations</th><th>Share</th></tr></thead>
        <tbody>{data.map(item => <tr key={item.key}><th scope="row">{item.label}</th><td>{item.value.toLocaleString()}</td><td>{percent(item.value, counts.observations)}</td></tr>)}</tbody>
        <tfoot><tr><th scope="row">Total</th><td>{counts.observations.toLocaleString()}</td><td>100%</td></tr></tfoot>
      </table></div>
    </details>
  </div>
}

export function ServiceBarChart({ services, total, href }: {
  services: EvidenceSummary['top_services']; total: number; href: (service: string) => string
}) {
  if (!services.length) return <EmptyState title="No error-level logs">Errors appear here when your saved evidence contains them.</EmptyState>
  const maximum = Math.max(...services.map(item => item.observations), 1)
  const displayed = services.reduce((sum, item) => sum + item.observations, 0)
  return <div className="service-bar-chart">
    <div className="service-axis" aria-hidden="true"><span>0</span><span>{Math.round(maximum / 2).toLocaleString()}</span><span>{maximum.toLocaleString()} logs</span></div>
    <div className="service-ranking">{services.map((service, index) => <a key={service.service} href={href(service.service)} className="service-bar-row">
      <div className="service-bar-heading"><span className="service-rank">{String(index + 1).padStart(2, '0')}</span><code>{service.service}</code><strong>{service.observations.toLocaleString()}</strong></div>
      <div className="ranking-track" aria-hidden="true"><span style={{ width: service.observations / maximum * 100 + '%' }} /></div>
      <div className="service-bar-detail"><span>{percent(service.observations, total)} of saved error-level logs</span><span>Explore observations</span></div>
    </a>)}</div>
    <p className="small-note">{displayed.toLocaleString()} of {total.toLocaleString()} error-level log observations shown{total > displayed ? '; other services account for ' + (total - displayed).toLocaleString() + '.' : '.'} Counts are not unique requests or failure rates.</p>
    <details className="chart-data"><summary>View service data</summary><div className="table-scroll" tabIndex={0}><table>
      <caption>Services ranked by saved error-level log observations. Shares use all {total.toLocaleString()} matching error-level logs.</caption>
      <thead><tr><th>Service</th><th>Observations</th><th>Share</th></tr></thead>
      <tbody>{services.map(item => <tr key={item.service}><th scope="row">{item.service}</th><td>{item.observations.toLocaleString()}</td><td>{percent(item.observations, total)}</td></tr>)}{total > displayed && <tr><th scope="row">Other services</th><td>{(total - displayed).toLocaleString()}</td><td>{percent(total - displayed, total)}</td></tr>}</tbody>
    </table></div></details>
  </div>
}
