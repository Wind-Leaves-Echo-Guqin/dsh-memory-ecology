import { useEffect, useRef, useState } from 'react'
import { Ico } from '../lib/icons'
import { ui, useUi } from '../lib/ui'

/** iOS 底部卡片（sheet）：替代 v2.2 侧抽屉。拖动把手下滑可关闭；背后的应用会缩小退后。 */
export function Sheet() {
  const s = useUi()
  const [drag, setDrag] = useState(0)
  const start = useRef<number | null>(null)
  useEffect(() => { setDrag(0) }, [s.drawer])
  if (!s.drawer) return null
  const end = (y: number) => {
    const d = start.current == null ? 0 : y - start.current
    start.current = null
    if (d > 120) ui.closeDrawer()
    setDrag(0)
  }
  return (
    <>
      <div className="sheet-mask" onClick={() => ui.closeDrawer()} />
      <aside className={'sheet' + (drag ? ' dragging' : '')} style={drag ? { transform: `translateX(-50%) translateY(${drag}px)` } : undefined} role="dialog" aria-label={s.drawer.title}>
        <div className="sheet-grab"
          onPointerDown={e => { start.current = e.clientY; (e.target as HTMLElement).setPointerCapture(e.pointerId) }}
          onPointerMove={e => { if (start.current != null) setDrag(Math.max(0, e.clientY - start.current)) }}
          onPointerUp={e => end(e.clientY)}>
          <i />
        </div>
        <header className="sheet-head">
          <span className="sheet-head-l" />
          <div className="sheet-title">
            <b>{s.drawer.title}</b>
            {s.drawer.sub && <small>{s.drawer.sub}</small>}
          </div>
          <button className="sheet-done" onClick={() => ui.closeDrawer()} title="完成（Esc）">完成</button>
        </header>
        <div className="sheet-body drawer-body">{s.drawer.node}</div>
      </aside>
    </>
  )
}

const FEATURES: Array<{ ico: string; color: string; title: string; body: string }> = [
  { ico: 'gates', color: '#34c759', title: 'v3.2.0 · 四门实况', body: '哪个门死了、死了多久。区分「没跑」（账本一行都没有）与「空转」（有行全是心跳）——旧界面看不出后者，四门就是这样停摆两个月。' },
  { ico: 'circles', color: '#0a84ff', title: 'v3.2.0 · 圈与记忆包', body: '多生态圈按启动目录自动隔离；记忆包可只读挂载（mount）或吸收（integrate，走门①）。新端点前缀 /api/v3.2.0/，与旧根九视图并存不混算。' },
  { ico: 'dashboard', color: '#0a84ff', title: '看健康', body: '主屏小组件 10 秒扫完：体检评分、记忆水位、告警与生态计数。点任意小组件进入对应应用。' },
  { ico: 'bell', color: '#ff9f0a', title: '处理告警', body: '点状态栏左侧时间打开通知中心；每条告警可「拷贝」或「去处理」。需要写入的动作一律走确认闸门。' },
  { ico: 'starmap', color: '#5e5ce6', title: '一个视图就是一个 App', body: '四门实况 / 圈的视角 / 注入观测 / 记忆包 / 圈管理 / 成本面板 / 急救箱，加上原有九个观测视图，都是独立应用，从图标放大启动。' },
  { ico: 'gear', color: '#8e8e93', title: '控制中心与设置', body: '点状态栏右侧图标打开控制中心：深浅色、墙纸、动画强度、字号、巡航与待机。设置 App 里有完整选项。' },
  { ico: 'search', color: '#30d158', title: 'Spotlight 与快捷键', body: '按 / 或 Ctrl+K 打开 Spotlight，可检索经验 / 报错 / 生态，也能直接打开应用和执行操作。g + 首字母快速跳转。' },
]

/** 「新功能介绍」卡片（替代 v2.2 五步引导）：iOS 首次启动的 What's New 页面 */
export function WhatsNew() {
  const s = useUi()
  if (s.guideStep < 0) return null
  return (
    <>
      <div className="sheet-mask" onClick={() => ui.finishGuide()} />
      <aside className="sheet whatsnew" role="dialog" aria-label="新功能介绍">
        <div className="wn-inner">
          <h2>欢迎使用<br /><span>观测舱 v3.2.0</span></h2>
          <ul className="wn-list">
            {FEATURES.map(f => (
              <li key={f.title}>
                <span className="wn-ico" style={{ color: f.color }}><Ico name={f.ico} size={30} /></span>
                <div><b>{f.title}</b><p>{f.body}</p></div>
              </li>
            ))}
          </ul>
          <p className="wn-note">本版为只读观测：数据来自观测舱服务（8788），写操作代理到 v0.3。</p>
          <button className="ios-btn-filled wn-go" onClick={() => ui.finishGuide()}>继续</button>
        </div>
      </aside>
    </>
  )
}

const KBD_REF: Array<[string, string]> = [
  ['g g / g o / g i / g p', '四门实况 / 圈的视角 / 注入观测 / 记忆包'],
  ['g k / g $ / g f', '圈管理 / 成本面板 / 急救箱'],
  ['g d / g m / g e', '驾驶舱 / 记忆库 / 经验笔记'],
  ['g s / g l / g c', '技能星图 / 血缘谱系 / 候选孵化'],
  ['g t / g h / g a', '时间线 / 体检 / 动作日志'],
  ['g ,', '设置'],
  ['/ 或 Ctrl+K', 'Spotlight 搜索（应用 · 操作 · 三模式检索）'],
  ['Ctrl+↑', '多任务（最近使用的应用）'],
  ['r', '重新读取快照（刷新当前应用）'],
  ['?', '本参考表'],
  ['Ctrl+Shift+F', '巡航（轮播应用）'],
  ['Esc', '关闭卡片 / 面板 → 回到主屏幕'],
]

export function KbdSheet() {
  const s = useUi()
  if (s.overlay !== 'kbd') return null
  return (
    <>
      <div className="sheet-mask" onClick={() => ui.closeOverlays()} />
      <aside className="sheet compact" role="dialog" aria-label="快捷键">
        <header className="sheet-head">
          <span className="sheet-head-l" />
          <div className="sheet-title"><b>快捷键</b><small>再按 ? 或 Esc 关闭</small></div>
          <button className="sheet-done" onClick={() => ui.closeOverlays()}>完成</button>
        </header>
        <div className="sheet-body">
          <div className="ios-list">
            {KBD_REF.map(([k, d]) => (
              <div key={k} className="ios-row"><span>{d}</span><kbd className="kbd-key">{k}</kbd></div>
            ))}
          </div>
        </div>
      </aside>
    </>
  )
}
