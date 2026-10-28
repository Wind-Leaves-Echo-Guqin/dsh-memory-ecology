import { useEffect, useState } from 'react'
import { fetchGates, runAction, type GateRow, type GatesData } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { Ico } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G2 · 四门实况 —— **v3.2.0 最重要的视图**。
 *
 * 回答一个问题：**哪个门死了、死了多久**。
 * 判据全在核心（`ledger.gate_status()`），本视图只负责把结果画红，不自己判断。
 *
 * 五种状态（旧 GUI 看不出其中四种）：
 *   ok        正常
 *   dead      账本一行都没有 → **根本没跑**（查调度/进程）
 *   idle-spin 有行但全是心跳 → **进程活着、没产出**（查判据/输入）
 *   starved   有动作但从没成功过期望的那一种（门② 挤出 52 次 0 提升 = 净流出系统）
 *   stale     有有效动作但太久
 */

const GATE_COLOR: Record<string, string> = {
  gate1: '#007aff', gate2: '#5e5ce6', gate3: '#ff9f0a', gate4: '#30d158',
}
const KIND_LABEL: Record<string, string> = {
  ok: '正常', dead: '从未运行', 'idle-spin': '空转', starved: '只出不进', stale: '停摆',
}
const KIND_TAG: Record<string, string> = {
  ok: 'ok', dead: 'bad', 'idle-spin': 'warn', starved: 'bad', stale: 'warn',
}

const fmtAge = (d: number | null): string => {
  if (d == null) return '从未'
  if (d < 1) return (d * 24).toFixed(1) + ' 小时'
  if (d < 60) return d.toFixed(1) + ' 天'
  return (d / 30).toFixed(1) + ' 个月'
}
const n = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString('zh-CN'))

function GateCard({ g, onDetail }: { g: GateRow; onDetail: () => void }) {
  const color = GATE_COLOR[g.gate] ?? '#8e8e93'
  return (
    <div className={'eco-gate' + (g.dead ? ' dead' : '')} data-kind={g.kind}>
      <div className="eco-gate-hd">
        <i className="eco-dot" style={{ background: color }} />
        <b>{g.name}</b>
        <span className={'tag ' + (KIND_TAG[g.kind] ?? '')}>{KIND_LABEL[g.kind] ?? g.kind}</span>
      </div>
      <div className="eco-gate-num">
        <b className={g.dead ? 'bad' : 'ok'}>{n(g.effective)}</b>
        <small>累计有效动作 · 账本 {n(g.total)} 行</small>
      </div>
      <div className="eco-gate-meta">
        <div>最后真实动作：<b>{g.last_action || '—'}</b></div>
        {g.last_ts ? <div className="faint mono">{g.last_ts.replace('T', ' ')}</div> : null}
        <div>距今 <b>{fmtAge(g.age_days)}</b>
          {g.expected_count === 0 && g.effective > 0
            ? <span className="tag bad" style={{ marginLeft: 6 }}>从未 {g.expect}</span> : null}
        </div>
      </div>
      {g.reason ? <div className="eco-gate-why">{g.reason}</div> : null}
      <button className="linklike eco-gate-more" onClick={onDetail}>为什么？</button>
    </div>
  )
}

export default function GatesView() {
  const s = useUi()
  const { data, err } = useAsync<GatesData>(() => fetchGates(), [s.refreshTick])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const rows: ExportRow[] = (data?.gates ?? []).map(g => ({
      k: g.gate,
      v: `${g.name} · ${KIND_LABEL[g.kind] ?? g.kind} · 有效 ${g.effective}/${g.total} · `
        + `期望动作 ${g.expected_count} · 最后 ${g.last_action || '—'} ${g.last_ts || ''} · 距今 ${fmtAge(g.age_days)}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  const runMaintain = async () => {
    setBusy(true)
    try {
      const d = await runAction('core_maintain', 'run')
      ui.toast(d.ok ? '四门已跑完（写前已自动备份）' : '失败：' + (d.error || '见动作日志'), d.ok)
      if (d.ok) ui.bumpRefresh()
    } finally {
      setBusy(false)
    }
  }

  if (err) {
    return (
      <div className="dash view-enter">
        <div className="vh">四门实况 <small>核心不可达</small></div>
        <div className="panel">
          <div className="empty">
            <Ico name="bell" size={30} />
            <div style={{ marginTop: 8 }}>读不到四门账本：{err}</div>
            <div className="faint" style={{ marginTop: 6 }}>
              多半是找不到 memory-ecology 核心包。用 <span className="mono">--eco-core</span> 指向
              <span className="mono"> eco-dsh/src </span>后重启观测舱。
            </div>
          </div>
        </div>
      </div>
    )
  }

  const gates = data?.gates ?? []
  const dead = gates.filter(g => g.dead)

  return (
    <div className="dash view-enter">
      <div className="vh">
        四门实况
        <small>账本口径 · 有效动作 vs 心跳分列 · 判据在核心里，这里只负责画红</small>
        <button className="ghost-btn" style={{ marginLeft: 10 }} disabled={busy} onClick={() => void runMaintain()}>
          {busy ? '跑着…' : '跑一次四门'}
        </button>
      </div>

      {dead.length ? (
        <div className="eco-alarm">
          <b>{dead.length} 个门处于空转</b> —— 治理链路名存实亡：
          {dead.map(g => g.name).join('、')}。
          <div className="faint" style={{ marginTop: 6, color: 'inherit' }}>
            空转不等于「没跑」：账本有 idle 心跳说明进程活着但没产出（多为判据无输入）；
            一行都没有说明根本没跑。
          </div>
        </div>
      ) : null}

      {gates.length === 0 ? (
        <div className="panel"><div className="empty">还没有四门账本。跑一次维护就会建账。</div></div>
      ) : (
        <div className="eco-gates">
          {gates.map(g => (
            <GateCard
              key={g.gate}
              g={g}
              onDetail={() => ui.openDrawer({
                title: g.name + ' · ' + (KIND_LABEL[g.kind] ?? g.kind),
                sub: g.reason || '正常',
                node: (
                  <div className="eco-kv">
                    <div><span>累计有效动作</span><b className="num">{n(g.effective)}</b></div>
                    <div><span>账本总行数</span><b className="num">{n(g.total)}</b></div>
                    <div><span>期望动作（{g.expect}）</span><b className="num">{n(g.expected_count)}</b></div>
                    <div><span>最后一次真实动作</span><b>{g.last_action || '—'}</b></div>
                    <div><span>最后动作时间</span><b className="mono">{(g.last_ts || '—').replace('T', ' ')}</b></div>
                    <div><span>距今天数</span><b className="num">{g.age_days == null ? '—' : g.age_days}</b></div>
                    <div><span>判定</span><b>{KIND_LABEL[g.kind] ?? g.kind}{g.dead ? '（判死）' : ''}</b></div>
                    <div><span>原因</span><b>{g.reason || '—'}</b></div>
                    <div className="eco-kv-note">
                      有效动作白名单是写死在核心里的：idle 心跳与 SELFCHECK 自检**不算**有效动作。
                      旧项目就是被 246 行 idle 骗了——四门停摆两个月没人发现。
                    </div>
                  </div>
                ),
              })}
            />
          ))}
        </div>
      )}

      <section className="panel eco-note">
        <b>判据</b>
        <div className="faint">
          空转判定：<span className="mono">有效动作 == 0</span> 或
          <span className="mono"> 最后有效动作距今 &gt; {data?.stale_days ?? 3} 天</span>
          或 <span className="mono">期望动作（promote/demote）== 0</span>。
          物化视图：{data?.materialized
            ? <span className="tag ok">正常</span>
            : <span className="tag warn">缺失/过期（插件注入不到目录）</span>}
          · detail 条目 {n(data?.detail)} 条
        </div>
        {(data?.notes ?? []).length ? (
          <ul className="eco-notes">{data!.notes.map((t, i) => <li key={i}>{t}</li>)}</ul>
        ) : null}
      </section>
    </div>
  )
}
