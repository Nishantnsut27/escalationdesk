import { useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, FileText, ShieldCheck } from 'lucide-react'
import { api, userMessage, type Session } from './api'
import { PasswordField, AccountPage } from './account'
import { Brand, Notice, Spinner } from './components/common'
import { Button } from './components/ui/button'
import { CodePreview, exampleLogLines } from './components/code-preview'

export function Auth({ mode = 'login', onSession }: { mode?: string; onSession: (session: Session) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [demoState, setDemoState] = useState<{ demo_available: boolean; demo_case_count: number } | null>(null)
  const signup = mode === 'signup', demo = mode === 'demo-login'
  useEffect(() => { if (!demo) return; let alive = true; api<{ demo_available: boolean; demo_case_count: number }>('/health').then(value => { if (alive) setDemoState(value) }).catch(e => { if (alive) setError(userMessage(e)) }); return () => { alive = false } }, [demo])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    try { onSession(await api<Session>(demo ? '/auth/demo-login' : signup ? '/auth/signup' : '/auth/login', demo ? { password: 'Demo@login' } : Object.fromEntries(new FormData(event.currentTarget)))) }
    catch (e) { setError(userMessage(e)) } finally { setBusy(false) }
  }
  if (mode === 'forgot') return <AccountPage onExit={() => { location.hash = '/login' }} onRegister={() => { location.hash = '/signup' }} />
  return <div className="auth-page"><section className="auth-story"><Brand /><div><span className="eyebrow">// YOUR INVESTIGATION WORKSPACE</span><h1>Keep the source<br />in sight.</h1><p>Collect the logs. Investigate the failure. Follow the evidence.</p><CodePreview filename="checkout.log" lines={exampleLogLines.slice(0, 6)} /><div className="auth-story-line"><FileText size={18} /><span>Evidence → Investigation → Understanding</span></div></div><span className="auth-story-foot">saved evidence / explicit investigation / source citations</span></section><section className="auth-form-side"><a className="back-link" href="#/"><ArrowLeft size={14} />Back to TraceDesk</a><div className="auth-form-wrap"><span className="eyebrow">{demo ? 'RECORDED DEMO' : signup ? 'YOUR WORKSPACE' : 'WELCOME BACK'}</span><h1>{demo ? 'Explore a real recording.' : signup ? 'Create your account.' : 'Sign in to TraceDesk.'}</h1><p>{demo ? 'Follow saved investigations and inspect their sources.' : signup ? 'Bring your evidence. Start an investigation when you are ready.' : 'Pick up where your investigation left off.'}</p><div className="auth-tabs"><a href="#/login" aria-current={!signup && !demo ? 'page' : undefined}>Sign in</a><a href="#/signup" aria-current={signup ? 'page' : undefined}>Create account</a><a href="#/demo-login" aria-current={demo ? 'page' : undefined}>Demo</a></div>
      <form onSubmit={submit}>{signup && <label>Your name<input name="name" autoComplete="name" required maxLength={60} disabled={busy} placeholder="Your name" /></label>}{!demo && <><label>Email address<input name="email" type="email" autoComplete="email" required maxLength={200} disabled={busy} placeholder="you@example.com" /></label><PasswordField label="Password" name="password" autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 10 : 1} maxLength={128} disabled={busy} placeholder={signup ? 'At least 10 characters' : 'Your password'} /></>}{demo && <Notice>{demoState ? demoState.demo_available ? <>{demoState.demo_case_count} reviewed recordings. Read-only access. No analysis requests while browsing.</> : 'Recorded demos are unavailable. You can create an account and import your own logs.' : 'Checking recorded demos…'}</Notice>}{error && <Notice kind="error">{error}</Notice>}<Button className="full" disabled={busy || (demo && !demoState?.demo_available)}>{busy ? <Spinner /> : null}{demo ? 'Enter recorded demo' : signup ? 'Create account' : 'Sign in'}<ArrowRight size={16} /></Button></form>
      {!signup && !demo && <a className="forgot-link" href="#/forgot">Forgot password?</a>}<p className="auth-privacy"><ShieldCheck size={16} />{demo ? 'Example data is clearly labelled throughout.' : 'Your saved evidence belongs to your account.'}</p><a className="text-link" href="#/privacy">How evidence is processed<ArrowUpRightIcon /></a>
    </div></section></div>
}
function ArrowUpRightIcon() { return <span aria-hidden="true">↗</span> }
