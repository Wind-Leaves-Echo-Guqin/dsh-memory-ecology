import { useEffect, useMemo, useState } from 'react'
import { fetchL2, fetchLogDay, fetchLogs, type LogRow } from '../lib/api'
import { GATE_COL, GATE_NAME, histogramSvg, swimlaneSvg } from '../lib/charts'
import { md } from '../lib/md'
import { openMemDetail } from '../lib/drawers'
import { ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'
import BlackHole from './BlackHole'

/** 时间线 = v1 四形态结构 + v2 星系视界特效：
 * 形态① 星系视界（默认，v2 黑洞吸积盘特效——技能按血缘分量的公转星系）
 * 形态② 泳道图 / ③ 按日直方图（v1 SVG 图表原样移植）+ 最近 3 日明细卡
 * 形态④ 文本模式（按日分组 + 当日人读日报展开）。
 * 演化回放形态 v2.1 暂缺（README 有记录）。 */

type Form = 'galaxy' | 'swim' | 'hist' | 'text'

export default function Timeline() {
  const s = useUi()
  const [form, setForm] = useState<Form>('galaxy')
  const [filter, setFilter] = useState('')
  const [tip, setTip] = useState<{ x: number; y: number; row: LogRow } | null>(null)
  const [dayOpen, setDayOpen] = useState<string | null>(null)
  const [dayBody, setDayBody] = useState<string | null>(null)
  const { data, err } = useAsync(() => fetchLogs(300), [s.refreshTick])
  const { data: slugs } = useAsync(() => fetchL2(30000).then(items => items.map(x => x.slug)), [])

  const rows = useMemo(() => {
    const all = data?.rows ?? []
    return (filter ? all.filter(r => r.table === filter) : all)
      .slice()
      .sort((a, b) => a.ts.localeCompare(b.ts))
      .reverse()
  }, [data, filter])

  const days = useMemo(() => {
    const m: Record<string, LogRow[]> = {}
    for (const r of (filter ? (data?.rows ?? []).filter(r => r.table === filter) : (data?.rows ?? []))) {
      ;(m[r.ts.slice(0, 10)] ??= []).push(r)
    }
    return m
  }, [data, filter])

  useEffect(() => {
    if (!data) return
    const er: ExportRow[] = rows.slice(0, 60).map(r => ({ k: r.ts.slice(0, 16), v: `${GATE_NAME[r.table] ?? r.table} · ${r.action} · ${r.target ?? ''}` }))
    ui.setExportRows(er)
  }, [data, rows])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">API 不可达：{err}</div>

  const openDay = async (day: string) => {
    if (dayOpen === day) { setDayOpen(null); setDayBody(null); return }
    setDayOpen(day); setDayBody(null)
    try { setDayBody(await fetchLogDay(day)) } catch { setDayBody(null) }
  }

  const counts = data?.counts ?? {}

  return (
    <div className="dash view-enter">
      <div className="vh">时间线 <small>eco.db 四表合并 · 星系视界/泳道/直方图/文本 形态 · 图文同源（{data?.rows.length ?? 0} 条）</small></div>
      <div className="filters">
        <div className="pills">
          {['', ...Object.keys(GATE_NAME)].map(t => (
            <button key={t || 'all'} className={filter === t ? 'on' : ''}
              onClick={() => setFilter(t)}>{t ? GATE_NAME[t] : '全部'}</button>
          ))}
        </div>
        <div className="pills">
          {([['galaxy', '星系视界'], ['swim', '泳道'], ['hist', '按日直方图'], ['text', '文本模式']] as const).map(([k, n]) => (
            <button key={k} className={form === k ? 'on' : ''} onClick={() => setForm(k)}>{n}</button>
          ))}
        </div>
        <span className="faint">
          累计：{Object.entries(counts).map(([k, n]) => `${GATE_NAME[k.replace('_log', '')] ?? k} ${n}`).join(' · ')}
        </span>
      </div>

      {form === 'galaxy' ? (
        <div className="galaxy-wrap">
          <BlackHole />
        </div>
      ) : form === 'swim' ? (
        <section className="panel">
          <h3>四门泳道 <small>事件色块按门着色 · 悬停看详情 · 点击跳转（演化回放形态暂缺）</small></h3>
          {rows.length ? (
            <div className="swim-box" style={{ position: 'relative' }}
              onMouseOver={e => {
                const g = (e.target as HTMLElement).closest('[data-ev]')
                if (!g) { setTip(null); return }
                const r = rows[Number((g as HTMLElement).dataset.ev)]
                if (!r) return
                const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
                const el = (g as HTMLElement).getBoundingClientRect()
                setTip({ x: Math.min(el.left - box.left + 12, box.width - 260), y: el.top - box.top - 8, row: r })
              }}
              onMouseLeave={() => setTip(null)}
              onClick={e => {
                const g = (e.target as HTMLElement).closest('[data-ev]')
                if (!g) return
                const r = rows[Number((g as HTMLElement).dataset.ev)]
                if (!r) return
                if (slugs?.includes(r.target ?? '')) void openMemDetail(r.target!)
                else ui.toast(r.target ? `目标「${r.target}」不是 L2 实体` : '无目标实体', true)
              }}>
              <div dangerouslySetInnerHTML={{ __html: swimlaneSvg(rows) }} />
              {tip && (
                <div className="tooltip" style={{ left: tip.x, top: tip.y }}>
                  <b>{tip.row.action}</b> {tip.row.ts.slice(5, 16).replace('T', ' ')}
                  <br />{tip.row.target ?? ''}
                  {tip.row.note && <><br /><span className="faint">{tip.row.note.slice(0, 80)}</span></>}
                </div>
              )}
            </div>
          ) : <div className="empty">暂无事件</div>}
        </section>
      ) : form === 'hist' ? (
        <section className="panel">
          <h3>按日直方图 <small>每天各门动作数（堆叠柱，悬停看明细）</small></h3>
          <div dangerouslySetInnerHTML={{ __html: histogramSvg(rows) }} />
        </section>
      ) : (
        <section className="panel">
          {Object.keys(days).length ? Object.entries(days).map(([day, rs]) => (
            <div key={day}>
              <div className="tl-day">{day}
                <span className="dayfile" onClick={() => void openDay(day)}>展开当日人读日报 →</span>
              </div>
              {dayOpen === day && (
                <div className="day-file">
                  {dayBody ? <div className="md" dangerouslySetInnerHTML={{ __html: md(dayBody) }} />
                    : <div className="faint" style={{ padding: '6px 10px' }}>加载中/当日无人读日报文件。</div>}
                </div>
              )}
              {rs.map((r, i) => (
                <div key={i} className="tl-row">
                  <span className="ts">{r.ts.slice(11, 19)}</span>
                  <i className="gdot" style={{ background: GATE_COL[r.table], width: 7, height: 7, borderRadius: '50%' }} />
                  <span className="act">{r.action}</span>
                  <span className="tgt">{r.target}</span>
                  <span className="note">{r.note}</span>
                  {slugs?.includes(r.target ?? '') && (
                    <span className="jump" onClick={() => void openMemDetail(r.target!)}>查看实体 →</span>
                  )}
                </div>
              ))}
            </div>
          )) : <div className="empty">暂无日志记录</div>}
        </section>
      )}

      {form !== 'text' && form !== 'galaxy' && (
        <div className="tl-side">
          {Object.keys(days).sort().reverse().slice(0, 3).map(day => {
            const rs = days[day]
            const byGate: Record<string, number> = {}
            const acts: Record<string, number> = {}
            for (const r of rs) {
              byGate[r.table] = (byGate[r.table] ?? 0) + 1
              acts[r.action] = (acts[r.action] ?? 0) + 1
            }
            const top = Object.entries(acts).sort((a, b) => b[1] - a[1]).slice(0, 3)
            return (
              <section key={day} className="panel">
                <h3>{day} <small>{rs.length} 个动作</small></h3>
                <div className="row" style={{ marginBottom: 6 }}>
                  {Object.entries(byGate).map(([k, n]) => <span key={k} className="tag">{GATE_NAME[k] ?? k} × {n}</span>)}
                </div>
                <div className="faint small">高频动作：{top.map(([a, n]) => `${a} ×${n}`).join(' · ') || '—'}</div>
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
