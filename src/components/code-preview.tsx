import type { ReactNode } from 'react'
import { FileCode2 } from 'lucide-react'

export function CodePreview({ filename, lines }: { filename: string; lines: ReactNode[] }) {
  return <figure className="code-preview">
    <figcaption><FileCode2 size={14} /><span>{filename}</span><small>Synthetic example</small></figcaption>
    <pre tabIndex={0} aria-label={filename + ' synthetic source'}><code>{lines.map((line, index) => <span className="code-line" key={index}><span className="line-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span><span>{line || '\u00a0'}</span></span>)}</code></pre>
  </figure>
}

export const exampleLogLines = [
  <><span className="syntax-comment">// checkout request · trace a73f</span></>,
  <><span className="syntax-key">INFO</span>  gateway  POST /checkout</>,
  <><span className="syntax-key">INFO</span>  billing  authorize(invoice)</>,
  <><span className="syntax-error">ERROR</span> billing  NullPointerException</>,
  <>  at Billing.authorize(<span className="syntax-string">Billing.java:42</span>)</>,
  <>  <span className="syntax-error">invoice is null</span></>,
  '',
  <><span className="syntax-error">ERROR</span> gateway  upstream request failed</>,
  <><span className="syntax-comment">// Source retained. Cause not yet established.</span></>,
]
