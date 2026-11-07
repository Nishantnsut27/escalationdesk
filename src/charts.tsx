import { useId, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import type { HistogramData, HistogramBucket } from './api'
import { ChartContainer, type ChartConfig } from './components/ui/chart'
import { EmptyState } from './components/common'
import { Button } from './components/ui/button'
import { chartTime, sourceDate, sourceTime } from './lib/time'

const chartConfig: ChartConfig = {
  trace: { label: 'Trace', color: 'var(--chart-trace)' }, debug: { label: 'Debug', color: 'var(--chart-debug)' }, info: { label: 'Info', color: 'var(--chart-info)' },
  warning: { label: 'Warning', color: 'var(--chart-warning)' }, error: { label: 'Error', color: 'var(--chart-error)' }, unknown: { label: 'Unknown', color: 'var(--chart-unknown)' },
}
export function Histogram({ data, onBucket, label = 'Log volume by severity' }: { data: HistogramData; onBucket?: (bucket: HistogramBucket) => void; label?: string }) {
  const id = useId().replaceAll(':', '')
  const [selectedStart, setSelectedStart] = useState('')
  const present = Object.keys(chartConfig).filter(key => data.buckets.some(bucket => Number(bucket[key as keyof HistogramBucket]) > 0))
  const first = sourceDate(data.buckets[0]?.start)?.getTime() || 0
  const interval = (sourceDate(data.buckets[1]?.start)?.getTime() || (sourceDate(data.buckets[0]?.end)?.getTime() || first) + 1) - first
  const extent = (sourceDate(data.buckets.at(-1)?.end)?.getTime() || first) - first
  const index = Math.max(0, data.buckets.findIndex(bucket => bucket.start === selectedStart)), selected = data.buckets[index]
  const tick = (value: string) => chartTime(value, interval, extent)
  return <div className="histogram">
    <div className="chart-legend">{present.map(key => <span key={key}><i className="chart-swatch" data-severity={key} style={{ color: chartConfig[key].color, backgroundColor: ['info', 'error'].includes(key) ? chartConfig[key].color : 'var(--background)' }} aria-hidden="true" />{chartConfig[key].label}</span>)}<span className="chart-total">{data.timed.toLocaleString()} timed log observations</span></div>
    {data.buckets.length ? <ChartContainer config={chartConfig} label={label + '. Use the data table below to inspect exact values.'}>
      <BarChart data={data.buckets} margin={{ top: 16, right: 8, bottom: 2, left: -14 }} barCategoryGap="22%" onClick={state => {
        if (state.activeTooltipIndex == null) return
        const bucket = data.buckets[Number(state.activeTooltipIndex)]
        if (bucket) { setSelectedStart(bucket.start); onBucket?.(bucket) }
      }} accessibilityLayer>
        <defs>{present.map(key => <pattern key={key} id={id + key} width="8" height="8" patternUnits="userSpaceOnUse">
          <rect width="8" height="8" fill={['info', 'error'].includes(key) ? chartConfig[key].color : 'var(--background)'} />
          {key === 'trace' && <><circle cx="2" cy="2" r="1.5" fill={chartConfig[key].color} /><circle cx="6" cy="6" r="1.5" fill={chartConfig[key].color} /></>}
          {['debug', 'warning'].includes(key) && <path d="M-2 2L2-2M0 8L8 0M6 10L10 6" stroke={chartConfig[key].color} strokeWidth="2" />}
          {key === 'unknown' && <path d="M0 2H8M0 6H8" stroke={chartConfig[key].color} strokeWidth="1" />}
        </pattern>)}</defs>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 6" />
        <XAxis dataKey="start" tickFormatter={tick} tickLine={false} axisLine={false} minTickGap={26} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={52} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
        <Tooltip cursor={{ fill: 'var(--chart-hover)' }} content={({ active, payload }) => {
          const bucket = payload?.[0]?.payload as HistogramBucket | undefined
          if (!active || !bucket) return null
          return <div className="chart-tooltip"><strong>{sourceDate(bucket.start)?.toLocaleDateString()}</strong><span>{sourceTime(bucket.start)} – {sourceTime(bucket.end)}</span>
            {present.map(key => <div key={key}><span><i className="chart-swatch" data-severity={key} style={{ color: chartConfig[key].color, backgroundColor: ['info', 'error'].includes(key) ? chartConfig[key].color : 'var(--background)' }} />{chartConfig[key].label}</span><b>{Number(bucket[key as keyof HistogramBucket]).toLocaleString()}</b></div>)}
            <footer><span>Total observations</span><b>{bucket.count.toLocaleString()}</b></footer>
          </div>
        }} />
        {onBucket && selectedStart && selected && <ReferenceLine x={selected.start} stroke="var(--primary)" strokeDasharray="3 3" />}
        {present.map((key, position) => <Bar key={key} dataKey={key} name={chartConfig[key].label} stackId="volume" fill={'url(#' + id + key + ')'} radius={position === present.length - 1 ? [3, 3, 0, 0] : undefined} maxBarSize={52} activeBar={{ stroke: chartConfig[key].color, strokeWidth: 1.5 }} isAnimationActive={false} />)}
      </BarChart>
    </ChartContainer> : <EmptyState title="No timed logs in this view">Change the filters or inspect observations without timestamps below.</EmptyState>}
    <div className="chart-foot"><span>{data.unknown_timestamps.toLocaleString()} logs without a timestamp, excluded from buckets.</span><span>Source time · {Intl.DateTimeFormat().resolvedOptions().timeZone}</span></div>
    {!!data.buckets.length && <details className="chart-data"><summary>View chart data as a table</summary><div className="table-scroll" tabIndex={0}><table>
      <caption>{label}. Counts cover the complete filtered dataset, independent of observation pages.</caption>
      <thead><tr><th>Source time</th>{present.map(key => <th key={key}>{chartConfig[key].label}</th>)}<th>Total</th>{onBucket && <th>Filter</th>}</tr></thead>
      <tbody>{data.buckets.map(bucket => <tr key={bucket.start}><td><time dateTime={bucket.start}>{sourceDate(bucket.start)?.toLocaleDateString()} {sourceTime(bucket.start)}</time></td>{present.map(key => <td key={key}>{bucket[key as keyof HistogramBucket]}</td>)}<td>{bucket.count.toLocaleString()}</td>{onBucket && <td><Button variant="ghost" size="sm" onClick={() => onBucket(bucket)}>Show this range</Button></td>}</tr>)}</tbody>
    </table></div></details>}
  </div>
}
