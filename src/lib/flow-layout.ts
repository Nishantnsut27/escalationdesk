type Relationship = { source: string; target: string }

// Placement uses a spanning forest. The renderer retains every recorded edge.
export function treePositions(ids: string[], edges: Relationship[]) {
  const known = new Set(ids), outgoing = new Map(ids.map(id => [id, [] as string[]]))
  const incoming = new Set<string>()
  for (const edge of edges) {
    if (!known.has(edge.source) || !known.has(edge.target)) continue
    outgoing.get(edge.source)!.push(edge.target); incoming.add(edge.target)
  }
  const seen = new Set<string>(), positions = new Map<string, { x: number; y: number }>()
  let cursor = 0
  for (const root of [...ids.filter(id => !incoming.has(id)), ...ids]) {
    if (seen.has(root)) continue
    const children = new Map<string, string[]>(), order: string[] = [], depth = new Map([[root, 0]])
    const stack = [root]; seen.add(root)
    while (stack.length) {
      const id = stack.pop()!; order.push(id)
      const branches: string[] = []
      for (const child of outgoing.get(id)!) {
        if (seen.has(child)) continue
        seen.add(child); branches.push(child); depth.set(child, depth.get(id)! + 1)
      }
      children.set(id, branches); stack.push(...[...branches].reverse())
    }
    const widths = new Map<string, number>()
    for (const id of [...order].reverse()) widths.set(id, Math.max(1, children.get(id)!.reduce((sum, child) => sum + widths.get(child)!, 0)))
    const left = new Map([[root, cursor]])
    for (const id of order) {
      positions.set(id, { x: (left.get(id)! + (widths.get(id)! - 1) / 2) * 300, y: depth.get(id)! * 238 })
      let next = left.get(id)!
      for (const child of children.get(id)!) { left.set(child, next); next += widths.get(child)! }
    }
    cursor += widths.get(root)! + 0.75
  }
  return positions
}
