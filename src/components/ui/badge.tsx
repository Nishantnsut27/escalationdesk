import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import type { HTMLAttributes } from 'react'
import { cn } from '../../lib/utils'

const variants = cva('badge', {
  variants: { variant: { default: 'good', secondary: 'neutral', destructive: 'bad', outline: 'badge-outline' } },
  defaultVariants: { variant: 'secondary' },
})

export function Badge({ className, variant, asChild = false, ...props }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof variants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : 'span'
  return <Component data-slot="badge" className={cn(variants({ variant }), className)} {...props} />
}
