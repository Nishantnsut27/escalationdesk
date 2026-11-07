// Adapted from React Bits FadeContent, c83b5e7. See public/licenses/react-bits.txt.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** A one-time reveal. Focus, reduced motion and missing browser APIs show content immediately. */
export function FadeContent({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const element = ref.current
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (!element || media.matches || !('IntersectionObserver' in window)) return
    // Only prepare offscreen content, so first-paint text and deep links stay readable.
    if (element.getBoundingClientRect().top < window.innerHeight) return
    setVisible(false)
    const show = () => { setVisible(true); observer.disconnect() }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) show()
    }, { threshold: 0, rootMargin: '0px 0px 24px 0px' })
    const motionChanged = () => { if (media.matches) show() }
    observer.observe(element)
    media.addEventListener('change', motionChanged)
    return () => { observer.disconnect(); media.removeEventListener('change', motionChanged) }
  }, [])

  return <div ref={ref} className={cn('fade-content', className)} data-visible={visible} onFocusCapture={() => setVisible(true)}>{children}</div>
}
