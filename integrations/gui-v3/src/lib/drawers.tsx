import { fetchExpDetail, fetchMemDetail, type ExpDetail } from './api'
import { ui } from './ui'

/** 共享详情抽屉：L2 记忆条目 + 经验五段条目（v1 gotoDetail/openExpDrawer 语义）。 */

export async function openMemDetail(slug: string): Promise<void> {
  try {
    const d = await fetchMemDetail(slug)
    if (!d) { ui.toast('未找到该条目', false); return }
    const fm = d.fm ?? {}
    const rows: Array<[string, unknown]> = [
      ['type', fm.type], ['status', fm.status], ['occurrences', fm.occurrences],
      ['session_count', fm.session_count], ['first_seen', fm.first_seen], ['last_seen', fm.last_seen],
      ['valid_time（事件钟）', fm.valid_time], ['transaction_time（事务钟）', fm.transaction_time],
      ['last_verified', fm.last_verified], ['origin', fm.origin], ['superseded_by', fm.superseded_by],
    ]
    ui.openDrawer({
      title: 'L2 详情', sub: slug,
      node: (
        <>
          <div className="fmtable">
            {rows.flatMap(([k, v], i) => [
              <span key={'k' + i}>{k}</span>,
              <span key={'v' + i}>{String(v ?? '—')}</span>,
            ])}
          </div>
          {fm.superseded_by ? <p className="dim">已被替换 → {String(fm.superseded_by)}</p> : null}
          <div className="md prewrap">{d.body || '(空)'}</div>
        </>
      ),
    })
  } catch (e) {
    ui.toast('读取失败：' + (e instanceof Error ? e.message : String(e)), false)
  }
}

const EXP_SECT_ZH: Record<string, string> = { symptom: '症状', cause: '原因', action: '正确做法', evidence: '证据', boundary: '边界' }
export const EXP_TYPE_ZH: Record<string, string> = {
  error: '错误案例', pattern: '可复用模式', negative: '无效做法', success: '成功经验', link: '外部关联',
}

export function openExpDetailDrawer(it: ExpDetail): void {
  ui.openDrawer({
    title: it.title, sub: it.id,
    node: (
      <>
        <div className="fmtable">
          {[['id', it.id], ['创建', it.created ?? '—'], ['最近命中', it.last_hit ?? '—'], ['来源', it.provenance ?? '—']]
            .flatMap(([k, v], i) => [
              <span key={'k' + i}>{k}</span>,
              <span key={'v' + i} className="mono">{String(v)}</span>,
            ])}
        </div>
        <div className="row" style={{ margin: '8px 0 12px' }}>
          <span className={'etyp exp-' + it.type}>{EXP_TYPE_ZH[it.type] ?? it.type}</span>
          <span className={'est' + (it.status === 'verified' ? ' gold' : '')}>
            {it.status === 'verified' ? '已验证 ★' : '草稿'}{it.status !== 'verified' ? ' · 命中≥2 自动转正' : ''}
          </span>
          {it.distilled_to && <span className="est">已蒸馏 → 技能 {it.distilled_to}</span>}
        </div>
        {(['symptom', 'cause', 'action', 'evidence', 'boundary'] as const).map(k => it.sections?.[k] ? (
          <div key={k} className={'sect ' + k}>
            <div className="sect-h">{EXP_SECT_ZH[k]}</div>
            <div className={'sect-b' + (k === 'evidence' ? ' mono' : '')}>{it.sections[k]}</div>
          </div>
        ) : null)}
      </>
    ),
  })
}

export async function openExpDetail(id: string): Promise<void> {
  try {
    openExpDetailDrawer(await fetchExpDetail(id))
  } catch (e) {
    ui.toast('读取失败：' + (e instanceof Error ? e.message : String(e)), false)
  }
}
