import { useEffect } from 'react'
import { fetchCost, type CostData } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G6 · 成本面板 —— 钱花在哪。
 *
 * 口径要点（**这也是踩过的坑**）：
 * KPI（≤3 条 / ≤1,200 字符每会话）**只对本插件自己的注入源**（`eco-*`）成立。
 * 把所有注入源（time-context / skill-catalog / subagent …）都算进来会让"超标"
 * 变成常态误报 —— 实测同一会话本插件 368 字符、全量 92k 字符，全算等于永远红，
 * 告警一响就没人看了。全量单列，不参与判死。
 */

const n = (v: number | null | undefined) =>
  v == null ? '—' : v.toLocaleString('zh-CN')

export default function CostView() {
  const s = useUi()
  const { data, err } = useAsync<CostData>(() => fetchCost(), [s.refreshTick])

  useEffect(() => {
    const rows: ExportRow[] = (data?.rows ?? []).map(r => ({
      k: (r.ts || '').replace('T', ' '),
      v: `工具 ${n(r.tools_schema)} · 系统提示词 ${n(r.system_prompt)} · 目录 ${n(r.catalog)}`
        + ` · 本插件 ${r.eco_msgs == null ? '—' : n(r.eco_msgs) + ' 条/' + n(r.eco_chars)}`
        + ` · 全量 ${n(r.injected_msgs)} 条/${n(r.injected_chars)}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">成本面板读取失败：{err}</div>

  const kpi = data?.kpi ?? { max_messages: 3, max_chars: 1200, applies_to: '本插件' }
  const unknown = !data?.has_split
  const over = !!data?.over_kpi

  return (
    <div className="dash view-enter">
      <div className="vh">
        成本面板
        <small>工具 schema / 系统提示词 / 目录字符 / 每会话注入条数 · 与 tools/measure.py 同一批账本</small>
      </div>

      {over ? (
        <div className="eco-alarm">
          <b>本插件注入 KPI 超标</b> —— {n(data?.worst_messages)} 条 / {n(data?.worst_chars)} 字符，
          上限 {kpi.max_messages} 条 / {n(kpi.max_chars)} 字符。
          <div className="faint" style={{ marginTop: 6, color: 'inherit' }}>
            追加是 append-only，每条都会加长之后每一次请求的前缀。
          </div>
        </div>
      ) : null}

      {unknown && (data?.rows ?? []).length ? (
        <div className="eco-alarm" style={{ background: 'rgba(255,214,10,.12)', borderColor: 'rgba(255,214,10,.4)', color: '#ffd60a' }}>
          <b>这批成本快照没有分来源字段</b>
          <div className="faint" style={{ marginTop: 6, color: 'inherit' }}>
            旧格式的 cost 行只有全量数字，无法区分「本插件」与「其它注入源」。
            重新跑一次 <span className="mono">python -m memory_ecology measure</span>（或让插件在会话后自动记）
            就会写入带 eco_* 字段的新行。
          </div>
        </div>
      ) : null}

      <div className="eco-stats">
        <div className="eco-stat">
          <b className="num">{n(data?.tools_schema)}</b>
          <small>工具 schema（字符）</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
            常驻，每次请求都付。这是最大单笔支出。
          </div>
        </div>
        <div className="eco-stat">
          <b className="num">{n(data?.system_prompt)}</b>
          <small>系统提示词（字符）</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
            前缀，走缓存；本插件的目录段在这里面
          </div>
        </div>
        <div className="eco-stat">
          <b className="num">{n(data?.catalog_chars)}</b>
          <small>目录段（字符）</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
            预算 600；条数不是闸门，字符才是
          </div>
        </div>
        <div className="eco-stat">
          <b className={'num ' + (over ? 'bad' : unknown ? '' : 'ok')}>
            {unknown ? '—' : n(data?.worst_messages) + ' 条'}
          </b>
          <small>本插件每会话追加</small>
          <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
            目录是 section 不是 message —— 默认 <b>0 条</b>
          </div>
        </div>
      </div>

      {(data?.rows ?? []).length === 0 ? (
        <div className="panel">
          <div className="empty">
            <EmptyArt kind="timeline" />
            <div style={{ marginTop: 10 }}>还没有成本快照。</div>
            <div className="faint" style={{ marginTop: 6 }}>
              跑一次 <span className="mono">python tools/measure.py</span> 就会有。
            </div>
          </div>
        </div>
      ) : (
        <section className="panel">
          <div className="vh" style={{ fontSize: 13 }}>
            最近会话 <small>数据来自核心 cost 账本（与 tools/measure.py 同一批行）</small>
          </div>
          <div className="table eco-cost-table">
            <div className="tr head">
              <span>时间</span><span>会话</span><span>工具 schema</span>
              <span>系统提示词</span><span>目录</span><span>本插件</span><span>全量注入</span>
            </div>
            {data!.rows.map((r, i) => {
              const bad = r.eco_msgs != null
                && (r.eco_msgs > kpi.max_messages || (r.eco_chars ?? 0) > kpi.max_chars)
              return (
                <div key={i} className="tr">
                  <span className="faint mono">{(r.ts || '').replace('T', ' ')}</span>
                  <span className="faint mono ellipsis" style={{ fontSize: 10 }}>
                    {(r.session || '').slice(0, 26)}
                  </span>
                  <span className="num">{n(r.tools_schema)}</span>
                  <span className="num">{n(r.system_prompt)}</span>
                  <span className="num">{n(r.catalog)}</span>
                  <span>
                    {r.eco_msgs == null
                      ? <span className="tag">旧行</span>
                      : <span className={'tag ' + (bad ? 'bad' : 'ok')}>
                          {n(r.eco_msgs)} 条 / {n(r.eco_chars)}
                        </span>}
                  </span>
                  <span className="num faint">{n(r.injected_msgs)} 条 / {n(r.injected_chars)}</span>
                </div>
              )
            })}
          </div>
        </section>
      )}

      <section className="panel eco-note">
        <b>为什么「目录进前缀」比「追加消息」便宜</b>
        <div className="faint">
          实测上下文是 append-only：每条追加都会加长<b>之后每一次请求</b>的前缀。
          目录做成系统提示词里的一段，只付一次且走缓存；做成消息则每会话至少一条，
          且在 100 轮之后摊成十几倍。
          <br /><br />
          对照（本机实测）：同一会话里 <span className="mono">time-context</span> 6 条、
          <span className="mono"> skill-catalog</span> 1 条 7k 字符、
          <span className="mono"> subagent-settled</span> 3 条；本插件是
          <b> 1 条目录段 / 114–644 字符</b>，且 0 条消息。
          <br /><b>KPI 只对本插件成立</b>——把别的来源算进来会让告警永远响，等于没有告警。
        </div>
      </section>
    </div>
  )
}
