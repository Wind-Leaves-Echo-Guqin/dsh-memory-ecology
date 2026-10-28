import { useEffect, useState } from 'react'
import { fetchCoreFirstaid, runAction, type AidCheck, type AidData } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { Ico } from '../lib/icons'
import { copyText, ui, useUi, type ExportRow } from '../lib/ui'

/** G7 · 急救箱（v3.2.0 根口径）—— 只诊断，修复合法的只有两类幂等操作。
 *
 * 四项检查：根自证 / 物化视图完整性 / 账本一致性 / 四门活性。
 * **没有任何「删除/清理记忆」的按钮** —— 这个项目在代码层禁用物理删除，
 * 急救箱也不该成为那个后门。
 */

const ICON: Record<AidCheck['status'], string> = {
  ok: 'check', warn: 'warn', fail: 'close',
}
const TAG: Record<AidCheck['status'], string> = {
  ok: 'ok', warn: 'warn', fail: 'bad',
}

export default function FirstaidView() {
  const s = useUi()
  const { data, err } = useAsync<AidData>(() => fetchCoreFirstaid(), [s.refreshTick])
  const [busy, setBusy] = useState('')

  useEffect(() => {
    const rows: ExportRow[] = (data?.checks ?? []).map(c => ({
      k: c.id,
      v: `[${c.status}] ${c.name} · ${c.detail}${c.action ? ' · 建议：' + c.action : ''}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  const fix = async (kind: 'core_materialize' | 'core_maintain') => {
    setBusy(kind)
    try {
      const d = await runAction(kind, 'run')
      ui.toast(d.ok ? '已完成' : '失败：' + (d.error || d.output || ''), d.ok)
      if (d.ok) ui.bumpRefresh()
    } finally { setBusy('') }
  }

  if (err) return <div className="empty view-enter">急救箱读取失败：{err}</div>

  const checks = data?.checks ?? []
  const allOk = data?.all_ok ?? true

  return (
    <div className="dash view-enter">
      <div className="vh">
        急救箱
        <small>根自证 / 物化完整性 / 账本一致性 / 四门活性 · 只诊断，不自动改任何东西</small>
        <button className="ghost-btn" style={{ marginLeft: 10 }}
          onClick={() => void copyText(JSON.stringify(data ?? {}, null, 2), '诊断结果已复制')}>
          复制诊断
        </button>
      </div>

      {allOk ? (
        <div className="eco-alarm" style={{ background: 'rgba(48,209,88,.12)', borderColor: 'rgba(48,209,88,.42)', color: '#30d158' }}>
          <b>✓ 全部检查通过</b>
          <div className="faint" style={{ marginTop: 6, color: 'inherit' }}>
            数据根 <span className="mono">{data?.root ?? '—'}</span>
            {' · '}{(data?.ts ?? '').replace('T', ' ')}
          </div>
        </div>
      ) : (
        <div className="eco-alarm">
          <b>{data?.fail_count ?? 0} 项检查失败</b> —— 按下面的「建议动作」逐条处理。
          急救箱只诊断，不会自动改任何东西。
        </div>
      )}

      <section className="panel">
        <div className="vh" style={{ fontSize: 13 }}>诊断项 <small>只读 · 幂等</small></div>
        {checks.length === 0 ? (
          <div className="empty">还没有诊断结果。</div>
        ) : (
          <div className="eco-aid">
            {checks.map(c => (
              <div key={c.id} className={'eco-aid-row ' + c.status}>
                <span className={'eco-aid-ico ' + c.status}><Ico name={ICON[c.status]} size={13} /></span>
                <span className="eco-aid-name">
                  <b>{c.name}</b>
                  <span className="faint mono" style={{ fontSize: 10 }}>{c.id}</span>
                </span>
                <span className="eco-aid-detail">
                  {c.detail}
                  {c.action ? (
                    <div className="faint" style={{ fontSize: 11, marginTop: 3 }}>
                      建议：{c.action}
                    </div>
                  ) : null}
                </span>
                <span><span className={'tag ' + TAG[c.status]}>{c.status === 'ok' ? '通过' : c.status === 'warn' ? '注意' : '失败'}</span></span>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="vh" style={{ fontSize: 13 }}>可安全执行的修复 <small>只有这两类</small></div>
        <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>
          只有两类：重建目录视图（纯派生数据）与跑一次四门（幂等、写前自动备份）。
          <b>没有任何「删除 / 清理记忆」的按钮</b> —— 这个项目在代码层禁用物理删除，
          测试 <span className="mono">test_no_unlink_of_memories_anywhere</span> 扫描全部源码把这条钉住。
        </div>
        <div className="row">
          <button className="btn" disabled={busy !== ''} onClick={() => void fix('core_materialize')}>
            {busy === 'core_materialize' ? '重建中…' : '重建目录视图'}
          </button>
          <button className="btn" disabled={busy !== ''} onClick={() => void fix('core_maintain')}>
            {busy === 'core_maintain' ? '跑着…' : '跑一次四门'}
          </button>
        </div>
      </section>
    </div>
  )
}
