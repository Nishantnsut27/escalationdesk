import { createContext, useContext, useEffect, useId, useState, type FormEvent, type InputHTMLAttributes } from 'react'
import { ArrowLeft, Eye, EyeOff, LoaderCircle, Mail, ShieldCheck } from 'lucide-react'
import { api, type User } from './api'

export type AuthenticationLink = { purpose: 'verify' | 'reset'; token: string }
export const WritePermission = createContext(false)
export function useWritePermission() { return useContext(WritePermission) }
export function parseAuthenticationLink(fragment: string): AuthenticationLink | null {
  const match = /^#auth\/(verify|reset)\/([A-Za-z0-9_-]{32,128})$/.exec(fragment)
  return match ? { purpose: match[1] as 'verify' | 'reset', token: match[2] } : null
}

export function PasswordField({ label, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  return <div className="password-field"><label htmlFor={id}>{label}</label><div className="password-input">
    <input {...props} id={id} type={visible ? 'text' : 'password'} />
    <button type="button" disabled={props.disabled} aria-label={`${visible ? 'Hide' : 'Show'} ${label.toLowerCase()}`} aria-pressed={visible} aria-controls={id} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={18} /> : <Eye size={18} />}</button>
  </div></div>
}

export function AccountPage({ link, invalid = false, onExit, onVerified, onReset, onRegister }: {
  link?: AuthenticationLink; invalid?: boolean; onExit: () => void; onVerified?: () => void; onReset?: () => void; onRegister?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const verify = link?.purpose === 'verify'
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError('')
    const fields = new FormData(event.currentTarget)
    if (link?.purpose === 'reset' && fields.get('password') !== fields.get('confirm')) {
      setError('The passwords do not match.'); return
    }
    setBusy(true)
    try {
      const response = await api<{ message: string }>(link ? verify ? '/auth/verification-confirmation' : '/auth/reset-password' : '/auth/forgot-password',
        link ? { token: link.token, ...(verify ? {} : { password: fields.get('password') }) } : { email: fields.get('email') })
      setMessage(response.message); setDone(true)
      if (link?.purpose === 'reset') onReset?.()
    } catch (e) { setError(e instanceof Error ? e.message : 'The request could not be completed.') }
    finally { setBusy(false) }
  }
  return <div className="account-page"><section className="auth-form-wrap account-card" aria-labelledby="account-title">
    <span className="tiny-label">TRACEDESK · ACCOUNT ACCESS</span>
    <h1 id="account-title">{invalid ? 'This link is invalid.' : done ? verify ? 'Email verified.' : link ? 'Password changed.' : 'Check your email.' : verify ? 'Verify your email.' : link ? 'Choose a new password.' : 'Forgot your password?'}</h1>
    {invalid ? <p role="alert">Return to sign in and request a new verification or password reset email.</p> : !done && <p>{verify ? 'Confirm below to verify the email address that received this link.' : link ? 'Use 10–128 characters. Changing your password signs out all existing sessions.' : 'Enter your account email. We’ll send a link if it belongs to a regular account.'}</p>}
    {!invalid && (done ? <><p role="status">{message}</p><button className="button primary full" onClick={verify && onVerified ? onVerified : onExit}>{verify ? 'Continue to workspace' : 'Return to sign in'}</button></> : <form onSubmit={submit}>
      {!link && <label>Email address<input name="email" type="email" autoComplete="email" required maxLength={200} disabled={busy} /></label>}
      {link?.purpose === 'reset' && <><PasswordField label="New password" name="password" autoComplete="new-password" minLength={10} maxLength={128} required disabled={busy} /><PasswordField label="Confirm new password" name="confirm" autoComplete="new-password" minLength={10} maxLength={128} required disabled={busy} /><p className="account-help">Choose a password different from your current one. The link is checked when you submit; used or expired links cannot change your password.</p></>}
      {error && <p className="error-banner" role="alert">{error}</p>}
      <button className="button primary full" disabled={busy}>{busy ? <LoaderCircle size={16} className="spin" /> : verify ? <ShieldCheck size={16} /> : <Mail size={16} />}{verify ? 'Confirm email address' : link ? 'Change password' : 'Send reset link'}</button>
    </form>)}
    {!done && <button className="button account-back" disabled={busy} onClick={onExit}><ArrowLeft size={16} />Return to sign in</button>}
    {!link && !invalid && onRegister && <div className="account-register"><p>New to TraceDesk? Create an account first. Password recovery is for existing accounts.</p><button className="button" disabled={busy} onClick={onRegister}>Create account</button></div>}
  </section></div>
}

export function VerificationBanner({ user, onRefresh }: { user: User; onRefresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  const [remaining, setRemaining] = useState(0)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    if (!remaining) return
    const timer = setTimeout(() => setRemaining(value => Math.max(0,value-1)), 1000)
    return () => clearTimeout(timer)
  }, [remaining])
  if (user.read_only || user.email_verified) return null
  async function request() {
    setBusy(true); setError('')
    try { const result = await api<{ message: string }>('/auth/verification-request',{}); setMessage(result.message); setRemaining(60) }
    catch(e) { setError(e instanceof Error ? e.message : 'Unable to request verification.') }
    finally { setBusy(false) }
  }
  async function refresh() {
    setBusy(true); setError('')
    try { await onRefresh(); setMessage('Status checked. If this screen remains, open your newest verification email and confirm your address.') }
    catch(e) { setError(e instanceof Error ? e.message : 'Unable to refresh verification.') }
    finally { setBusy(false) }
  }
  return <section className="verification-banner" aria-label="Email verification"><div><strong>Verify {user.email}</strong><p>Your saved history is retained. Verify your email to enter your workspace.</p>{message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}</div><div className="account-actions"><button className="button" disabled={busy || remaining > 0} onClick={request}>{remaining ? `Resend in ${remaining}s` : 'Send verification email'}</button><button className="button" disabled={busy} onClick={refresh}>Check verification</button></div></section>
}

export function VerifyAccountPage({ user, onRefresh, onLogout, error }: { user: User; onRefresh: () => Promise<void>; onLogout: () => Promise<void>; error?: string }) {
  const [leaving, setLeaving] = useState(false)
  return <div className="account-page"><section className="auth-form-wrap account-card" aria-labelledby="verify-title">
    <span className="tiny-label">TRACEDESK · ACCOUNT ACCESS</span>
    <h1 id="verify-title">Verify your email to continue.</h1>
    <p>Open the TraceDesk verification email and confirm your address. Then check verification below to enter your workspace.</p>
    <VerificationBanner user={user} onRefresh={onRefresh} />
    {error && <p className="error-banner" role="alert">{error}</p>}
    <button className="button account-back" disabled={leaving} onClick={async () => { setLeaving(true); try { await onLogout() } finally { setLeaving(false) } }}>{leaving ? <LoaderCircle size={16} className="spin" /> : <ArrowLeft size={16} />}Sign out</button>
  </section></div>
}
