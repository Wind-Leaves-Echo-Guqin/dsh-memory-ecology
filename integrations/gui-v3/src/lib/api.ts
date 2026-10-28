/** gui-v2.1 数据层：v0.3 观测舱（8788）全部只读端点的类型化封装。
 * 端点形状与 v1 GUI（static/js）消费的完全同源——v1 能渲染的，v2.1 就能渲染。
 * 全部 GET；v2.1 无写通道（写操作语义上仍归 v0.3 观测舱 + 确认闸门）。 */

type Json = Record<string, unknown>

const j = async (url: string): Promise<Json> => {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  const d = (await r.json()) as Json
  if (d.ok === false) throw new Error(String(d.error ?? '请求失败'))
  return d
}

/** 带内存 TTL 缓存的 GET（对齐 v1 core.cached：同视图轮询不重复打接口）。 */
const cache = new Map<string, { at: number; data: Json }>()
export function invalidate(prefix = ''): void {
  if (!prefix) return cache.clear()
  for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k)
}
export const cached = async (key: string, ttlMs: number, url: string): Promise<Json> => {
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < ttlMs) return hit.data
  const data = await j(url)
  cache.set(key, { at: Date.now(), data })
  return data
}

// ── meta / overview ──────────────────────────────────────────────────
export type LockInfo = { mtime?: string; age_hours?: number | null }
export type Meta = {
  root: string
  root_exists: boolean
  version: string
  score_model: string
  snapshot: string
  locks: Record<string, LockInfo>
  writer_today_ran?: boolean
  scripts_dir?: string
  search_cli_ready?: boolean
}
export const fetchMeta = (ttl = 15000) => cached('meta', ttl, '/api/meta') as Promise<unknown> as Promise<Meta>

export type TrendPoint = { score: number; time: string; model: string }
export type CronFail = { job: string; job_name?: string; n?: number; finished_at?: string; error?: string; provider?: string | null; model?: string | null }
export type CronJob = { id: string; name: string; eco?: boolean; display?: string; enabled: boolean; last_status?: string; last_run_at?: string; script?: string; provider?: string | null; model?: string | null }
export type Overview = {
  score: { score: number; time: string; model: string } | null
  history: TrendPoint[]
  watermark: { chars: number; quota: number }
  counts: Record<string, number | Record<string, number>>
  gate_counts: Record<string, number>
  gates: Record<string, { ts?: string; action?: string } | null>
  cron: {
    executions_7d?: { ts?: string; n_exec?: number; n_fails?: number }
    jobs_snapshot?: { ok?: number; err?: number; ts?: string }
    recent_fails?: CronFail[]
    fail_details?: CronFail[]
    eco_jobs?: CronJob[]
    jobs?: CronJob[]
  }
  candidates: { by_kind: Record<string, number> }
  health_row: { date: string; gene: string; cron: string; fail: string; mem: string } | string | null
  gene?: string[]
}
export const fetchOverview = (ttl = 8000) => cached('overview', ttl, '/api/overview') as Promise<unknown> as Promise<Overview>

// ── 记忆库 ───────────────────────────────────────────────────────────
export type L1Entry = { text: string; linked_slug?: string }
export type L1File = { name?: string; chars: number; quota: number; entries: L1Entry[] | string[] }
export type L1Pair = { memory: L1File; user: L1File }
export const fetchL1 = (ttl = 8000) => cached('l1', ttl, '/api/memories/l1') as Promise<unknown> as Promise<L1Pair>

export type DetailItem = {
  slug: string; type: string; status: string
  occ: number | null; sess: number | null
  first: string; last: string; verified: string
  chars?: number; body?: string
  origin?: string; superseded_by?: string
  valid_time?: string; transaction_time?: string
}
export const fetchL2 = (ttl = 8000) => cached('l2', ttl, '/api/memories/l2')
  .then(d => (d.items ?? []) as DetailItem[])

export type MemDetail = {
  slug: string
  fm?: Record<string, string | number | null>
  body?: string
}
export const fetchMemDetail = async (slug: string): Promise<MemDetail | null> => {
  const d = await j(`/api/memories/detail?slug=${encodeURIComponent(slug)}`)
  return (d.detail ?? null) as MemDetail | null
}

export type AuxFile = { name: string; mtime?: string; preview?: string }
export type Aux = {
  quarantine: AuxFile[]
  pending: AuxFile[]
  user_candidates: AuxFile[]
  gate_logs?: Array<{ name: string; kind: string; mtime: string }>
}
export const fetchAux = (ttl = 15000) => cached('aux', ttl, '/api/memories/aux') as Promise<unknown> as Promise<Aux>

// ── 经验笔记本 ───────────────────────────────────────────────────────
export type ExpItem = {
  id: string; type: string; status: string; title: string
  evidence?: string; symptom?: string
  created?: string; last_hit?: string
  distilled_to?: string; provenance?: string
}
export type ExpPage = {
  items: ExpItem[]; total: number; page: number; pages: number
  pending: Array<{ name: string; mtime: string; preview?: string }>
}
export const fetchExperiences = async (type = '', status = '', page = 1, sort = 'created'): Promise<ExpPage> => {
  const qs = new URLSearchParams({ page: String(page) })
  if (type) qs.set('type', type)
  if (status) qs.set('status', status)
  const d = await j(`/api/experiences?${qs}`)
  const items = (d.items ?? []) as ExpItem[]
  if (sort === 'last_hit') items.sort((a, b) => (b.last_hit ?? '').localeCompare(a.last_hit ?? ''))
  return {
    items,
    total: Number(d.total ?? 0),
    page: Number(d.page ?? 1),
    pages: Number(d.pages ?? 1),
    pending: (d.pending ?? []) as ExpPage['pending'],
  }
}

export type ExpDetail = {
  id: string; type: string; status: string; title: string
  created?: string; last_hit?: string; provenance?: string; distilled_to?: string
  sections?: Partial<Record<'symptom' | 'cause' | 'action' | 'evidence' | 'boundary', string>>
}
export const fetchExpDetail = async (id: string): Promise<ExpDetail> => {
  const d = await j(`/api/experiences/detail?id=${encodeURIComponent(id)}`)
  return d.item as ExpDetail
}

// ── 候选孵化台 ───────────────────────────────────────────────────────
export type CandItem = { name: string; kind: string; desc: string; mtime: string; path?: string }
export const fetchCandidates = (ttl = 10000) => cached('candidates', ttl, '/api/candidates').then(d => ({
  items: (d.items ?? []) as CandItem[],
  byKind: (d.by_kind ?? {}) as Record<string, number>,
}))

export type CandDetail = { name: string; kind: string; body?: string; frontmatter?: Record<string, string>; path?: string }
export const fetchCandDetail = async (kind: string, name: string): Promise<CandDetail> => {
  const d = await j(`/api/candidates/detail?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`)
  return d.detail as CandDetail
}

// ── 技能 / 星图 / 血缘 ───────────────────────────────────────────────
export type SkillRow = {
  name: string; cat?: string; desc?: string; version?: string
  status?: string; fate?: string; domain?: string
  evolved_from?: string | null; merged_into?: string | null
  related?: string[]; declared?: boolean; dangling?: string[]
  indeg?: number; refs?: number
}
export const fetchSkills = (ttl = 30000) => cached('skills', ttl, '/api/skills')
  .then(d => (d.skills ?? []) as SkillRow[])

export type LineageEdge = { child: string; parent: string; kind: 'evolved_from' | 'merged_into' }
export const fetchLineage = (ttl = 30000) => cached('lineage', ttl, '/api/lineage')
  .then(d => (d.edges ?? []) as LineageEdge[])

// ── 时间线（eco.db 四表） ────────────────────────────────────────────
export type LogRow = { table: string; ts: string; action: string; target?: string; note?: string }
export const fetchLogs = async (limit = 300): Promise<{ rows: LogRow[]; counts: Record<string, number> }> => {
  const d = await j(`/api/logs?limit=${limit}`)
  return { rows: (d.rows ?? []) as LogRow[], counts: (d.counts ?? {}) as Record<string, number> }
}
export const fetchLogDay = async (date: string): Promise<string | null> => {
  const d = await j(`/api/logs/day?date=${encodeURIComponent(date)}`)
  const day = d.day as { body?: string } | undefined
  return day?.body ?? null
}

// ── 体检评测 ─────────────────────────────────────────────────────────
export type Arch = { name: string; day?: string; mtime?: string; source?: string }
export type Health = {
  report: { name?: string; mtime?: string; body?: string }
  archives: Arch[]
  history: TrendPoint[]
  eval_latest?: { name?: string; body?: string }
  evals?: Array<{ name: string; body?: string }>
  backups: Array<{ date: string; n: number; files: string[] }>
}
export const fetchHealth = (ttl = 30000) => cached('health', ttl, '/api/health') as Promise<unknown> as Promise<Health>
export const fetchHealthReport = async (name: string): Promise<string> => {
  const d = await j(`/api/health/report?name=${encodeURIComponent(name)}`)
  const r = d.report as { body?: string } | undefined
  return r?.body ?? ''
}

// ── 动作账本（v0.3 GUI 自身审计） ────────────────────────────────────
export type ActionEntry = { ts: string; action: string; stage: string; ok: boolean; ms?: number; params?: Record<string, unknown>; out?: string }
export const fetchActionLog = (ttl = 8000) => cached('action_log', ttl, '/api/action_log')
  .then(d => (d.entries ?? []) as ActionEntry[])

// ── cron（观测口径） ─────────────────────────────────────────────────
export const fetchCron = (ttl = 10000) => cached('cron', ttl, '/api/cron').then(d => (d.jobs ?? []) as CronJob[])

// ── 三模式检索（经验 / 报错 / 生态） ─────────────────────────────────
export type SearchHit = {
  id?: string; name?: string; type?: string; status?: string
  trigger?: string; evidence?: string; desc?: string; sections?: string; indeg?: number
}
export type SearchResult = { kind: string; hits: SearchHit[]; exc?: string[]; raw?: string }
export const fetchSearch = async (kind: string, q: string): Promise<SearchResult> => {
  const d = await j(`/api/search?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(q)}`)
  return d as unknown as SearchResult
}
