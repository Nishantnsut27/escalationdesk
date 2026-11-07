import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Pause, Play } from 'lucide-react'
import { Button } from './button'
import { Badge } from './badge'
import { cn } from '@/lib/utils'

export type CarouselFeature = { id: string; label: string; content: ReactNode }
const interval = 5000
// Keep the walkthrough still while reading or when reduced motion is requested.
export function FeatureCarousel({ features }: { features: CarouselFeature[] }) {
  const [index, setIndex] = useState(0), [playing, setPlaying] = useState(true), [paused, setPaused] = useState(false)
  const [hidden, setHidden] = useState(false)
  useEffect(() => {
    const update = () => setHidden(document.hidden)
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  useEffect(() => {
    if (!playing || paused || hidden) return
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (media.matches) { setPlaying(false); return }
    const stop = () => { if (media.matches) setPlaying(false) }
    media.addEventListener('change', stop)
    const timer = setInterval(() => setIndex(value => (value + 1) % features.length), interval)
    return () => { clearInterval(timer); media.removeEventListener('change', stop) }
  }, [playing, paused, hidden, features.length])
  if (!features.length) return null
  const current = index % features.length
  function select(value: number) { setIndex((value + features.length) % features.length); setPlaying(false) }
  return <section className="feature-carousel" aria-label="Interactive synthetic workflow demonstration" aria-roledescription="carousel" data-autoplay={playing} data-paused={paused || hidden} style={{ '--walkthrough-duration': `${interval}ms` } as CSSProperties} onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocusCapture={() => setPaused(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false) }}>
    <div className="feature-toolbar"><span><span className="specimen-dot" />A failure, followed through.</span><Badge variant="outline">Illustrative walkthrough</Badge></div>
    <div className="feature-workbench"><div className="feature-chips"><div role="group" aria-label="Workflow steps">{features.map((feature, i) => <Button key={feature.id} variant="ghost" className={cn('feature-chip', current === i && 'is-active')} aria-pressed={current === i} onClick={() => select(i)}><span className="feature-step-number" aria-hidden="true">0{i + 1}</span><span>{feature.label}</span><span key={`${current}-${playing}-${paused || hidden}`} className="feature-progress" aria-hidden="true" /></Button>)}</div></div>
    <div className="feature-stage"><div className="feature-stack" aria-live={playing ? 'off' : 'polite'}>{features.map((feature, i) => {
      const distance = (i - current + features.length) % features.length
      const position = distance === 0 ? 'active' : distance === 1 ? 'next' : distance === features.length - 1 ? 'previous' : 'hidden'
      return <div key={feature.id} className={'feature-slide is-' + position} aria-hidden={position !== 'active'} inert={position !== 'active'}>{feature.content}</div>
    })}</div></div></div>
    <div className="feature-footer"><span>step {current + 1} / {features.length}<span className="feature-footer-note"> · saved evidence → assessment → source</span></span><button className="icon-button" aria-label={playing ? 'Pause walkthrough' : 'Play walkthrough'} onClick={() => setPlaying(value => !value)}>{playing ? <Pause size={14} /> : <Play size={14} />}</button></div>
  </section>
}
export default FeatureCarousel
