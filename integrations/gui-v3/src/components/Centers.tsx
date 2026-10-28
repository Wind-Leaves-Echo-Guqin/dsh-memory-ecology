import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { invalidate } from '../lib/api'
import { Ico } from '../lib/icons'
import { BELL_APP, useEco, type BellItem } from '../lib/eco'
import { applyAppearance, applyWallpaper, getWallpaper, resolvedAppearance, WALLPAPERS, type WallpaperId } from '../lib/palette'
import { copyDiagnostic, useClock, useServiceAlive } from '../lib/sys'
import { copyText, exportView, ui, useUi, type AnimLevel } from '../lib/ui'
import { APP, AppGlyph, Glass } from './kit'
import { openFirstAid, scoreColor } from './HomeScreen'

/* ───────────────────────── 控制中心 ───────────────────────── */

/** 竖向滑块（亮度/音量样式）：steps 个离散档位，拖动或点击设置 */
function VSlider({ value, steps, onChange, ico, label }: { value: number; steps: number; onChange: (v: number) => void; ico: string; label: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  const pick = (clientY: number) => {
    const r = ref.current!.getBoundingClientRect()
    const f = 1 - (clientY - r.top) / r.height
    onChange(Math.max(0, Math.min(steps - 1, Math.round(f * (steps - 1)))))
  }
  const pct = steps <= 1 ? 100 : (value / (steps - 1)) * 82 + 18
  return (
    <Glass className="cc-tile cc-vslider" title={label}>
      <div ref={ref} className="vs-track"
        onPointerDown={e => { dragging.current = true; (e.target as HTMLElement).setPointerCapture(e.pointerId); pick(e.clientY) }}
        onPointerMove={e => { if (dragging.current) pick(e.clientY) }}
        onPointerUp={() => { dragging.current = false }}>
        <i className="vs-fill" style={{ height: pct + '%' }} />
        <span className="vs-ico"><Ico name={ico} size={20} /></span>
      </div>
      <span className="cc-cap">{label}</span>
    </Glass>
  )
}

function Tile({ ico, label, on, onClick, span, children, title }: { ico?: string; label: string; on?: boolean; onClick?: () => void; span?: string; children?: ReactNode; title?: string }) {
  return (
    <Glass as="button" interactive className={'cc-tile' + (on ? ' on' : '') + (span ? ' ' + span : '')} onClick={onClick} title={title ?? label}>
      {ico && <span className="cc-tile-ico"><Ico name={ico} size={22} /></span>}
      {children}
      <span className="cc-cap">{label}</span>
    </Glass>
  )
}

function Round({ ico, on, label, color, onClick }: { ico: string; on: boolean | null; label: string; color: string; onClick?: () => void }) {
  return (
    <button className={'cc-round' + (on ? ' on' : '')} style={{ '--c': color } as CSSProperties} title={label} onClick={onClick}>
      <Ico name={ico} size={20} />
      <span>{label}</span>
    </button>
  )
}

const ANIM_STEPS: AnimLevel[] = ['low', 'medium', 'full']
const ANIM_ZH: Record<AnimLevel, string> = { low: '轻度', medium: '中度', full: '满载' }

export function ControlCenter() {
  const s = useUi()
  const { meta } = useEco()
  const alive = useServiceAlive()
  const [dark, setDark] = useState(() => resolvedAppearance() === 'dark')
  const [wp, setWp] = useState<WallpaperId>(getWallpaper)
  if (s.overlay !== 'cc') return null
  const run = (f: () => void) => () => { ui.closeOverlays(); f() }
  return (
    <div className="center-mask cc-mask" onClick={() => ui.closeOverlays()}>
      <div className="cc" onClick={e => e.stopPropagation()}>
        <Glass className="cc-tile span-2x2 cc-conn">
          <div className="cc-conn-grid">
            <Round ico="wave" on={alive} color="#0a84ff" label={alive ? '服务在线' : '服务离线'} onClick={() => ui.toast(alive ? '观测舱服务 8788 在线' : '观测舱服务 8788 未连接', !!alive)} />
            <Round ico="memories" on={!!meta?.writer_today_ran} color="#30d158" label={meta?.writer_today_ran ? '今日已写入' : '今日未写入'} onClick={() => ui.toast('单写入方 Hermes cron：今日' + (meta?.writer_today_ran ? '已运行' : '未运行'), true)} />
            <Round ico="search" on={!!meta?.search_cli_ready} color="#ff9f0a" label="检索 CLI" onClick={() => ui.toast('检索 CLI ' + (meta?.search_cli_ready ? '就绪' : '不可用'), !!meta?.search_cli_ready)} />
            <Round ico="refresh" on={s.patrol > 0} color="#5e5ce6" label="巡检" onClick={run(() => { invalidate(); ui.bumpRefresh(); ui.toast('已重新读取快照', true) })} />
          </div>
        </Glass>
        <Glass className="cc-tile span-2x2 cc-now">
          <div className="cc-now-head">
            <AppGlyph app={APP.dashboard} size={40} />
            <div>
              <b>生态 {meta?.version ?? '—'}</b>
              <span>评分模型 {meta?.score_model ?? '—'}</span>
            </div>
          </div>
          <div className="cc-now-meta">数据截至 {meta?.snapshot?.replace('T', ' ') ?? '—'}</div>
          <div className="cc-now-ctl">
            <button title="刷新" onClick={run(() => { invalidate(); ui.bumpRefresh(); ui.toast('已重新读取快照', true) })}><Ico name="refresh" size={20} /></button>
            <button title="巡航轮播" onClick={run(() => ui.setCruise('rotate'))}><Ico name="play" size={22} /></button>
            <button title="拷贝诊断" onClick={() => void copyDiagnostic(s.bellCount)}><Ico name="diag" size={20} /></button>
          </div>
        </Glass>
        <Tile ico={dark ? 'moon' : 'sun'} label={dark ? '深色' : '浅色'} on={dark} onClick={() => { const v = !dark; applyAppearance(v ? 'dark' : 'light'); setDark(v) }} />
        <Tile ico="lock" label="锁定" onClick={() => ui.lock()} />
        <VSlider value={s.fontStep} steps={4} ico="text" label="字号" onChange={v => ui.setFontStep(v)} />
        <VSlider value={ANIM_STEPS.indexOf(s.animLevel)} steps={3} ico="sparkle" label={'动效 ' + ANIM_ZH[s.animLevel]} onChange={v => ui.setAnimLevel(ANIM_STEPS[v])} />
        <Tile ico="grid" label="多任务" onClick={run(() => ui.setOverlay('switcher'))} />
        <Tile ico="standby" label="待机" onClick={run(() => ui.setCruise('big'))} />
        <Glass className="cc-tile span-2x1 cc-wp">
          <div className="cc-wp-row">
            {WALLPAPERS.map(w => (
              <button key={w.id} className={'cc-wp-dot' + (wp === w.id ? ' on' : '')} style={{ background: w.preview }} title={'墙纸 · ' + w.name}
                onClick={() => { applyWallpaper(w.id); setWp(w.id) }} />
            ))}
          </div>
          <span className="cc-cap">墙纸</span>
        </Glass>
        <Tile ico="firstaid" label="急救箱" onClick={run(openFirstAid)} />
        <Tile ico="kbd" label="快捷键" onClick={run(() => ui.setOverlay('kbd'))} />
        {!s.home && (
          <Glass className="cc-tile span-2x1 cc-export">
            <div className="cc-export-row">
              <button onClick={run(() => exportView('json', s.view))}>JSON</button>
              <button onClick={run(() => exportView('md', s.view))}>MD</button>
              <button onClick={run(() => exportView('text', s.view))}>文本</button>
            </div>
            <span className="cc-cap">导出当前应用</span>
          </Glass>
        )}
        <Tile ico="gear" label="设置" onClick={run(() => ui.navigate('settings'))} />
      </div>
    </div>
  )
}

/* ───────────────────────── 通知 ───────────────────────── */

const DISMISS_KEY = 'eco_v23_dismissed'
const loadDismissed = (): string[] => { try { return JSON.parse(sessionStorage.getItem(DISMISS_KEY) ?? '[]') as string[] } catch { return [] } }

export function useVisibleBell(): [BellItem[], (k: string) => void, () => void] {
  const { bell } = useEco()
  const [gone, setGone] = useState<string[]>(loadDismissed)
  const save = (v: string[]) => { setGone(v); try { sessionStorage.setItem(DISMISS_KEY, JSON.stringify(v)) } catch { /* */ } }
  return [bell.filter(b => !gone.includes(b.key)), k => save([...gone, k]), () => save([...gone, ...bell.map(b => b.key)])]
}

const SRC_NAME: Record<string, string> = { cron: 'cron 调度', mem: '记忆水位', cand: '候选池', report: '体检报告', dangling: '技能引用' }

export function NotifCard({ it, onDismiss, compact = false }: { it: BellItem; onDismiss?: () => void; compact?: boolean }) {
  const app = APP[BELL_APP[it.src] ?? it.goto]
  return (
    <Glass className={'notif' + (compact ? ' compact' : '') + ' sev-' + it.sev}>
      <div className="notif-head">
        <AppGlyph app={app} size={compact ? 22 : 24} />
        <span className="notif-app">{app.name}</span>
        <span className="notif-time">{SRC_NAME[it.src] ?? '现在'}</span>
      </div>
      <div className="notif-body">{it.text}</div>
      {!compact && (
        <div className="notif-acts">
          <button onClick={() => void copyText(it.copy, '已拷贝')}><Ico name="copy" size={13} /> 拷贝</button>
          <button onClick={() => { ui.closeOverlays(); ui.navigate(it.goto) }}><Ico name="chev" size={13} /> 去处理</button>
          {onDismiss && <button onClick={onDismiss}><Ico name="close" size={12} /> 忽略</button>}
        </div>
      )}
    </Glass>
  )
}

export function NotificationCenter() {
  const s = useUi()
  const clock = useClock()
  const { bellReady } = useEco()
  const [items, dismiss, dismissAll] = useVisibleBell()
  if (s.overlay !== 'nc') return null
  return (
    <div className="center-mask nc-mask" onClick={() => ui.closeOverlays()}>
      <div className="nc" onClick={e => e.stopPropagation()}>
        <div className="nc-clock">
          <div className="nc-date">{clock.date}</div>
          <div className="nc-time num">{clock.hm}</div>
        </div>
        <div className="nc-headline">
          <b>通知中心</b>
          {items.length > 0 && <button className="nc-clear" onClick={dismissAll}>全部忽略</button>}
        </div>
        <div className="nc-list">
          {!bellReady && <div className="nc-empty">正在聚合告警…</div>}
          {bellReady && items.length === 0 && (
            <div className="nc-empty"><Ico name="check" size={18} /> 无告警——cron / 水位 / 悬空引用 / 报告 P0 / 候选均正常</div>
          )}
          {items.map(it => <NotifCard key={it.key} it={it} onDismiss={() => dismiss(it.key)} />)}
        </div>
        <div className="nc-foot">只读观测——修复动作请在 v0.3 观测舱执行（跳转后由其确认闸门接管）。「忽略」仅在本次会话隐藏。</div>
      </div>
    </div>
  )
}

/* ───────────────────────── 锁屏 ───────────────────────── */

function LockGauge({ value, label, color, text }: { value: number | null; label: string; color: string; text: string }) {
  const r = 22, c = 2 * Math.PI * r
  return (
    <div className="lk-gauge" title={label}>
      <svg viewBox="0 0 54 54" width="62" height="62">
        <circle cx="27" cy="27" r={r} fill="none" stroke="currentColor" strokeOpacity=".22" strokeWidth="5" strokeDasharray={`${c * 0.75} ${c}`} transform="rotate(135 27 27)" strokeLinecap="round" />
        <circle cx="27" cy="27" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={`${c * 0.75 * Math.min(1, (value ?? 0) / 100)} ${c}`} transform="rotate(135 27 27)" />
      </svg>
      <b className="num">{text}</b>
      <span>{label}</span>
    </div>
  )
}

export function LockScreen() {
  const s = useUi()
  const clock = useClock()
  const { ov, meta } = useEco()
  const [items] = useVisibleBell()
  const [leaving, setLeaving] = useState(false)
  const [drag, setDrag] = useState(0)
  const start = useRef<number | null>(null)

  const unlock = () => {
    if (leaving) return
    if (s.animLevel === 'low') { ui.unlock(); return }
    setLeaving(true)
    window.setTimeout(() => { ui.unlock(); setLeaving(false); setDrag(0) }, 460)
  }

  useEffect(() => {
    if (!s.locked) return
    const onKey = (e: KeyboardEvent) => { if (!['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) { e.preventDefault(); e.stopPropagation(); unlock() } }
    const onWheel = (e: WheelEvent) => { if (e.deltaY > 20) unlock() }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('wheel', onWheel)
    return () => { window.removeEventListener('keydown', onKey, true); window.removeEventListener('wheel', onWheel) }
  }) // eslint-disable-line react-hooks/exhaustive-deps

  if (!s.locked) return null
  const score = ov?.score?.score ?? null
  const wm = ov?.watermark
  const pct = wm ? Math.round(wm.chars / Math.max(1, wm.quota) * 100) : null
  return (
    <div className={'lock' + (leaving ? ' leaving' : '')} style={drag ? { transform: `translateY(${-drag}px)`, transition: 'none' } : undefined}
      onPointerDown={e => { if ((e.target as HTMLElement).closest('button')) return; start.current = e.clientY; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) }}
      onPointerMove={e => { if (start.current != null) setDrag(Math.max(0, start.current - e.clientY)) }}
      onPointerUp={e => {
        if (start.current == null) return
        const d = start.current - e.clientY
        start.current = null
        if (d > 90 || Math.abs(d) < 4) unlock(); else setDrag(0)
      }}>
      <div className="lock-dim" />
      <div className="lk-top">
        <div className="lk-lock"><Ico name="lock" size={18} /></div>
        <div className="lk-date">{clock.dateLong}</div>
        <div className="lk-time num">{clock.hm}</div>
        <div className="lk-widgets">
          <LockGauge value={score} label="体检" color={scoreColor(score)} text={score == null ? '—' : String(score)} />
          <LockGauge value={pct} label="水位" color={wm && wm.chars > wm.quota ? '#ff453a' : '#64d2ff'} text={pct == null ? '—' : pct + '%'} />
          <div className="lk-rect">
            <b>记忆生态 {meta?.version ?? ''}</b>
            <span><Ico name="bell" size={12} /> {s.bellCount} 条告警</span>
            <span>快照 {meta?.snapshot?.slice(11, 16) ?? '—'}</span>
          </div>
        </div>
      </div>
      <div className="lk-notifs">
        {items.slice(0, 3).map(it => <NotifCard key={it.key} it={it} compact />)}
        {items.length > 3 && <div className="lk-more">还有 {items.length - 3} 条通知</div>}
      </div>
      <div className="lk-bottom">
        <Glass as="button" interactive className="lk-btn" title="急救箱" onClick={() => { unlock(); window.setTimeout(openFirstAid, 480) }}><Ico name="firstaid" size={22} /></Glass>
        <div className="lk-hint">上滑或单击以解锁</div>
        <Glass as="button" interactive className="lk-btn" title="刷新快照" onClick={() => { invalidate(); ui.bumpRefresh() }}><Ico name="refresh" size={22} /></Glass>
      </div>
      <div className="home-ind static"><i /></div>
    </div>
  )
}
