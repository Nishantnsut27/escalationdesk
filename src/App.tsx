import { Fragment, lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Cable, ChevronRight, FileInput, Fingerprint, LayoutDashboard, Layers3, LogOut, Menu, ShieldCheck, TriangleAlert, X } from 'lucide-react'
import { api, ApiError, setCsrf, userMessage, type Session, type User } from './api'
import { AccountPage, parseAuthenticationLink, VerifyAccountPage, WritePermission } from './account'
import { Auth } from './auth'
import Home, { DataUse } from './home'
import { Brand, EmptyState, ErrorState, LoadingState } from './components/common'
import { useRoute, navigate, destinationAfterAuth, isProtectedRoute } from './routes'

const Dashboard = lazy(() => import('./dashboard').then(module => ({ default: module.Dashboard })))
const Errors = lazy(() => import('./dashboard').then(module => ({ default: module.Errors })))
const Collections = lazy(() => import('./workspace').then(module => ({ default: module.Collections })))
const CollectionWorkspace = lazy(() => import('./workspace').then(module => ({ default: module.CollectionWorkspace })))
const Investigations = lazy(() => import('./workspace').then(module => ({ default: module.Investigations })))
const ConnectionsWorkspace = lazy(() => import('./connections').then(module => ({ default: module.ConnectionsWorkspace })))
const ImportWorkspace = lazy(() => import('./imports').then(module => ({ default: module.ImportWorkspace })))
const DemoWorkspace = lazy(() => import('./scenarios').then(module => ({ default: module.DemoWorkspace })))
const LogsWorkspace = lazy(() => import('./logs'))
export { ReportView } from './assessments'

const destinationKey = 'tracedesk:return-to'
function rememberedDestination() { try { return sessionStorage.getItem(destinationKey) || '' } catch { return '' } }
function rememberDestination(hash: string) { if (isProtectedRoute(hash)) { try { sessionStorage.setItem(destinationKey, hash) } catch { /* The current hash also retains the destination. */ } } }
function clearDestination() { try { sessionStorage.removeItem(destinationKey) } catch { /* Storage can be disabled by the browser. */ } }
function replaceHash(hash: string) { history.replaceState(null, '', hash); window.dispatchEvent(new HashChangeEvent('hashchange')) }

export default function App() {
  const route = useRoute()
  const [user, setUser] = useState<User | null>(null), [initializing, setInitializing] = useState(true)
  const [error, setError] = useState(''), [mobileNav, setMobileNav] = useState(false)
  const [authLink, setAuthLink] = useState(() => parseAuthenticationLink(window.location.hash))
  const [invalidAuthLink, setInvalidAuthLink] = useState(() => window.location.hash.startsWith('#auth/') && !parseAuthenticationLink(window.location.hash))
  const epoch = useRef(0), main = useRef<HTMLElement>(null), sidebar = useRef<HTMLElement>(null), lastPage = useRef(route.page)

  const acceptSession = useCallback((value: Session, restore = true) => {
    epoch.current++; setCsrf(value.csrf); setUser(value.user); setError('')
    if (restore) {
      const destination = destinationAfterAuth(window.location.hash, rememberedDestination(), value.user.read_only)
      if (value.user.email_verified || value.user.read_only) clearDestination()
      else rememberDestination(destination)
      replaceHash(destination)
    }
  }, [])
  const refreshSession = useCallback(async () => {
    const value = await api<Session>('/auth/me')
    acceptSession(value, false)
    if (value.user.email_verified && rememberedDestination()) {
      const destination = destinationAfterAuth('', rememberedDestination(), value.user.read_only)
      clearDestination(); replaceHash(destination)
    }
  }, [acceptSession])
  const expireSession = useCallback(() => {
    rememberDestination(window.location.hash)
    epoch.current++; setUser(null); setCsrf(''); setError('Your session expired. Sign in to return to this view.')
  }, [])

  useEffect(() => {
    let alive = true
    const revision = epoch.current
    api<Session>('/auth/me').then(value => { if (alive && revision === epoch.current) acceptSession(value, false) })
      .catch(e => { if (alive && !(e instanceof ApiError && e.status === 401)) setError(userMessage(e)) })
      .finally(() => { if (alive) setInitializing(false) })
    window.addEventListener('tracedesk:session-expired', expireSession)
    return () => { alive = false; window.removeEventListener('tracedesk:session-expired', expireSession) }
  }, [acceptSession, expireSession])
  useEffect(() => {
    function consume() {
      if (!window.location.hash.startsWith('#auth/')) return
      const link = parseAuthenticationLink(window.location.hash)
      setAuthLink(link); setInvalidAuthLink(!link)
      // The one-time token lives only in component state after the link is opened.
      replaceHash('#/account')
    }
    consume(); window.addEventListener('hashchange', consume)
    return () => window.removeEventListener('hashchange', consume)
  }, [])
  useEffect(() => {
    if (!initializing && (!user || (!user.email_verified && !user.read_only))) rememberDestination(window.location.hash)
    if (user && ['login', 'signup', 'demo-login'].includes(route.page)) {
      const destination = destinationAfterAuth('', rememberedDestination(), user.read_only)
      if (user.email_verified || user.read_only) clearDestination()
      replaceHash(destination)
    }
    setMobileNav(false)
    if (lastPage.current !== route.page) { main.current?.focus(); window.scrollTo(0, 0); lastPage.current = route.page }
  }, [route.page, route.id, route.params.toString(), user, initializing])
  useEffect(() => {
    if (!user || user.email_verified || user.read_only) return
    const refresh = () => { void refreshSession().catch(e => { if (e instanceof ApiError && e.status === 401) expireSession(); else setError(userMessage(e)) }) }
    window.addEventListener('focus', refresh)
    return () => window.removeEventListener('focus', refresh)
  }, [user, refreshSession, expireSession])
  useEffect(() => {
    document.title = route.page === 'logs' ? 'Logs · TraceDesk' : route.page && route.page !== 'auth-link' ? route.page[0].toUpperCase() + route.page.slice(1) + ' · TraceDesk' : 'TraceDesk · Follow the evidence'
  }, [route.page])
  useEffect(() => {
    if (!mobileNav) return
    const previous = document.activeElement as HTMLElement | null, overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    sidebar.current?.querySelector<HTMLElement>('a,button')?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [mobileNav])
  async function logout() {
    try { await api('/auth/logout', {}); epoch.current++; setUser(null); setCsrf(''); clearDestination(); navigate('login'); setError('') }
    catch (e) { if (e instanceof ApiError && e.status === 401) { expireSession(); navigate('login') } else setError(userMessage(e)) }
  }
  const exitAccount = () => { setAuthLink(null); setInvalidAuthLink(false); navigate('login') }
  if (authLink || invalidAuthLink) return <AccountPage key={authLink?.token || 'invalid'} link={authLink || undefined} invalid={invalidAuthLink} onExit={exitAccount} onReset={() => { epoch.current++; setUser(null); setCsrf('') }} onVerified={() => {
    void api<Session>('/auth/me').then(value => acceptSession(value)).catch(() => { setUser(null); setCsrf(''); navigate('login') }).finally(() => setAuthLink(null))
  }} />
  if (route.page === 'privacy') return <div className="public-detail"><Brand /><DataUse /><a className="text-link" href={user ? '#/dashboard' : '#/'}>Return to {user ? 'workspace' : 'TraceDesk'}</a></div>
  if (!route.page) return <Home />
  if (initializing) return <div className="initializing"><Brand /><LoadingState>Opening your workspace…</LoadingState></div>
  if (!user) return <>{error && <div className="auth-notice"><ErrorState error={error} /></div>}<Auth key={route.page} mode={route.page === 'demo' ? 'demo-login' : route.page} onSession={acceptSession} /></>
  if (!user.read_only && !user.email_verified) return <VerifyAccountPage user={user} onRefresh={refreshSession} onLogout={logout} error={error} />

  const nav = [
    { page: 'dashboard', label: 'Dashboard', icon: LayoutDashboard }, { page: 'collections', label: 'Saved evidence', icon: Layers3 },
    { page: 'errors', label: 'Errors', icon: TriangleAlert }, { page: 'investigations', label: 'Investigations', icon: Fingerprint },
    ...(!user.read_only ? [{ page: 'connections', label: 'Connections', icon: Cable }, { page: 'import', label: 'Import logs', icon: FileInput }] : []),
  ]
  const title = route.page === 'logs' ? 'Log workspace' : route.page === 'demo' ? 'Recorded demos' : nav.find(item => item.page === route.page)?.label || 'Workspace'
  let content
  switch (route.page) {
    case 'dashboard': content = <Dashboard readOnly={user.read_only} />; break
    case 'collections': content = route.id ? <CollectionWorkspace key={route.id} id={route.id} readOnly={user.read_only} /> : <Collections readOnly={user.read_only} />; break
    case 'errors': content = <Errors />; break
    case 'investigations': content = <Investigations key={route.id} id={route.id} readOnly={user.read_only} />; break
    case 'logs': content = <LogsWorkspace />; break
    case 'demo': content = <DemoWorkspace slug={route.id} />; break
    case 'connections': content = user.read_only ? <EmptyState title="Read-only demonstration">Connections are available in a regular account.</EmptyState> : <ConnectionsWorkspace />; break
    case 'import': content = user.read_only ? <EmptyState title="Read-only demonstration">Import your own evidence using a regular account.</EmptyState> : <ImportWorkspace />; break
    default: content = <EmptyState title="This page is unavailable"><a href="#/dashboard">Return to your dashboard</a> to open saved evidence.</EmptyState>
  }
  return <WritePermission.Provider value={user.email_verified && !user.read_only}><div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus() }}>Skip to content</a>
    {mobileNav && <button className="nav-backdrop" tabIndex={-1} aria-label="Close navigation" onClick={() => setMobileNav(false)} />}
    <aside ref={sidebar} id="workspace-navigation" role={mobileNav ? 'dialog' : undefined} aria-modal={mobileNav || undefined} aria-label="Workspace navigation" className={'sidebar' + (mobileNav ? ' nav-open' : '')} onKeyDown={event => {
      if (!mobileNav) return
      if (event.key === 'Escape') { event.preventDefault(); setMobileNav(false) }
      if (event.key === 'Tab') {
        const targets = Array.from(sidebar.current?.querySelectorAll<HTMLElement>('a,button:not(:disabled)') || []).filter(element => element.offsetParent !== null)
        const first = targets[0], last = targets.at(-1)
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }}>
      <div className="sidebar-brand"><Brand /><button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMobileNav(false)}><X size={18} /></button></div>
      <div className="workspace-label"><span className="workspace-avatar">{user.read_only ? 'D' : user.name.charAt(0).toUpperCase()}</span><div><strong>{user.read_only ? 'Demo workspace' : 'Personal workspace'}</strong><span>{user.read_only ? 'Recorded · Read-only' : 'Your saved evidence'}</span></div></div>
      <span className="nav-label">EXPLORER</span><nav aria-label="Workspace">{nav.map(({ page, label, icon: Icon }) => <Fragment key={page}>{page === 'connections' && <span className="nav-label nav-section-label">SOURCES</span>}<a href={'#/' + page} aria-current={route.page === page || (page === 'collections' && route.page === 'logs') ? 'page' : undefined}><Icon size={18} />{label}</a></Fragment>)}</nav>
      {user.read_only && <a className={'demo-nav' + (route.page === 'demo' ? ' active' : '')} href="#/demo"><Layers3 size={17} />Recorded demo gallery</a>}
      <div className="sidebar-bottom"><div className="snapshot-note"><ShieldCheck size={16} /><p>{user.read_only ? 'Reviewed recordings. Browsing does not start analysis.' : 'Saved snapshots. Investigate when you are ready.'}</p></div><div className="user-row"><span className="avatar">{user.name.charAt(0).toUpperCase()}</span><div><strong>{user.name}</strong><span>{user.email}</span></div><button className="icon-button" aria-label="Sign out" onClick={() => void logout()}><LogOut size={16} /></button></div></div>
    </aside><div className="main-shell" inert={mobileNav}><header className="topbar"><div><button className="icon-button mobile-only" aria-label="Open navigation" aria-expanded={mobileNav} aria-controls="workspace-navigation" onClick={() => setMobileNav(!mobileNav)}><Menu size={20} /></button><span>Workspace</span><ChevronRight size={13} /><strong>{title}</strong></div><span className="connection-pill"><i />{user.read_only ? 'Recorded demonstration' : 'Saved evidence'}</span></header>
      <main ref={main} id="main-content" tabIndex={-1} key={user.id}>{error && <div className="global-error"><ErrorState error={error} /><button className="icon-button" aria-label="Dismiss message" onClick={() => setError('')}><X size={16} /></button></div>}<Suspense fallback={<LoadingState />}>{content}</Suspense><footer className="page-footer"><span>saved snapshots / manual investigation</span><a href="#/privacy">Data use</a></footer></main>
    </div></div></WritePermission.Provider>
}
