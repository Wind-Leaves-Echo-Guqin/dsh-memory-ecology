import type { ReactNode } from 'react'
import { Ico } from '../lib/icons'
import { BELL_APP, useEco } from '../lib/eco'
import { useClock, V03_URL } from '../lib/sys'
import { ui, useUi, type ViewId } from '../lib/ui'
import { APP, APPS, AppGlyph, AppIcon, DOCK, Glass, type AppDef } from './kit'

/** 墙纸：多层模糊光斑缓慢漂移（纯 CSS），html[data-wallpaper] 决定配色 */
export function Wallpaper() {
  return (
    <div className="wallpaper" aria-hidden="true">
      <i className="wp-b wp-b1" /><i className="wp-b wp-b2" /><i className="wp-b wp-b3" /><i className="wp-b wp-b4" />
      <i className="wp-grain" />
    </div>
  )
}

function Widget({ size, children, onClick, title, className = '' }: { size: 's' | 'm'; children: ReactNode; onClick?: () => void; title?: string; className?: string }) {
  return (
    <Glass className={`widget w-${size} ${className}`} interactive onClick={onClick} title={title}>
      {children}
    </Glass>
  )
}

function Ring({ value, size = 92, stroke = 9, color }: { value: number | null; size?: number; stroke?: number; color: string }) {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="w-ring">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeOpacity=".16" strokeWidth={stroke} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={value == null ? c : c * (1 - Math.min(100, value) / 100)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`} className="w-ring-arc" />
    </svg>
  )
}

export const scoreColor = (v: number | null) => v == null ? '#8e8e93' : v >= 80 ? '#30d158' : v >= 60 ? '#ffd60a' : '#ff453a'

function Widgets() {
  const s = useUi()
  const { ov, bell } = useEco()
  const score = ov?.score?.score ?? null
  const wm = ov?.watermark
  const pct = wm ? Math.round(wm.chars / Math.max(1, wm.quota) * 100) : null
  const over = !!wm && wm.chars > wm.quota
  const c = ov?.counts ?? {}
  const n = (k: string) => (typeof c[k] === 'number' ? (c[k] as number) : 0)
  const ck = ov?.candidates?.by_kind ?? {}
  const nCand = (ck.skill ?? 0) + (ck.profile ?? 0) + (ck.experience ?? 0)
  return (
    <div className="widgets">
      <Widget size="s" title="体检评分 → 驾驶舱" onClick={() => ui.navigate('dashboard')}>
        <div className="w-head"><Ico name="health" size={13} /> 体检评分</div>
        <div className="w-ringbox">
          <Ring value={score} color={scoreColor(score)} />
          <b className="w-ring-num num">{score ?? '—'}</b>
        </div>
        <div className="w-foot">{score == null ? '暂无评分' : score >= 80 ? '健康' : score >= 60 ? '亚健康' : '需关注'} · {ov?.score?.model ?? '—'}</div>
      </Widget>
      <Widget size="s" title="L1 常驻记忆水位 → 记忆库" onClick={() => ui.navigate('memories')} className={over ? 'w-alarm' : ''}>
        <div className="w-head"><Ico name="memories" size={13} /> 记忆水位</div>
        <div className="w-big num">{pct ?? '—'}<small>%</small></div>
        <div className="w-bar"><i style={{ width: Math.min(100, pct ?? 0) + '%' }} className={over ? 'over' : ''} /></div>
        <div className="w-foot num">{wm ? `${wm.chars} / ${wm.quota} 字` : '—'}{over ? ' · 越线' : ''}</div>
      </Widget>
      <Widget size="m" title="告警 → 通知中心" onClick={() => ui.setOverlay('nc')}>
        <div className="w-head"><Ico name="bell" size={13} /> 告警 <span className="w-head-r num">{s.bellCount} 条</span></div>
        {bell.length === 0
          ? <div className="w-calm"><Ico name="check" size={22} /> cron / 水位 / 悬空引用 / 报告 P0 / 候选 均正常</div>
          : (
            <ul className="w-list">
              {bell.slice(0, 3).map(b => (
                <li key={b.key}><i className={'w-dot ' + b.sev} /><span className="ellipsis">{b.text}</span></li>
              ))}
            </ul>
          )}
      </Widget>
      <Widget size="m" title="生态计数（目录实时统计）">
        <div className="w-head"><Ico name="grid" size={13} /> 生态计数</div>
        <div className="w-counts">
          {([
            ['L1 条目', n('l1_entries'), 'memories'], ['L2 详情', n('l2'), 'memories'], ['经验', n('exp_total'), 'experiences'],
            ['技能', n('skills'), 'starmap'], ['候选', nCand, 'candidates'], ['生态 cron', n('cron_eco'), 'actions'],
          ] as Array<[string, number, ViewId]>).map(([k, v, go]) => (
            <button key={k} className="w-count" onClick={e => { e.stopPropagation(); ui.navigate(go) }}>
              <b className="num">{v}</b><span>{k}</span>
            </button>
          ))}
        </div>
      </Widget>
    </div>
  )
}

/* 曾经的 FIRSTAID 占位图标已移除：v3.2.0 起急救箱是**真正的应用**
 * （`firstaid` 视图，读 dsh 根的根自证/物化/账本/四门四项检查），
 * 两个都叫「急救箱」会让主屏出现重名图标（实测截图发现）。
 * 旧的 v0.3 急救箱入口保留在「经典版」里，不重复占位。 */
const CLASSIC: AppDef = { id: 'dashboard', name: '经典版', ico: 'compass', bg: 'linear-gradient(160deg,#64d2ff,#0a84ff)', hint: 'v0.3 观测舱（旧根九视图 + 写操作 + 确认闸门）' }

function LinkIcon({ app, onClick }: { app: AppDef; onClick: () => void }) {
  return (
    <button className="app-icon" title={`${app.name} · ${app.hint}`} onClick={onClick}>
      <span className="app-icon-wrap"><AppGlyph app={app} size={60} /></span>
      <span className="app-label">{app.name}</span>
    </button>
  )
}

export function useBadges(): Partial<Record<ViewId, number>> {
  const { ov, bell } = useEco()
  const out: Partial<Record<ViewId, number>> = {}
  for (const b of bell) {
    const a = BELL_APP[b.src]
    if (a && a !== 'candidates') out[a] = (out[a] ?? 0) + 1
  }
  const ck = ov?.candidates?.by_kind ?? {}
  const nCand = (ck.skill ?? 0) + (ck.profile ?? 0) + (ck.experience ?? 0)
  if (nCand) out.candidates = nCand
  return out
}

/** 急救箱（控制中心 / 锁屏入口）：v3.2.0 起**就在本应用里**，不再是跳转。
 *
 * 旧实现是把用户甩到 v0.3 观测舱（另一个界面、另一套数据根），
 * 于是"点急救箱"等于"离开当前应用"。现在 `firstaid` 视图直接读 dsh 根，
 * 四项检查（根自证/物化/账本/四门）+ 两类幂等修复都在原地。 */
export function openFirstAid(): void {
  ui.closeOverlays()
  ui.navigate('firstaid')
}

export function HomeScreen() {
  const s = useUi()
  const badges = useBadges()
  const clock = useClock()
  const recentsOutsideDock = s.recents.filter(v => !DOCK.includes(v) && v !== 'settings').slice(0, 3)
  return (
    <div className={'home' + (s.home ? '' : ' behind')} aria-hidden={!s.home}>
      <div className="home-page">
        <div className="home-left">
          <div className="home-greet">
            <div className="home-greet-d">{clock.date}</div>
            <div className="home-greet-t">记忆生态 · 观测舱</div>
          </div>
          <Widgets />
        </div>
        <div className="app-grid">
          {APPS.map(a => <AppIcon key={a.id} id={a.id} badge={badges[a.id]} />)}
          <LinkIcon app={CLASSIC} onClick={() => window.open(V03_URL, '_blank')} />
        </div>
      </div>
      <button className="home-search" onClick={() => ui.setOverlay('spot')} title="Spotlight 搜索（/ 或 Ctrl+K）">
        <Glass className="home-search-g"><Ico name="search" size={13} /> 搜索</Glass>
      </button>
      <Glass className="dock">
        {DOCK.map(id => <AppIcon key={id} id={id} label={false} size={58} inDock badge={badges[id]} />)}
        <AppIcon id="settings" label={false} size={58} inDock />
        {recentsOutsideDock.length > 0 && <i className="dock-sep" />}
        {recentsOutsideDock.map(id => <AppIcon key={id} id={id} label={false} size={58} inDock badge={badges[id]} />)}
      </Glass>
    </div>
  )
}

export { APP }
