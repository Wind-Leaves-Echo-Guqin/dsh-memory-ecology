/** gui-v2.3 全局 UI 状态仓库（对齐 v1 core.js 的 S 状态 + toast/抽屉/浮层语义），
 * 在 v2.2 基础上加 iOS 外壳状态：主屏/前台应用、启动动画原点、最近使用（多任务）、锁屏。
 * 用 useSyncExternalStore 的极简实现——不引第三方状态库（v2 红线：零新依赖）。 */
import { useSyncExternalStore, type ReactNode } from 'react'

export type ViewId =
  | 'dashboard' | 'memories' | 'experiences' | 'starmap' | 'lineage'
  | 'candidates' | 'timeline' | 'health' | 'actions' | 'settings'
  // v3.2.0 新增（dsh 根口径；与上面九个旧根视图**并存，不混算**）
  | 'gates' | 'circles' | 'inject' | 'packs' | 'manage' | 'cost' | 'firstaid'

export const VIEW_TITLE: Record<ViewId, string> = {
  dashboard: '驾驶舱', memories: '记忆库', experiences: '经验笔记本', starmap: '技能星图',
  lineage: '血缘谱系', candidates: '候选孵化台', timeline: '时间线', health: '体检评测',
  actions: '动作日志', settings: '设置',
  gates: '四门实况', circles: '圈的视角', inject: '注入观测', packs: '记忆包',
  manage: '圈管理', cost: '成本面板', firstaid: '急救箱',
}

export type Focus =
  | { kind: 'detail'; slug: string }
  | { kind: 'exp'; id: string }
  | null

export type ExportRow = { k: string; v: string }
export type DrawerState = { title: string; sub?: string; node: ReactNode } | null
/** cc=控制中心 · nc=通知中心 · spot=Spotlight 搜索 · switcher=多任务 · kbd=快捷键表 */
export type Overlay = '' | 'cc' | 'nc' | 'spot' | 'switcher' | 'kbd'
export type AnimLevel = 'low' | 'medium' | 'full'
export type Rect = { x: number; y: number; w: number; h: number }

export type UiState = {
  view: ViewId
  home: boolean // true = 主屏幕在前台（应用已退到后台）
  launch: Rect | null // 应用图标位置：启动/返回主屏的缩放动画原点
  recents: ViewId[] // 最近使用（多任务卡片），新的在前
  locked: boolean
  focus: Focus
  drawer: DrawerState
  toast: { msg: string; ok: boolean; key: number } | null
  overlay: Overlay
  guideStep: number // -1 = 关闭
  cruise: null | 'rotate' | 'big'
  animLevel: AnimLevel
  fontStep: number
  patrol: number // >0 表示巡检灯亮的轮次
  bellCount: number
  exportRows: ExportRow[]
  refreshTick: number // 手动刷新/轮询触发视图重取的信号
}

const FONT_SIZES = [13, 14, 15, 16.5]

const readLS = (k: string, dflt: string) => {
  try { return localStorage.getItem(k) ?? dflt } catch { return dflt }
}
const writeLS = (k: string, v: string) => {
  try { localStorage.setItem(k, v) } catch { /* 隐私模式 */ }
}

const hashView = (): ViewId | null => {
  const h = location.hash.replace(/^#\/?/, '')
  return h && h in VIEW_TITLE ? (h as ViewId) : null
}

const bootView = hashView()

let state: UiState = {
  view: bootView ?? 'dashboard',
  home: bootView === null,
  launch: null,
  recents: bootView ? [bootView] : [],
  locked: readLS('eco_v23_lock_boot', '1') === '1',
  focus: null,
  drawer: null,
  toast: null,
  overlay: '',
  guideStep: -1,
  cruise: null,
  animLevel: (['low', 'medium', 'full'].includes(readLS('eco_v23_anim', 'full')) ? readLS('eco_v23_anim', 'full') : 'full') as AnimLevel,
  fontStep: Math.min(3, Math.max(0, Number(readLS('eco_v23_font_step', '1')) || 1)),
  patrol: 0,
  bellCount: 0,
  exportRows: [],
  refreshTick: 0,
}

const listeners = new Set<() => void>()
const emit = () => { for (const l of listeners) l() }
const set = (patch: Partial<UiState>) => { state = { ...state, ...patch }; emit() }

export const useUi = (): UiState => useSyncExternalStore(
  (cb) => { listeners.add(cb); return () => listeners.delete(cb) },
  () => state,
)

const pushRecent = (v: ViewId) => [v, ...state.recents.filter(x => x !== v)].slice(0, 12)

export const ui = {
  /** 打开应用（origin = 被点的图标位置，用于 iOS 缩放启动动画） */
  navigate(view: ViewId, focus: Focus = null, origin: Rect | null = null) {
    try { location.hash = '#/' + view } catch { /* ignore */ }
    set({ view, focus, home: false, launch: origin ?? state.launch, recents: pushRecent(view), drawer: null })
  },
  /** 回到主屏幕（应用缩回图标） */
  goHome() {
    try { history.replaceState(null, '', location.pathname + location.search) } catch { /* */ }
    set({ home: true, drawer: null, overlay: state.overlay === 'switcher' ? '' : state.overlay })
  },
  /** hash 回退/前进时同步（App 里挂 hashchange） */
  syncFromHash() {
    const v = hashView()
    if (v === null) { if (!state.home) set({ home: true }) ; return }
    if (v !== state.view || state.home) set({ view: v, home: false, focus: null, recents: pushRecent(v) })
  },
  closeRecent(v: ViewId) {
    const recents = state.recents.filter(x => x !== v)
    set(v === state.view && !state.home ? { recents, home: true } : { recents })
  },
  unlock() { set({ locked: false }) },
  lock() { set({ locked: true, overlay: '', drawer: null }) },
  setLockOnBoot(on: boolean) { writeLS('eco_v23_lock_boot', on ? '1' : '0') },
  lockOnBoot: () => readLS('eco_v23_lock_boot', '1') === '1',
  openDrawer(drawer: NonNullable<DrawerState>) { set({ drawer }) },
  closeDrawer() { set({ drawer: null }) },
  toast(msg: string, ok = true) {
    set({ toast: { msg, ok, key: Date.now() } })
  },
  clearToast() { set({ toast: null }) },
  setOverlay(overlay: Overlay) { set({ overlay: state.overlay === overlay ? '' : overlay }) },
  closeOverlays() { set({ overlay: '' }) },
  setGuideStep(guideStep: number) {
    set({ guideStep })
  },
  /** 结束引导（跳过或完成都算）——写持久化标记，之后刷新不再弹。 */
  finishGuide() {
    writeLS('eco_v23_guide_done', '1')
    set({ guideStep: -1 })
  },
  guideDone: () => readLS('eco_v23_guide_done', '') === '1',
  setCruise(cruise: UiState['cruise']) { set({ cruise }) },
  setAnimLevel(animLevel: AnimLevel) {
    writeLS('eco_v23_anim', animLevel)
    document.documentElement.dataset.anim = animLevel
    set({ animLevel })
  },
  setFontStep(fontStep: number) {
    const step = Math.min(3, Math.max(0, fontStep))
    writeLS('eco_v23_font_step', String(step))
    document.body.style.fontSize = FONT_SIZES[step] + 'px'
    set({ fontStep: step })
  },
  applyFont() { document.body.style.fontSize = FONT_SIZES[state.fontStep] + 'px' },
  pingPatrol() { set({ patrol: state.patrol + 1 }) },
  setBellCount(bellCount: number) { if (bellCount !== state.bellCount) set({ bellCount }) },
  setExportRows(exportRows: ExportRow[]) { set({ exportRows }) },
  bumpRefresh() { set({ refreshTick: state.refreshTick + 1 }) },
  get: () => state,
}

/** 复制（三级兜底：clipboard → execCommand → 手动全选弹层，保证一定复制得到）。 */
export async function copyText(text: string, okMsg = '已复制到剪贴板'): Promise<void> {
  const done = () => ui.toast(okMsg, true)
  const manual = () => {
    ui.openDrawer({
      title: '请手动复制',
      sub: '窗口剪贴板不可用——文本已全选，按 Ctrl+C 复制',
      node: (
        <textarea className="manual-copy" readOnly onFocus={e => e.target.select()} autoFocus
          value={text} onChange={() => { /* readOnly */ }} />
      ),
    })
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      done()
      return
    }
  } catch { /* 走兜底 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;left:-999px;top:0'
    document.body.appendChild(ta)
    ta.focus(); ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    if (ok) { done(); return }
  } catch { /* 走兜底 */ }
  manual()
}

/** 当前视图数据导出（JSON 下载 / MD、文本走剪贴板）——对齐 v1 exportView。 */
export function exportView(format: 'json' | 'md' | 'text', view: ViewId): void {
  const rows = ui.get().exportRows
  if (!rows.length) { ui.toast('当前应用暂无可导出的数据', false); return }
  const stamp = new Date().toLocaleString('sv-SE').slice(0, 16).replace(/[ :]/g, '-')
  const name = VIEW_TITLE[view]
  if (format === 'json') {
    const payload = { view, view_name: name, exported: new Date().toISOString(), data: rows }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `eco-${view}-${stamp}.json`
    document.body.appendChild(a); a.click(); document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(a.href), 4000)
    ui.toast('已导出 JSON 文件', true)
    return
  }
  if (format === 'md') {
    let mdText = `# 记忆生态 · ${name}\n\n导出时间：${new Date().toLocaleString()}\n\n`
    mdText += '| 字段 | 值 |\n|---|---|\n' + rows.map(r => `| ${r.k} | ${String(r.v).replace(/\|/g, '\\|')} |`).join('\n')
    void copyText(mdText, '已复制 Markdown')
    return
  }
  const text = `记忆生态 · ${name}（${new Date().toLocaleString()}）\n` + rows.map(r => `${r.k}：${r.v}`).join('\n')
  void copyText(text, '已复制纯文本')
}
