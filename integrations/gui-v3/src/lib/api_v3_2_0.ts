/** v3.2.0 数据层（dsh 根）：四门实况 / 圈的视角 / 注入观测 / 记忆包。
 *
 * **双根并存**：本文件只管 v3.2.0 的新根（`/api/v3.2.0/*`），旧根视图（记忆库/
 * 经验/技能/体检/时间线）继续走 `api.ts` 的 `/api/*`。两边**绝不混算**，
 * 每个视图的角标都写明口径。
 *
 * 与 `api.ts` 同款：内存 TTL 缓存 + 失败抛错（上层 fail-open）。
 */

type Json = Record<string, unknown>

const j = async (url: string): Promise<Json> => {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const d = (await r.json()) as Json
  if (d.ok === false) throw new Error(String(d.error ?? '请求失败'))
  return d
}

const cache = new Map<string, { at: number; data: Json }>()
export function invalidateCore(): void {
  cache.clear()
}
const cached = async (key: string, ttlMs: number, url: string): Promise<Json> => {
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < ttlMs) return hit.data
  const data = await j(url)
  cache.set(key, { at: Date.now(), data })
  return data
}

// ── meta ─────────────────────────────────────────────────────────────
export type CoreMeta = {
  root: string
  root_kind: string
  version: string
  root_id: string
  readonly: boolean
  readonly_reason: string
  core_ok: boolean
  core_error: string
  core_src: string
}
export const fetchCoreMeta = (ttl = 8000) =>
  cached('coremeta', ttl, '/api/v3.2.0/meta') as Promise<unknown> as Promise<CoreMeta>

export type CoreStats = {
  detail: number
  pending: number
  by_status: Record<string, number>
  by_scope: Record<string, number>
  gate1?: number
  gate2?: number
  gate3?: number
  gate4?: number
  injects?: number
  hits?: number
  packs?: number
}
export const fetchCoreStats = (ttl = 5000) =>
  cached('corestats', ttl, '/api/v3.2.0/stats') as Promise<unknown> as Promise<CoreStats>

// ── G2 四门实况 ──────────────────────────────────────────────────────
export type GateKind = 'ok' | 'dead' | 'idle-spin' | 'starved' | 'stale'
export type GateRow = {
  gate: 'gate1' | 'gate2' | 'gate3' | 'gate4'
  name: string
  total: number
  effective: number
  expected_count: number
  last_action: string
  last_ts: string
  age_days: number | null
  dead: boolean
  kind: GateKind
  reason: string
  expect: string
}
export type GatesData = {
  gates: GateRow[]
  dead_count: number
  stale_days: number | null
  status: string
  notes: string[]
  materialized: boolean
  detail: number
}
export const fetchGates = (ttl = 4000) =>
  cached('coregates', ttl, '/api/v3.2.0/gates') as Promise<unknown> as Promise<GatesData>

// ── G1 圈的视角 ──────────────────────────────────────────────────────
export type CircleRow = {
  id: string
  count: number
  declared: boolean
  budget_chars: number
  catalog_limit: number
  catalog_count: number
  catalog_chars: number
  readonly: boolean
  sources: string[]
  match: { cwd_prefix?: string[] }
  display_name: string
}
export type CirclesData = { items: CircleRow[]; global_budget_chars: number; source?: string }
export const fetchCircles = (ttl = 5000) =>
  cached('corecircles', ttl, '/api/v3.2.0/circles') as Promise<unknown> as Promise<CirclesData>

export type CircleConfig = {
  circles: unknown[]
  global_budget_chars?: number
  path?: string
  exists?: boolean
  mtime?: string
}
export const fetchCirclesConfig = () => j('/api/v3.2.0/circles/config') as Promise<unknown> as Promise<CircleConfig>

// ── G3 注入观测 ──────────────────────────────────────────────────────
export type InjectSource = {
  source: string
  events: number
  chars: number
  items: number
  tokens: number
  avg_chars: number
}
export type InjectRecent = { ts: string; source: string; form: string; items: number; chars: number }
export type InjectData = {
  sources: InjectSource[]
  total_events: number
  dead_rate: number | null
  reload_rate: number | null
  recent: InjectRecent[]
  kpi: { max_messages: number; max_chars: number }
}
export const fetchInject = (ttl = 6000) =>
  cached('coreinject', ttl, '/api/v3.2.0/inject') as Promise<unknown> as Promise<InjectData>

// ── G5 记忆包 ────────────────────────────────────────────────────────
export type PackRow = {
  id: string
  version: string
  mode: string
  enabled: boolean
  entries: number
  provenance: string
  path: string
  exists?: boolean
  hash_ok?: boolean | null
  errors?: string[]
  warnings?: string[]
}
export const fetchPacks = (ttl = 6000) =>
  cached('corepacks', ttl, '/api/v3.2.0/packs').then(d => (d.packs ?? []) as PackRow[])

// ── G6 成本 ──────────────────────────────────────────────────────────
export type CostRow = {
  ts: string
  session: string
  tools_schema: number
  system_prompt: number
  catalog: number
  injected_msgs: number
  injected_chars: number
  eco_msgs: number | null
  eco_chars: number | null
}
export type CostData = {
  rows: CostRow[]
  has_split: boolean
  catalog_chars: number
  tools_schema: number
  system_prompt: number
  worst_messages: number
  worst_chars: number
  total_messages: number
  total_chars: number
  kpi: { max_messages: number; max_chars: number; applies_to: string }
  over_kpi: boolean
  kpi_known: boolean
}
export const fetchCost = (ttl = 25000) =>
  cached('corecost', ttl, '/api/v3.2.0/cost') as Promise<unknown> as Promise<CostData>

// ── G7 急救箱 ────────────────────────────────────────────────────────
export type AidCheck = { id: string; name: string; status: 'ok' | 'warn' | 'fail'; detail: string; action: string }
export type AidData = { checks: AidCheck[]; root: string; all_ok: boolean; fail_count: number; ts: string }
export const fetchCoreFirstaid = (ttl = 9000) =>
  cached('coreaid', ttl, '/api/v3.2.0/firstaid') as Promise<unknown> as Promise<AidData>

// ── 写动作（全部转发给核心 CLI；只允许这三类）────────────────────────
export type ActionSpec = { name: string; risk: 'strong' | 'light'; impact: string; rollback: string }
export const fetchActionSpecs = (ttl = 60000) =>
  cached('coreactions', ttl, '/api/actions').then(d => (d.actions ?? {}) as Record<string, ActionSpec>)

export type ActionResult = {
  ok: boolean
  action: string
  stage: string
  spec?: ActionSpec
  output?: string
  error?: string
  ms?: number
  cmd?: string
}
export const runAction = async (
  name: string, stage: 'dry' | 'run', params: Record<string, string> = {},
): Promise<ActionResult> => {
  const r = await fetch('/api/action/' + encodeURIComponent(name), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage, params }),
  })
  // 服务端失败也返 200（前端靠 j.ok 判），所以这里不按 HTTP 状态抛
  const d = (await r.json()) as ActionResult
  return d
}
