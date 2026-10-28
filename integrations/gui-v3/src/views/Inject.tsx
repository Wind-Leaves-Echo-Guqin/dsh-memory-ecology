import { useEffect } from 'react'
import { fetchInject, type InjectData } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G3 · 注入观测 —— "这次注入有没有用"。
 *
 * 两个反馈量（定义写死在核心，避免各处口径漂移）：
 *   reload_rate = 注入过、之后又被 load/search 拉回的比例 —— 越高说明注入有用
 *   dead_rate   = 注入过、之后再没被任何检索命中的比例
 *
 * **诚实优先**：样本不足时显示"不可算"，不填 0。填 0 会让"注入全是死重"永远看不见。
 */

const rate = (x: number | null) =>
  x == null ? null : (x * 100).toFixed(1) + '%'

function RateCard({ label, value, hint, invert }: {
  label: string; value: number | null; hint: string; invert?: boolean
}) {
  const txt = rate(value)
  // reload 越高越好；dead 越高越差
  const good = value == null ? '' : invert ? (value < 0.5 ? 'ok' : 'bad') : (value > 0.3 ? 'ok' : 'warn')
  return (
    <div className="eco-stat">
      <b className={'num ' + good}>{txt ?? '—'}</b>
      <small>{label}</small>
      <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
        {txt == null ? '不可算（样本不足）' : hint}
      </div>
    </div>
  )
}

export default function InjectView() {
  const s = useUi()
  const { data, err } = useAsync<InjectData>(() => fetchInject(), [s.refreshTick])

  useEffect(() => {
    const rows: ExportRow[] = (data?.sources ?? []).map(x => ({
      k: x.source,
      v: `${x.events} 次 · ${x.items} 条 · ${x.chars} 字符（均 ${x.avg_chars}）`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">注入观测读取失败：{err}</div>

  const srcs = data?.sources ?? []
  const maxChars = Math.max(1, ...srcs.map(x => x.chars || 0))
  const kpi = data?.kpi ?? { max_messages: 3, max_chars: 1200 }

  return (
    <div className="dash view-enter">
      <div className="vh">
        注入观测
        <small>按 source 聚合 · 有没有被回取（reload_rate / dead_rate）</small>
      </div>

      <div className="eco-stats">
        <div className="eco-stat">
          <b className="num">{data?.total_events ?? 0}</b>
          <small>注入事件</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>本插件写入的账（不含其它注入源）</div>
        </div>
        <RateCard label="回取率 reload_rate" value={data?.reload_rate ?? null}
          hint="注入过、之后又被 load/search 拉回" />
        <RateCard label="死重率 dead_rate" value={data?.dead_rate ?? null} invert
          hint="注入过、之后再没被任何检索命中" />
        <div className="eco-stat">
          <b className="num" style={{ fontSize: 18 }}>{kpi.max_messages} 条 / {kpi.max_chars}</b>
          <small>每会话注入 KPI</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
            本插件默认 <b>0 条追加</b>（目录进前缀）
          </div>
        </div>
      </div>

      {srcs.length === 0 ? (
        <div className="panel">
          <div className="empty">
            <EmptyArt kind="timeline" />
            <div style={{ marginTop: 10 }}>这个根还没有注入账本。</div>
            <div className="faint" style={{ marginTop: 6 }}>
              会话里用到 <span className="mono">memory_load</span> /
              <span className="mono"> memory_search</span> 之后就会开始记账。
            </div>
          </div>
        </div>
      ) : (
        <section className="panel">
          <div className="vh" style={{ fontSize: 13 }}>按来源 <small>chars 为该 source 累计注入字符</small></div>
          <div className="table eco-inject-table">
            <div className="tr head">
              <span>source</span><span>事件</span><span>条目</span><span>字符</span>
              <span>占比</span><span>估算 token</span>
            </div>
            {srcs.map(x => (
              <div key={x.source} className="tr">
                <span><b>{x.source}</b></span>
                <span className="num">{x.events}</span>
                <span className="num">{x.items}</span>
                <span className="num">{x.chars}<span className="faint">（均 {x.avg_chars}）</span></span>
                <span><div className="eco-bar"><i style={{ width: (x.chars / maxChars) * 100 + '%' }} /></div></span>
                <span className="num faint">{x.tokens}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {(data?.recent ?? []).length ? (
        <section className="panel">
          <div className="vh" style={{ fontSize: 13 }}>最近 30 条 <small>时间倒序</small></div>
          <div className="table eco-inject-recent">
            <div className="tr head"><span>时间</span><span>source</span><span>形态</span><span>条目</span><span>字符</span></div>
            {data!.recent.map((r, i) => (
              <div key={i} className="tr">
                <span className="faint mono">{(r.ts || '').replace('T', ' ')}</span>
                <span>{r.source || '—'}</span>
                <span>{r.form || '—'}</span>
                <span className="num">{r.items}</span>
                <span className="num">{r.chars}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="panel eco-note">
        <b>为什么这两个率重要</b>
        <div className="faint">
          目录是<b>猜测</b>：猜错了就是纯 token 浪费。所以必须有反馈量回来——
          <span className="mono">reload_rate</span> 高说明目录里的条目确实被用到；
          <span className="mono">dead_rate</span> 高说明目录在塞没用的东西，该收紧准入。
          <br />两个数都只从事件流算，不靠推断；样本不足时显示「不可算」，<b>绝不填 0</b>。
        </div>
      </section>
    </div>
  )
}
