import { useEffect, useState } from 'react'
import { fetchAux, fetchL1, fetchL2, type Aux, type L1File } from '../lib/api'
import { EmptyArt } from '../lib/icons'
import { openMemDetail } from '../lib/drawers'
import { ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'

/** 记忆库 = v1 同构：L1 常驻层（MEMORY/USER 双 tab + 水位 + 条目卡，⇧ 已沉淀跳详情）
 * + 隔离区（低温视觉）+ 待处理区/画像候选 + L2 详情层（筛选/排序/双时态表格，点击行开抽屉）。 */

const TYPES = ['semantic', 'episodic', 'procedural', 'lesson']
const STATUSES = ['active', 'superseded']

function Water({ f, label }: { f: L1File; label: string }) {
  const over = f.chars > f.quota
  const pct = Math.min(100, (f.chars / Math.max(1, f.quota)) * 100)
  return (
    <div className="water">
      <div className="water-bar">
        <div className={'water-fill' + (over ? ' over-fill' : '')} style={{ width: pct + '%' }} />
      </div>
      <div className="water-legend">
        <span>{label} <b className="num">{f.chars}</b> 字 / {f.quota}（去空白口径）</span>
        <span className={over ? 'alert-text' : 'faint'}>{over ? '已越线' : '未越线'} · {f.entries.length} 条</span>
      </div>
    </div>
  )
}

export default function Memories() {
  const s = useUi()
  const { data: l1, err: e1 } = useAsync(() => fetchL1(), [s.refreshTick])
  const { data: l2, err: e2 } = useAsync(() => fetchL2(), [s.refreshTick])
  const { data: aux } = useAsync(() => fetchAux(), [s.refreshTick])
  const [tab, setTab] = useState<'memory' | 'user'>('memory')
  const [fType, setFType] = useState('')
  const [fStatus, setFStatus] = useState('')
  const [sort, setSort] = useState<'last' | 'occ' | 'first'>('last')

  const a: Aux | null = aux
  useEffect(() => {
    if (!l2) return
    const rows: ExportRow[] = l2.slice(0, 60).map(x => ({
      k: x.slug.slice(0, 40), v: `${x.type} · ${x.status} · occ ${x.occ ?? '—'} · ${x.last}`,
    }))
    ui.setExportRows(rows)
  }, [l2])
  useEffect(() => () => ui.setExportRows([]), [])

  if (e1 || e2) return <div className="empty view-enter">API 不可达：{e1 ?? e2}</div>
  if (!l1 || !l2) return <div className="empty view-enter">正在读取记忆层…</div>

  const items = l2
    .filter(x => (!fType || x.type === fType) && (!fStatus || x.status === fStatus))
    .sort((x, y) =>
      sort === 'occ' ? (y.occ ?? 0) - (x.occ ?? 0)
        : sort === 'first' ? (x.first ?? '').localeCompare(y.first ?? '')
          : (y.last ?? '').localeCompare(x.last ?? ''))
  const cur: L1File = tab === 'memory' ? l1.memory : l1.user
  const entries = cur.entries.map(e => typeof e === 'string' ? { text: e } : e)

  return (
    <div className="dash view-enter">
      <div className="vh">记忆库 <small>L1 常驻 + L2 详情层 · 只读浏览</small></div>
      <div className="mem-layout">
        <div>
          <section className="panel">
            <h3>L1 常驻层</h3>
            <div className="pills l1-tabs">
              <button className={tab === 'memory' ? 'on' : ''} onClick={() => setTab('memory')}>MEMORY.md</button>
              <button className={tab === 'user' ? 'on' : ''} onClick={() => setTab('user')}>USER.md</button>
            </div>
            <Water f={cur} label={tab === 'memory' ? 'MEMORY' : 'USER'} />
            <div className="l1-list">
              {entries.map((e, i) => (
                <div key={i} className="l1-entry">
                  {e.linked_slug && (
                    <span className="jumpup" title="L1↔L2 关联：查看详情层条目"
                      onClick={() => void openMemDetail(e.linked_slug!)}>⇧ 已沉淀</span>
                  )}
                  {e.text}
                </div>
              ))}
            </div>
          </section>
          <section className="panel frozen">
            <h3>隔离区 <small>superseded 旧条目 · 永不物理删除</small></h3>
            {a?.quarantine?.length
              ? a.quarantine.map((f, i) => <div key={i} className="l1-entry">{f.name}</div>)
              : <div className="empty-ok">暂无实例 —— 0 条 superseded，这是健康状态（机制在，矛盾未发生）。</div>}
          </section>
          <section className="panel">
            <h3>待处理区 <small>提取产出 → 门①消费（.done 已排除）</small></h3>
            {a?.pending?.length
              ? a.pending.map((f, i) => (
                <div key={i} className="l1-entry">
                  <b>{f.name}</b> <span className="faint">{(f.mtime ?? '').slice(0, 16)}</span>
                  <div className="faint small" style={{ marginTop: 3 }}>{(f.preview ?? '').slice(0, 120)}…</div>
                </div>
              ))
              : <div className="empty-ok">空 —— 门①已消化完候选（目录有进有出）。</div>}
            <h3 style={{ marginTop: 14 }}>画像候选 <small>门③观察期</small></h3>
            {a?.user_candidates?.length
              ? a.user_candidates.map((f, i) => <div key={i} className="l1-entry">{f.name}</div>)
              : <div className="empty-ok">空 —— 尚无达观察期的稳定特质。</div>}
          </section>
        </div>
        <section className="panel">
          <h3>L2 详情层 <small>{l2.length} 条 · 点击行开详情抽屉</small></h3>
          <div className="filters">
            <select className="mini-input" value={fType} onChange={e => setFType(e.target.value)}>
              <option value="">type 全部</option>
              {TYPES.map(t => <option key={t}>{t}</option>)}
            </select>
            <select className="mini-input" value={fStatus} onChange={e => setFStatus(e.target.value)}>
              <option value="">status 全部</option>
              {STATUSES.map(t => <option key={t}>{t}</option>)}
            </select>
            <select className="mini-input" value={sort} onChange={e => setSort(e.target.value as 'last' | 'occ' | 'first')}>
              <option value="last">按 last_seen</option>
              <option value="occ">按 occurrences</option>
              <option value="first">按 first_seen</option>
            </select>
            <span className="faint">筛选后 {items.length} 条</span>
          </div>
          {items.length ? (
            <div className="table l2-table">
              <div className="tr head">
                <span>条目</span><span>type</span><span>status</span><span>occ</span><span>首见</span><span>最近核验</span><span>双时态</span>
              </div>
              {items.map(x => (
                <div key={x.slug} className="tr clickable" onClick={() => void openMemDetail(x.slug)}>
                  <span className="ellipsis" title={x.slug}>
                    {x.slug}
                    {x.origin === 'extrude-from-L1' && <span className="faint" title="由 L1 挤出沉淀"> ⇩挤出</span>}
                    {x.superseded_by && <span className="tag bad">被取代</span>}
                  </span>
                  <span><span className={'pill t-' + x.type}>{x.type}</span></span>
                  <span><span className={'pill s-' + x.status}>{x.status}</span></span>
                  <span className="num">{x.occ ?? '—'}</span>
                  <span className="dim">{x.first || '—'}</span>
                  <span className="dim">{x.verified || '—'}</span>
                  <span className="faint small">{(x.valid_time || '—')} / {(x.transaction_time || '').replace('T', ' ').slice(0, 16)}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyArt kind="memories" title="筛选结果为空" hint="放宽条件试试——type / status / 排序组合里至少留一个「全部」。" />
          )}
        </section>
      </div>
    </div>
  )
}
