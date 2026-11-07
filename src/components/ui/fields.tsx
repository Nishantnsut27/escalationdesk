import type { InputHTMLAttributes, SelectHTMLAttributes } from 'react'
import { cn } from '../../lib/utils'
export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) { return <input data-slot="input" className={cn('input', className)} {...props} /> }
export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) { return <select data-slot="select" className={cn('input', className)} {...props} /> }
