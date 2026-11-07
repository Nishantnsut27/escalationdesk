/** Source time needs an explicit timezone; browser locale must not invent it. */
export function sourceDate(value?: string | null): Date | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}(?::?\d{2})?)$/.test(value)) return null
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  if (month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null
  const parsed = new Date(value.replace(/([+-]\d{2})$/, '$1:00'))
  return Number.isFinite(parsed.getTime()) ? parsed : null
}

export function sourceTime(value?: string | null) {
  const date = sourceDate(value)
  if (!date) return 'Time unknown'
  const fraction = value?.match(/\.(\d+)/)?.[1] || '000'
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) + '.' + fraction.padEnd(3, '0')
}

export function chartTime(value: string, intervalMs: number, extentMs: number) {
  const date = sourceDate(value)
  if (!date) return 'Undated'
  if (extentMs >= 86400000) return date.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
  return date.toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', hour12: false,
    ...(intervalMs < 60000 ? { second: '2-digit' as const } : {}),
    ...(intervalMs < 1000 ? { fractionalSecondDigits: 3 as const } : {}),
  })
}
