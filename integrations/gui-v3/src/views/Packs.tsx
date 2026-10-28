import { useEffect, useState } from 'react'
import { fetchPacks, runAction, type PackRow } from '../lib/api_v3_2_0'
import { useAsync } from '../hooks/useAsync'
import { EmptyArt } from '../lib/icons'
import { ui, useUi, type ExportRow } from '../lib/ui'

/** G5 · 记忆包 —— mount（只读挂载）/ integrate（吸收走门①）。
 *
 * 安全边界（写进核心实现，不是写进文档）：
 *   只读 · 来源白名单 · 每篇 sha256 · kind 只接受 readonly-pack ·
 *   装/卸只改状态位（**物理文件永远保留**）· 疑似注入内容打标。
 */

function hashBadge(p: PackRow) {
  if (p.hash_ok === true) return <span className="tag ok">✓ 校验通过</span>
  if (p.hash_ok === false) {
    return <span className="tag bad" title={(p.errors ?? []).join('\n')}>
      ✗ {String((p.errors ?? [])[0] ?? '校验失败').slice(0, 26)}
    </span>
  }
  return <span className="tag">—</span>
}

export default function PacksView() {
  const s = useUi()
  const { data, err } = useAsync<PackRow[]>(() => fetchPacks(), [s.refreshTick])
  const [busy, setBusy] = useState('')

  useEffect(() => {
    const rows: ExportRow[] = (data ?? []).map(p => ({
      k: p.id,
      v: `${p.version} · ${p.mode} · ${p.enabled ? '启用' : '停用'} · ${p.entries} 条`
        + ` · 哈希 ${p.hash_ok ? 'ok' : p.hash_ok === false ? 'FAIL' : '—'} · ${p.provenance}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  const toggle = async (p: PackRow) => {
    const name = p.enabled ? 'pack_disable' : 'pack_enable'
    // 停用是 strong（决定哪些内容进可见范围），启用是 light
    if (p.enabled) {
      ui.openDrawer({
        title: '停用记忆包 · ' + p.id,
        sub: '目录里不再出现该包的内容（不影响本库记忆，物理文件保留）',
        node: (
          <div className="eco-kv">
            <div><span>包 id</span><b>{p.id}</b></div>
            <div><span>模式</span><b>{p.mode}</b></div>
            <div><span>回滚</span><b>再点启用即可；物理文件始终保留</b></div>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button className={'btn strong'} disabled={busy === p.id}
                onClick={async () => {
                  setBusy(p.id)
                  try {
                    const d = await runAction(name, 'run', { id: p.id })
                    ui.toast(d.ok ? '已停用 ' + p.id : '失败：' + (d.error || ''), d.ok)
                    if (d.ok) { ui.closeDrawer(); ui.bumpRefresh() }
                  } finally { setBusy('') }
                }}>确认停用</button>
            </div>
          </div>
        ),
      })
      return
    }
    setBusy(p.id)
    try {
      const d = await runAction(name, 'run', { id: p.id })
      ui.toast(d.ok ? '已启用 ' + p.id : '失败：' + (d.error || ''), d.ok)
      if (d.ok) ui.bumpRefresh()
    } finally { setBusy('') }
  }

  if (err) return <div className="empty view-enter">记忆包读取失败：{err}</div>

  const packs = data ?? []
  return (
    <div className="dash view-enter">
      <div className="vh">
        记忆包
        <small>mount = 只读挂载（内容进目录、不进本库）· integrate = 吸收（走门①）</small>
      </div>

      {packs.length === 0 ? (
        <div className="panel">
          <div className="empty">
            <EmptyArt kind="generic" />
            <div style={{ marginTop: 10 }}>没有记忆包。</div>
            <div className="faint" style={{ marginTop: 6 }}>
              装一个：<span className="mono">python -m memory_ecology pack install
              --dir &lt;包目录&gt; --mode mount</span>
            </div>
          </div>
        </div>
      ) : (
        <section className="panel">
          <div className="table eco-pack-table">
            <div className="tr head">
              <span>id</span><span>版本</span><span>模式</span><span>状态</span>
              <span>哈希</span><span>条目</span><span>来源</span><span>操作</span>
            </div>
            {packs.map(p => (
              <div key={p.id} className="tr">
                <span>
                  <b>{p.id}</b>
                  <div className="faint mono ellipsis" style={{ fontSize: 10 }} title={p.path}>
                    {(p.path || '').slice(-42)}
                  </div>
                </span>
                <span className="mono">{p.version || '—'}</span>
                <span>
                  <span className={'tag ' + (p.mode === 'mount' ? '' : p.mode === 'integrate' ? 'warn' : 'ok')}>
                    {p.mode || '—'}
                  </span>
                </span>
                <span>
                  {p.enabled ? <span className="tag ok">启用</span> : <span className="tag">停用</span>}
                  {p.exists === false ? <span className="tag bad" style={{ marginLeft: 4 }}>目录丢失</span> : null}
                </span>
                <span>{hashBadge(p)}</span>
                <span className="num">{p.entries}</span>
                <span className="faint ellipsis" style={{ fontSize: 11 }}>{p.provenance || '—'}</span>
                <span>
                  <button className="ghost-btn" disabled={busy === p.id} onClick={() => void toggle(p)}>
                    {p.enabled ? '停用' : '启用'}
                  </button>
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="panel eco-note">
        <b>安全边界（写进实现，不是写进文档）</b>
        <div className="faint">
          第三方记忆包本质是一条 <b>prompt injection 通道</b>，所以：
          <br />① <b>只读</b> —— mount 只把内容渲染进目录，不落 detail、不写 L1、不过门；
          <br />② <b>来源白名单</b> —— manifest 必须 <span className="mono">whitelisted: true</span> 且带 provenance；
          <br />③ <b>内容哈希</b> —— 每篇声明 sha256，不符即<b>整体拒载</b>（不留半加载状态）；
          <br />④ <b>不可执行</b> —— <span className="mono">kind</span> 只接受 <span className="mono">readonly-pack</span>；
          <br />⑤ <b>装/卸只改状态位</b> —— 物理文件永远保留（红线：代码层禁用物理删除）；
          <br />⑥ 疑似指令注入的内容会被打标，<b>并且拒绝 integrate</b>（不给它洗成"本库记忆"）。
        </div>
      </section>
    </div>
  )
}
