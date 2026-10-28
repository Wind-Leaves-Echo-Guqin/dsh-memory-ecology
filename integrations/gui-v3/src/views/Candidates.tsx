import { useEffect, useState } from 'react'
import { fetchCandDetail, fetchCandidates, type CandItem } from '../lib/api'
import { EmptyArt, Ico } from '../lib/icons'
import { md } from '../lib/md'
import { copyText, ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'

/** 候选孵化台 = v1 同构：三源聚合（技能 .candidates / 画像 user_candidates / 经验 pending）
 * + 候选表（来源/时间/预览/操作列）+ 详情抽屉（frontmatter + 正文渲染 + 复制全文）。
 * v2.1 只读：操作列保留 v1 结构，「孵化/采纳」代理为「复制候选 → 去 v0.3 执行」。 */

const KIND_NAME: Record<string, string> = { skill: '技能候选', profile: '画像候选', experience: '经验候选' }
const KIND_DESC: Record<string, string> = {
  skill: '技能候选（skills/.candidates）——孵化后进入观察期，再由门③蒸馏转正。',
  profile: '画像候选（memories/user_candidates）——门③观察期语义，期满自动升降级，GUI 只读。',
  experience: '经验候选（experiences/pending）——采纳后成为正式经验条目；外源内容自动降级 draft。',
}

export default function Candidates() {
  const s = useUi()
  const { data, err } = useAsync(() => fetchCandidates(), [s.refreshTick])
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof fetchCandDetail>> | null>(null)

  useEffect(() => {
    if (!data) return
    const rows: ExportRow[] = data.items.map(x => ({
      k: x.kind + ' · ' + x.name, v: `${(x.mtime ?? '').slice(0, 16)} · ${(x.desc ?? '').slice(0, 80)}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">API 不可达：{err}</div>
  if (!data) return <div className="empty view-enter">正在读取候选池…</div>

  const byKind = data.byKind
  const open = async (it: CandItem) => {
    try {
      const d = await fetchCandDetail(it.kind, it.name)
      setDetail(d)
      ui.openDrawer({
        title: it.name, sub: KIND_NAME[it.kind] ?? it.kind,
        node: (
          <>
            <div className="empty-ok">{KIND_DESC[it.kind] ?? ''}</div>
            {d.frontmatter && Object.keys(d.frontmatter).length > 0 && (
              <div className="fmtable">
                {Object.entries(d.frontmatter).flatMap(([k, v], i) => [
                  <span key={'k' + i}>{k}</span>,
                  <span key={'v' + i}>{v}</span>,
                ])}
              </div>
            )}
            <div className="md cand-body" dangerouslySetInnerHTML={{ __html: md(d.body || '(空)') }} />
            <div className="row" style={{ marginTop: 12 }}>
              {(it.kind === 'skill' || it.kind === 'experience') && (
                <a href="http://127.0.0.1:8788/static/index.html#/candidates" target="_blank" rel="noreferrer" className="gate-go linklike-strong">
                  → 去 v0.3 {it.kind === 'skill' ? '孵化' : '采纳'}（确认闸门） <Ico name="external" size={12} />
                </a>
              )}
              {it.kind === 'profile' && <span className="faint">观察期内只读（门③期满自动处理）</span>}
              <button className="ghost-btn" onClick={() => void copyText(d.body ?? '', '全文已复制')}>复制全文</button>
            </div>
          </>
        ),
      })
    } catch (e) {
      ui.toast('详情读取失败：' + (e instanceof Error ? e.message : String(e)), false)
    }
  }

  return (
    <div className="dash view-enter">
      <div className="vh">候选孵化台 <small>三源聚合：技能（.candidates）+ 画像（user_candidates）+ 经验（pending）</small></div>
      <div className="vsub">候选 = 各门/提取管道产出、等待被消费的半成品。只读预览——采纳/孵化仍由 v0.3 观测舱执行（确认闸门 + 审计）。</div>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="chip">技能候选 <b>{byKind.skill ?? 0}</b> <span className="faint">→ 孵化（eco_breed）</span></span>
        <span className="chip">画像候选 <b>{byKind.profile ?? 0}</b> <span className="faint">→ 门③观察期（只读）</span></span>
        <span className="chip">经验候选 <b>{byKind.experience ?? 0}</b> <span className="faint">→ 采纳（eco_note_adopt）</span></span>
      </div>
      {data.items.length ? (
        <section className="panel">
          <div className="table cand-table">
            <div className="tr head"><span>候选</span><span>来源</span><span>时间</span><span>预览</span><span>操作</span></div>
            {data.items.map((it, i) => (
              <div key={it.kind + '/' + it.name} className="tr clickable a-stagger"
                style={{ animationDelay: Math.min(i * 40, 500) + 'ms', gridTemplateColumns: '200px 96px 150px 1fr 150px' }}
                onClick={() => void open(it)}>
                <span className="cand-name" title={it.name}><b>{it.name}</b></span>
                <span><span className={'pill kind-' + it.kind}>{KIND_NAME[it.kind] ?? it.kind}</span></span>
                <span className="faint">{(it.mtime ?? '').slice(0, 16).replace('T', ' ')}</span>
                <span className="faint ellipsis" title={it.desc}>{it.desc}</span>
                <span onClick={e => e.stopPropagation()}>
                  {it.kind === 'skill' && (
                    <a href="http://127.0.0.1:8788/static/index.html#/candidates" target="_blank" rel="noreferrer" className="op-link">去 v0.3 孵化</a>
                  )}
                  {it.kind === 'experience' && (
                    <a href="http://127.0.0.1:8788/static/index.html#/candidates" target="_blank" rel="noreferrer" className="op-link">去 v0.3 采纳</a>
                  )}
                  {it.kind === 'profile' && <span className="faint" title="门③语义：观察期内只读，期满自动升降级">观察期 · 只读</span>}
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyArt kind="candidates" title="暂无任何候选（三源皆空）"
          hint="出现条件：①技能候选 = 孵化动作写入 skills/.candidates；②画像候选 = 门③蒸馏到达观察期的稳定特质；③经验候选 = 生态捕获产出。空态是诚实状态——机制在，实例零。" />
      )}
      {(byKind.experience ?? 0) > 0 && (
        <section className="panel">
          <h3>批量采纳经验候选</h3>
          <p className="faint" style={{ marginBottom: 8 }}>
            对全部 pending 候选执行一次 eco_note_adopt（外源自动降级 draft，候选源保留不删除）——写操作在 v0.3 观测舱执行。
          </p>
          <a href="http://127.0.0.1:8788/static/index.html#/candidates" target="_blank" rel="noreferrer" className="op-link">
            → 打开 v0.3 候选孵化台 <Ico name="external" size={12} />
          </a>
        </section>
      )}
      {detail && null /* 详情渲染在抽屉中；此 state 仅用于缓存最近一次详情 */}
    </div>
  )
}
