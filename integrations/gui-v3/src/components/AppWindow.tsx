import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { invalidate } from '../lib/api'
import { Ico } from '../lib/icons'
import { copyDiagnostic } from '../lib/sys'
import { exportView, ui, useUi, VIEW_TITLE, type ViewId } from '../lib/ui'
import { APP, AppGlyph, DOCK, Glass, appRect } from './kit'
import { useBadges } from './HomeScreen'
import Dashboard from '../views/Dashboard'
import Memories from '../views/Memories'
import Experiences from '../views/Experiences'
import Nebula from '../views/Nebula'
import Lineage from '../views/Lineage'
import Candidates from '../views/Candidates'
import Timeline from '../views/Timeline'
import Health from '../views/Health'
import ActionsView from '../views/ActionsView'
import Settings from '../views/Settings'
import GatesView from '../views/Gates'
import CirclesView from '../views/Circles'
import InjectView from '../views/Inject'
import PacksView from '../views/Packs'
import ManageView from '../views/Manage'
import CostView from '../views/Cost'
import FirstaidView from '../views/Firstaid'

function ViewRouter({ view }: { view: ViewId }) {
  switch (view) {
    case 'memories': return <Memories />
    case 'experiences': return <Experiences />
    case 'starmap': return <Nebula />
    case 'lineage': return <Lineage />
    case 'candidates': return <Candidates />
    case 'timeline': return <Timeline />
    case 'health': return <Health />
    case 'actions': return <ActionsView />
    case 'gates': return <GatesView />
    case 'circles': return <CirclesView />
    case 'inject': return <InjectView />
    case 'packs': return <PacksView />
    case 'manage': return <ManageView />
    case 'cost': return <CostView />
    case 'firstaid': return <FirstaidView />
    case 'settings': return <Settings />
    default: return <Dashboard />
  }
}

const EASE = 'cubic-bezier(.32,.72,0,1)'

/** 图标矩形 → 全屏的 clip-path 起止帧 */
function clipFrom(r: DOMRect | { x: number; y: number; w: number; h: number } | null): string {
  const W = window.innerWidth, H = window.innerHeight
  if (!r) return `inset(${H * 0.3}px ${W * 0.3}px ${H * 0.3}px ${W * 0.3}px round 40px)`
  const x = 'left' in r ? r.left : r.x, y = 'top' in r ? r.top : r.y
  const w = 'width' in r ? r.width : r.w, h = 'height' in r ? r.height : r.h
  return `inset(${y}px ${W - x - w}px ${H - y - h}px ${x}px round ${Math.round(w * 0.2237)}px)`
}

/** 前台应用宿主：负责「从图标放大启动 / 缩回图标」的生命周期 */
export function AppHost() {
  const s = useUi()
  const [gen, setGen] = useState(0)
  const [mounted, setMounted] = useState(!s.home)
  const [closing, setClosing] = useState(false)
  useEffect(() => {
    if (!s.home) {
      // 缩回途中又被打开：换一个新窗口重新走启动动画，避免停在半缩回的裁剪帧
      if (closing) setGen(g => g + 1)
      setMounted(true); setClosing(false)
    } else if (mounted) setClosing(true)
  }, [s.home]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!mounted) return null
  return <AppWindow key={gen} closing={closing} onClosed={() => { setMounted(false); setClosing(false) }} />
}

type Menu = '' | 'share' | 'more'

function AppWindow({ closing, onClosed }: { closing: boolean; onClosed: () => void }) {
  const s = useUi()
  const winRef = useRef<HTMLDivElement>(null)
  const splashRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const lastTop = useRef(0)
  const [scrolled, setScrolled] = useState(false)
  const [tabMin, setTabMin] = useState(false)
  const [menu, setMenu] = useState<Menu>('')
  const [openView, setOpenView] = useState<ViewId>(s.view)
  const low = s.animLevel === 'low'

  // 启动动画：从图标位置 clip-path 展开 + 启动画面淡出
  useLayoutEffect(() => {
    const el = winRef.current, sp = splashRef.current
    if (!el || !sp) return
    if (low) { sp.style.opacity = '0'; return }
    const from = clipFrom(appRect(s.view) ?? s.launch)
    el.animate([{ clipPath: from }, { clipPath: 'inset(0px 0px 0px 0px round 0px)' }], { duration: 520, easing: EASE })
    el.querySelector('.appwin-content')?.animate([{ transform: 'scale(.86)' }, { transform: 'none' }], { duration: 520, easing: EASE })
    sp.animate([{ opacity: 1 }, { opacity: 1, offset: 0.35 }, { opacity: 0 }], { duration: 520, easing: 'ease-out', fill: 'forwards' })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 返回主屏：缩回当前应用图标
  useEffect(() => {
    if (!closing) return
    const el = winRef.current, sp = splashRef.current
    if (!el || !sp || low) { onClosed(); return }
    const to = clipFrom(appRect(s.view))
    const anims = [
      sp.animate([{ opacity: 0 }, { opacity: 1, offset: 0.5 }, { opacity: 1 }], { duration: 440, easing: 'ease-in', fill: 'forwards' }),
      el.querySelector('.appwin-content')?.animate([{ transform: 'none' }, { transform: 'scale(.86)' }], { duration: 440, easing: EASE, fill: 'forwards' }),
    ]
    const a = el.animate([{ clipPath: 'inset(0px 0px 0px 0px round 0px)' }, { clipPath: to }], { duration: 440, easing: EASE, fill: 'forwards' })
    a.onfinish = () => onClosed()
    return () => { a.onfinish = null; a.cancel(); for (const x of anims) x?.cancel() }
  }, [closing]) // eslint-disable-line react-hooks/exhaustive-deps

  // 切换应用（标签栏 / Spotlight）：回顶、恢复标签栏
  useEffect(() => {
    if (s.view === openView) return
    setOpenView(s.view)
    scrollRef.current?.scrollTo({ top: 0 })
    setScrolled(false); setTabMin(false)
  }, [s.view, openView])

  useEffect(() => {
    if (!menu) return
    const onDown = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest('.ctx-menu, .nav-group')) setMenu('') }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menu])

  const onScroll = () => {
    const t = scrollRef.current?.scrollTop ?? 0
    setScrolled(t > 46)
    if (t > lastTop.current + 6 && t > 80) setTabMin(true)
    else if (t < lastTop.current - 6 || t < 40) setTabMin(false)
    lastTop.current = t
  }

  const refresh = () => { invalidate(); ui.bumpRefresh(); ui.toast('已重新读取快照', true) }
  const app = APP[s.view]
  const sheetBehind = !!s.drawer || s.guideStep >= 0

  return (
    <div ref={winRef} className={'appwin' + (sheetBehind ? ' sheet-behind' : '') + (closing ? ' closing' : '')} data-app={s.view}>
      <div className="appwin-content">
        <div className="appwin-scroll" ref={scrollRef} onScroll={onScroll}>
          <main className="view" key={`${s.view}:${s.animLevel}`}>
            <ViewRouter view={s.view} />
          </main>
        </div>
        <div className="scroll-edge top" aria-hidden="true" />
        <div className="scroll-edge bottom" aria-hidden="true" />
        <nav className="navbar">
          <Glass as="button" interactive className="nav-btn circle" title="返回主屏幕（Esc）" onClick={() => ui.goHome()}>
            <Ico name="back" size={17} />
          </Glass>
          <div className={'nav-title' + (scrolled ? ' show' : '')}>{VIEW_TITLE[s.view]}</div>
          <Glass className="nav-group">
            <button title="重新读取快照（r）" onClick={refresh}><Ico name="refresh" size={16} /></button>
            <button title="导出当前应用数据" className={menu === 'share' ? 'on' : ''} onClick={() => setMenu(m => (m === 'share' ? '' : 'share'))}><Ico name="share" size={16} /></button>
            <button title="更多" className={menu === 'more' ? 'on' : ''} onClick={() => setMenu(m => (m === 'more' ? '' : 'more'))}><Ico name="more" size={16} /></button>
          </Glass>
          {menu === 'share' && (
            <CtxMenu onPick={() => setMenu('')} items={[
              { label: '导出 JSON 文件', ico: 'share', run: () => exportView('json', s.view) },
              { label: '拷贝为 Markdown', ico: 'copy', run: () => exportView('md', s.view) },
              { label: '拷贝为纯文本', ico: 'text', run: () => exportView('text', s.view) },
            ]} />
          )}
          {menu === 'more' && <MoreMenu onPick={() => setMenu('')} />}
        </nav>
        <TabBar min={tabMin} />
        <HomeIndicator />
      </div>
      <div ref={splashRef} className="appwin-splash" style={{ background: app.bg }} aria-hidden="true">
        <AppGlyph app={app} size={96} />
      </div>
    </div>
  )
}

type CtxItem = { label: string; ico: string; run: () => void; danger?: boolean; sep?: boolean }

export function CtxMenu({ items, onPick }: { items: CtxItem[]; onPick: () => void }) {
  return (
    <Glass className="ctx-menu">
      {items.map(it => (
        <button key={it.label} className={'ctx-item' + (it.danger ? ' danger' : '') + (it.sep ? ' sep' : '')}
          onClick={() => { onPick(); it.run() }}>
          <span>{it.label}</span><Ico name={it.ico} size={15} />
        </button>
      ))}
    </Glass>
  )
}

function MoreMenu({ onPick }: { onPick: () => void }) {
  const s = useUi()
  return (
    <CtxMenu onPick={onPick} items={[
      { label: '多任务', ico: 'grid', run: () => ui.setOverlay('switcher') },
      { label: '巡航 · 轮播应用', ico: 'play', run: () => ui.setCruise('rotate') },
      { label: '待机 · StandBy 大屏', ico: 'standby', run: () => ui.setCruise('big') },
      { label: '拷贝诊断信息', ico: 'diag', run: () => void copyDiagnostic(s.bellCount), sep: true },
      { label: '快捷键', ico: 'kbd', run: () => ui.setOverlay('kbd') },
      { label: '新功能介绍', ico: 'help', run: () => ui.setGuideStep(0) },
      { label: '在 v0.3 观测舱打开', ico: 'external', run: () => window.open('http://127.0.0.1:8788/static/index.html', '_blank'), sep: true },
    ]} />
  )
}

/** iOS 26 悬浮液态玻璃标签栏：Dock 应用常驻 + 当前非常驻应用临时追加；下滑时收缩为单个胶囊 */
function TabBar({ min }: { min: boolean }) {
  const s = useUi()
  const badges = useBadges()
  const tabs: ViewId[] = DOCK.includes(s.view) ? DOCK : [...DOCK, s.view]
  const idx = tabs.indexOf(s.view)
  return (
    <div className={'tabbar-wrap' + (min ? ' min' : '')}>
      <Glass className="tabbar" style={{ ['--n' as string]: tabs.length, ['--i' as string]: idx }}>
        <span className="tab-lens" aria-hidden="true" />
        {tabs.map(id => (
          <button key={id} className={'tab' + (id === s.view ? ' on' : '')} onClick={() => ui.navigate(id)} title={APP[id].hint}>
            <span className="tab-ico"><Ico name={APP[id].ico} size={21} />{badges[id] ? <i className="tab-badge">{badges[id]}</i> : null}</span>
            <span className="tab-label">{APP[id].name}</span>
          </button>
        ))}
      </Glass>
      <Glass as="button" interactive className="tab-search" title="搜索（/）" onClick={() => ui.setOverlay('spot')}>
        <Ico name="search" size={19} />
      </Glass>
    </div>
  )
}

/** Home 指示条：单击 / 上滑 = 回主屏；双击 = 多任务 */
export function HomeIndicator({ onHome }: { onHome?: () => void }): ReactNode {
  const start = useRef<number | null>(null)
  const [lift, setLift] = useState(0)
  const home = onHome ?? (() => ui.goHome())
  return (
    <div className="home-ind" title="单击 / 上滑：回到主屏幕 · 双击：多任务"
      style={{ transform: `translateX(-50%) translateY(${-lift}px)` }}
      onPointerDown={e => { start.current = e.clientY; (e.target as HTMLElement).setPointerCapture(e.pointerId) }}
      onPointerMove={e => { if (start.current != null) setLift(Math.max(0, Math.min(80, start.current - e.clientY))) }}
      onPointerUp={e => {
        const d = start.current == null ? 0 : start.current - e.clientY
        start.current = null; setLift(0)
        if (d > 50 || Math.abs(d) < 4) home()
      }}
      onDoubleClick={() => ui.setOverlay('switcher')}>
      <i />
    </div>
  )
}
