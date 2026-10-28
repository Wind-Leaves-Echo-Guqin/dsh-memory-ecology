/** 外壳共用的小工具：时钟、帧率、服务心跳、诊断包、配额门倒计时。 */
import { useEffect, useState } from 'react'
import { copyText } from './ui'

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

export type Clock = { hm: string; date: string; dateLong: string }

const fmt = (d: Date): Clock => ({
  hm: `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`,
  date: `${d.getMonth() + 1}月${d.getDate()}日 ${WEEK[d.getDay()]}`,
  dateLong: `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${WEEK[d.getDay()]}`,
})

/** 分钟级时钟（对齐到整分刷新） */
export function useClock(): Clock {
  const [c, setC] = useState(() => fmt(new Date()))
  useEffect(() => {
    let t = 0
    const tick = () => {
      setC(fmt(new Date()))
      t = window.setTimeout(tick, 60000 - (Date.now() % 60000) + 20)
    }
    t = window.setTimeout(tick, 60000 - (Date.now() % 60000) + 20)
    return () => clearTimeout(t)
  }, [])
  return c
}

/** 实测帧率：3D 视图激活时采样（全局 rAF 与 three 渲染同帧循环，数值近似）。 */
export function useFps(active: boolean): number | null {
  const [fps, setFps] = useState<number | null>(null)
  useEffect(() => {
    if (!active) { setFps(null); return }
    let frames = 0
    let raf = 0
    let t0 = performance.now()
    const tick = (t: number) => {
      frames++
      if (t - t0 >= 500) {
        setFps(Math.round(frames * 1000 / (t - t0)))
        frames = 0
        t0 = t
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [active])
  return fps
}

/** 观测舱服务心跳（/api/healthz，20s）：状态栏 Wi-Fi 图标 = 8788 连通性 */
export function useServiceAlive(): boolean | null {
  const [alive, setAlive] = useState<boolean | null>(null)
  useEffect(() => {
    let dead = false
    const ping = async () => {
      try {
        const r = await fetch('/api/healthz', { cache: 'no-store' })
        const d = await r.json()
        if (!dead) setAlive(Boolean(d.ok))
      } catch { if (!dead) setAlive(false) }
    }
    void ping()
    const t = window.setInterval(ping, 20000)
    return () => { dead = true; clearInterval(t) }
  }, [])
  return alive
}

/** 配额门（每小时 25 分）下次运行倒计时 */
export function quotaGateCountdown(): string {
  const now = new Date()
  const next = new Date(now)
  next.setMinutes(25, 0, 0)
  if (next <= now) next.setHours(next.getHours() + 1)
  const sec = Math.round((next.getTime() - now.getTime()) / 1000)
  return sec >= 60 ? `${Math.floor(sec / 60)} 分 ${sec % 60} 秒` : `${sec} 秒`
}

/** 诊断包（对齐 v1 copyDiagnostic）：一键复制可读诊断段。 */
export async function copyDiagnostic(bellCount: number): Promise<void> {
  const lines: string[] = ['记忆生态 · 观测舱 v2.3（液态玻璃）诊断包', '生成时间：' + new Date().toLocaleString()]
  try {
    const meta = await (await fetch('/api/meta')).json()
    lines.push('生态版本：' + meta.version + ' · 评分模型：' + meta.score_model)
    lines.push('数据根：' + meta.root + '（存在=' + meta.root_exists + '）')
    lines.push('数据快照：' + meta.snapshot + ' · 单写入方今日运行：' + (meta.writer_today_ran ? '是' : '否'))
  } catch { /* fail-open */ }
  try {
    const ov = await (await fetch('/api/overview')).json()
    const wm = ov.watermark ?? {}
    lines.push('记忆水位：' + wm.chars + ' / ' + wm.quota + (wm.chars > wm.quota ? '（越线）' : '（正常）'))
  } catch { /* fail-open */ }
  lines.push('当前告警（通知中心）：' + bellCount + ' 条')
  void copyText(lines.join('\n'), '诊断信息已复制')
}

export const V03_URL = 'http://127.0.0.1:8788/static/index.html'
