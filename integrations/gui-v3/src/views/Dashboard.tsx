import { useEffect, useMemo } from 'react'
import { fetchHealth, fetchOverview, type TrendPoint } from '../lib/api'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, copyText, type ExportRow } from '../lib/ui'
import { quotaGateCountdown } from '../lib/sys'
import { trendSvg } from '../lib/trend'
import { useAsync } from '../hooks/useAsync'

/** 驾驶舱 = v1 同构：cron 失败任务卡（每条可复制修复）+ 评分环 + 评分历史 + L1 水位
 * + 待处理候选 + 生态计数 + 四道门（账本/运行证据）+ 每日健康行（双口径）+ P0 建议。
 * v2.1 为只读预览：v1 的写操作按钮（重跑/挤出/立即执行）代理为「复制命令 / 去 v0.3」。 */

const GATE_META: Array<[string, string, string, string | null]> = [
  ['gate', '门① 写入整合', '#38bdf8', 'gate_integrate'],
  ['quota', '门② 巩固/配额', '#f59e0b', 'quota_extrude'],
  ['distill', '门③ 画像蒸馏', '#a78bfa', null],
  ['review', '门④ 复核', '#34d399', null],
]

function extractP0(body?: string): string[] {
  if (!body) return []
  const out: string[] = []
  for (const L of body.split('\n')) {
    if (/\bP0\b/.test(L) && L.trim().length > 12 && !out.includes(L.trim())) {
      out.push(L.trim().replace(/^[|#*\-\s]+/, '').slice(0, 160))
      if (out.length >= 2) break
    }
  }
  return out
}

const num = (v: unknown): number => (typeof v === 'number' ? v : 0)

export default function Dashboard() {
  const s = useUi()
  const { data: d, err } = useAsync(() => fetchOverview(), [s.refreshTick])
  const { data: h } = useAsync(() => fetchHealth(), [s.refreshTick])

  const p0 = useMemo(() => extractP0(h?.report?.body), [h])

  // 导出采集：视图数据就绪后登记（状态栏 JSON/MD/文本 导出用）
  useEffect(() => {
    if (!d) return
    const rows: ExportRow[] = []
    const score = d.score?.score
    if (score != null) rows.push({ k: '体检评分', v: `${score}/100（${d.score?.model} · ${d.score?.time}）` })
    rows.push({ k: 'L1 水位', v: `${d.watermark.chars} / ${d.watermark.quota}` })
    const c = d.counts
    for (const [k, v] of Object.entries(c)) if (typeof v === 'number') rows.push({ k, v: String(v) })
    for (const [k, v] of Object.entries(d.gate_counts ?? {})) rows.push({ k: '门账本 ' + k, v: String(v) })
    for (const f of d.cron?.recent_fails ?? []) rows.push({ k: 'cron 失败 ' + f.job, v: `${f.n ?? 1} 次 · ${f.error?.slice(0, 80) ?? ''}` })
    ui.setExportRows(rows)
  }, [d])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">API 不可达：{err}（请确认观测舱服务 8788 已启动）</div>
  if (!d) return <div className="empty view-enter">正在读取生态快照…</div>

  const score = d.score?.score ?? null
  const ringCls = score == null ? '' : score >= 80 ? 'ok' : score >= 60 ? 'warn' : 'bad'
  const ringColor = ({ ok: '#34d399', warn: '#f59e0b', bad: '#ef4444' } as Record<string, string>)[ringCls] ?? 'rgba(148,163,184,.4)'
  const over = d.watermark.chars > d.watermark.quota
  const pct = Math.min(100, (d.watermark.chars / Math.max(1, d.watermark.quota)) * 100)
  const overW = Math.max(0, ((d.watermark.chars - d.watermark.quota) / Math.max(1, d.watermark.quota)) * 100)
  const c = d.counts
  const e7 = d.cron?.executions_7d ?? {}
  const snap = d.cron?.jobs_snapshot ?? {}
  const failDetails = d.cron?.fail_details ?? []
  const ck = d.candidates?.by_kind ?? {}
  const nCand = (ck.skill ?? 0) + (ck.profile ?? 0) + (ck.experience ?? 0)
  const hist: TrendPoint[] = d.history ?? []
  const healthRow = typeof d.health_row === 'string'
    ? { date: d.health_row, gene: '', cron: '', fail: '', mem: '' }
    : d.health_row

  const copyFull = (i: number) => {
    const f = failDetails[i]
    if (f) void copyText(`【${f.job_name ?? f.job}】${f.finished_at ?? ''}\n${f.error ?? ''}`, '错误全文已复制')
  }

  return (
    <div className="dash view-enter">
      {failDetails.length > 0 && (
        <section className="panel fail-card">
          <h3>cron 失败任务 <small>近 7 天 {num(e7?.n_fails) || failDetails.length} 次（口径 executions.db · 截至 {(e7?.ts ?? '').slice(11, 16)}）——每条可复制修复</small></h3>
          {failDetails.map((f, i) => {
            const drift = /drift_skip|config drifted/.test(f.error ?? '')
            const pinCmd = f.provider ? `hermes cron edit ${f.job} --provider ${f.provider} --model ${f.model ?? '<model>'}` : ''
            return (
              <div key={i} className="fail-item">
                <div className="fail-head">
                  <span className="dot bad" />
                  <b>{f.job_name ?? f.job}</b>
                  <span className="faint">{(f.finished_at ?? '').replace('T', ' ')}</span>
                  <span className="fail-ops">
                    <a onClick={() => void copyText(`hermes cron run ${f.job}`, '重跑命令已复制（到 v0.3 观测舱执行）')}>复制重跑命令</a>
                    {' · '}
                    <a href="http://127.0.0.1:8788/static/index.html#/dashboard" target="_blank" rel="noreferrer">去 v0.3 重跑</a>
                    {drift && pinCmd && (
                      <>
                        {' · '}
                        <a onClick={() => void copyText(pinCmd, '固定命令已复制')}>复制固定命令（消除 drift_skip）</a>
                      </>
                    )}
                  </span>
                </div>
                <div className="fail-err mono">
                  {(f.error ?? '').slice(0, 400)}{(f.error ?? '').length > 400 ? '…' : ''}
                  {' '}
                  <a className="faint" onClick={() => copyFull(i)}>复制全文</a>
                </div>
              </div>
            )
          })}
        </section>
      )}

      {over && (
        <div className="alarm-inline over-line">
          🔴 <b>L1 记忆已越线</b>：{d.watermark.chars} / 管理线 {d.watermark.quota} 字符（超 {d.watermark.chars - d.watermark.quota}）
          {' · '}下次配额门 <b>{quotaGateCountdown()}</b> 后自动运行（每小时 25 分）
          <a href="http://127.0.0.1:8788/static/index.html#/dashboard" target="_blank" rel="noreferrer"> → 去 v0.3 立即挤出</a>
        </div>
      )}

      <div className="vh">驾驶舱 <small>只读预览 · 10 秒扫完 · 快照 {d.score?.time ?? '—'}</small></div>
      <div className="vsub">体检评分口径 = 每日 12:40 报告 · 其余计数 = 目录实时统计（两者时刻不同，不混称）</div>

      <div className="grid2">
        <section className="panel">
          <h3>体检评分</h3>
          <div className="ringbox">
            <svg className="ring" width="96" height="96" viewBox="0 0 100 100">
              <circle className="ring-track" cx="50" cy="50" r="45" strokeWidth="7" />
              <circle className="ring-arc" cx="50" cy="50" r="45" strokeWidth="7"
                style={{ stroke: ringColor, strokeDashoffset: score == null ? 283 : 283 * (1 - score / 100) }} />
            </svg>
            <div className="ring-info">
              <b>{score ?? '—'}</b> <span className="dim">/100</span><br />
              <span className={'dot ' + (ringCls || 'off')} />
              {score == null ? '暂无评分' : score >= 80 ? '🟢 健康' : score >= 60 ? '🟡 亚健康' : '🔴 需关注'}<br />
              <span className="faint small">模型 {d.score?.model ?? '—'} · 7 维明细 → <a onClick={() => ui.navigate('health')}>体检视图</a></span>
            </div>
          </div>
        </section>
        <section className="panel">
          <h3>评分历史 <small>同模型才连线，跨版本不比（生态口径）</small></h3>
          {hist.length
            ? <div dangerouslySetInnerHTML={{ __html: trendSvg(hist, 'var(--accent2)') }} />
            : <div className="empty">暂无评分历史</div>}
        </section>
      </div>

      <section className="panel">
        <div className="water">
          <div className="water-bar">
            <div className="water-fill" style={{ width: pct + '%' }} />
            {over && <div className="water-over" style={{ left: (100 - overW) + '%', width: overW + '%' }} />}
          </div>
          <div className="water-legend">
            <span>L1 常驻记忆 <b className="num">{d.watermark.chars}</b> 字 / 管理线 {d.watermark.quota}（去空白口径）</span>
            <span className={over ? 'alert-text' : 'faint'}>{over ? `已越线 +${d.watermark.chars - d.watermark.quota} · 红色段呼吸=真实状态` : '未越线'}</span>
          </div>
        </div>
      </section>

      {nCand > 0 && (
        <section className="panel warn-panel">
          <h3>待处理候选 <small>{nCand} 项——出现即有对应门/孵化台可消费</small></h3>
          <div className="row">
            <span className="chip click" onClick={() => ui.navigate('candidates')}>技能候选 <b>{ck.skill ?? 0}</b></span>
            <span className="chip click" onClick={() => ui.navigate('candidates')}>画像候选 <b>{ck.profile ?? 0}</b></span>
            <span className="chip click" onClick={() => ui.navigate('candidates')}>经验候选 <b>{ck.experience ?? 0}</b></span>
            <a onClick={() => ui.navigate('candidates')}>→ 去候选孵化台</a>
          </div>
        </section>
      )}

      <section className="panel">
        <h3>生态计数 <small>目录实时统计</small></h3>
        <div className="row wrapchips">
          <span className="chip click" onClick={() => ui.navigate('memories')}>L1 <b>{num(c.l1_entries)}</b> 条</span>
          <span className="chip click" onClick={() => ui.navigate('memories')}>L2 详情 <b>{num(c.l2)}</b></span>
          <span className="chip click" onClick={() => ui.navigate('memories')}>隔离区 <b>{num(c.quarantine)}</b></span>
          <span className="chip click" onClick={() => ui.navigate('experiences')}>经验 <b>{num(c.exp_total)}</b><span className="faint">（draft {num(c.exp_draft)} · verified {num(c.exp_verified)}）</span></span>
          <span className="chip click" onClick={() => ui.navigate('starmap')}>技能 <b>{num(c.skills)}</b><span className="faint">（唯一 {num(c.skills_unique)} · 归档 {num(c.skills_archive)} · 候选 {num(c.skills_candidates)}）</span></span>
          <span className="chip">生态 cron <b>{num(c.cron_eco)}</b>/{num(c.cron_total)}</span>
        </div>
      </section>

      <section className="panel">
        <h3>四道门 <small>账本 = eco.db 机读记录 · 运行证据 = cron 计划最近执行</small></h3>
        <div className="row stretch">
          {GATE_META.map(([k, nm, col, act]) => {
            const g = d.gates?.[k]
            const cnt = d.gate_counts?.[k] ?? 0
            const job = (d.cron?.eco_jobs ?? []).find(j =>
              (k === 'gate' && (j.name ?? '').includes('写入')) || (k === 'quota' && (j.name ?? '').includes('配额')) ||
              (k === 'distill' && (j.name ?? '').includes('蒸馏')) || (k === 'review' && (j.name ?? '').includes('复核')))
            return (
              <div key={k} className="gate">
                <div className="gname"><i className="gdot" style={{ background: col }} />{nm}</div>
                <div className="gmeta">
                  账本动作 <b className="num">{cnt}</b> 条
                  <br />{g ? `最近 ${(g.ts ?? '').replace('T', ' ')} · ${g.action ?? ''}` : <span className="faint">账本尚无动作</span>}
                  {job && (
                    <><br /><span className="faint">运行证据：cron {job.display} · 最近 {job.last_run_at ?? '—'} · <span className={'tag ' + (job.last_status === 'ok' ? 'ok' : 'bad')}>{job.last_status ?? '—'}</span></span></>
                  )}
                </div>
                {act && (
                  <div style={{ marginTop: 7 }}>
                    <button className="ghost-btn act-btn" onClick={() => ui.toast('只读预览——写操作请到 v0.3 观测舱执行', false)}>立即执行</button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </section>

      <section className="panel">
        <h3>每日健康行 <small>VERSION.md 口径（生态心电图）</small></h3>
        {healthRow ? (
          <div className="healthline">
            <span>{healthRow.date}</span>
            <span>基因库 {healthRow.gene}</span>
            <span>cron {healthRow.cron}</span>
            <span>连续失败 {healthRow.fail}</span>
            <span>记忆占用 {healthRow.mem}</span>
          </div>
        ) : <div className="empty">VERSION.md 无健康行</div>}
        <div className="healthline" style={{ marginTop: 10 }}>
          <span className="tag">瞬时口径（jobs.json）：{num(snap?.ok)} ok / {num(snap?.err)} err · 采集 {(snap?.ts ?? '').slice(11, 16)}</span>
          <span className={'tag ' + (num(e7?.n_fails) ? 'warn' : 'ok')}>近 7 天口径（executions.db）：{num(e7?.n_exec)} 次执行 · {num(e7?.n_fails)} 次失败 · 采集 {(e7?.ts ?? '').slice(11, 16)}</span>
        </div>
        {(d.gene?.length ?? 0) > 0 && (
          <div className="healthline" style={{ marginTop: 10 }}>
            <span className="faint">基因库近提交：</span>
            {d.gene!.slice(0, 3).map((g, i) => <span key={i} className="faint mono">{g}</span>)}
          </div>
        )}
      </section>

      {p0.length > 0 && (
        <section className="panel">
          <h3>最优先行动 <small>体检报告 §9 · P0/P1/P2 分级建议</small></h3>
          <div className="row">
            {p0.map((t, i) => (
              <span key={i} className="chip wide-chip">{t} <a onClick={() => ui.navigate('health')}>→ 去体检视图定位</a></span>
            ))}
          </div>
        </section>
      )}

      {!d.score && !hist.length && <EmptyArt kind="generic" title="快照为空" hint="确认观测舱服务（8788）在跑，然后右上角刷新。" />}
    </div>
  )
}
