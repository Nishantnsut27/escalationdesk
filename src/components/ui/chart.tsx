import type { ComponentProps, CSSProperties } from 'react'
import { ResponsiveContainer } from 'recharts'
import { cn } from '../../lib/utils'
export type ChartConfig = Record<string, { label: string; color: string }>
export function ChartContainer({ children, config, label, className }: { children: ComponentProps<typeof ResponsiveContainer>['children']; config: ChartConfig; label: string; className?: string }) {
  const colors = Object.fromEntries(Object.entries(config).map(([key, value]) => ['--color-' + key, value.color]))
  return <div data-slot="chart" className={cn('chart-container', className)} style={colors as CSSProperties} role="group" aria-label={label}>
    <ResponsiveContainer width="100%" height="100%" minWidth={1} minHeight={1}>{children}</ResponsiveContainer>
  </div>
}
