import { useEffect, useRef, useState } from 'react'
import { Ico } from '../lib/icons'
import { useEco } from '../lib/eco'
import { useClock } from '../lib/sys'
import { ui, useUi, type ViewId } from '../lib/ui'
import { APP, AppGlyph, Glass, Segmented } from './kit'
import { scoreColor } from './HomeScreen'

/** 多任务：最近使用的应用卡片横排；点击进入，上划（或点 ×）关闭 */
function SwitchCard({ id, onOpen }: { id: ViewId; onOpen: () => void }) {
  const app = APP[id]
  const [dy, setDy] = useState(0)
  const [gone, setGone] = useState(false)
  const start = useRef<number | null>(null)
  const kill = () => { setGone(true); window.setTimeout(() => ui.closeRecent(id), 260) }
  return (
    <div className={'sw-card' + (gone ? ' gone' : '')} style={dy ? { transform: `translateY(${-dy}px)`, opacity: 1 - dy / 400, transition: 'none' } : undefined}>
      <div className="sw-label"><AppGlyph app={app} size={26} /><span>{app.name}</span>
        <button className="sw-x" title="关闭应用" onClick={kill}><Ico name="close" size={10} /></button>
      </div>
      <div className="sw-shot"
        onPointerDown={e => { start.current = e.clientY; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) }}
        onPointerMove={e => { if (start.current != null) setDy(Math.max(0, start.current - e.clientY)) }}
        onPointerUp={e => {
          const d = start.current == null ? 0 : start.current - e.clientY
          start.current = null
          if (d > 110) kill(); else { setDy(0); if (Math.abs(d) < 4) onOpen() }
        }}>
        <div className="sw-shot-bg" style={{ background: app.bg }} />
        <div className="sw-shot-ui">
          <div className="sw-sk-title">{app.name}</div>
          <div className="sw-sk-row"><i /><i /></div>
          <div className="sw-sk-card"><i /><i /><i /></div>
          <div className="sw-sk-card short"><i /><i /></div>
        </div>
        <div className="sw-shot-glyph"><AppGlyph app={app} size={72} /></div>
      </div>
    </div>
  )
}

export function AppSwitcher() {
  const s = useUi()
  if (s.overlay !== 'switcher') return null
  return (
    <div className="center-mask sw-mask" onClick={() => ui.closeOverlays()}>
      <div className="sw-strip" onClick={e => e.stopPropagation()}>
        {s.recents.length === 0 && <div className="sw-empty">没有最近使用的应用</div>}
        {s.recents.map(id => <SwitchCard key={id} id={id} onOpen={() => { ui.closeOverlays(); ui.navigate(id) }} />)}
      </div>
      <div className="sw-hint">点按卡片打开 · 上划卡片关闭 · 点空白处返回</div>
    </div>
  )
}

/* ───────────── 巡航（轮播应用）/ 待机（StandBy 大屏）───────────── */

/* 巡航轮播顺序：v3.2.0 的四门实况放第一位（它是最该被反复看到的那个），
   新视图与旧视图交替，轮播一圈能同时扫到"治理"和"库存"。 */
const ORDER: ViewId[] = ['gates', 'dashboard', 'circles', 'health', 'memories', 'inject',
  'cost', 'experiences', 'starmap', 'firstaid', 'lineage', 'candidates', 'packs',
  'timeline', 'actions']

function StandBy() {
  const clock = useClock()
  const { ov, meta } = useEco()
  const s = useUi()
  const score = ov?.score?.score ?? null
  const wm = ov?.watermark
  const r = 70, c = 2 * Math.PI * r
  const [h, m] = clock.hm.split(':')
  return (
    <div className="standby">
      <div className="sby-clock">
        <span className="num">{h}</span><span className="sby-colon">:</span><span className="num">{m}</span>
        <div className="sby-date">{clock.date}</div>
      </div>
      <div className="sby-widget">
        <svg viewBox="0 0 160 160" width="190" height="190">
          <circle cx="80" cy="80" r={r} fill="none" stroke="rgba(255,255,255,.12)" strokeWidth="14" />
          <circle cx="80" cy="80" r={r} fill="none" stroke={scoreColor(score)} strokeWidth="14" strokeLinecap="round"
            strokeDasharray={c} strokeDashoffset={score == null ? c : c * (1 - score / 100)} transform="rotate(-90 80 80)" />
        </svg>
        <div className="sby-score num">{score ?? '—'}</div>
        <div className="sby-lines">
          <span>体检评分 · {ov?.score?.model ?? '—'}</span>
          <span>L1 水位 {wm ? `${wm.chars} / ${wm.quota}` : '—'}{wm && wm.chars > wm.quota ? ' · 越线' : ''}</span>
          <span>告警 {s.bellCount} 条 · 生态 {meta?.version ?? '—'}</span>
        </div>
      </div>
    </div>
  )
}

export function Cruise() {
  const s = useUi()
  const mode = s.cruise
  const [dwell, setDwell] = useState(() => Number(localStorage.getItem('eco_v23_cruise_dwell') ?? 10) || 10)
  const [progress, setProgress] = useState(0)
  const [shown, setShown] = useState(true)

  useEffect(() => {
    if (mode !== 'rotate') return
    let idx = Math.max(0, ORDER.indexOf(ui.get().view))
    ui.navigate(ORDER[idx])
    const stepMs = 250
    const total = Math.max(3, dwell) * 1000
    let elapsed = 0
    const t = window.setInterval(() => {
      elapsed += stepMs
      setProgress(Math.min(1, elapsed / total))
      if (elapsed >= total) {
        elapsed = 0
        idx = (idx + 1) % ORDER.length
        ui.navigate(ORDER[idx])
      }
    }, stepMs)
    return () => clearInterval(t)
  }, [mode, dwell])

  useEffect(() => {
    if (!mode) return
    let hide = 0
    const show = () => { setShown(true); clearTimeout(hide); hide = window.setTimeout(() => setShown(false), 3000) }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || ['Shift', 'Control', 'Alt', 'Meta', 'F11'].includes(e.key)) return
      ui.setCruise(null); ui.toast('已退出巡航', true)
    }
    window.addEventListener('mousemove', show)
    window.addEventListener('keydown', onKey, true)
    show()
    return () => { window.removeEventListener('mousemove', show); window.removeEventListener('keydown', onKey, true); clearTimeout(hide) }
  }, [mode])

  if (!mode) return null
  return (
    <>
      {mode === 'big' && <StandBy />}
      <div className={'cruise-ctl' + (shown ? '' : ' hidden')}>
        <Glass className="cruise-pill">
          <Segmented value={mode} onChange={m => ui.setCruise(m)}
            options={[{ v: 'rotate', label: '轮播应用' }, { v: 'big', label: '待机大屏' }]} />
          {mode === 'rotate' && (
            <label className="cc-dwell">停留
              <input type="range" min={5} max={60} step={5} value={dwell}
                onChange={e => {
                  const v = Number(e.target.value)
                  setDwell(v)
                  try { localStorage.setItem('eco_v23_cruise_dwell', String(v)) } catch { /* */ }
                }} />
              <b className="num">{dwell}s</b>
            </label>
          )}
          <button className="cruise-exit" onClick={() => { ui.setCruise(null); ui.toast('已退出巡航', true) }}>退出</button>
        </Glass>
        {mode === 'rotate' && <div className="cruise-progress"><i style={{ width: (progress * 100).toFixed(1) + '%' }} /></div>}
      </div>
    </>
  )
}
