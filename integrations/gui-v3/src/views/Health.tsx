import { useEffect, useMemo, useState } from 'react'
import { fetchHealth, fetchHealthReport, fetchMeta, type Arch, type TrendPoint } from '../lib/api'
import { md, mdP0 } from '../lib/md'
import { copyText, ui, useUi, type ExportRow } from '../lib/ui'
import { trendSvg } from '../lib/trend'
import { useAsync } from '../hooks/useAsync'

/** 体检评测 = v1 六页签同构：体检报告（mdP0 渲染）/ 归档时间轴 / 双报告对比 / 评分历史
 * / 评测门禁（LongMemEval + INJ/GOLD）/ 备份岩层。
 * 修复 v2 预览版的 bug：报告以 Markdown 版式渲染（此前是原始 JSON 文本）。 */

type Tab = 'report' | 'archive' | 'compare' | 'history' | 'eval' | 'backups'
const TABS: Array<[Tab, string]> = [
  ['report', '体检报告'], ['archive', '归档时间轴'], ['compare', '双报告对比'],
  ['history', '评分历史'], ['eval', '评测门禁'], ['backups', '备份岩层'],
]

export default function Health() {
  const s = useUi()
  const [tab, setTab] = useState<Tab>('report')
  const { data, err } = useAsync(() => fetchHealth(), [s.refreshTick])
  const { data: meta } = useAsync(() => fetchMeta(), [s.refreshTick])

  const archives = data?.archives ?? []
  const curScore = useMemo(() => (data?.report?.body?.match(/总分[^\d]{0,6}(\d{1,3})/) ?? [])[1] ?? null, [data])

  useEffect(() => {
    if (!data) return
    const rows: ExportRow[] = []
    if (curScore) rows.push({ k: '当前评分', v: curScore })
    rows.push({ k: '最新报告', v: data.report?.name ?? '—' })
    for (const p of data.history ?? []) rows.push({ k: '评分 ' + p.time, v: String(p.score) })
    ui.setExportRows(rows)
  }, [data, curScore])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">API 不可达：{err}</div>
  if (!data) return <div className="empty view-enter">正在读取体检数据…</div>

  const mtime = (data.report?.mtime ?? '').slice(0, 16).replace('T', ' ')
  const ageH = data.report?.mtime ? ((Date.now() - new Date(data.report.mtime).getTime()) / 3600000).toFixed(1) : null

  const openReport = async (name: string) => {
    try {
      const body = await fetchHealthReport(name)
      ui.openDrawer({
        title: '报告', sub: name,
        node: (
          <>
            <div className="md" dangerouslySetInnerHTML={{ __html: mdP0(body) }} />
            <div className="row" style={{ marginTop: 10 }}>
              <button className="ghost-btn" onClick={() => void copyText(body, '已复制')}>复制全文</button>
            </div>
          </>
        ),
      })
    } catch (e) {
      ui.toast('读取失败：' + (e instanceof Error ? e.message : String(e)), false)
    }
  }

  return (
    <div className="dash view-enter">
      <div className="vh">体检评测 <small>报告为只读渲染——评分来自体检脚本，本页不做计算（数据忠实性）</small></div>

      <section className="panel">
        <h3>生态版本 × 报告对照 <small>报告快照语义：生成后生态可能已变化</small></h3>
        <div className="row wrapchips">
          <span className="chip">生态版本 <b>{meta?.version ?? '—'}</b></span>
          <span className="chip">评分模型 <b>{meta?.score_model ?? '—'}</b></span>
          <span className="chip">最新报告 <b>{data.report?.name ?? '—'}</b></span>
          <span className="chip">生成于 {mtime || '—'}</span>
          {ageH != null && <span className={'tag ' + (Number(ageH) > 30 ? 'warn' : 'ok')}>距今 {ageH} 小时</span>}
          <span className="chip">归档 <b>{archives.length}</b> 份</span>
        </div>
      </section>

      <div className="pills htabs">
        {TABS.map(([k, n]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{n}</button>
        ))}
      </div>

      {tab === 'report' && (
        data.report?.body ? (
          <section className="panel">
            <div className="md report-md" dangerouslySetInnerHTML={{ __html: mdP0(data.report.body) }} />
            <p className="faint" style={{ marginTop: 10 }}>
              报告文件：{data.report.name ?? ''} · 生成于 {mtime} · 快照语义：生成后生态可能已变化 ·{' '}
              <a onClick={() => void copyText(data.report?.body ?? '', '报告全文已复制')}>复制报告全文</a>
            </p>
          </section>
        ) : <div className="empty">未找到体检报告（v0.3 观测舱「立即体检」或跑一次 eco_health_check.py 即生成）。</div>
      )}

      {tab === 'archive' && (
        <section className="panel">
          <h3>归档时间轴 <small>按日归档（保留 30 份）+ 当前覆盖式文件 · 点击查看</small></h3>
          {archives.length ? (
            <div className="archive-tl">
              {archives.map(a => (
                <ArchItem key={a.name} a={a} onClick={() => void openReport(a.name)} />
              ))}
            </div>
          ) : <div className="empty">暂无归档——跑一次体检即按日落一份。</div>}
        </section>
      )}

      {tab === 'compare' && (
        archives.length < 2
          ? <div className="empty">对比至少需要两份报告（当前 {archives.length} 份）——归档积累后可用。</div>
          : <CompareTab archives={archives} />
      )}

      {tab === 'history' && (
        <section className="panel">
          <h3>评分历史 <small>按模型分段，不跨版本连线</small></h3>
          {(data.history?.length ?? 0)
            ? <div dangerouslySetInnerHTML={{ __html: trendSvg((data.history ?? []) as TrendPoint[]) }} />
            : <div className="empty">暂无评分历史</div>}
          <div className="table" style={{ marginTop: 10 }}>
            {(data.history ?? []).slice().reverse().map((x, i) => (
              <div key={i} className="tr hist-tr">
                <span className="mono dim">{x.time}</span>
                <span className="num">{x.score}</span>
                <span className="dim">{x.model}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === 'eval' && (
        data.eval_latest ? (
          <>
            <section className="panel">
              <h3>评测报告 <small>LongMemEval 五维 + INJ/GOLD 门禁 · INSUFFICIENT 诚实单列</small></h3>
              <div className="md" dangerouslySetInnerHTML={{ __html: md(data.eval_latest.body) }} />
            </section>
            {(data.evals?.length ?? 0) > 1 && (
              <section className="panel">
                <h3>历史评测</h3>
                {(data.evals ?? []).slice(1).map(ev => (
                  <div key={ev.name} className="eval-item" onClick={() => {
                    ui.openDrawer({ title: '评测报告', sub: ev.name, node: <div className="md" dangerouslySetInnerHTML={{ __html: md(ev.body) }} /> })
                  }}>{ev.name}</div>
                ))}
              </section>
            )}
          </>
        ) : <div className="empty">尚无评测报告（跑一次 python eco_eval.py --gate 生成）。</div>
      )}

      {tab === 'backups' && (
        <section className="panel">
          <h3>备份岩层 <small>备份链=地质沉积层，越深越旧（目录实时统计）</small></h3>
          {data.backups?.length ? (
            <div className="strata">
              {data.backups.slice(0, 16).map(b => (
                <div key={b.date} className="strata-row">
                  <span>{b.date}</span>
                  <span className="num">{b.n}</span>
                  <div className="strata-bar">
                    <div className="strata-fill" style={{ width: Math.min(100, b.n * 6) + '%' }} />
                    <span className="faint small">{b.files.slice(0, 2).join(' · ')}{b.files.length > 2 ? ' …' : ''}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : <div className="empty-ok">无备份文件——各门写前自动备份，出现即说明治理动作发生过。</div>}
        </section>
      )}
    </div>
  )
}

function ArchItem({ a, onClick }: { a: Arch; onClick: () => void }) {
  return (
    <div className="archive-item" onClick={onClick}>
      <i className="gdot" style={{ background: a.source === 'current' ? 'var(--accent)' : '#a78bfa' }} />
      <b>{a.day ?? (a.mtime ?? '').slice(0, 10)}</b>
      <span className="faint">{(a.mtime ?? '').slice(0, 16).replace('T', ' ')}</span>
      <span className="tag">{a.source === 'current' ? '当前' : '归档'}</span>
    </div>
  )
}

function CompareTab({ archives }: { archives: Arch[] }) {
  const [a, setA] = useState(archives[1]?.name ?? archives[0]?.name ?? '')
  const [b, setB] = useState(archives[0]?.name ?? '')
  const [bodyA, setBodyA] = useState('')
  const [bodyB, setBodyB] = useState('')
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [ra, rb] = await Promise.all([fetchHealthReport(a), fetchHealthReport(b)])
        if (alive) { setBodyA(ra); setBodyB(rb) }
      } catch { /* */ }
    })()
    return () => { alive = false }
  }, [a, b])
  return (
    <section className="panel">
      <h3>双报告并排对比 <small>左右两列，同步滚动</small></h3>
      <div className="row" style={{ marginBottom: 10 }}>
        <select className="mini-input" value={a} onChange={e => setA(e.target.value)}>
          {archives.map(x => <option key={x.name} value={x.name}>{x.name}（{x.source === 'current' ? '当前' : '归档'}）</option>)}
        </select>
        <span className="faint">vs</span>
        <select className="mini-input" value={b} onChange={e => setB(e.target.value)}>
          {archives.map(x => <option key={x.name} value={x.name}>{x.name}（{x.source === 'current' ? '当前' : '归档'}）</option>)}
        </select>
      </div>
      <div className="cmp-wrap">
        <div className="md cmp-col" dangerouslySetInnerHTML={{ __html: mdP0(bodyA) || '<div class="empty">读取中…</div>' }} />
        <div className="md cmp-col" dangerouslySetInnerHTML={{ __html: mdP0(bodyB) || '<div class="empty">读取中…</div>' }} />
      </div>
    </section>
  )
}
