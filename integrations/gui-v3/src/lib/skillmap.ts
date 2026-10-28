/** 技能星图数据层（v2）：从观测舱既有 API 取技能与血缘，构建星图所需的图结构。
 * 数据源：/api/skills（collector.scan_skills）+ /api/lineage（evolved_from/merged_into 边）。
 * 同源只读，零后端改动。 */

export type Skill = {
  name: string
  cat: string
  desc: string
  version: string
  status: string
  fate: string
  evolved_from: string | null
  merged_into: string | null
  related: string[]
  indeg: number
  dup: boolean
  chars: number
}

export type Edge = { child: string; parent: string; kind: 'evolved' | 'merged' | 'related' }

export type SkillMap = {
  skills: Skill[]
  byName: Map<string, Skill>
  edges: Edge[]
  childrenOf: Map<string, string[]> // evolved 父 → 子
  parentsOf: Map<string, string[]>  // 子 → evolved 父
  components: string[][]            // 连通分量（按规模降序）
  compOf: Map<string, number>
  depth: Map<string, number>        // 分量内 evolved 代际深度
}

/** 确定性字符串哈希 → [0,1)：布局/相位用它，保证同名每次渲染同位。 */
export function hash01(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 100000) / 100000
}

export async function fetchSkillMap(): Promise<SkillMap> {
  const [sk, ln] = await Promise.all([
    fetch('/api/skills').then((r) => r.json()),
    fetch('/api/lineage').then((r) => r.json()),
  ])
  if (sk.ok === false || ln.ok === false) throw new Error(sk.error ?? ln.error ?? 'API 失败')

  const skills: Skill[] = (sk.skills ?? []).map((s: Record<string, unknown>) => ({
    name: String(s.name),
    cat: String(s.cat ?? ''),
    desc: String(s.desc ?? ''),
    version: String(s.version ?? '?'),
    status: String(s.status ?? 'undeclared'),
    fate: String(s.fate ?? 'undeclared'),
    evolved_from: (s.evolved_from as string) || null,
    merged_into: (s.merged_into as string) || null,
    related: (s.related as string[]) ?? [],
    indeg: Number(s.indeg ?? 0),
    dup: Boolean(s.dup),
    chars: Number(s.chars ?? 0),
  }))
  const byName = new Map(skills.map((s) => [s.name, s]))

  const edges: Edge[] = (ln.edges ?? [])
    .filter((e: Record<string, unknown>) => byName.has(String(e.child)) && byName.has(String(e.parent)))
    .map((e: Record<string, unknown>) => ({
      child: String(e.child),
      parent: String(e.parent),
      kind: e.kind === 'merged_into' ? 'merged' : 'evolved',
    }))
  // related_skills 引用边（用户语义："相互有关系的"聚成星团/恒星系）——去重成对加入
  const seenRel = new Set<string>()
  for (const s of skills) {
    for (const r of s.related) {
      if (!byName.has(r) || r === s.name) continue
      const key = [s.name, r].sort().join('→')
      if (seenRel.has(key)) continue
      seenRel.add(key)
      edges.push({ child: s.name, parent: r, kind: 'related' })
    }
  }

  const childrenOf = new Map<string, string[]>()
  const parentsOf = new Map<string, string[]>()
  for (const e of edges) {
    if (e.kind !== 'evolved') continue
    childrenOf.set(e.parent, [...(childrenOf.get(e.parent) ?? []), e.child])
    parentsOf.set(e.child, [...(parentsOf.get(e.child) ?? []), e.parent])
  }

  // 连通分量（血缘边，evolved+merged 都算——合并死技能与其吸收者是一族）
  const compOf = new Map<string, number>()
  const comps: string[][] = []
  for (const s of skills) {
    if (compOf.has(s.name)) continue
    const comp: string[] = []
    const stack = [s.name]
    compOf.set(s.name, comps.length)
    while (stack.length) {
      const cur = stack.pop()!
      comp.push(cur)
      for (const e of edges) {
        const other = e.child === cur ? e.parent : e.parent === cur ? e.child : null
        if (other && byName.has(other) && !compOf.has(other)) {
          compOf.set(other, comps.length)
          stack.push(other)
        }
      }
    }
    comps.push(comp)
  }
  comps.sort((a, b) => b.length - a.length)
  // 审查 M2 根因修复：compOf 原是 DFS 发现序，排序后重映射——否则"按规模占带"拿到错序号
  comps.forEach((comp, idx) => {
    for (const n of comp) compOf.set(n, idx)
  })

  // 代际深度：分量内沿 evolved 边从根 BFS
  const depth = new Map<string, number>()
  for (const comp of comps) {
    const queue: string[] = []
    for (const n of comp) {
      const ps = (parentsOf.get(n) ?? []).filter((p) => compOf.get(p) === compOf.get(n))
      if (ps.length === 0) {
        depth.set(n, 0)
        queue.push(n)
      }
    }
    while (queue.length) {
      const cur = queue.shift()!
      for (const ch of childrenOf.get(cur) ?? []) {
        if (!depth.has(ch)) {
          depth.set(ch, (depth.get(cur) ?? 0) + 1)
          queue.push(ch)
        }
      }
    }
    for (const n of comp) if (!depth.has(n)) depth.set(n, 0)
  }

  return { skills, byName, edges, childrenOf, parentsOf, components: comps, compOf, depth }
}

/** fate → 主题色（线性 RGB 0-1，供粒子着色）。 */
export function fateColor(fate: string): [number, number, number] {
  switch (fate) {
    case 'active':
      return [0.49, 0.83, 0.99] // 青白
    case 'retained':
      return [0.45, 0.95, 0.75] // 青绿
    case 'archived':
      return [0.85, 0.45, 0.2] // 暗橙余烬
    case 'merged':
      return [0.62, 0.5, 0.95] // 幽紫（被吞并）
    default:
      return [0.55, 0.6, 0.95] // 未声明/演化中：靛蓝
  }
}
