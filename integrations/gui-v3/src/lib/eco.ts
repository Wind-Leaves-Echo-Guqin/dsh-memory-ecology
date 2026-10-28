/** 外壳共享数据：生态快照（overview + meta）与告警聚合（通知中心/锁屏/灵动岛/图标角标共用一份）。
 * 模块级单例 + useSyncExternalStore：多处订阅只打一次接口。 */
import { useEffect, useSyncExternalStore } from 'react'
import { fetchHealth, fetchMeta, fetchOverview, fetchSkills, type Meta, type Overview } from './api'
import { ui, useUi, type ViewId } from './ui'

export type BellItem = { sev: 'bad' | 'warn'; src: string; text: string; copy: string; goto: ViewId; key: string }

type EcoState = { ov: Overview | null; meta: Meta | null; bell: BellItem[]; bellReady: boolean; at: number }

let st: EcoState = { ov: null, meta: null, bell: [], bellReady: false, at: 0 }
const subs = new Set<() => void>()
const put = (p: Partial<EcoState>) => { st = { ...st, ...p }; for (const f of subs) f() }

export const useEco = (): EcoState => useSyncExternalStore(
  cb => { subs.add(cb); return () => subs.delete(cb) },
  () => st,
)

async function loadSnapshot(): Promise<void> {
  const [ov, meta] = await Promise.allSettled([fetchOverview(), fetchMeta()])
  put({
    ov: ov.status === 'fulfilled' ? ov.value : st.ov,
    meta: meta.status === 'fulfilled' ? meta.value : st.meta,
    at: Date.now(),
  })
}

/** 告警五源聚合（对齐 v1 bell.js）：cron 近7天失败 / 记忆越线 / 候选>0 / 报告 P0 / 悬空引用 */
async function loadBell(): Promise<void> {
  const out: BellItem[] = []
  try {
    const d: Overview = await fetchOverview(0)
    const fails = d.cron?.recent_fails ?? []
    if (fails.length) {
      out.push({
        sev: 'bad', src: 'cron', key: 'cron:' + fails.map(f => f.job + (f.n ?? 1)).join(','),
        text: '近 7 天 cron 失败：' + fails.map(f => f.job + '×' + (f.n ?? 1)).join(' · '),
        copy: '近 7 天 cron 失败（口径 executions.db）：\n' + fails.map(f => `${f.job} ×${f.n ?? 1}`).join('\n'),
        goto: 'timeline',
      })
    }
    const wm = d.watermark ?? { chars: 0, quota: 0 }
    if (wm.chars > wm.quota) {
      out.push({
        sev: 'bad', src: 'mem', key: 'mem:' + wm.chars,
        text: `L1 记忆越线：${wm.chars} > ${wm.quota}（超 ${wm.chars - wm.quota}）`,
        copy: `记忆水位越线：MEMORY.md ${wm.chars} 字符 > 管理线 ${wm.quota}（超 ${wm.chars - wm.quota}）。可在 v0.3 观测舱一键挤出。`,
        goto: 'dashboard',
      })
    }
    const ck = d.candidates?.by_kind ?? {}
    const nCand = (ck.skill ?? 0) + (ck.profile ?? 0) + (ck.experience ?? 0)
    if (nCand > 0) {
      out.push({
        sev: 'warn', src: 'cand', key: `cand:${ck.skill ?? 0}/${ck.profile ?? 0}/${ck.experience ?? 0}`,
        text: `待处理候选 ${nCand} 项（技能 ${ck.skill ?? 0} · 画像 ${ck.profile ?? 0} · 经验 ${ck.experience ?? 0}）`,
        copy: `候选池：技能 ${ck.skill ?? 0} / 画像 ${ck.profile ?? 0} / 经验 ${ck.experience ?? 0}`,
        goto: 'candidates',
      })
    }
  } catch { /* fail-open */ }
  try {
    const h = await fetchHealth(0)
    const body = h.report?.body ?? ''
    const lines = body.split('\n').filter(L => /\bP0\b/.test(L) && L.trim().length > 12).slice(0, 3)
    for (const L of lines) {
      const t = L.trim().replace(/^[|#*\-\s]+/, '').slice(0, 120)
      out.push({ sev: 'bad', src: 'report', key: 'p0:' + t, text: '体检 P0：' + t, copy: '体检报告 P0 项：' + t, goto: 'health' })
    }
  } catch { /* fail-open */ }
  try {
    const d = await fetchSkills(0)
    const names = new Set(d.map(x => x.name))
    const dang = new Set<string>()
    for (const sk of d) for (const r of sk.dangling ?? []) if (!names.has(r)) dang.add(r)
    if (dang.size) {
      out.push({
        sev: 'warn', src: 'dangling', key: 'dang:' + [...dang].join(','),
        text: `技能悬空引用 ${dang.size} 个：${[...dang].slice(0, 3).join('、')}${dang.size > 3 ? '…' : ''}`,
        copy: '悬空引用（引用了不存在的技能）：\n' + [...dang].join('\n'),
        goto: 'starmap',
      })
    }
  } catch { /* fail-open */ }
  put({ bell: out, bellReady: true })
  ui.setBellCount(out.length)
}

/** App 根组件挂一次：快照随 refreshTick 重取；告警 90s 轮询 */
export function useEcoFeeder(): void {
  const s = useUi()
  useEffect(() => { void loadSnapshot() }, [s.refreshTick])
  useEffect(() => {
    void loadBell()
    const t = window.setInterval(() => void loadBell(), 90000)
    return () => clearInterval(t)
  }, [s.refreshTick])
}

/** 通知来源 → 显示成哪个 App 发出的通知 */
export const BELL_APP: Record<string, ViewId> = {
  cron: 'timeline', mem: 'memories', cand: 'candidates', report: 'health', dangling: 'starmap',
}
