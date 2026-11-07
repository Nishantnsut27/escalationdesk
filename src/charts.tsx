import { useId, useState } from 'react'
import { Bar, BarChart, CartesianGrid, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts'
import type { HistogramData, HistogramBucket } from './api'
import { ChartContainer, type ChartConfig } from './components/ui/chart'
import { EmptyState } from './components/common'
import { Button } from './components/ui/button'
import { chartTime, sourceDate, sourceTime } from './lib/time'

const chartConfig: ChartConfig = {
  trace: { label: 'Trace', color: '#7d8796' }, debug: { label: 'Debug', color: '#9b8ed5' }, info: { label: 'Info', color: 'var(--primary)' },
  warning: { label: 'Warning', color: '#edbc6a' }, error: { label: 'Error', color: '#f17882' }, unknown: { label: 'Unknown', color: '#a0aab6' },
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
    <div className="chart-legend">{present.map(key => <span key={key}><i style={{ background: chartConfig[key].color }} aria-hidden="true" />{chartConfig[key].label}</span>)}<span className="chart-total">{data.timed.toLocaleString()} timed log observations</span></div>
    {data.buckets.length ? <ChartContainer config={chartConfig} label={label + '. Use the data table below to inspect exact values.'}>
      <BarChart data={data.buckets} margin={{ top: 16, right: 8, bottom: 2, left: -14 }} barCategoryGap="22%" onClick={state => {
        if (state.activeTooltipIndex == null) return
        const bucket = data.buckets[Number(state.activeTooltipIndex)]
        if (bucket) { setSelectedStart(bucket.start); onBucket?.(bucket) }
      }} accessibilityLayer>
        <defs>{present.map(key => <linearGradient key={key} id={id + key} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={chartConfig[key].color} /><stop offset="100%" stopColor={chartConfig[key].color} stopOpacity={0.58} /></linearGradient>)}</defs>
        <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 6" />
        <XAxis dataKey="start" tickFormatter={tick} tickLine={false} axisLine={false} minTickGap={26} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={52} tick={{ fill: 'var(--muted-foreground)', fontSize: 11 }} />
        <Tooltip cursor={{ fill: 'var(--chart-hover)' }} content={({ active, payload }) => {
          const bucket = payload?.[0]?.payload as HistogramBucket | undefined
          if (!active || !bucket) return null
          return <div className="chart-tooltip"><strong>{sourceDate(bucket.start)?.toLocaleDateString()}</strong><span>{sourceTime(bucket.start)} – {sourceTime(bucket.end)}</span>
            {present.map(key => <div key={key}><span><i style={{ background: chartConfig[key].color }} />{chartConfig[key].label}</span><b>{Number(bucket[key as keyof HistogramBucket]).toLocaleString()}</b></div>)}
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
