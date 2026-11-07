import { ArrowRight, ArrowUpRight, ChevronRight, FileText, FileCheck2, GitBranch, LockKeyhole, Play, Search } from 'lucide-react'
import { Brand } from './components/common'
import { Button } from './components/ui/button'
import { FeatureCarousel } from './components/ui/feature-carousel'
import { FadeContent } from './components/ui/fade-content'

export function WorkflowPreview() {
  return <FeatureCarousel features={[
    { id: 'evidence', label: 'Select evidence', content: <div className="preview-reading">
      <div className="preview-explanation"><h3>Start with what happened.</h3><p>Bring the error and the observations around it into one collection. Keep the original context close.</p><span className="preview-annotation">Save a snapshot. Keep its context.</span></div>
      <div className="story-scene story-collection"><div className="story-document"><div className="story-document-heading"><FileText size={18} /><div><strong>Checkout failure</strong><span>Saved evidence collection</span></div><span className="story-caption">Example</span></div><div className="story-observation"><span className="story-dot" /><div><strong>Checkout request received</strong><span>Gateway · Request started</span></div></div><div className="story-observation"><span className="story-dot is-error" /><div><strong>Invoice reference is missing</strong><span>Billing · Authorization failed</span></div></div><div className="story-observation"><span className="story-dot is-error" /><div><strong>Checkout returned an error</strong><span>Gateway · Same recorded trace</span></div></div><footer>Three observations. One place to start.</footer></div></div>
    </div> },
    { id: 'investigate', label: 'Investigate', content: <div className="preview-reading">
      <div className="preview-explanation"><h3>Give evidence a closer look.</h3><p>Follow the recorded request across services. Separate the failure you can observe from the cause you still need to establish.</p><span className="preview-annotation">Context before conclusions.</span></div>
      <div className="story-scene story-relationship"><span className="story-caption">One recorded request</span><div className="story-flow"><div className="story-node"><GitBranch size={20} /><strong>Gateway</strong><span>Checkout request</span></div><div className="story-edge"><ArrowRight size={18} /><span>calls</span></div><div className="story-node is-error"><Search size={20} /><strong>Billing</strong><span>Authorization failed</span></div></div><div className="story-insight"><span className="story-dot is-error" /><p>The exception and failed checkout share recorded trace context.</p></div><span className="story-footnote">Only recorded relationships are shown.</span></div>
    </div> },
    { id: 'assessment', label: 'Read assessment', content: <div className="preview-reading">
      <div className="preview-explanation"><h3>A finding you can inspect.</h3><p>Get an assessment with supporting observations and clearly stated limits. Every finding gives you somewhere to look next.</p><span className="preview-annotation">Claims stay attached to sources.</span></div>
      <div className="story-scene"><div className="story-document story-assessment"><div className="story-document-heading"><FileCheck2 size={18} /><strong>Illustrative assessment</strong><span className="story-caption">Supported</span></div><p className="story-finding">Billing authorization encountered a null invoice reference.</p><div className="story-citation"><span>01</span>Exception recorded in billing<ArrowUpRight size={14} /></div><div className="story-citation"><span>02</span>Checkout failed in the same trace<ArrowUpRight size={14} /></div><footer><strong>Still unknown</strong>Why the invoice was absent.</footer></div></div>
    </div> },
    { id: 'source', label: 'Inspect source', content: <div className="preview-reading">
      <div className="preview-explanation"><h3>Go straight to the source.</h3><p>Follow a citation to the exact observation. Read the retained evidence, then decide what to check next.</p><span className="preview-annotation">From a conclusion back to its context.</span></div>
      <div className="story-scene"><div className="story-source-path"><span>Assessment</span><ArrowRight size={15} /><span>Supporting source</span></div><div className="story-document story-source"><div className="story-document-heading"><span className="story-dot is-error" /><strong>Billing authorization</strong><span className="story-caption">Error</span></div><p className="story-finding">Invoice reference was null.</p><dl><div><dt>Location</dt><dd>Billing.java · line 42</dd></div><div><dt>Context</dt><dd>Checkout request</dd></div><div><dt>Original observation</dt><dd>Retained for inspection</dd></div></dl><footer>The assessment stays connected to its evidence.</footer></div></div>
    </div> },
  ]} />
}

export function DataUse() {
  return <section className="data-use" id="data-use"><LockKeyhole size={18} /><div>
    <h2>Data use & processing</h2>
    <p>Saved observations and reports belong to your account. Common credential patterns are redacted; review your evidence before investigating. An investigation sends selected excerpts to external AI providers. Browsing, importing and opening reports do not start analysis.</p>
    <details><summary>Provider and processing details</summary><p>TraceDesk uses TypeSafe Jev through OpenRouter to classify evidence, and Groq to assess it and write a report. Provider credentials remain on the server. Reports may be inconclusive and should be checked against their sources.</p></details>
  </div></section>
}

const questions = [
  ['Do I need a Sentry connection?', 'No. Paste logs or import a UTF-8 .log, .txt or .jsonl file. Sentry is an optional way to import saved evidence windows.'],
  ['Is this live monitoring?', 'The dashboard describes saved snapshots. Live infrastructure monitoring is not connected. Missing timestamps, traces and metrics remain visible as coverage gaps.'],
  ['Will every investigation find a root cause?', 'No. A report is supported or inconclusive based on the supplied evidence. It separates the observed failure mechanism from unknown deeper causes.'],
  ['What can I do in the demo?', 'Browse reviewed recordings and their saved reports. Demo access is read-only and makes no analysis requests.'],
]

export default function Home() {
  return <div className="home-page">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>Skip to content</a>
    <header className="home-nav"><Brand /><span className="home-nav-note">Application failure analysis</span><nav aria-label="Public navigation"><a href="#/login">Sign in</a><Button asChild variant="secondary"><a href="#/signup">Create account<ArrowUpRight size={14} /></a></Button></nav></header>
    <main id="main-content" tabIndex={-1}>
      <section className="hero">
        <div className="hero-copy"><span className="eyebrow">FOLLOW THE EVIDENCE</span><h1>Every failure<br /><span>leaves a trail.</span></h1></div>
        <div className="hero-introduction"><p>Application logs in. A source-linked assessment out. TraceDesk helps you inspect the failure, understand the context, and decide what to check next.</p><div className="hero-actions"><Button asChild><a href="#/signup">Create workspace<ArrowRight size={15} /></a></Button><a className="button ghost" href="#/demo-login"><Play size={13} />Open recorded demo</a></div></div>
      </section>
      <WorkflowPreview />
      <div className="source-strip"><span>START WITH YOUR EVIDENCE</span><span>Log files</span><span>Pasted logs</span><span>Sentry snapshots</span><span className="source-strip-note">Your logs. Your service names.</span></div>
      <FadeContent><section className="home-workflow" aria-labelledby="workflow-title"><div className="home-section-title"><span className="eyebrow">/ WORKFLOW</span><h2 id="workflow-title">A shorter path<br />through the logs.</h2><a className="text-link" href="#/demo-login">Inspect a recorded investigation<ArrowUpRight size={14} /></a></div><div className="workflow-columns">{[
        ['01', 'Collect', 'Import logs or a Sentry snapshot. Review source coverage before you investigate.'],
        ['02', 'Investigate', 'Read the assessed mechanism, supporting observations, and what remains unknown.'],
        ['03', 'Inspect', 'Follow citations into searchable logs, request flow and a chronological timeline.'],
      ].map(([number, title, text]) => <article key={number}><span className="workflow-number">{number}</span><div><h3>{title}</h3><p>{text}</p></div></article>)}</div></section></FadeContent>
      <FadeContent><section className="home-faq"><div><span className="eyebrow">/ BEFORE YOU START</span><h2>Know the boundaries.</h2><p>Saved evidence. Explicit investigation. Inspectable results.</p></div><div>{questions.map(([question, answer]) => <details key={question}><summary>{question}<ChevronRight size={15} /></summary><p>{answer}</p></details>)}</div></section></FadeContent>
      <DataUse />
      <section className="home-cta"><div><span className="eyebrow">/ START A COLLECTION</span><h2>Bring a failure worth investigating.</h2></div><Button asChild><a href="#/signup">Create account<ArrowRight size={15} /></a></Button></section>
    </main><footer className="home-footer"><Brand /><span>Evidence-led investigation.</span><a href="#/privacy">Data use<ArrowUpRight size={13} /></a></footer>
  </div>
}
