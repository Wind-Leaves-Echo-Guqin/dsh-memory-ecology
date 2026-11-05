import { useEffect, useState } from 'react'
import {
  fetchCircles, fetchCirclesStats, fetchCircleDetail,
  type CircleRow, type CirclesData, type CirclesStatsData,
} from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G1d · 副圈详情 —— 逐圈看详细信息（简单页面式，用户指定：不花哨）。
 *
 * 选一个圈 → 概要 + 预算 + 路由 + 成员条目清单 + 跨圈命中。
 * 数据全部来自核心（circles / catalog --scope / circles-stats），不自算。
 * 旧核心缺 circles-stats → 跨圈命中面板降级提示（能力探测口径）。
 */

function Bar({ used, max }: { used: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (used / max) * 100) : 0
  const cls = pct > 95 ? 'bad' : pct > 80 ? 'warn' : ''
  return <div className="eco-bar"><i className={cls} style={{ width: pct + '%' }} /></div>
}

export default function CircleDetailView() {
  const s = useUi()
  const { data: circ, err } = useAsync<CirclesData>(() => fetchCircles(), [s.refreshTick])
  const { data: stats } = useAsync<CirclesStatsData>(() => fetchCirclesStats(), [s.refreshTick])
  const items = circ?.items ?? []
  const circles = items.filter((c) => c.id !== 'global')
  const [sel, setSel] = useState<string>('')

  // 默认选第一个副圈；没有副圈就选 global
  useEffect(() => {
    if (!circ) return
    if (!sel || !items.some((c) => c.id === sel)) {
      setSel(circles[0]?.id ?? 'global')
    }
  }, [circ]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data: detail, err: dErr } = useAsync<CircleRow extends never ? never : import('../lib/api_v3_2_0').CircleDetailData>(
    () => fetchCircleDetail(sel || 'global'),
    [s.refreshTick, sel],
  )
  const row: CircleRow | null = detail?.row ?? items.find((c) => c.id === sel) ?? null
  const entries = detail?.items ?? []
  const press = row && row.budget_chars > 0 ? Math.round((row.catalog_chars / row.budget_chars) * 100) : 0
  const pres = row?.match?.cwd_prefix ?? []
  const statRows = stats?.rows ?? []
  const myBridges = statRows.filter((r) => r.routed === sel || r.scope === sel)

  useEffect(() => {
    if (!row) { ui.setExportRows([]); return }
    const rows: ExportRow[] = [
      { k: '圈', v: `${row.id}${row.display_name && row.display_name !== row.id ? `（${row.display_name}）` : ''}` },
      { k: '条目', v: `${row.count} · 目录 ${row.catalog_count}/${row.catalog_limit} 条 ${row.catalog_chars}/${row.budget_chars} 字（${press}%）` },
      { k: '属性', v: `${row.declared ? '已声明' : '自动'} · ${row.readonly ? '只读' : '可写'}` },
      { k: '路由前缀', v: pres.length ? pres.join(' , ') : '—（按目录名）' },
      ...entries.slice(0, 40).map((e) => ({ k: `条目 ${e.id}`, v: `[${e.item_scope}] ${e.line}` })),
    ]
    ui.setExportRows(rows)
  }, [detail]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">圈数据读取失败：{err}</div>

  const name = row ? (row.display_name && row.display_name !== row.id ? `${row.display_name}（${row.id}）` : row.id) : '—'

  return (
    <div className="dash view-enter">
      <div className="vh">
        副圈详情
        <small>逐圈看详细信息 · 数据与核心 <span className="mono">circles / catalog --scope / circles-stats</span> 逐值一致</small>
      </div>

      {items.length === 0 ? (
        <div className="panel">
          <div className="empty">
            <EmptyArt kind="memories" />
            <div style={{ marginTop: 10 }}>还没有任何圈。</div>
            <div className="faint" style={{ marginTop: 6 }}>
              去「圈管理」声明 circles.json（可一键载入首批副圈模板），或投递时用
              <span className="mono"> --scope &lt;项目名&gt;</span> 建圈。
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* ── 圈选择 ── */}
          <section className="panel">
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
              {items.map((c) => (
                <button key={c.id}
                  className={'chip' + (c.id === sel ? ' ok' : '')}
                  style={{ cursor: 'pointer', border: c.id === sel ? '1px solid #30d158' : undefined }}
                  onClick={() => setSel(c.id)}>
                  {c.id === 'global' ? '☉ global（主圈）' : c.id}
                  <span className="faint" style={{ marginLeft: 4 }}>{c.count}</span>
                </button>
              ))}
            </div>
          </section>

          {/* ── 概要 ── */}
          <section className="panel">
            <div className="row" style={{ marginBottom: 10 }}>
              <b style={{ fontSize: 15 }}>{name}</b>
              {row?.declared ? <span className="tag">已声明</span> : <span className="tag">自动</span>}
              {row?.readonly ? <span className="tag warn">只读</span> : <span className="tag ok">可写</span>}
              {sel === 'global' && <span className="tag ok">主圈 · 对所有泡生效</span>}
              {myBridges.some((b) => b.suggest) && <span className="tag ok">有跨圈命中建议</span>}
            </div>
            <div className="eco-stats">
              <div className="eco-stat"><b className="num">{row?.count ?? '—'}</b><small>条目总数</small></div>
              <div className="eco-stat"><b className="num">{row?.catalog_count ?? '—'}<span className="faint"> / {row?.catalog_limit ?? '—'}</span></b><small>目录条数</small></div>
              <div className="eco-stat"><b className="num">{row?.catalog_chars ?? '—'}<span className="faint"> / {row?.budget_chars ?? '—'}</span></b><small>目录占用（字符）</small></div>
              <div className="eco-stat"><b className="num">{press}%</b><small>预算压力{press > 80 ? ' · 超压' : ''}</small></div>
              <div className="eco-stat"><b className="num">{detail?.total ?? '—'}</b><small>可进目录总量</small></div>
            </div>
            <div style={{ marginTop: 8 }}><Bar used={row?.catalog_chars ?? 0} max={row?.budget_chars ?? 1} /></div>
            <div className="faint" style={{ marginTop: 8, fontSize: 12 }}>
              路由前缀：{pres.length ? pres.join(' , ') : '—（按目录名）'}
              {circ?.tail_max_chars ? <> · 副圈尾段总闸 <span className="mono">{circ.tail_max_chars}</span> 字符</> : null}
            </div>
          </section>

          {/* ── 成员条目清单 ── */}
          <section className="panel">
            <div className="row" style={{ marginBottom: 8 }}>
              <b>成员条目（目录内）</b>
              <span className="chip">显示 <b>{entries.length}</b> 条</span>
              <span className="faint" style={{ fontSize: 12 }}>
                标注 <span className="mono">global</span> 的条目 = 主圈共有（对所有圈生效）
              </span>
            </div>
            {dErr ? (
              <div className="empty">条目清单读取失败：{dErr}</div>
            ) : entries.length === 0 ? (
              <div className="empty">
                <EmptyArt kind="memories" />
                <div style={{ marginTop: 8 }}>这个圈目前目录里没有条目。</div>
                <div className="faint" style={{ marginTop: 6 }}>
                  在命中路由前缀的目录里开会话产生的记忆会进这个圈（投递 <span className="mono">--scope {sel}</span> 也行）。
                </div>
              </div>
            ) : (
              <div className="table eco-detail-table">
                <div className="tr head">
                  <span>id</span><span>类型</span><span>归属</span><span>摘要</span><span>命中</span><span>更新</span>
                </div>
                {entries.map((e) => (
                  <div key={e.id} className="tr">
                    <span className="mono ellipsis" style={{ fontSize: 11, maxWidth: 190 }}>{e.id}</span>
                    <span>{e.kind}</span>
                    <span>{e.item_scope === sel
                      ? <span className="tag ok">本圈</span>
                      : <span className="tag">global</span>}</span>
                    <span className="ellipsis" style={{ maxWidth: 430 }} title={e.line}>{e.line}</span>
                    <span className="num">{e.hits}</span>
                    <span className="faint" style={{ fontSize: 11 }}>{(e.updated || '').replace('T', ' ').slice(0, 16)}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── 跨圈命中 ── */}
          <section className="panel">
            <div className="row" style={{ marginBottom: 8 }}>
              <b>跨圈命中（circle_hit）</b>
              {stats && !stats.available && <span className="chip">需 v3.2.2+ 核心（已降级）</span>}
              {stats?.available && <span className="chip">建议阈值 {stats.suggest_after}（按去重条目计）</span>}
            </div>
            {myBridges.length === 0 ? (
              <div className="faint" style={{ fontSize: 12 }}>
                暂无记录。A 圈会话里命中了 B 圈（挂载圈）的条目时记一笔——这是跨圈迁移/驯化的数据源。
                {stats && !stats.available ? '（当前核心版本未提供该账目，升级核心后亮起）' : ''}
              </div>
            ) : (
              <div className="table eco-bridge-table">
                <div className="tr head"><span>会话圈</span><span>命中圈</span><span>次数</span><span>去重条目</span><span>最近</span><span>建议</span></div>
                {myBridges.map((b, i) => (
                  <div key={i} className="tr">
                    <span className="mono" style={{ fontSize: 12 }}>{b.routed || 'global'}</span>
                    <span className="mono" style={{ fontSize: 12 }}>{b.scope}</span>
                    <span className="num">{b.hits}</span>
                    <span className="num">{b.distinct}</span>
                    <span className="faint" style={{ fontSize: 11 }}>{(b.last_ts || '').replace('T', ' ').slice(0, 16)}</span>
                    <span>{b.suggest ? <span className="tag ok">建议挂载</span> : <span className="faint">—</span>}</span>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="panel eco-note">
            <b>怎么读这一页</b>
            <div className="faint">
              主圈（global）= 大气层：charter 规定 + USER 气候 + 全局目录，对所有副圈常驻生效、不可挤掉。
              副圈 = 特化泡：成员条目 + global 条目合并成圈目录（命中排序），会话里由 agent 用
              <span className="mono"> memory_circle_attach / detach</span> 热挂载/分离，多圈可同时启用。
              「跨圈命中」是 A 圈会话命中 B 圈条目的账——攒多了就是"这条记忆该搬家/该共享"的证据。
            </div>
          </section>
        </>
      )}
    </div>
  )
}
