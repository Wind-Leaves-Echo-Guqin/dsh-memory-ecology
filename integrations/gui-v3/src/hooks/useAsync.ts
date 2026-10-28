import { useEffect, useState } from 'react'

/** 视图数据加载 hook：deps 变化（含 App 的 refreshTick）自动重取；
 * 卸载后不再 setState。err 只在抛错时出现（fail-open 由视图自行决定）。 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; err: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    setErr(null)
    fn().then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) setErr(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, err }
}
