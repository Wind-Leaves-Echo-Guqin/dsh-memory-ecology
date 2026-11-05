import { useEffect } from 'react'
import { fetchCircles, fetchCirclesStats, fetchCoreStats, type CircleRow, type CirclesData, type CirclesStatsData, type CoreStats } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G1 · 圈的视角 —— v3.2.0 的"多生态圈"。
 *
 * 圈是**同一数据根里的作用域**，不是多个数据根（多根 = 多门多 cron = N 个写者）。
 * 数字全部来自核心 `circles`，本视图不自己算——否则与插件口径必然漂移。
 * v3.2.2+ 增强：尾段总闸（tail_max_chars）、跨圈命中建议（circles-stats，
 * 旧核心自动降级隐藏）。
 */

function Bar({ used, max }: { used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0
  const cls = pct > 95 ? 'bad' : pct > 80 ? 'warn' : ''
  return <div className="eco-bar"><i className={cls} style={{ width: pct + '%' }} /></div>
}

function CircleTable({ items, statRows }: { items: CircleRow[]; statRows: CirclesStatsData['rows'] }) {
  const suggestOf = (id: string) => statRows.filter((r) => r.scope === id && r.suggest)
  return (
    <div className="table eco-circle-table">
      <div className="tr head">
        <span>圈</span><span>条目</span><span>目录条数</span><span>目录占用</span>
        <span>来源</span><span>路由前缀</span><span>跨圈命中</span><span>状态</span>
      </div>
      {items.map(c => {
        const sug = suggestOf(c.id)
        const pre = (c.match?.cwd_prefix ?? []).join(' , ')
        return (
          <div key={c.id} className="tr">
            <span>
              <b>{c.id}</b>
              {c.display_name && c.display_name !== c.id
                ? <div className="faint" style={{ fontSize: 11 }}>{c.display_name}</div> : null}
            </span>
            <span className="num">{c.count}</span>
            <span className="num">{c.catalog_count}<span className="faint"> / {c.catalog_limit}</span></span>
            <span>
              <span className="num">{c.catalog_chars}</span>
              <span className="faint"> / {c.budget_chars}</span>
              <Bar used={c.catalog_chars} max={c.budget_chars} />
            </span>
            <span>{c.declared
              ? <span className="tag">已声明</span>
              : <span className="tag">自动</span>}</span>
            <span className="mono faint ellipsis" style={{ fontSize: 11 }}>
              {pre || '—（按目录名）'}
            </span>
            <span>
              {sug.length
                ? sug.map((r, i) => (
                  <span key={i} className="tag ok" title={`${r.routed || 'global'} 会话命中 ${r.hits} 次（去重 ${r.distinct}）`}>
                    建议挂载 ·{r.distinct}
                  </span>
                ))
                : <span className="faint" style={{ fontSize: 11 }}>—</span>}
            </span>
            <span>
              {c.readonly ? <span className="tag warn">只读</span> : <span className="tag ok">可写</span>}
              {(c.sources ?? []).length
                ? <span className="tag" title={(c.sources ?? []).join(',')} style={{ marginLeft: 4 }}>
                    挂载 {c.sources.length}</span> : null}
            </span>
          </div>
        )
      })}
    </div>
  )
}

export default function CirclesView() {
  const s = useUi()
  const { data, err } = useAsync<CirclesData>(() => fetchCircles(), [s.refreshTick])
  const { data: st } = useAsync<CoreStats>(() => fetchCoreStats(), [s.refreshTick])
  const { data: stats } = useAsync<CirclesStatsData>(() => fetchCirclesStats(), [s.refreshTick])

  useEffect(() => {
    const rows: ExportRow[] = (data?.items ?? []).map(c => ({
      k: c.id,
      v: `${c.count} 条 · 目录 ${c.catalog_count}/${c.catalog_limit} 条 ${c.catalog_chars}/${c.budget_chars} 字`
        + ` · ${c.declared ? '已声明' : '自动'} · ${c.readonly ? '只读' : '可写'}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">圈的视角读取失败：{err}</div>

  const items = data?.items ?? []
  const realCircles = items.filter(c => c.id !== 'global')
  const totalItems = items.reduce((a, b) => a + (b.count || 0), 0)
  const statRows = stats?.rows ?? []

  return (
    <div className="dash view-enter">
      <div className="vh">
        圈的视角
        <small>与核心 <span className="mono">circles</span> 逐值一致 · 双击圈看详情 · 空间形态看「生态泡」</small>
      </div>

      <div className="eco-stats">
        <div className="eco-stat"><b className="num">{realCircles.length}</b><small>项目圈（另加 global）</small></div>
        <div className="eco-stat"><b className="num">{totalItems}</b><small>条目总数</small></div>
        <div className="eco-stat"><b className="num">{data?.global_budget_chars ?? '—'}</b><small>全局目录预算（字符）</small></div>
        <div className="eco-stat"><b className="num">{data?.tail_max_chars ?? '—'}</b><small>副圈尾段总闸{data?.tail_max_chars ? '' : '（需 v3.2.2+ 核心）'}</small></div>
        <div className="eco-stat"><b className="num">{st?.detail ?? '—'}</b><small>detail 条数</small></div>
      </div>

      {items.length === 0 ? (
        <div className="panel">
          <div className="empty">
            <EmptyArt kind="memories" />
            <div style={{ marginTop: 10 }}>还没有任何圈。</div>
            <div className="faint" style={{ marginTop: 6 }}>
              投递时用 <span className="mono">--scope &lt;项目名&gt;</span> 即可建圈；
              或在「设置 → 圈配置」里声明路由前缀。
            </div>
          </div>
        </div>
      ) : (
        <section className="panel">
          <div className="row" style={{ marginBottom: 10 }}>
            <span className="chip">共 <b>{items.length}</b> 个圈</span>
            <span className="chip">来源 <b>{data?.source ?? 'core-cli'}</b></span>
            {stats && !stats.available && <span className="chip">跨圈命中需 v3.2.2+ 核心（已降级）</span>}
          </div>
          <CircleTable items={items} statRows={statRows} />
        </section>
      )}

      <section className="panel eco-note">
        <b>怎么理解「圈」</b>
        <div className="faint">
          圈是<b>同一个数据根里的作用域</b>，不是多个数据根 —— 多根就是多门多 cron，等于多个写者，
          直接违反「唯一写者」。圈由投递目录天然表达（<span className="mono">pending/&lt;scope&gt;/</span>），
          在会话里靠宿主的同名段遮蔽实现隔离（nearest layer wins）。
          <br />圈内条目只对本圈可见；<span className="mono">global</span> 对所有圈可见。
          <br /><b>路由</b>：cwd 前缀最长匹配 → 最近祖先圈 → 都不中则 global。
          <b>没声明就不猜</b>——按目录名判圈会让无关项目看到别的圈的机密（已修）。
          <br /><b>v3.2.2+ 机制</b>：主圈 = 大气层（charter 规定 + 气候 + 基础物种，常驻不可挤掉）；
          副圈 = 特化泡（目录随消息末端注入、会话中由 agent 热挂载/分离、多圈可同时启用）。
          空间形态看「<b>生态泡</b>」应用。
        </div>
      </section>
    </div>
  )
}
