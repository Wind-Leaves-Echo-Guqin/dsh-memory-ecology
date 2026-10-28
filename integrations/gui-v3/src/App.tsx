import { useEffect } from 'react'
import { invalidate } from './lib/api'
import { openExpDetail, openMemDetail } from './lib/drawers'
import { useEcoFeeder } from './lib/eco'
import { ui, useUi, type ViewId } from './lib/ui'
import { GlassDefs } from './components/kit'
import { StatusBar } from './components/StatusBar'
import { HomeScreen, Wallpaper } from './components/HomeScreen'
import { AppHost } from './components/AppWindow'
import { ControlCenter, LockScreen, NotificationCenter } from './components/Centers'
import { Spotlight } from './components/Spotlight'
import { AppSwitcher, Cruise } from './components/Switcher'
import { KbdSheet, Sheet, WhatsNew } from './components/Sheets'

/** gui-v2.3 外壳 = iOS / iPadOS 操作系统复刻（液态玻璃）：
 * 锁屏 → 主屏（小组件 + 应用图标 + Dock + 搜索）→ 应用（九个观测视图 + 设置，图标放大启动）
 * + 状态栏 / 灵动岛 / 控制中心 / 通知中心 / Spotlight / 多任务 / 底部卡片 / 巡航与待机。
 * 数据层与九视图沿用 v2.2（只读对接 8788 /api/*）。 */

export default function App() {
  const s = useUi()
  useEcoFeeder()

  // 当前前台表面：墙纸之上（主屏/锁屏/中心面板）状态栏用白字；应用之内跟随外观
  useEffect(() => {
    const wall = s.locked || s.home || ['cc', 'nc', 'spot', 'switcher'].includes(s.overlay) || s.cruise === 'big'
    document.documentElement.dataset.surface = wall ? 'wall' : 'app'
    document.documentElement.dataset.view = s.home ? 'home' : s.view
  }, [s.locked, s.home, s.overlay, s.cruise, s.view])

  // ── hash 同步（浏览器前进/后退 = 打开应用/回主屏） ──
  useEffect(() => {
    const onHash = () => ui.syncFromHash()
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  // ── 解锁后首次使用：弹「新功能介绍」 ──
  useEffect(() => {
    if (s.locked || ui.guideDone()) return
    const t = window.setTimeout(() => ui.setGuideStep(0), 700)
    return () => clearTimeout(t)
  }, [s.locked])

  // ── 全局轮询（30s）：快照变化 → 巡检提示 + 当前应用重取；浮层打开时不打断 ──
  useEffect(() => {
    let lastSnap = ''
    const timer = window.setInterval(async () => {
      try {
        const d = await (await fetch('/api/meta')).json()
        const snap = String(d.snapshot ?? '')
        if (snap && snap !== lastSnap) {
          if (lastSnap) {
            ui.pingPatrol()
            invalidate()
            const st = ui.get()
            if (!st.drawer && !st.overlay && !st.cruise) ui.bumpRefresh()
          }
          lastSnap = snap
        }
      } catch { /* fail-open */ }
    }, 30000)
    return () => clearInterval(timer)
  }, [])

  // ── focus 跳转（检索/跨视图跳详情）：数据到达后开卡片 ──
  useEffect(() => {
    const f = s.focus
    if (!f) return
    if (f.kind === 'detail') void openMemDetail(f.slug)
    else if (f.kind === 'exp') void openExpDetail(f.id)
  }, [s.focus])

  // ── 全局快捷键（对齐 v1：g 前缀跳转 / / 搜索 / r 刷新 / ? 参考；新增 Ctrl+↑ 多任务、Esc 回主屏） ──
  useEffect(() => {
    const GOTO: Record<string, ViewId> = {
      d: 'dashboard', m: 'memories', e: 'experiences', s: 'starmap', l: 'lineage',
      c: 'candidates', t: 'timeline', h: 'health', a: 'actions', ',': 'settings',
      // v3.2.0 新视图：g 前缀 + 单键（避开已占用的 d/m/e/s/l/c/t/h/a）
      g: 'gates', o: 'circles', i: 'inject', p: 'packs',
      k: 'manage', $: 'cost', f: 'firstaid',
    }
    let gPending = false
    let gTimer = 0
    const typing = (el: EventTarget | null): boolean => {
      if (!el || !(el instanceof HTMLElement)) return false
      const t = el.tagName.toLowerCase()
      return t === 'input' || t === 'textarea' || t === 'select' || el.isContentEditable
    }
    const onKey = (e: KeyboardEvent) => {
      const k = e.key
      const st = ui.get()
      if (st.locked) return
      if ((e.ctrlKey || e.metaKey) && (k === 'k' || k === 'K')) { e.preventDefault(); ui.setOverlay('spot'); return }
      if ((e.ctrlKey || e.metaKey) && k === 'ArrowUp') { e.preventDefault(); ui.setOverlay('switcher'); return }
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (k === 'f' || k === 'F')) { e.preventDefault(); ui.setCruise(st.cruise ? null : 'rotate'); return }
      if (k === 'Escape') {
        if (st.drawer) ui.closeDrawer()
        else if (st.overlay) ui.closeOverlays()
        else if (st.guideStep >= 0) ui.finishGuide()
        else if (st.cruise) ui.setCruise(null)
        else if (!st.home) ui.goHome()
        return
      }
      if (typing(e.target) || e.ctrlKey || e.metaKey || e.altKey) { gPending = false; return }
      if (gPending) {
        gPending = false
        const v = GOTO[k.toLowerCase()]
        if (v) { e.preventDefault(); ui.closeOverlays(); ui.navigate(v) }
        return
      }
      if (k === 'g' || k === 'G') {
        gPending = true
        clearTimeout(gTimer)
        gTimer = window.setTimeout(() => { gPending = false }, 400)
        return
      }
      if (k === '?' || (e.shiftKey && e.code === 'Slash')) { e.preventDefault(); ui.setOverlay('kbd'); return }
      if (k === '/') { e.preventDefault(); ui.setOverlay('spot'); return }
      if (k === 'r' || k === 'R') {
        e.preventDefault()
        invalidate()
        ui.bumpRefresh()
        ui.toast('已重新读取快照', true)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); clearTimeout(gTimer) }
  }, [])

  return (
    <>
      <GlassDefs />
      <Wallpaper />
      <div className={'os' + (s.cruise ? ' cruising' : '') + (s.cruise === 'big' ? ' standby-on' : '') + (s.locked ? ' locked' : '')}>
        <HomeScreen />
        <AppHost />
      </div>
      <StatusBar />
      <ControlCenter />
      <NotificationCenter />
      <Spotlight />
      <AppSwitcher />
      <Sheet />
      <WhatsNew />
      <KbdSheet />
      <Cruise />
      <LockScreen />
    </>
  )
}
