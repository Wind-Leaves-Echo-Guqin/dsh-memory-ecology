import { useEffect } from 'react'
import { fetchLineage } from '../lib/api'
import { ui, useUi, type ExportRow } from '../lib/ui'
import { useAsync } from '../hooks/useAsync'
import LineageView from './LineageView'

/** 血缘谱系 = v2 脉络特效（力导向发光网络，原样承载）+ v1 谱系故事卡：
 * evolved_from（繁殖：新技能由旧技能派生）与 merged_into（合并流：旧技能并入新技能后退役）。
 * v1 的「发起新的繁殖」为写操作，代理到 v0.3 观测舱。 */

export default function Lineage() {
  const s = useUi()
  const { data: edges } = useAsync(() => fetchLineage(), [s.refreshTick])

  useEffect(() => {
    if (!edges) return
    const rows: ExportRow[] = edges.map(e => ({
      k: `${e.child} ← ${e.parent}`, v: e.kind === 'evolved_from' ? '繁殖（evolved_from）' : '合并（merged_into）',
    }))
    ui.setExportRows(rows)
  }, [edges])
  useEffect(() => () => ui.setExportRows([]), [])

  const evolved = (edges ?? []).filter(e => e.kind === 'evolved_from')
  const merged = (edges ?? []).filter(e => e.kind === 'merged_into')
  // 合并流按父技能分组：一个旧技能可能吸收了多个子技能
  const mergedByParent = new Map<string, string[]>()
  for (const e of merged) {
    const list = mergedByParent.get(e.parent) ?? []
    list.push(e.child)
    mergedByParent.set(e.parent, list)
  }

  return (
    <div className="dash view-enter">
      <div className="vh">血缘谱系 <small>谁从谁繁衍而来（evolved_from）· 谁合并进了谁（merged_into）· v2 脉络特效承载</small></div>
      <div className="vsub">
        这是什么：技能之间的「家族关系」。繁衍 = 新技能由旧技能演化产生；合并 = 旧技能并入新技能后退役。
        拖拽旋转/滚轮缩放/悬停看名/点选看血缘。
        <a href="http://127.0.0.1:8788/static/index.html#/lineage" target="_blank" rel="noreferrer"> 发起新的繁殖 → v0.3（走闸门）</a>
      </div>
      <div className="lineage-3d">
        <LineageView />
      </div>
      <div className="tl-side">
        <section className="panel">
          <h3>繁殖链 <small>{evolved.length} 条 · 每条 = 一段「谁演化为谁」的故事</small></h3>
          {evolved.length ? evolved.map((e, i) => (
            <div key={i} className="lineage-row">
              <span className="mono">{e.parent}</span>
              <span className="lineage-arrow">→ 演化为 →</span>
              <span className="mono">{e.child}</span>
            </div>
          )) : <div className="empty-ok">还没有繁殖记录——孵化一个技能后这里会出现第一条链。</div>}
        </section>
        <section className="panel">
          <h3>合并流 <small>{merged.length} 条 · 旧技能并入新技能后退役</small></h3>
          {mergedByParent.size ? [...mergedByParent.entries()].map(([parent, children]) => (
            <div key={parent} className="lineage-row">
              {children.map((ch, i) => (
                <span key={ch} className="mono">{ch}{i < children.length - 1 ? ' + ' : ''}</span>
              ))}
              <span className="lineage-arrow merged">→ 并入 →</span>
              <span className="mono">{parent}</span>
            </div>
          )) : <div className="empty-ok">暂无合并记录。</div>}
        </section>
      </div>
    </div>
  )
}
