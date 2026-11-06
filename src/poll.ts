/** Schedule the next refresh only after the current request has settled. */
export function poll(task: () => Promise<unknown>, interval: number) {
  let cancelled = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const run = async () => {
    try { await task() }
    finally { if (!cancelled) timer = setTimeout(() => { void run() }, interval) }
  }
  void run()
  return () => { cancelled = true; clearTimeout(timer) }
}
