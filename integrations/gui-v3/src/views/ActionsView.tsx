import { useEffect } from 'react'
import { fetchActionLog, fetchCron, type CronJob } from '../lib/api'
import { EmptyArt } from '../lib/icons'
import { copyText, ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'

/** 动作日志 = v1 同构（action_log.jsonl 审计账本：时间/动作/阶段/结果/耗时/参数/输出摘要
 * + 复制全部 JSONL）。下方附 cron 任务一览（v2 增补保留：任务/计划/最近状态）。 */

export default function ActionsView() {
  const s = useUi()
  const { data: entries, err } = useAsync(() => fetchActionLog(), [s.refreshTick])
  const { data: jobs } = useAsync(() => fetchCron(), [s.refreshTick])

  useEffect(() => {
    if (!entries) return
    const rows: ExportRow[] = entries.slice(0, 60).map(e => ({
      k: e.ts, v: `${e.action} · ${e.stage} · ${e.ok ? '成功' : '失败'} · ${e.ms ?? 0}ms`,
    }))
    ui.setExportRows(rows)
  }, [entries])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">动作账本读取失败：{err}</div>

  return (
    <div className="dash view-enter">
      <div className="vh">动作日志 <small>GUI 自己的审计账本（action_log.jsonl）· 与 eco.db 生态账本分开 · 每次动作前后自动落一行</small></div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="chip">共 <b>{entries?.length ?? 0}</b> 条（最新 200）</span>
        <button className="ghost-btn" onClick={() => void copyText((entries ?? []).map(e => JSON.stringify(e)).join('\n'), 'JSONL 已复制')}>
          复制全部（JSONL）
        </button>
        <span className="faint">生态级长期账本见「时间线」视图（eco.db 四表）。</span>
      </div>

      {entries?.length ? (
        <section className="panel">
          <div className="table actions-table">
            <div className="tr head">
              <span>时间</span><span>动作</span><span>阶段</span><span>结果</span><span>耗时</span><span>参数</span><span>输出摘要</span>
            </div>
            {entries.map((e, i) => (
              <div key={i} className="tr" style={{ gridTemplateColumns: '140px 110px 56px 56px 64px 160px 1fr' }}>
                <span className="faint mono">{(e.ts ?? '').replace('T', ' ')}</span>
                <span><b>{e.action}</b></span>
                <span><span className={'tag ' + (e.stage === 'run' ? 'warn' : '')}>{e.stage === 'run' ? '执行' : '预览'}</span></span>
                <span><span className={'tag ' + (e.ok ? 'ok' : 'bad')}>{e.ok ? '成功' : '失败'}</span></span>
                <span className="num">{e.ms ?? 0}ms</span>
                <span className="faint ellipsis">{Object.entries(e.params ?? {}).map(([k, v]) => `${k}=${v}`).join(' ') || '—'}</span>
                <span className="faint ellipsis" title={(e.out ?? '').slice(0, 300)}>{(e.out ?? '').slice(0, 90)}</span>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyArt kind="actions" title="账本为空"
          hint="还没有通过 v0.3 观测舱执行过任何动作。去 v0.3 驾驶舱/候选孵化台试试「立即体检」或「立即挤出」，执行后这里会落账。" />
      )}

      <section className="panel">
        <h3>cron 任务一览 <small>{jobs?.length ?? 0} 个 · v2 增补保留（v0.3 观测舱可改）</small></h3>
        <div className="table cron-table">
          <div className="tr head"><span>状态</span><span>任务</span><span>计划</span><span>脚本</span><span>最近</span></div>
          {(jobs ?? []).map((j: CronJob) => (
            <div key={j.id ?? j.name} className="tr">
              <span><span className={'pill ' + (j.enabled ? 's-active' : 's-dormant')}>{j.enabled ? '启用' : '停用'}</span></span>
              <span className="ellipsis" title={j.name}>{j.name}</span>
              <span className="mono dim">{j.display ?? ''}</span>
              <span className="mono dim ellipsis">{j.script ?? ''}</span>
              <span className={'dim ' + (j.last_status === 'ok' ? '' : 'warn')}>{(j.last_run_at ?? '') + (j.last_status ? ` · ${j.last_status}` : '')}</span>
            </div>
          ))}
          {!jobs?.length && <div className="empty">无任务</div>}
        </div>
      </section>
    </div>
  )
}
