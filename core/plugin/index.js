/**
 * eco-memory-dsh — 记忆生态 · dsh 独立插件（v3.2.0）
 *
 * 设计约束（六条判据，见 docs/ARCHITECTURE.md）：
 *   1) 领域语义**全部**在 Python 核心（src/memory_ecology/），本文件只决定
 *      「什么内容 / 什么形态 / 什么时点 / 多少预算」——**不许在 JS 里判相似度、判状态迁移**；
 *   2) 与核心只有一个接口：一次子进程调用，JSON 进 JSON 出；
 *   3) **无 cron**：四道门挂在会话生命周期 + 空闲维护上，幂等，进程被杀后自动补账；
 *   4) 上下文是 append-only（实测 dsh 的 snapshot 是追加不是替换），所以 warm 目录文本
 *      在会话内**字节稳定**，只在真正变化时重建 —— 这是本文件最重要的不变量；
 *   5) 全链路 fail-open：任何异常都退化成「本轮没有记忆」，绝不阻塞会话；
 *   6) **零配置可用**：默认根 %LOCALAPPDATA%/dsh/memory-eco，首次自动创建。
 *
 * 上下文形态（成本结构，v2 拓扑 · 设计文档 docs/多生态圈动态机制-设计.md §3）：
 *   前缀  = ctx.systemPrompt.section
 *           ├─ eco:charter (9005)  主圈物理法则，字节稳定，仅版本/配置变更时变
 *           └─ eco:catalog (9100)  全局目录（主圈基础物种滚动层），≤600 字符
 *   末端  = agent/pre-step 追加 user 消息（append-only，随 compaction 滚出）
 *           ├─ eco:l1      L1 常驻（MEMORY.md=基础物种常驻层 + USER.md=气候），
 *           │              首轮全量 ≤1500，之后只在变更时增量
 *           └─ eco:circle  挂载圈目录合集（routed 圈默认挂载 + attach 的圈），
 *                          ≤900 字符总闸，变更才注入，分离即停
 *   cold  = 工具 memory_load(id) / memory_search(q) / memory_circle_*   正文，按需
 *
 * 注入预算 KPI：每轮最多 2 条追加消息，各自只在真正变化时出现；
 * 圈的挂载/分离 = agent 主导的动态热加载（不重启会话）。
 *
 * 配置（cordis.patch.yml 同 id 覆盖，或环境变量）：
 *   rootPath / ECO_HOME        数据根
 *   corePath / ECO_CORE        核心入口（默认走 `python -m memory_ecology`）
 *   python   / ECO_PYTHON      Python 解释器
 *   catalogLimit               目录条数上限
 *   catalogMaxChars            目录字符预算
 *   maintenanceIntervalMs      两次维护（四门）的最小间隔，默认 10 分钟
 *   reconcileIntervalMs        两次整合的最小间隔，默认 3 分钟
 *   packs                      { enabled: bool, mountOnly: bool } 只读记忆包开关
 */

import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const name = 'eco-memory';
const inject = ['tools', 'systemPrompt'];

const PKG_DIR = path.dirname(fileURLToPath(import.meta.url));
const SECTION_NAME = 'eco:catalog';
const SECTION_ORDER = 9100; // 排在 DELIVERABLE_FILE_REFERENCES(9000) 之后，前面所有段字节不动
const CALL_TIMEOUT_MS = 8000;
const DEFAULT_CATALOG_LIMIT = 20;
const DEFAULT_CATALOG_MAX_CHARS = 600; // 字符才是闸门，条数是假的
const DEFAULT_L1_MAX_CHARS = 1500;     // L1 常驻段的注入预算（与核心 L1_INJECT_BUDGET_CHARS 同口径）
const DEFAULT_CIRCLE_TAIL_MAX_CHARS = 900;  // 副圈尾段总闸（权威值来自核心 circles.tail_max_chars）
const DEFAULT_RECONCILE_MS = 3 * 60 * 1000;
const DEFAULT_MAINTENANCE_MS = 10 * 60 * 1000;
const CATALOG_SCHEMA = 2;

// ── 路径与解释器解析 ────────────────────────────────────────────────────

function resolveRoot(config) {
  const explicit = process.env.ECO_HOME || config?.rootPath;
  if (explicit) return explicit;
  if (process.env.LOCALAPPDATA) return path.join(process.env.LOCALAPPDATA, 'dsh', 'memory-eco');
  return path.join(os.homedir(), '.local', 'share', 'dsh', 'memory-eco');
}

/** 核心定位：优先 `python -m memory_ecology`，其次显式脚本路径。 */
function resolveCore(config) {
  const explicit = process.env.ECO_CORE || config?.corePath;
  if (explicit && existsSync(explicit)) return { mode: 'script', value: explicit };
  for (const rel of ['../src', 'src', '../../src']) {
    const cand = path.resolve(PKG_DIR, rel);
    if (existsSync(path.join(cand, 'memory_ecology', 'cli.py'))) {
      return { mode: 'module', value: cand };
    }
  }
  return { mode: 'module', value: null };
}

function pythonCandidates(config) {
  const explicit = process.env.ECO_PYTHON || config?.python;
  if (explicit) return [explicit];
  return process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
}

// ── 核心调用（唯一接口）─────────────────────────────────────────────────

function buildArgv(core, root, args) {
  if (core.mode === 'script') return [core.value, '--root', root, ...args];
  return ['-m', 'memory_ecology', '--root', root, ...args];
}

function buildEnv(core) {
  const env = {
    // 最小环境：不透传凭据类变量
    PATH: process.env.PATH,
    PATHEXT: process.env.PATHEXT,
    SYSTEMROOT: process.env.SYSTEMROOT,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
    HOME: process.env.HOME,
    USERPROFILE: process.env.USERPROFILE,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  };
  if (core.mode === 'module' && core.value) env.PYTHONPATH = core.value;
  return env;
}

function runEco(py, core, root, args, timeout = CALL_TIMEOUT_MS) {
  return new Promise((resolve) => {
    if (!py || !core || (core.mode === 'module' && !core.value)) {
      resolve({ ok: false, out: '', err: 'python-or-core-unresolved', code: null });
      return;
    }
    execFile(py, buildArgv(core, root, args), {
      timeout,
      windowsHide: true,
      maxBuffer: 8 * 1024 * 1024,
      env: buildEnv(core),
    }, (error, stdout, stderr) => {
      if (error) {
        resolve({ ok: false, out: String(stdout || ''),
                  err: String(stderr || error.message), code: error.code ?? null });
        return;
      }
      resolve({ ok: true, out: String(stdout || ''), err: String(stderr || ''), code: 0 });
    });
  });
}

function parseJsonLine(text) {
  const lines = String(text || '').trim().split(/\r?\n/).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    try {
      return JSON.parse(lines[i]);
    } catch {
      // 继续往上找
    }
  }
  return null;
}

// ── 目录文本渲染 ────────────────────────────────────────────────────────
// 与核心 retrieve.render_catalog_text **同格式**。格式改动会让前缀缓存失效，
// 所以两边必须一起改，并由 tests/test_plugin.mjs 钉住逐字节一致。
//
// **刻意不渲染 payload.l1**（第三轮审查）：L1 常驻段绝不进 systemPrompt.section
// ——section 改前缀=全量重新 prefill（实测一次 0.29 元）。L1 走 agent/pre-step
// 的 user 消息通道（见下方 L1 注入一节），物化载荷里的 l1 字段只供 GUI/诊断读。

function renderCatalog(payload, limit) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  if (items.length === 0) return '';
  const shown = items.slice(0, limit);
  const lines = shown.map((it) => {
    const kind = String(it.kind || 'semantic').slice(0, 8).padEnd(8, ' ');
    const body = String(it.line || '').replace(/\s+/g, ' ').trim();
    return `- ${it.id || ''} │ ${kind} │ ${body}`;
  });
  const total = Number(payload?.total || items.length);
  const head = total > shown.length
    ? `记忆目录（共 ${total} 条，显示最近 ${shown.length} 条；正文用 memory_load(id) 取）`
    : `记忆目录（共 ${total} 条；正文用 memory_load(id) 取）`;
  return [head, ...lines].join('\n');
}

// ── 副圈尾段拼装（设计文档 §4，纯函数，__test__ 可直接测）───────────────
//
// `sections` = [{circle, text}]，**挂载顺序**（routed 最先）。预算语义：
//   - 整圈为单位全进或全丢（半圈目录比没有更误导）；
//   - 超闸的圈整圈丢弃，**后面放得下的小圈仍可进**；
//   - 丢弃必须显式标注（静默缩水 = agent 以为挂了其实没挂）；
//   - **预算按最终文本实长判定**（质量门①审查 P2）：头行与"超预算未注入"
//     标注行都计入——先试排、超了才丢，保证输出 ≤ budget 字节级成立。
function buildCircleTail(sections, budget) {
  const usable = (sections || []).filter((s) => s && s.circle && String(s.text || '').trim());
  const byId = new Map(usable.map((s) => [s.circle, s]));
  const render = (ids, dropped) => {
    const blocks = ids.map((id) => `▣ 圈 ${id}\n${byId.get(id).text}`);
    if (dropped.length) {
      blocks.push(`（超预算未注入：${dropped.join(' , ')}——用 memory_circle_detach 释放预算后再挂）`);
    }
    if (blocks.length === 0) return '';
    // **JS 模板串**（二轮审查 P1：Python 的 `%` 格式化写进 JS 得到字面 NaN）
    return [`项目圈记忆（挂载 ${ids.length} 圈；正文用 memory_load(id) 取，分离用 memory_circle_detach）：`,
            ...blocks].join('\n');
  };
  const included = [];
  const dropped = [];
  let text = '';
  for (const s of usable) {
    if (included.includes(s.circle) || dropped.includes(s.circle)) continue;   // 同名圈只判一次
    const trial = render([...included, s.circle], dropped);
    if (trial.length <= budget) {
      included.push(s.circle);
      text = trial;
    } else {
      dropped.push(s.circle);
      text = render(included, dropped);
      // 丢弃标注行会让文本变长——若因此反超预算，从 included 尾部继续收缩
      // （块长度 ≥ 标注增量，循环必然收敛），保证最终文本 ≤ budget 字节级成立
      while (included.length && text.length > budget) {
        dropped.push(included.pop());
        text = render(included, dropped);
      }
    }
  }
  if (included.length === 0) {
    if (dropped.length === 0) return { text: '', included, dropped };
    // 全部圈超预算也要留标注（漏洞扫描低#7：静默空文本 = agent 以为
    // 挂了其实什么都没进来）；**标注本身也受预算约束**（二轮审查 P2：
    // 40 个长名圈的全丢标注实测可到 1390 字符）
    let note = `（挂载圈全部超预算未注入：${dropped.join(' , ')}`
      + '——用 memory_circle_detach 释放预算）';
    if (note.length > budget) {
      note = `（挂载圈全部超预算未注入：${dropped.length} 圈`
        + '——用 memory_circle_detach 释放预算）';
    }
    if (note.length > budget) note = note.slice(0, Math.max(0, budget));
    return { text: note, included, dropped };
  }
  return { text, included, dropped };
}

// ── 宿主模块解析（link 安装时裸导入走不到宿主依赖树）─────────────────────

function hostModulesRoots(config) {
  const home = os.homedir();
  return [
    config?.hostModulesDir,
    process.env.DSH_ECO_HOST_MODULES,
    process.env.DSH_HOME ? path.join(process.env.DSH_HOME, 'profiles', 'node_modules') : null,
    path.join(home, '.dsh', 'profiles', 'node_modules'),
    path.join(home, '.dsh', 'profiles', 'desktop', 'node_modules'),
  ].filter(Boolean);
}

async function importHostModule(config, spec) {
  const tried = [];
  for (const root of hostModulesRoots(config)) {
    tried.push(root);
    if (!existsSync(path.join(root, '@deepseek-ai', 'dsh-tools'))) continue;
    const req = createRequire(path.join(root, '_resolve_.js'));
    let entry;
    try {
      entry = req.resolve(spec);
    } catch {
      continue;
    }
    const mod = await import(pathToFileURL(entry).href);
    return { mod, entry, root };
  }
  throw new Error(`eco-memory: 未找到宿主 node_modules 树（尝试过：${tried.join(' , ') || '（无候选）'}）`);
}

// ── 插件主体 ────────────────────────────────────────────────────────────

export async function apply(ctx, config = {}) {
  const { mod: toolsMod } = await importHostModule(config, '@deepseek-ai/dsh-tools');
  const { defineTool } = toolsMod;

  // L1 注入需要 createUserMessage（@deepseek-ai/dsh-llm，参照旧线 eco-note）。
  // **fail-open**：宿主包解析不到就退化为原始 user 消息对象，绝不因此整插件失效。
  let createUserMessage = null;
  try {
    const llm = await importHostModule(config, '@deepseek-ai/dsh-llm');
    createUserMessage = llm.mod?.createUserMessage ?? null;
  } catch (e) {
    ctx.logger.debug(`eco-memory: dsh-llm 不可用，L1 注入退化为原始消息对象: ${e?.message ?? e}`);
  }

  const root = resolveRoot(config);
  const core = resolveCore(config);
  const pys = pythonCandidates(config);
  const limit = Number(config?.catalogLimit ?? DEFAULT_CATALOG_LIMIT) || DEFAULT_CATALOG_LIMIT;
  const maxChars = Number(config?.catalogMaxChars ?? DEFAULT_CATALOG_MAX_CHARS);
  const l1MaxChars = Number(config?.l1MaxChars ?? DEFAULT_L1_MAX_CHARS) || DEFAULT_L1_MAX_CHARS;
  const reconcileMs = Number(config?.reconcileIntervalMs ?? DEFAULT_RECONCILE_MS);
  const maintenanceMs = Number(config?.maintenanceIntervalMs ?? DEFAULT_MAINTENANCE_MS);
  const packsEnabled = config?.packs?.enabled !== false;

  const state = {
    py: null,
    ready: false,
    catalogText: '',
    lastReconcile: 0,
    lastMaintenance: 0,
    chain: Promise.resolve(),
    degraded: '',
    circle: '',
    routeMap: new Map(),   // cwd 前缀（已归一）→ 圈名；由核心 circles 填充
    packsText: '',
    // 多生态圈（设计文档 §3/§4）：**per-agent 桶**。一个宿主进程可能跑
    // 多个 agent——v3.2.2 靠 per-agent 同名段遮蔽隔离；v2 圈目录走尾段消息，
    // 隔离改由 per-agent 桶承担（agent/created 建，pre-step 按创建者的
    // agent.id 取）。桶同时承载 L1 注入状态（质量门①审查 P1：L1 三状态
    // 原先是进程级单例，第二个及之后的 agent 永远收不到 L1 常驻段）。
    // 工具（memory_search/load）没有 agent 上下文，用「最近创建的 agent」
    // 桶——与 v3.2.2 共享 state.circle 同一局限，不是新退化。
    circlesByAgent: new Map(),
    // agentKey → {circle, attached: [], tailHash: '', l1Hash: '', l1Loaded: false, l1Seen: Set}
    // **工具侧桶归属**（漏洞扫描【中】：state.circle/attached 镜像会跨会话串味
    // ——v2 里 attached 是授权 allowlist，"谁挂载"不能跨会话生效）。
    // lastAgentKey = 最近一次 pre-step/created 的 agent（每轮 pre-step 先于
    // 本轮全部工具调用发生，串行 turn 下即"当前 agent"）；工具 execute 开头
    // 统一经 toolBucket() 刷新镜像。多 agent 并发交错 turn 是残余风险（在案）。
    lastAgentKey: '',
    attached: [],
    tailBudget: DEFAULT_CIRCLE_TAIL_MAX_CHARS,
    charterText: '',
  };
  const circleCache = new Map();
  let warmed = false;

  /**
   * 工具侧桶归属（漏洞扫描【中】修复）：按 lastAgentKey 取**当前 agent** 的桶
   * 并刷新 state.circle/state.attached 镜像。没有桶就建一个并持久化（L1 状态
   * 必须跨轮存活）。取不到 = 无圈无授权 → 全局语义，绝不借别人的桶。
   */
  function toolBucket() {
    let b = state.circlesByAgent.get(state.lastAgentKey);
    if (!b) {
      b = { circle: '', attached: [], tailHash: '',
            l1Hash: '', l1Loaded: false, l1Seen: new Set() };
      state.circlesByAgent.set(state.lastAgentKey, b);
    }
    state.circle = b.circle;
    state.attached = b.attached;
    return b;
  }

  const call = (args, timeout) => runEco(state.py, core, root, args, timeout);

  /** 圈名归一：与核心 `store.norm_scope` 同口径。 */
  function normScope(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, '-')
      .replace(/^[-._]+|[-._]+$/g, '')
      .slice(0, 40);
  }

  /**
   * 工作目录 → 圈名。
   *
   * **绝不能自己按 basename 判圈**：`circles.json` 的路由是"cwd 前缀最长匹配"，
   * 而按目录末级名判会把 `D:/work/alpha` 判成圈 `alpha`——即使配置里 `alpha`
   * 只匹配 `D:/work/secret-project`。后果是**无关项目看到别的圈的机密**（实测复现）。
   * 这同时违反"插件里不许出现领域规则"。
   *
   * 做法：查启动时由核心 `circles` 预先算好的**路由表**（同步、零子进程）；
   * 表里没有该 cwd 的精确项时，退回"按前缀最长匹配 core 已声明的圈"，
   * 仍然由核心声明的 `match.cwd_prefix` 决定，不看目录名。
   */
  function normCwd(value) {
    let s = String(value || '').trim().replace(/\\/g, '/').replace(/\/+/g, '/');
    if (s.length > 1) s = s.replace(/\/+$/, '');
    return process.platform === 'win32' ? s.toLowerCase() : s;
  }

  function circleOf(cwd) {
    if (!cwd) return '';
    const target = normCwd(cwd);
    if (!target) return '';
    // 1) 启动时物化的精确路由表
    const exact = state.routeMap.get(target);
    if (exact) return exact === 'global' ? '' : exact;
    // 2) 前缀最长匹配（用的是核心声明的 cwd_prefix，不是目录名）
    //    **质量门①审查 P1 修复**：旧写法 `{ best, bestLen = scope, prefix.length; }`
    //    是逗号表达式——best 永不赋值、bestLen 被赋成圈名字符串。子目录 cwd
    //    落进这条 fallback 后永远路由不到圈 → 本应受圈限的会话全库直通。
    let best = '', bestLen = -1;
    for (const [prefix, scope] of state.routeMap) {
      if (!prefix) continue;
      if (target === prefix || target.startsWith(prefix.endsWith('/') ? prefix : prefix + '/')) {
        if (prefix.length > bestLen) { best = scope; bestLen = prefix.length; }
      }
    }
    if (best) return best === 'global' ? '' : best;
    // 3) 核心没有任何声明 → 只有全局记忆（**这正确**：没声明就不猜）
    return '';
  }

  /**
   * **S3 fail-closed：圈边界绝不交给模型。**
   *
   * 旧写法 `normScope(args?.scope) || normScope(state.circle)` 让模型写的
   * scope **优先于**路由出的圈——模型在参数里填目标圈名就能跨圈检索/读取
   * （REVIEW.md S3 实测复现：`memory_search scope="alpha"` 在 beta 会话命中
   * alpha 的口令）。`args.scope` 是**模型可写**的参数，不能当边界。
   *
   * 新契约（多生态圈 v2）：允许集合 = `{routed} ∪ attached`。
   *   - routed：cwd → 圈（核心声明），会话的默认边界；
   *   - attached：**已挂载**的圈——挂载本身是显式工具调用
   *     （`memory_circle_attach`，核心 `circle-attach` 校验 + 落账），
   *     所以"指向已挂载圈"是 auditable 的授权路径，不是走私。
   * 未挂载圈一律拒绝（fail-closed），只允许"等于允许集合中的圈"。
   * 全局会话（routed 为空）里显式 `global` 等于本圈，放行；圈会话里要
   * global/别的圈一律拒绝——本圈检索本来就 union 了 global 条目，拒绝
   * global 不损失任何合法可达内容，只关掉"跨圈"这一扇不该开的门。
   */
  function resolveScopeGuard(askedRaw) {
    const routed = normScope(state.circle);
    const asked = normScope(askedRaw);
    // **无圈会话兜底 global**（质量门①安全测试探测 4）：旧写法返回 ''，
    // search 据此不传 --scope → 核心全库直通，任意圈的 id+预览可读。
    // 兜成 'global' 后 search 显式 --scope global（核心语义：只回 global 条目），
    // 跨圈的正门只有 memory_circle_attach。
    if (!asked) return { ok: true, scope: routed || 'global' };
    if (asked === routed) return { ok: true, scope: asked };
    if (state.attached.includes(asked)) return { ok: true, scope: asked };
    if (!routed && asked === 'global') return { ok: true, scope: 'global' };
    return { ok: false, routed, asked };
  }

  function scopeDeniedText(guard) {
    const attached = state.attached.length ? `，已挂载：${state.attached.join(' , ')}` : '';
    return `（scope 被拒绝：本会话记忆圈是 ${guard.routed || 'global'}${attached}，`
      + `不接受未挂载的跨圈 scope=${guard.asked}；需要时先用 memory_circle_attach 挂载）`;
  }

  /**
   * 启动时向核心要一份"cwd 前缀 → 圈"的路由表。
   * 核心是唯一知道路由规则的地方；插件只缓存结果。
   */
  async function refreshRouteMap() {
    const res = await call(['circles'], CALL_TIMEOUT_MS);
    const payload = parseJsonLine(res.out);
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const map = new Map();
    for (const it of items) {
      const scope = String(it.id || '');
      const prefixes = ((it.match || {}).cwd_prefix) || [];
      for (const pre of prefixes) {
        const key = normCwd(pre);
        if (key) map.set(key, scope);
      }
    }
    state.routeMap = map;
    // 尾段总闸的权威值在核心（eco.toml）；插件 config 只在显式给出时覆盖
    const coreTail = Number(payload?.tail_max_chars);
    state.tailBudget = Number(config?.circleTailMaxChars)
      || (Number.isFinite(coreTail) && coreTail > 0 ? coreTail : DEFAULT_CIRCLE_TAIL_MAX_CHARS);
    return map.size;
  }

  function catalogDir() {
    return path.join(root, '.eco', 'catalog');
  }

  function readCatalogSync(scope) {
    try {
      const raw = readFileSync(path.join(catalogDir(), `${scope}.json`), 'utf8');
      const payload = JSON.parse(raw);
      if (!payload?.ok) return null;
      // 旧 schema 的物化文件一律当过期（宁可少注入，不可注入旧格式）
      if (payload.schema !== undefined && payload.schema !== CATALOG_SCHEMA) return null;
      return renderCatalog(payload, limit);
    } catch {
      return null; // fail-open：损坏就当作没有
    }
  }

  function listMaterializedScopes() {
    try {
      const idx = JSON.parse(readFileSync(path.join(catalogDir(), 'index.json'), 'utf8'));
      return Array.isArray(idx?.scopes) ? idx.scopes : [];
    } catch {
      return [];
    }
  }

  async function ensureReady() {
    if (state.ready) return true;
    if (!core || (core.mode === 'module' && !core.value)) {
      state.degraded = 'core-not-found';
      ctx.logger.warn('eco-memory: 找不到核心（src/memory_ecology）。设为 ECO_CORE 或 ECO_HOME 后重启。');
      return false;
    }
    for (const py of pys) {
      const res = await runEco(py, core, root, ['init'], CALL_TIMEOUT_MS);
      const payload = parseJsonLine(res.out);
      if (res.ok && payload && payload.ok) {
        state.py = py;
        state.ready = true;
        state.degraded = '';
        ctx.logger.info(`eco-memory: root=${root} python=${py} core=${core.mode} `
          + `root_id=${payload.root_id} version=${payload.version}`);
        return true;
      }
      state.degraded = payload?.reason || res.err || 'init-failed';
    }
    ctx.logger.warn(`eco-memory: 初始化失败（降级：只读 / 不注入）reason=${state.degraded}`);
    return false;
  }

  /**
   * 物化一次：核心把每个圈的目录写成 `.eco/catalog/<scope>.json`，插件随后只做同步读。
   *
   * 为什么必须这样：dsh 在 `agent/created` 之后 **~10ms** 就装配系统提示词，
   * 任何子进程调用都赢不了这个竞态（实测：created 619ms → render 629ms → 取数完成 739ms）。
   * 物化成文件后，热路径只是一次 readFileSync。
   */
  async function materializeAll({ audit = false } = {}) {
    // **不传全局 --limit/--max-chars**（二轮审查 P2）：核心物化时
    // `limit or c_limit / budget_chars or c_budget`——显式全局参数会永远压过
    // circles.json 的每圈预算（用户调小预算的圈照样收到 600 字符，
    // cmd_circles 上报的占用与实际注入字节漂移）。每圈用自己的预算才是
    // "用户授权的圈级预算"的本义。JS 侧 config.catalogLimit/catalogMaxChars
    // 只留给审计诊断调用。
    const res = await call(['materialize'], 20000);
    const payload = parseJsonLine(res.out);
    if (!payload?.ok) {
      ctx.logger.debug(`eco-memory: materialize 失败 ${payload?.reason || res.err || ''}`);
      return false;
    }
    let changed = false;
    const g = readCatalogSync('global');
    if (g !== null && g !== state.catalogText) {
      state.catalogText = g;
      changed = true;
      if (audit && g.length > 0) {
        await call(['catalog', '--limit', String(limit), '--max-chars', String(maxChars),
          '--scope', 'global', '--log-chars', String(g.length)]);
      }
    }
    for (const sc of listMaterializedScopes()) {
      if (sc === 'global') continue;
      const text = readCatalogSync(sc);
      if (text !== null) circleCache.set(sc, text);
    }
    warmed = true;
    return changed;
  }

  /** 物化完目录后顺手刷新路由表（两件事必须一起做，否则圈会错）。
      `audit` 必须透传：注入审计账是"这次注入到底发了什么"的唯一证据，
      中间加一层包装把这个开关吃掉，账本就静默不写了（实测 T7 因此失败）。 */
  async function syncView({ audit = false } = {}) {
    const changed = await materializeAll({ audit });
    try {
      await refreshRouteMap();
    } catch (e) {
      ctx.logger.debug(`eco-memory: 路由表刷新失败（fail-open，退化为只有全局）: ${e?.message ?? e}`);
    }
    return changed;
  }

  function serialize(task) {
    state.chain = state.chain
      .then(task)
      .catch((e) => {
        ctx.logger.debug(`eco-memory: 后台任务异常（fail-open）: ${e?.message ?? e}`);
      });
    return state.chain;
  }

  /** 门①（整合）：幂等，与核心 integrate 一一对应；失败只记日志。 */
  function enqueueReconcile(reason) {
    return serialize(async () => {
      if (!state.ready) return;
      const now = Date.now();
      if (now - state.lastReconcile < reconcileMs) return;
      state.lastReconcile = now;
      const res = await call(['reconcile'], 20000);
      const payload = parseJsonLine(res.out);
      if (payload?.ok) {
        if (payload.new > 0 || payload.merged > 0) {
          ctx.logger.info(`eco-memory: 门①(${reason}) new=${payload.new} `
            + `merged=${payload.merged} dups=${payload.dups}`);
          await syncView({ audit: true });
        }
      } else {
        ctx.logger.debug(`eco-memory: 门①(${reason}) 未成功：${payload?.reason || res.err || 'no-output'}`);
      }
    });
  }

  /**
   * 四门顺跑（cron 的替代：空闲维护 + 会话边界）。
   * **顺序有讲究**：先整合（产条目）→ 巩固（挑得出来的）→ 蒸馏 → 复核（降级用不到的）。
   * 反过来会把刚进来的条目当场降级。
   */
  function enqueueMaintenance(reason, force = false) {
    return serialize(async () => {
      if (!state.ready) return;
      const now = Date.now();
      if (!force && now - state.lastMaintenance < maintenanceMs) return;
      state.lastMaintenance = now;
      const res = await call(['gate', 'all'], 40000);
      const payload = parseJsonLine(res.out);
      if (!payload?.ok) {
        ctx.logger.debug(`eco-memory: 维护(${reason}) 未成功：${payload?.reason || res.err || 'no-output'}`);
        return;
      }
      const g2 = payload.gate2 || {};
      const g3 = payload.gate3 || {};
      if ((g2.promoted || []).length || (g3.promoted || 0) > 0) {
        ctx.logger.info(`eco-memory: 维护(${reason}) 门②promote=${(g2.promoted || []).length} `
          + `门③promote=${g3.promoted || 0} —— L1/画像已更新`);
        await syncView({ audit: true });
      } else {
        ctx.logger.debug(`eco-memory: 维护(${reason}) 零动作（门在跑）`);
      }
    });
  }

  // ---- 启动：初始化 + 物化全部圈的目录 + charter + 门① 补账 ----
  try {
    if (await ensureReady()) {
      await syncView({ audit: true });
      // charter（主圈物理法则）：**每进程取一次**。它是前缀字节稳定层——
      // 内容只随版本/eco.toml 变化，运行期重取既没必要又破坏稳定性语义。
      try {
        const cres = await call(['charter'], CALL_TIMEOUT_MS);
        const cp = parseJsonLine(cres.out);
        if (cp?.ok && cp.text) state.charterText = String(cp.text);
      } catch (e) {
        ctx.logger.debug(`eco-memory: charter 获取失败（fail-open，本轮无规定段）: ${e?.message ?? e}`);
      }
      enqueueReconcile('startup');
    }
  } catch (e) {
    ctx.logger.warn(`eco-memory: 启动阶段异常（fail-open）: ${e?.message ?? e}`);
  }

  // ---- charter：主圈物理法则段（order 9005，排在目录段之前——"主在尽量靠前"）----
  // 空文本时不产生输出；`interpolate: false` 与目录段同理（{{...}} 会炸装配）。
  ctx.systemPrompt.section({
    name: 'eco:charter',
    order: SECTION_ORDER - 95,   // 9005：紧贴目录段之前，仍在其余宿主段之后
    interpolate: false,
    text: () => state.charterText || '',
  });

  // ---- warm：目录段（每次 assembly 调用，但只返回缓存）----
  // `interpolate: false` 是**必须的**：text 默认 true，目录正文里只要出现 `{{...}}`
  // 就会被当模板变量解析，未知变量直接让**整个装配抛错**（实测宿主行为）。
  ctx.systemPrompt.section({
    name: SECTION_NAME,
    order: SECTION_ORDER,
    interpolate: false,
    text: () => state.catalogText,
  });

  // ---- 会话生命周期 + 副圈：**只有 `agent/created` 这一个入口** ----
  //
  // 实测（dsh 0.2.1-rc.x）：`agent/session-start` **不存在**——它已被并入
  // `agent/created`，payload 为 `{agent, source, signal}`，`source` 实际只发
  // 'startup' 与 'resume'（'clear'/'compact' 是保留值，无发出方）。旧代码监听
  // `agent/session-start` 是**静默死代码**：cordis 对未派发事件既不报错也不告警，
  // 于是"会话开始补账/重物化"从未真正生效。
  ctx.on('agent/created', (payload) => {
    const agent = payload?.agent;
    const source = String(payload?.source || 'startup');
    if (!agent) return;

    // 1) 会话边界补账：startup/resume 都要（resume 后磁盘可能已被别的进程改过）
    state.lastReconcile = 0; // 新会话允许立刻整合
    ensureReady()
      .then(async () => {
        if (source === 'resume' || !warmed) await syncView({ audit: true });
        enqueueReconcile(`created:${source}`);
      })
      .catch((e) => {
        ctx.logger.debug(`eco-memory: created 处理异常（fail-open）: ${e?.message ?? e}`);
      });

    // 2) 路由圈 = **默认挂载**的副圈（注入拓扑 v2，设计文档 §3）。
    //    v3.2.2 的同名段遮蔽把圈目录放进前缀——既不满足"副圈末端注入"，
    //    也无法会话中热切。v2 里圈目录全部走 pre-step 尾段；前缀只留
    //    主圈内容（charter + 全局目录），更稳定。
    //    state.circle 仍是检索/加载的默认边界（H4/S3 语义不变）。
    const cwd = agent?.session?.header?.cwd;
    const circle = circleOf(cwd);
    const bucket = {
      circle: circle || '', attached: [], tailHash: '',
      // L1 注入状态 per-agent（质量门①审查 P1 修复）
      l1Hash: '', l1Loaded: false, l1Seen: new Set(),
    };
    state.circlesByAgent.set(String(agent?.id ?? ''), bucket);
    state.lastAgentKey = String(agent?.id ?? '');
    // 桶上限（漏洞扫描低#6：长驻宿主 Map 无界涨）：超 64 删最旧（Map 按插入序），
    // 不删当前 agent
    while (state.circlesByAgent.size > 64) {
      const oldest = state.circlesByAgent.keys().next().value;
      if (oldest === undefined || oldest === state.lastAgentKey) break;
      state.circlesByAgent.delete(oldest);
    }
    if (circle) {
      bucket.attached.push(circle);   // routed 圈最先 → 预算挤兑时最后被丢
    }
    // 工具侧镜像指向本桶（最近创建的 agent）
    state.circle = bucket.circle;
    state.attached = bucket.attached;
    // 同步取：物化文件在启动时已写好，这里是纯文件读，不再有竞态
    if (circle && !circleCache.has(circle)) {
      const text = readCatalogSync(circle);
      if (text !== null) circleCache.set(circle, text);
    }
    ctx.logger.info(`eco-memory: 副圈路由 circle=${circle || '-'}（尾段注入，attached=${bucket.attached.length}）`);
  });

  // ---- L1 常驻注入（agent/pre-step，user 消息追加）----
  //
  // **为什么不是 systemPrompt.section**：section 进前缀，而 dsh 的上下文是
  // append-only——改前缀 = 之后每一次请求全量重新 prefill（实测一次 0.29 元）。
  // L1 内容会随门②③ 变化，走前缀等于反复付全量；走 pre-step 追加 user 消息
  // 只在真正变化时付一次（旧线 eco-note 的成熟写法，见
  // integrations/dsh/package-cordis/index.js 的 agent/pre-step）。
  //
  // 预算：首轮全量 ≤ l1MaxChars（默认 1500，核心 render_l1_injection 按 § 段
  // 截断并标注）；之后**只在 L1 内容变更（文件指纹对比）时追加增量**——
  // 按 § 段指纹判断哪些段是新的，不变更的轮次零注入（避免每轮重复注入）。
  //
  // 注意：dsh 0.2.1-rc.x 没有 agent/session-start 事件（已并入 agent/created），
  // 这里只监听真实存在的 agent/pre-step。
  function l1FilePaths() {
    return {
      mem: path.join(root, 'memories', 'MEMORY.md'),
      user: path.join(root, 'memories', 'USER.md'),
    };
  }

  /** 两份 L1 文件的联合指纹。缺文件按空串参与（门②③ 首次写入前是常态）。 */
  function l1FileHash() {
    try {
      const { mem, user } = l1FilePaths();
      const a = existsSync(mem) ? readFileSync(mem, 'utf8') : '';
      const b = existsSync(user) ? readFileSync(user, 'utf8') : '';
      if (!a.trim() && !b.trim()) return '';
      return createHash('sha1').update(a).update('\u0000').update(b).digest('hex');
    } catch {
      return '';
    }
  }

  /** 组注入消息。优先宿主 createUserMessage（与 eco-note 同款），退化用原始对象。 */
  function buildSnapshotMessage(sectionName, text) {
    const input = {
      content: [{ type: 'text', text }],
      source: { kind: name, form: 'snapshot', sections: [{ name: sectionName, text }] },
    };
    if (typeof createUserMessage === 'function') return createUserMessage(input);
    return { role: 'user', ...input };
  }

  function buildL1Message(text) {
    return buildSnapshotMessage('eco:l1', text);
  }

  /** 当前挂载圈的尾段（从物化缓存取，缺的 fail-open 跳过）。 */
  function circleTailSections(attached) {
    const secs = [];
    for (const c of attached || []) {
      const t = circleCache.get(c) ?? readCatalogSync(c);
      if (t) secs.push({ circle: c, text: t });
    }
    return secs;
  }

  function tailHash(text) {
    return createHash('sha1').update(String(text || '')).digest('hex');
  }

  async function fetchL1() {
    const res = await call(['l1', '--budget', String(l1MaxChars)], CALL_TIMEOUT_MS);
    const payload = parseJsonLine(res.out);
    return payload?.ok ? payload : null;
  }

  /** 增量块拼装：超预算按块丢弃（尾部优先保留）并标注，绝不注入超预算文本。 */
  function buildL1Delta(header, blocks, budget) {
    const out = [header];
    let used = header.length;
    let dropped = 0;
    for (const b of blocks) {
      const cost = b.text.length + 24;
      if (used + cost > budget) { dropped += 1; continue; }
      out.push(`- [L1·${b.file}] ${b.text}`);
      used += cost;
    }
    if (dropped) out.push(`（超预算省略 ${dropped} 段，全文在 memories/MEMORY.md）`);
    return out.join('\n');
  }

  /** L1 增量消息（v3.2.2 既有逻辑；状态在 per-agent 桶上——质量门① P1 修复）。 */
  async function composeL1Message(agent, bucket) {
    const h = l1FileHash();
    if (!h || h === bucket.l1Hash) return null;   // 无 L1 / 本 agent 已见过此版本 → 零注入
    const payload = await fetchL1();
    if (!payload) return null;
    const all = [
      ...((payload.memory?.sections) || []).map((s) => ({ ...s, file: 'MEMORY.md' })),
      ...((payload.user?.sections) || []).map((s) => ({ ...s, file: 'USER.md' })),
    ];
    let text;
    if (!bucket.l1Loaded) {
      // 首轮：全量（核心已按预算截断并标注），全部段记为已见
      for (const s of all) bucket.l1Seen.add(`${s.file}:${s.hash}`);
      // 上限修剪（漏洞扫描低#6：长驻宿主 Set 无界涨）
      if (bucket.l1Seen.size > 512) {
        bucket.l1Seen = new Set([...bucket.l1Seen].slice(-256));
      }
      text = String(payload.inject_text || '');
    } else {
      // 增量：只注入本轮没见过指纹的段（超预算整段丢弃并标注——
      // 丢弃的段同样记为已见：下轮文件再变时按当时内容重新判）
      const fresh = all.filter((s) => !bucket.l1Seen.has(`${s.file}:${s.hash}`));
      if (fresh.length === 0) {
        bucket.l1Hash = h;   // 变化只是重排/归一 → 无新段，不打扰对话
        return null;
      }
      for (const s of all) bucket.l1Seen.add(`${s.file}:${s.hash}`);
      if (bucket.l1Seen.size > 512) {
        bucket.l1Seen = new Set([...bucket.l1Seen].slice(-256));
      }
      text = buildL1Delta(`L1 常驻记忆增量（${fresh.length} 段新增/变更）：`, fresh, l1MaxChars);
    }
    if (!text) { bucket.l1Hash = h; return null; }
    bucket.l1Hash = h;
    bucket.l1Loaded = true;
    ctx.logger.debug(`eco-memory: L1 注入 chars=${text.length} `
      + `session=${String(agent?.id ?? '') || '-'}`);
    return buildL1Message(text);
  }

  /** 圈尾段消息：只在挂载集合或内容变化时出现（变更才注入）。per-agent。 */
  function composeCircleTailMessage(bucket) {
    const tail = buildCircleTail(circleTailSections(bucket.attached), state.tailBudget);
    if (!tail.text) return null;
    const h = tailHash(tail.text);
    if (h === bucket.tailHash) return null;
    bucket.tailHash = h;
    if (tail.dropped.length) {
      ctx.logger.debug(`eco-memory: 圈尾段超预算丢弃 ${tail.dropped.join(',')}`);
    }
    return buildSnapshotMessage('eco:circle', tail.text);
  }

  // ---- pre-step：L1 + 副圈尾段（每轮最多两条追加消息，各自只在变化时出现）----
  //
  // **为什么不是 systemPrompt.section**：section 进前缀，而 dsh 的上下文是
  // append-only——改前缀 = 之后每一次请求全量重新 prefill（实测一次 0.29 元）。
  // L1 会随门②③ 变化、圈目录随挂载集合变化，走前缀等于反复付全量；
  // 走 pre-step 追加 user 消息只在真正变化时付一次（旧线 eco-note 的成熟写法）。
  // 分离圈 = 停止追加；已注入的历史消息随 compaction 自然滚出。
  //
  // 注意：dsh 0.2.1-rc.x 没有 agent/session-start 事件（已并入 agent/created），
  // 这里只监听真实存在的 agent/pre-step。
  ctx.on('agent/pre-step', async ({ agent, signal }, next) => {
    const decision = await next();
    if (!decision || decision.kind === 'reject' || signal?.aborted) return decision;
    try {
      if (!state.ready) return decision;
      const base = decision.messages || [];
      const extra = [];
      // per-agent 桶：agent/created 时没建桶的（异常路径）**建并持久化**——
      // L1 状态必须跨轮存活，临时桶会让 L1 每轮重复注入。
      // lastAgentKey 在此刷新：pre-step 先于本轮全部工具调用，串行 turn 下
      // 它就是"当前 agent"（工具侧桶归属的依据，漏洞扫描【中】）。
      const agentKey = String(agent?.id ?? '');
      state.lastAgentKey = agentKey;
      let bucket = state.circlesByAgent.get(agentKey);
      if (!bucket) {
        bucket = { circle: '', attached: [], tailHash: '',
                   l1Hash: '', l1Loaded: false, l1Seen: new Set() };
        state.circlesByAgent.set(agentKey, bucket);
      }
      const l1Msg = await composeL1Message(agent, bucket);
      if (l1Msg) extra.push(l1Msg);
      const tailMsg = composeCircleTailMessage(bucket);
      if (tailMsg) extra.push(tailMsg);
      if (extra.length === 0) return decision;
      return { kind: 'enter', messages: [...base, ...extra] };
    } catch (e) {
      ctx.logger.debug(`eco-memory: pre-step 注入异常（fail-open）: ${e?.message ?? e}`);
      return decision;
    }
  }, { prepend: true });

  // ---- cold：按需读取 ----
  ctx.tools.register(defineTool({
    name: 'memory_load',
    description: '按 id 读取一条长期记忆的正文（只读）。id 来自上下文里的「记忆目录」或 memory_search 结果。',
    parameters: {
      id: { type: 'string', required: true, description: '记忆 id，如 mem-20261027-ab12cd34' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: async (args) => {
      toolBucket();   // 桶归属刷新（当前 agent 的圈边界，防跨会话串味）
      const id = String(args?.id ?? '').trim();
      if (!id) return { result: '（id 必填）' };
      // **S3：scope 不是本工具的声明参数，模型 smuggle 进来的 scope 更不能当边界。**
      // fail-closed：给的 scope 与路由圈不一致 → 直接拒绝（不调核心、不确认存在）。
      const guard = resolveScopeGuard(args?.scope);
      if (!guard.ok) return { result: scopeDeniedText(guard) };
      // --routed 必须在 `--` 之前：`--` 之后全是位置参数（argparse 契约）
      const loadArgv = ['load'];
      if (state.circle) loadArgv.push('--routed', state.circle);
      loadArgv.push('--', id);
      const res = await call(loadArgv);
      const payload = parseJsonLine(res.out);
      if (!payload) return { result: `（记忆读取失败：${res.err || '无输出'}）` };
      if (!payload.ok) {
        if (payload.readonly) return { result: `（只读降级：${payload.reason}）` };
        if (payload.error === 'not-found') return { result: `（没有这条记忆：${id}）` };
        return { result: `（记忆读取失败：${payload.error || 'unknown'}）` };
      }
      // **H4：load 也要过圈。** `id` 是模型给的，而检索/目录都受圈约束；
      // 唯独 load 直通，等于给了一条"知道 id 就能读任意圈"的旁路
      // （跨圈泄漏实测：projb 会话 `memory_load` 能读出 proja 的口令正文）。
      // 判定口径与核心 `retrieve` 一致：允许集合（routed ∪ attached）+ global
      // 可见，其余一律不返回正文。圈的两个来源都是**非模型**的：
      // routed 来自 cwd 路由，attached 来自显式挂载工具（resolveScopeGuard
      // 已把关模型给的 scope）。
      const allowed = new Set(['global']);
      if (guard.scope) allowed.add(normScope(guard.scope));
      for (const c of state.attached) allowed.add(normScope(c));
      const got = normScope(payload.scope || 'global');
      if (!allowed.has(got)) {
        return { result: `（没有这条记忆：${id}）` };   // 不确认它存在，避免探测圈名
      }
      const kind = payload.kind ? ` [${payload.kind}]` : '';
      const status = payload.status && payload.status !== 'active' ? ` (${payload.status})` : '';
      return { result: `${id}${kind}${status}\n${payload.body}` };
    },
    presentCall: (args) => ({
      card: 'generic', title: '读取长期记忆', kind: 'other', rawInput: args,
    }),
  }));

  // ---- cold：检索（目录放不下的记忆靠它可达）----
  ctx.tools.register(defineTool({
    name: 'memory_search',
    description: '在长期记忆里做关键词检索（只读）。目录里没有的条目用它在全库找，再用 memory_load 取正文。',
    parameters: {
      query: { type: 'string', required: true, description: '关键词，中英文皆可' },
      limit: { type: 'integer', description: '最多返回条数（默认 8）' },
      scope: { type: 'string', description: '可选：只搜某个项目圈（省略=全库）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: async (args) => {
      toolBucket();   // 桶归属刷新（当前 agent 的圈边界，防跨会话串味）
      const q = String(args?.query ?? '').trim();
      if (!q) return { result: '（query 必填）' };
      const n = Math.max(1, Math.min(20, Number(args?.limit ?? 8) || 8));
      const argv = ['search', '--limit', String(n)];
      // **H4：scope 必须有兜底，不能交给模型自己填。**
      // **S3：模型显式给的 scope 只能等于路由圈，不一致一律拒绝（fail-closed）。**
      // 旧实现 `normScope(args?.scope) || normScope(state.circle)` 让模型写的
      // 圈名**优先于**路由——填个目标圈名就能跨圈检索（REVIEW.md S3 实测）。
      // 本圈检索本来就 union 全局条目（核心 `--scope <circle>` = 本圈 + global），
      // 所以拒绝跨圈不损失任何合法内容。
      const guard = resolveScopeGuard(args?.scope);
      if (!guard.ok) return { result: scopeDeniedText(guard) };
      const scopeArg = guard.scope;
      // **scope 必须显式下发**（质量门①安全测试探测 4）：省略 --scope 在核心
      // 语义里是"全库直通"，不是"global"。routed/global 都要显式传。
      if (scopeArg) argv.push('--scope', scopeArg);
      if (state.circle) argv.push('--routed', state.circle);   // 跨圈命中账（设计文档 §6）
      argv.push('--', q);
      const res = await call(argv);
      const payload = parseJsonLine(res.out);
      if (!payload) return { result: `（检索失败：${res.err || '无输出'}）` };
      if (!payload.ok) return { result: `（检索失败：${payload.error || 'unknown'}）` };
      if (payload.count === 0) return { result: `（没有命中：${q}）` };
      const lines = payload.items.map((it) => `- ${it.id} │ ${it.kind} │ ${it.line}`);
      return { result: `命中 ${payload.count} 条（用 memory_load(id) 取正文）\n${lines.join('\n')}` };
    },
    presentCall: (args) => ({
      card: 'generic', title: '检索长期记忆', kind: 'other', rawInput: args,
    }),
  }));

  // ---- 多生态圈：agent 主导激活（设计文档 §4）----
  //
  // **领域判定全在核心**：能不能挂（已声明/attachable/未挂载）由 `circle-attach`
  // fail-closed 判定并落账，插件只搬运结果。attach 的授权语义：挂载是显式工具
  // 调用（auditable），挂了之后 `resolveScopeGuard` 才允许指向该圈——
  // 未挂载圈在 search/load 里依旧拒绝（S3 不回退）。
  ctx.tools.register(defineTool({
    name: 'memory_circle_list',
    description: '列出可用的记忆圈（项目生态）与当前挂载状态，附跨圈命中建议。挂载前先看这里。',
    parameters: {},
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: async () => {
      toolBucket();   // 桶归属刷新（当前 agent 的圈边界，防跨会话串味）
      const res = await call(['circles'], CALL_TIMEOUT_MS);
      const payload = parseJsonLine(res.out);
      if (!payload?.ok) return { result: `（圈清单读取失败：${res.err || payload?.error || '无输出'}）` };
      const rows = (payload.items || []).filter((c) => c.id !== 'global');
      if (rows.length === 0) {
        return { result: '（还没有声明任何项目圈；圈由 circles.json 声明或投递 --scope 建立）' };
      }
      const lines = rows.map((c) => {
        const flags = [
          c.id === state.circle ? 'routed' : '',
          state.attached.includes(c.id) ? '已挂载' : '未挂载',
          c.readonly ? '只读' : '',
        ].filter(Boolean).join('·');
        return `- ${c.id}${c.display_name ? `（${c.display_name}）` : ''}`
          + ` │ ${c.count} 条 │ ${flags || '可挂载'}`;
      });
      // 跨圈命中建议（驯化数据源；fail-open——stats 失败不影响清单）
      let sug = '';
      try {
        const sres = await call(['circles-stats', '--limit', '8'], CALL_TIMEOUT_MS);
        const sp = parseJsonLine(sres.out);
        const hits = (sp?.rows || []).filter((r) => r.suggest);
        if (hits.length) {
          sug = '\n跨圈命中建议（在这些会话里被反复命中的圈，考虑挂载）：\n'
            + hits.map((r) => `- ${r.scope}（在 ${r.routed || 'global'} 会话命中 ${r.hits} 次）`).join('\n');
        }
      } catch { /* fail-open */ }
      return { result: `记忆圈清单（挂载用 memory_circle_attach，分离用 memory_circle_detach）：\n${lines.join('\n')}${sug}` };
    },
    presentCall: (args) => ({
      card: 'generic', title: '列出记忆圈', kind: 'other', rawInput: args,
    }),
  }));

  ctx.tools.register(defineTool({
    name: 'memory_circle_attach',
    description: '挂载一个记忆圈（项目生态）：挂载后该圈目录随消息末端注入，检索/读取也对其放行。任务切换时挂对应的圈，用完用 memory_circle_detach 分离。',
    parameters: {
      circle: { type: 'string', required: true, description: '圈 id（用 memory_circle_list 查）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: async (args) => {
      // 读-改-写进串行链（漏洞扫描【中】：并发 attach 同圈会让 includes 检查
      // 与 push 之间隔着 await，产生重复项 → 一次 detach 报"已分离"但授权仍在）
      return serialize(async () => {
        // **先捕获本 agent 的桶**（二轮审查 P1：await 期间 agent/created 或别的
        // 工具调用会把 state.attached 重绑到别的桶——复查和 push 若走镜像，
        // 授权会写进别人的桶：B 未挂载却拿到了 A 挂的圈的检索权，A 反而丢失）。
        const bucket = toolBucket();
        const circle = normScope(args?.circle);
        if (!circle) return { result: '（circle 必填）' };
        if (circle === 'global') return { result: '（global 无需挂载：它对所有会话可见）' };
        if (bucket.attached.includes(circle)) {
          return { result: `（圈 ${circle} 已挂载）` };
        }
        const argv = ['circle-attach', '--circle', circle];
        if (bucket.circle) argv.push('--routed', bucket.circle);
        const res = await call(argv, CALL_TIMEOUT_MS);
        const payload = parseJsonLine(res.out);
        if (!payload?.ok) {
          // 核心是权威：未声明 / attachable:false / 未知错误 → 原样告知，不猜
          return { result: `（挂载被拒绝：${payload?.detail || payload?.error || res.err || '未知原因'}）` };
        }
        // **await 之后对 bucket 复查**：并发窗口里可能已被同 agent 的另一次 attach 挂上
        if (!bucket.attached.includes(circle)) bucket.attached.push(circle);
        if (payload.text) circleCache.set(circle, payload.text);
        ctx.logger.info(`eco-memory: 圈挂载 circle=${circle} entries=${payload.count} attached=${bucket.attached.length}`);
        return { result: `已挂载圈 ${circle}（${payload.count} 条进目录，尾段总闸 ${state.tailBudget} 字符）。`
          + `下一轮起该圈记忆随消息注入；不需要时用 memory_circle_detach 分离。` };
      });
    },
    presentCall: (args) => ({
      card: 'generic', title: '挂载记忆圈', kind: 'other', rawInput: args,
    }),
  }));

  ctx.tools.register(defineTool({
    name: 'memory_circle_detach',
    description: '分离一个已挂载的记忆圈：停止注入该圈目录（已注入的历史消息随上下文压缩自然滚出）。某圈的经验用完就分离，保持上下文干净。',
    parameters: {
      circle: { type: 'string', required: true, description: '圈 id（用 memory_circle_list 查当前挂载）' },
    },
    output: {
      schema: {
        type: 'object', additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: async (args) => {
      // 串行链 + **删尽所有匹配**（漏洞扫描【中】：重复项残留会让"已分离、
      // 检索不再放行"的回执变成假话——授权集合里还留着圈）
      return serialize(async () => {
        const bucket = toolBucket();   // 同 attach：对捕获的桶操作，不经镜像
        const circle = normScope(args?.circle);
        if (!circle) return { result: '（circle 必填）' };
        if (!bucket.attached.includes(circle)) {
          return { result: `（圈 ${circle} 未挂载）` };
        }
        // **原地移除**（不能 filter 重新赋值）：桶数组被 pre-step/工具共享，
        // 重新赋值会断开引用；**删尽所有匹配**（重复项残留会让"已分离"的
        // 回执变成假话——授权集合里还留着圈）
        let removed = 0;
        let idx;
        while ((idx = bucket.attached.indexOf(circle)) >= 0) {
          bucket.attached.splice(idx, 1);
          removed += 1;
        }
        // 落账 fail-open：账写不进去不挡分离本身（分离是本地状态操作）
        try {
          await call(['circle-detach', '--circle', circle], CALL_TIMEOUT_MS);
        } catch { /* fail-open */ }
        ctx.logger.info(`eco-memory: 圈分离 circle=${circle} x${removed} attached=${bucket.attached.length}`);
        return { result: `已分离圈 ${circle}。该圈目录停止注入，检索/读取也不再放行`
          + `（重新挂载随时可用 memory_circle_attach）。` };
      });
    },
    presentCall: (args) => ({
      card: 'generic', title: '分离记忆圈', kind: 'other', rawInput: args,
    }),
  }));

  // ---- 记忆包（mount 只读挂载）：挂载包的内容进目录，不进本库 ----
  async function mountedSection(scope) {
    if (!packsEnabled) return '';
    const argv = ['pack', 'mounted'];
    if (scope) argv.push('--scope', scope);
    const res = await call(argv, CALL_TIMEOUT_MS);
    const payload = parseJsonLine(res.out);
    const entries = Array.isArray(payload?.entries) ? payload.entries : [];
    if (entries.length === 0) return '';
    const lines = entries.slice(0, 8).map((e) => `- [${e.pack}] ${e.title}`);
    return ['外部只读记忆包（只读引用，非本库记忆）', ...lines].join('\n');
  }

  ctx.systemPrompt.section({
    name: 'eco:packs',
    order: SECTION_ORDER + 10,
    // **interpolate:false**（二轮审查 P2，改动前已存在、顺带修）：pack 标题
    // 来自外部 manifest——含 `{{...}}` 会让整个装配抛错，与 fail-open 承诺冲突
    interpolate: false,
    text: () => state.packsText || '',
  });

  if (packsEnabled) {
    serialize(async () => {
      if (!state.ready) return;
      state.packsText = await mountedSection('');
    });
  }

  // ---- 一轮收尾：跑四门维护（cron 的替代）----
  //
  // `agent/turn-stopping` 是 serial 且被 await 的：一轮真正收尾、无待处理输入，
  // 是唯一"安全地做异步领域工作"的时点（宿主明令禁止在 agent/created 里 await
  // whenIdle()，那会死锁）。
  //
  // **H1（已实测确认根因）：这里不能用 `runMaintenance`。**
  // 宿主 `dsh-agent-loop` 的调用顺序是
  //   `turn()` 内部派发 `agent/turn-stopping` → turn 返回 → `kick().finally` 才切 idle
  // 所以本处理器运行时 `phase.kind` **必然**是 `running`，而
  //   `runMaintenance(job) { if (this.phase.kind !== "idle") throw ... }`
  // 于是旧实现每一轮都**必抛**，落进 catch，而 catch 只 `logger.debug`。
  // 后果是 `gate all`（门②③④ 的唯一调用点）结构性地从不执行：
  // quota_log / distill_log / review_log 恒为空，"四道门"实际只有门①在跑。
  //
  // 正确做法：直接排进插件自己的串行链。`enqueueMaintenance` 内部已按
  // `maintenanceIntervalMs` 门控，四门本身幂等（指纹 + 文件状态），漏一次可补；
  // 且它不抢 phase，不会和宿主的 turn 生命周期打架。
  ctx.on('agent/turn-stopping', async () => {
    if (!state.ready && !(await ensureReady())) return;
    try {
      await enqueueMaintenance('turn-stopping');
    } catch (e) {
      // 真正的异常要能被看见（旧实现一律 debug，等于把所有失败都藏了）
      ctx.logger.warn(`eco-memory: 维护失败（本轮跳过，下轮会补）: ${e?.message ?? e}`);
    }
  });
}

export { name, inject };

// 供测试直接验证渲染口径（与核心 retrieve.render_catalog_text 必须逐字节一致）
export const __test__ = { renderCatalog, buildCircleTail, resolveRoot, normScopeOf: (v) => String(v || '')
  .toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-._]+|[-._]+$/g, '').slice(0, 40) };


