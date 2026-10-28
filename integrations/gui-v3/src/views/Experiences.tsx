import { useEffect, useState } from 'react'
import { fetchExperiences } from '../lib/api'
import { EmptyArt } from '../lib/icons'
import { EXP_TYPE_ZH, openExpDetail } from '../lib/drawers'
import { ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'

/** 经验笔记本 = v1 同构：候选区日期胶囊 + 类型/状态筛选（中文名）+ 排序
 * + 五类着色卡片流（症状摘要/创建/命中）+ 分页 + 类型速览；点卡片开五段详情抽屉。 */

const EXP_TYPE_DESC: Record<string, string> = {
  error: '踩过的坑与正确解法', pattern: '可复用的通用套路', negative: '验证过无效的做法（避免再犯）',
  success: '已验证有效的做法', link: '与外部资源/系统的关联',
}
const TYPE_COLORS: Record<string, string> = {
  error: '#f87171', pattern: '#60a5fa', negative: '#fbbf24', success: '#34d399', link: '#c4b5fd',
}
const EXP_STATUS_ZH: Record<string, string> = { draft: '草稿', verified: '已验证' }

export default function Experiences() {
  const s = useUi()
  const [type, setType] = useState('')
  const [status, setStatus] = useState('')
  const [sort, setSort] = useState<'created' | 'last_hit'>('created')
  const [page, setPage] = useState(1)
  const { data, err } = useAsync(() => fetchExperiences(type, status, page, sort), [type, status, page, sort, s.refreshTick])

  useEffect(() => {
    if (!data) return
    const rows: ExportRow[] = data.items.map(x => ({
      k: x.id, v: `${EXP_TYPE_ZH[x.type] ?? x.type} · ${EXP_STATUS_ZH[x.status] ?? x.status} · ${x.title}`,
    }))
    ui.setExportRows(rows)
  }, [data])
  useEffect(() => () => ui.setExportRows([]), [])

  if (err) return <div className="empty view-enter">API 不可达：{err}</div>
  if (!data) return <div className="empty view-enter">正在读取经验库…</div>

  return (
    <div className="dash view-enter">
      <div className="vh">经验笔记本 <small>{data.total} 条 · 命中≥2 自动草稿→已验证（金标成长位）</small></div>
      <div className="vsub">这是什么：Hermes 运行中踩坑/总结出的经验条目，报错时会自动检索匹配（注入回路在跑 ● · dsh 适配器未实装 ○）。只读，采纳入口在 v0.3 候选孵化台。</div>

      <section className="panel">
        <h3>候选区 <small>宿主前缀日期文件 · 可在候选孵化台采纳</small></h3>
        {data.pending.length ? (
          <div className="row wrapchips">
            {data.pending.map(p => (
              <span key={p.name} className="chip" title={p.preview}>
                {p.name} <span className="faint">{p.mtime.slice(5, 10)}</span>
              </span>
            ))}
            <a onClick={() => ui.navigate('candidates')}>→ 去孵化台采纳</a>
          </div>
        ) : <span className="faint">空</span>}
      </section>

      <div className="filters">
        <div className="pills">
          {['', 'error', 'pattern', 'negative', 'success', 'link'].map(t => (
            <button key={t || 'all'} className={type === t ? 'on' : ''}
              onClick={() => { setType(t); setPage(1) }}>{t ? EXP_TYPE_ZH[t] : '全部类型'}</button>
          ))}
        </div>
        <div className="pills">
          {['', 'draft', 'verified'].map(t => (
            <button key={t || 'any'} className={status === t ? 'on' : ''}
              onClick={() => { setStatus(t); setPage(1) }}>{t ? EXP_STATUS_ZH[t] : '全部状态'}</button>
          ))}
        </div>
        <select className="mini-input" value={sort} onChange={e => setSort(e.target.value as 'created' | 'last_hit')}>
          <option value="created">按创建</option>
          <option value="last_hit">按 last_hit</option>
        </select>
        <span className="faint">{data.total} 条 · 第 {data.page}/{data.pages} 页</span>
      </div>
      <div className="vsub">
        类型速览：{Object.keys(EXP_TYPE_DESC).map(t => (
          <span key={t}><b style={{ color: TYPE_COLORS[t] }}>{EXP_TYPE_ZH[t]}</b>={EXP_TYPE_DESC[t]} · </span>
        ))}
      </div>

      {data.items.length ? (
        <div className="exps">
          {data.items.map((x, i) => (
            <div key={x.id} className="exp a-stagger" style={{ animationDelay: Math.min(i * 32, 480) + 'ms' }}
              onClick={() => void openExpDetail(x.id)}>
              <div className="etags">
                <span className="etyp" style={{ background: TYPE_COLORS[x.type] ?? 'rgba(148,163,184,.4)' }}>
                  {EXP_TYPE_ZH[x.type] ?? x.type}
                </span>
                <span className={'est' + (x.status === 'verified' ? ' gold' : '')}>
                  {EXP_STATUS_ZH[x.status] ?? x.status}{x.status === 'verified' ? ' ★' : ''}
                </span>
                {x.distilled_to && <span className="est">已蒸馏 → 技能 {x.distilled_to}</span>}
                {x.provenance?.includes('session') && <span className="faint" title="来源会话（可溯源）">⌘ {x.provenance.slice(0, 24)}</span>}
              </div>
              <h4>{x.title}</h4>
              {x.symptom && <div className="sym">{x.symptom.slice(0, 90)}</div>}
              <div className="meta"><span>建 {x.created ?? '—'}</span><span>hit {x.last_hit ?? '—'}</span></div>
            </div>
          ))}
        </div>
      ) : (
        <EmptyArt kind="memories" title="这个筛选组合下没有经验条目" hint="换一个类型/状态筛选，或去生态里制造一些——经验来自真实踩坑与复盘。" />
      )}

      {data.pages > 1 && (
        <div className="row center" style={{ marginTop: 14 }}>
          {data.page > 1 && <button className="ghost-btn" onClick={() => setPage(data.page - 1)}>‹ 上一页</button>}
          <span className="faint">{data.page} / {data.pages}</span>
          {data.page < data.pages && <button className="ghost-btn" onClick={() => setPage(data.page + 1)}>下一页 ›</button>}
        </div>
      )}
    </div>
  )
}
