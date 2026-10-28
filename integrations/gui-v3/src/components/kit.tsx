/** iOS 外壳基础件：液态玻璃 Glass / 应用图标 AppIcon / 开关 Switch / 分段控件 Segmented / 应用注册表 APPS。 */
import { useRef, type CSSProperties, type ReactNode } from 'react'
import { Ico } from '../lib/icons'
import { ui, type ViewId } from '../lib/ui'

export type AppDef = { id: ViewId; name: string; ico: string; bg: string; fg?: string; hint: string }

/** 应用注册表：一个观测视图 = 一个 App（图标配色取 iOS 系统色）
 *
 * 前九个是**旧根**（%LOCALAPPDATA%\\hermes：记忆/经验/技能/体检/时间线），
 * 后四个是 **v3.2.0 新根**（dsh：四门/圈/注入/记忆包）。两套并存、口径不混算，
 * 新应用的图标用"系统色 + 深底"区分，主屏上能一眼看出不是一个数据源。 */
export const APPS: AppDef[] = [
  { id: 'dashboard', name: '驾驶舱', ico: 'dashboard', bg: 'linear-gradient(160deg,#5ac8fa,#007aff)', hint: '观测总览（10 秒扫完）' },
  { id: 'memories', name: '记忆库', ico: 'memories', bg: 'linear-gradient(160deg,#c77dff,#5e5ce6)', hint: 'L1 常驻 + L2 详情层' },
  { id: 'experiences', name: '经验笔记', ico: 'experiences', bg: 'linear-gradient(160deg,#ffe066,#ff9f0a)', hint: '经验条目 + 根因检索' },
  { id: 'starmap', name: '技能星图', ico: 'starmap', bg: 'linear-gradient(160deg,#3a3a8c,#0b0b2a)', hint: '球状星系团' },
  { id: 'lineage', name: '血缘谱系', ico: 'lineage', bg: 'linear-gradient(160deg,#70d7ff,#30b0c7)', hint: '记忆脉络力导向图' },
  { id: 'candidates', name: '候选孵化', ico: 'candidates', bg: 'linear-gradient(160deg,#5be37d,#28a745)', hint: '三源候选聚合' },
  { id: 'timeline', name: '时间线', ico: 'timeline', bg: 'linear-gradient(160deg,#ff8a80,#ff3b30)', hint: '星系视界 / 泳道 / 直方图 / 文本' },
  { id: 'health', name: '体检', ico: 'health', bg: 'linear-gradient(160deg,#ffffff,#eef0f4)', fg: '#ff2d55', hint: '报告 / 归档 / 对比 / 评分历史' },
  { id: 'actions', name: '动作日志', ico: 'ledger', bg: 'linear-gradient(160deg,#ffb35c,#d9731a)', hint: 'GUI 审计账本 + cron 一览' },
  // ── v3.2.0 新根 ──
  { id: 'gates', name: '四门实况', ico: 'gates', bg: 'linear-gradient(160deg,#34c759,#0a7a2f)', hint: '哪个门死了、死了多久' },
  { id: 'circles', name: '圈的视角', ico: 'circles', bg: 'linear-gradient(160deg,#64d2ff,#0a84ff)', hint: '多生态圈 + 目录预算' },
  { id: 'inject', name: '注入观测', ico: 'inject', bg: 'linear-gradient(160deg,#bf5af2,#5e5ce6)', hint: '这次注入有没有用' },
  { id: 'packs', name: '记忆包', ico: 'packs', bg: 'linear-gradient(160deg,#ffd60a,#c28b00)', hint: 'mount / integrate + 哈希校验' },
  { id: 'manage', name: '圈管理', ico: 'circles', bg: 'linear-gradient(160deg,#30b0c7,#0a6b8a)', hint: '编辑 circles.json（走确认闸门）' },
  { id: 'cost', name: '成本面板', ico: 'cost', bg: 'linear-gradient(160deg,#ff9f0a,#b35f00)', hint: '常驻开销 + 注入 KPI' },
  { id: 'firstaid', name: '急救箱', ico: 'firstaid', bg: 'linear-gradient(160deg,#ff453a,#a11d16)', hint: '根自证 / 物化 / 账本 / 四门' },
  { id: 'settings', name: '设置', ico: 'gear', bg: 'linear-gradient(160deg,#b8b8be,#6c6c72)', hint: '墙纸 / 外观 / 动效 / 关于本机' },
]
export const APP: Record<ViewId, AppDef> = Object.fromEntries(APPS.map(a => [a.id, a])) as Record<ViewId, AppDef>

/** Dock / 应用内标签栏常驻的应用 */
export const DOCK: ViewId[] = ['dashboard', 'memories', 'gates', 'circles', 'health']

/** 液态玻璃：折射层（SVG 位移滤镜，仅 Chromium 生效，其余浏览器退化为磨砂）+ 着色层 + 高光边。
 * 指针在玻璃上移动时，高光跟随（--gx/--gy）。 */
export function Glass({ children, className = '', style, onClick, title, as = 'div', interactive = false, ...rest }: {
  children?: ReactNode; className?: string; style?: CSSProperties; onClick?: (e: React.MouseEvent<HTMLElement>) => void
  title?: string; as?: 'div' | 'button'; interactive?: boolean; [k: `data-${string}`]: string | undefined
}) {
  const ref = useRef<HTMLElement>(null)
  const onMove = (e: React.PointerEvent<HTMLElement>) => {
    const el = ref.current
    if (!el) return
    const r = el.getBoundingClientRect()
    el.style.setProperty('--gx', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%')
    el.style.setProperty('--gy', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%')
  }
  const Tag = as as 'div'
  return (
    <Tag ref={ref as React.RefObject<HTMLDivElement>} className={'glass' + (interactive ? ' glass-i' : '') + (className ? ' ' + className : '')}
      style={style} onClick={onClick} title={title} onPointerMove={onMove} {...rest}>
      <span className="glass-fx" aria-hidden="true" />
      <span className="glass-tint" aria-hidden="true" />
      <span className="glass-shine" aria-hidden="true" />
      <span className="glass-body">{children}</span>
    </Tag>
  )
}

/** 液态玻璃折射滤镜定义（全局挂一次） */
export function GlassDefs() {
  return (
    <svg className="glass-defs" width="0" height="0" aria-hidden="true" focusable="false">
      <filter id="lg-refract" x="0%" y="0%" width="100%" height="100%" colorInterpolationFilters="sRGB">
        <feTurbulence type="fractalNoise" baseFrequency="0.009 0.012" numOctaves="2" seed="7" result="noise" />
        <feGaussianBlur in="noise" stdDeviation="2.5" result="soft" />
        <feDisplacementMap in="SourceGraphic" in2="soft" scale="58" xChannelSelector="R" yChannelSelector="G" />
      </filter>
    </svg>
  )
}

export function appRect(id: ViewId): DOMRect | null {
  const el = document.querySelector<HTMLElement>(`[data-app-icon="${id}"]`)
  return el ? el.getBoundingClientRect() : null
}

export function AppGlyph({ app, size = 60 }: { app: AppDef; size?: number }) {
  return (
    <span className="app-glyph" style={{ width: size, height: size, background: app.bg, color: app.fg ?? '#fff', borderRadius: size * 0.2237 }}>
      <Ico name={app.ico} size={Math.round(size * 0.54)} />
    </span>
  )
}

export function AppIcon({ id, badge, label = true, size = 60, inDock = false }: { id: ViewId; badge?: number; label?: boolean; size?: number; inDock?: boolean }) {
  const app = APP[id]
  return (
    <button className={'app-icon' + (inDock ? ' in-dock' : '')} title={`${app.name} · ${app.hint}`}
      data-app-icon={inDock ? undefined : id}
      onClick={e => {
        const r = (e.currentTarget.querySelector('.app-glyph') as HTMLElement).getBoundingClientRect()
        ui.navigate(id, null, { x: r.left, y: r.top, w: r.width, h: r.height })
      }}>
      <span className="app-icon-wrap">
        <AppGlyph app={app} size={size} />
        {badge != null && badge > 0 && <i className="app-badge">{badge > 99 ? '99+' : badge}</i>}
      </span>
      {label && <span className="app-label">{app.name}</span>}
    </button>
  )
}

export function Switch({ on, onChange, title }: { on: boolean; onChange: (v: boolean) => void; title?: string }) {
  return (
    <button role="switch" aria-checked={on} title={title} className={'ios-switch' + (on ? ' on' : '')} onClick={() => onChange(!on)}>
      <i />
    </button>
  )
}

export function Segmented<T extends string>({ value, options, onChange, className = '' }: {
  value: T; options: Array<{ v: T; label: ReactNode; title?: string }>; onChange: (v: T) => void; className?: string
}) {
  const idx = Math.max(0, options.findIndex(o => o.v === value))
  return (
    <div className={'segmented ' + className} style={{ '--n': options.length, '--i': idx } as CSSProperties}>
      <span className="seg-thumb" aria-hidden="true" />
      {options.map(o => (
        <button key={o.v} className={o.v === value ? 'on' : ''} title={o.title} onClick={() => onChange(o.v)}>{o.label}</button>
      ))}
    </div>
  )
}

/** 状态栏用的 iOS 系统图标（信号 / Wi-Fi / 电池） */
export function WifiIcon({ on }: { on: boolean | null }) {
  const o = on ? 1 : 0.28
  return (
    <svg width="17" height="12" viewBox="0 0 17 12" aria-hidden="true">
      <path d="M8.5 2.2c2.4 0 4.6.9 6.3 2.5l1.2-1.3A10.6 10.6 0 0 0 8.5.4 10.6 10.6 0 0 0 1 3.4l1.2 1.3A8.8 8.8 0 0 1 8.5 2.2Z" fill="currentColor" opacity={o} />
      <path d="M8.5 5.6c1.5 0 2.9.6 3.9 1.5l1.2-1.3a7.4 7.4 0 0 0-10.2 0l1.2 1.3c1-.9 2.4-1.5 3.9-1.5Z" fill="currentColor" opacity={o} />
      <path d="M8.5 9c.6 0 1.2.2 1.6.6L8.5 11.4 6.9 9.6c.4-.4 1-.6 1.6-.6Z" fill="currentColor" opacity={on === false ? 0.28 : 1} />
    </svg>
  )
}
export function SignalIcon({ level }: { level: number }) {
  return (
    <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true">
      {[0, 1, 2, 3].map(i => (
        <rect key={i} x={i * 4.6} y={8.6 - i * 2.8} width="3.2" height={3.4 + i * 2.8} rx="0.9" fill="currentColor" opacity={i < level ? 1 : 0.3} />
      ))}
    </svg>
  )
}
export function BatteryIcon({ pct }: { pct: number | null }) {
  const p = pct == null ? 0 : Math.max(0, Math.min(100, pct))
  const col = pct == null ? 'currentColor' : p < 60 ? '#ff453a' : p < 80 ? '#ffd60a' : 'currentColor'
  return (
    <svg width="27" height="13" viewBox="0 0 27 13" aria-hidden="true">
      <rect x="0.6" y="0.6" width="22.6" height="11.8" rx="3.6" fill="none" stroke="currentColor" opacity="0.4" strokeWidth="1.1" />
      <rect x="2.3" y="2.3" width={Math.max(1.5, 19.2 * p / 100)} height="8.4" rx="2.1" fill={col} />
      <path d="M24.6 4.4v4.2c.9-.3 1.5-1.1 1.5-2.1s-.6-1.8-1.5-2.1Z" fill="currentColor" opacity="0.45" />
    </svg>
  )
}
