import { useEffect, useRef, useState } from 'react'
import { Ico } from '../lib/icons'
import { useEco } from '../lib/eco'
import { useClock, useFps, useServiceAlive } from '../lib/sys'
import { ui, useUi } from '../lib/ui'
import { BatteryIcon, SignalIcon, WifiIcon } from './kit'

/** iOS 状态栏：左=时间日期（点开通知中心）· 中=灵动岛 · 右=信号/Wi-Fi/电池（点开控制中心）。
 * 映射：Wi-Fi = 观测舱服务 8788 连通；信号格 = cron 健康（近 7 天失败越多格越少）；电池 = 体检评分。 */
export function StatusBar() {
  const s = useUi()
  const { ov, meta } = useEco()
  const clock = useClock()
  const alive = useServiceAlive()
  const score = ov?.score?.score ?? null
  const fails = ov?.cron?.executions_7d?.n_fails ?? 0
  const signal = alive === false ? 0 : fails === 0 ? 4 : fails < 3 ? 3 : fails < 8 ? 2 : 1

  return (
    <header className="sb">
      <button className="sb-left" title="通知中心（告警）" onClick={() => ui.setOverlay('nc')}>
        <span className="sb-time num">{clock.hm}</span>
        <span className="sb-date">{clock.date}</span>
      </button>
      <Island />
      <button className="sb-right" title={`控制中心\nWi-Fi = 观测舱服务 ${alive ? '已连接' : '未连接'}\n信号 = cron 近 7 天失败 ${fails} 次\n电池 = 体检评分 ${score ?? '—'}`}
        onClick={() => ui.setOverlay('cc')}>
        {meta?.writer_today_ran && <span className="sb-pill" title="单写入方 Hermes cron 今日已运行">写入</span>}
        <SignalIcon level={signal} />
        <WifiIcon on={alive} />
        <span className="sb-batt-pct num">{score ?? '—'}</span>
        <BatteryIcon pct={score} />
        {s.patrol > 0 && <i key={s.patrol} className="sb-patrol" />}
      </button>
    </header>
  )
}

type IslandMode = 'idle' | 'toast' | 'expanded'

const FPS_VIEWS = new Set(['starmap', 'timeline', 'lineage'])

/** 灵动岛：toast 通知在岛上展开；有告警时以「实时活动」紧凑态显示铃铛+数量；点击展开生态摘要。 */
export function Island() {
  const s = useUi()
  const { ov, meta } = useEco()
  const [mode, setMode] = useState<IslandMode>('idle')
  const ref = useRef<HTMLDivElement>(null)
  const fps = useFps(!s.home && FPS_VIEWS.has(s.view) && mode === 'expanded')

  useEffect(() => {
    if (!s.toast) return
    setMode('toast')
    const t = window.setTimeout(() => { ui.clearToast(); setMode(m => (m === 'toast' ? 'idle' : m)) }, 3000)
    return () => clearTimeout(t)
  }, [s.toast])

  useEffect(() => {
    if (mode !== 'expanded') return
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setMode('idle') }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [mode])

  const score = ov?.score?.score ?? null
  const wm = ov?.watermark
  const live = s.bellCount > 0
  const cls = 'island ' + (mode === 'toast' ? 'is-toast' : mode === 'expanded' ? 'is-expanded' : live ? 'is-live' : 'is-idle')

  return (
    <div ref={ref} className={cls} onClick={() => mode !== 'expanded' && setMode('expanded')} title="灵动岛（点击展开生态摘要）">
      {mode === 'toast' && s.toast && (
        <div className="isl-toast">
          <span className={'isl-ico ' + (s.toast.ok ? 'ok' : 'bad')}><Ico name={s.toast.ok ? 'check' : 'warn'} size={15} /></span>
          <span className="isl-msg">{s.toast.msg}</span>
        </div>
      )}
      {mode === 'idle' && live && (
        <div className="isl-live">
          <span className="isl-live-l"><Ico name="bell" size={13} /></span>
          <span className="isl-live-r num">{s.bellCount}</span>
        </div>
      )}
      {mode === 'expanded' && (
        <div className="isl-exp">
          <div className="isl-ring">
            <svg viewBox="0 0 40 40" width="54" height="54">
              <circle cx="20" cy="20" r="16" fill="none" stroke="rgba(255,255,255,.16)" strokeWidth="4.4" />
              <circle cx="20" cy="20" r="16" fill="none" strokeWidth="4.4" strokeLinecap="round"
                stroke={score == null ? '#8e8e93' : score >= 80 ? '#30d158' : score >= 60 ? '#ffd60a' : '#ff453a'}
                strokeDasharray={100.5} strokeDashoffset={score == null ? 100.5 : 100.5 * (1 - score / 100)}
                transform="rotate(-90 20 20)" />
            </svg>
            <b className="num">{score ?? '—'}</b>
          </div>
          <div className="isl-exp-info">
            <div className="isl-exp-t">体检评分 · {ov?.score?.model ?? '—'}</div>
            <div className="isl-exp-d">L1 水位 {wm ? `${wm.chars}/${wm.quota}` : '—'}{wm && wm.chars > wm.quota ? ' · 越线' : ''} · 告警 {s.bellCount}</div>
            <div className="isl-exp-d">快照 {meta?.snapshot?.replace('T', ' ').slice(5, 16) ?? '—'}{fps != null ? ` · ${fps} fps` : ''}</div>
          </div>
          <button className="isl-go" onClick={e => { e.stopPropagation(); setMode('idle'); ui.navigate('dashboard') }}>打开</button>
        </div>
      )}
    </div>
  )
}
