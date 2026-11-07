import { useMemo, useRef } from 'react'
import { Background, ReactFlow, ReactFlowProvider, MiniMap, Handle, Position, MarkerType, useReactFlow, useViewport, type Node, type NodeProps } from '@xyflow/react'
import { Focus, Maximize2, Minimize2, Minus, Plus, Server, TriangleAlert } from 'lucide-react'
import type { FlowData, FlowNode } from './api'
import { EmptyState, StatusBadge } from './components/common'
import { Button } from './components/ui/button'
import { RequestEvents } from './timeline'
import { treePositions } from './lib/flow-layout'
import { navigate, useRoute } from './routes'

type ServiceNode = Node<{ observation: FlowNode; onSelect: (id: string) => void }, 'service'>
function ServiceCard({ data, selected }: NodeProps<ServiceNode>) {
  const node = data.observation
  return <>
    <Handle type="target" position={Position.Left} />
    <button className={'service-flow-card nodrag' + (selected ? ' is-selected' : '') + (node.status === 'error' ? ' is-error' : '')}
      aria-label={`Inspect operation ${node.service}: ${node.operation}, ${node.status}`} aria-pressed={selected} title={node.operation} onClick={() => data.onSelect(node.id)}>
      <span className="flow-service"><span className="flow-service-icon"><Server size={17} /></span><strong>{node.service}</strong></span>
      <span className="flow-operation">{node.operation}</span>
      <span className="flow-measure"><StatusBadge state={node.status} /><span>{node.duration_ms == null ? 'Duration unknown' : node.duration_ms.toLocaleString(undefined, { maximumFractionDigits: 3 }) + ' ms'}</span></span>
      <span className="flow-parent">{node.missing_parent ? <><TriangleAlert size={12} />{node.ambiguous_parent ? 'Ambiguous parent' : 'Parent not recorded'}</> : node.parent_span_id ? 'Recorded parent relationship' : 'No parent recorded'}</span>
    </button>
    <Handle type="source" position={Position.Right} />
  </>
}
const nodeTypes = { service: ServiceCard }
export function layoutFlow(data: FlowData, onSelect: (id: string) => void = () => {}, selected?: string): ServiceNode[] {
  const positions = treePositions(data.nodes.map(node => node.id), data.edges)
  return data.nodes.map(observation => {
    const point = positions.get(observation.id)!
    return { id: observation.id, type: 'service', position: { x: point.y / 238 * 328, y: point.x / 300 * 226 },
      selected: selected === observation.id, data: { observation, onSelect }, style: { width: 248, height: 178 } }
  })
}
function FlowCanvas({ data, selected, onSelect }: { data: FlowData; selected?: string; onSelect: (id: string) => void }) {
  const nodes = useMemo(() => layoutFlow(data, onSelect, selected), [data, selected, onSelect])
  const edges = useMemo(() => data.edges.map(edge => ({ ...edge, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--text-secondary)' }, style: { stroke: 'var(--text-secondary)', strokeWidth: 1.6 } })), [data])
  const graph = useReactFlow(), viewport = useViewport(), route = useRoute(), expanded = route.params.get('canvas') === 'expanded'
  const first = nodes.find(node => node.id === selected) || nodes[0]
  // Fit-all can shrink a long trace beyond readability. Open on a full-size operation instead.
  const initialViewport = useRef({ x: 32 - (first?.position.x || 0), y: 64 - (first?.position.y || 0), zoom: 1 })
  function readableView() {
    const target = nodes.find(node => node.id === selected) || nodes[0]
    if (target) void graph.setCenter(target.position.x + 124, target.position.y + 89, { zoom: 1 })
  }
  function expand() { const next = new URLSearchParams(route.params); expanded ? next.delete('canvas') : next.set('canvas', 'expanded'); navigate('logs', '', Object.fromEntries(next)) }
  return <>
    <div className="flow-toolbar" role="group" aria-label="Graph navigation">
      <Button variant="secondary" size="sm" onClick={() => graph.fitView({ padding: 0.18, maxZoom: 1 })}><Maximize2 size={14} />Fit graph</Button>
      <Button variant="secondary" size="sm" onClick={readableView}>Readable view</Button>
      <Button variant="secondary" size="sm" disabled={!nodes.some(node => node.id === selected)} onClick={() => graph.fitView({ nodes: [{ id: selected! }], maxZoom: 1, padding: 0.7 })}><Focus size={14} />Center selected</Button>
      <span className="flow-zoom"><Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => graph.zoomOut()}><Minus size={15} /></Button><output aria-label="Zoom percentage">{Math.round(viewport.zoom * 100)}%</output><Button variant="ghost" size="icon" aria-label="Zoom in" onClick={() => graph.zoomIn()}><Plus size={15} /></Button></span>
      <Button className="flow-expand" variant="ghost" size="sm" aria-pressed={expanded} onClick={expand}>{expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}{expanded ? 'Compact canvas' : 'Expand canvas'}</Button>
    </div>
    <div className={'flow-canvas' + (expanded ? ' is-expanded' : '')}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} defaultViewport={initialViewport.current} minZoom={0.12} maxZoom={1.6}
        nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false}
        zoomOnScroll={false} panOnScroll={false} preventScrolling={false} colorMode="dark" attributionPosition="bottom-right">
        <Background color="var(--border)" gap={24} size={1} />
        {nodes.length > 4 && <MiniMap pannable zoomable ariaLabel="Recorded flow minimap" nodeColor={node => node.selected ? 'var(--primary)' : 'var(--border-strong)'} maskColor="var(--flow-mask)" />}
      </ReactFlow>
    </div>
    <p className="small-note flow-help">Follow the request from left to right. Drag to pan, or use Fit graph for the whole request and Readable view for full-size operations. Select an operation to inspect its source.</p>
  </>
}
export function RequestFlow({ data, selected, onSelect }: { data: FlowData; selected?: string; onSelect: (id: string) => void }) {
  return <div className="request-flow"><p className="context-note"><strong>{data.scope}</strong> · {data.note}</p>{data.mode === 'unselected' ? <EmptyState title="Choose a request or trace">Use the exact request / trace filter above to inspect recorded relationships.</EmptyState> : data.mode === 'events' ? <RequestEvents rows={data.events} selected={selected} onSelect={onSelect} /> : <>
    <ReactFlowProvider><FlowCanvas data={data} selected={selected} onSelect={onSelect} /></ReactFlowProvider>
    <details className="chart-data"><summary>View recorded operations as a table</summary><div className="table-scroll" tabIndex={0}><table><caption>Only recorded parent-span relationships are shown. Missing parents and disconnected spans remain separate.</caption><thead><tr><th>Service / operation</th><th>Status</th><th>Measured duration</th><th>Recorded parent</th><th>Source</th></tr></thead><tbody>{data.nodes.map(node => <tr key={node.id}><td><strong>{node.service}</strong><br />{node.operation}</td><td><StatusBadge state={node.status} /></td><td>{node.duration_ms == null ? 'Not recorded' : node.duration_ms + ' ms'}</td><td>{node.parent_span_id || 'No parent recorded'}{node.missing_parent && (node.ambiguous_parent ? ' (ambiguous)' : ' (unavailable)')}</td><td><Button variant="ghost" size="sm" onClick={() => onSelect(node.id)}>Inspect observation</Button></td></tr>)}</tbody></table></div></details>
  </>}</div>
}
