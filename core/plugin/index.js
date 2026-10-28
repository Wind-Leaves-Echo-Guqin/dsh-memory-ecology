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
 * 上下文形态（成本结构）：
 *   warm  = ctx.systemPrompt.section({name:'eco:catalog', order:9100})   目录，≤20 行 / ≤600 字符
 *   cold  = 工具 memory_load(id) / memory_search(q)                       正文，按需
 *   **没有 hot 档**：已用六条判据枪毙（主/副记忆用目录排序表达，见 ARCHITECTURE §9.1）
 *
 * 注入预算 KPI：≤3 条消息 / ≤1,200 字符每会话。本插件默认**0 条追加**（目录进前缀）。
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

  const root = resolveRoot(config);
  const core = resolveCore(config);
  const pys = pythonCandidates(config);
  const limit = Number(config?.catalogLimit ?? DEFAULT_CATALOG_LIMIT) || DEFAULT_CATALOG_LIMIT;
  const maxChars = Number(config?.catalogMaxChars ?? DEFAULT_CATALOG_MAX_CHARS);
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
  };
  const circleCache = new Map();
  let warmed = false;

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
    let best = '', bestLen = -1;
    for (const [prefix, scope] of state.routeMap) {
      if (!prefix) continue;
      if (target === prefix || target.startsWith(prefix.endsWith('/') ? prefix : prefix + '/')) {
        if (prefix.length > bestLen) { best, bestLen = scope, prefix.length; }
      }
    }
    if (best) return best === 'global' ? '' : best;
    // 3) 核心没有任何声明 → 只有全局记忆（**这正确**：没声明就不猜）
    return '';
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
    const res = await call(['materialize', '--limit', String(limit),
      '--max-chars', String(maxChars)], 20000);
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

  // ---- 启动：初始化 + 物化全部圈的目录 + 门① 补账 ----
  try {
    if (await ensureReady()) {
      await syncView({ audit: true });
      enqueueReconcile('startup');
    }
  } catch (e) {
    ctx.logger.warn(`eco-memory: 启动阶段异常（fail-open）: ${e?.message ?? e}`);
  }

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

    // 2) 副圈遮蔽段（按 cwd 决定圈；无圈则用全局段）
    const cwd = agent?.session?.header?.cwd;
    const circle = circleOf(cwd);
    if (!circle) return;
    state.circle = circle;
    // 同步取：物化文件在启动时已写好，这里是纯文件读，不再有竞态
    const fallback = circleCache.get(circle) ?? readCatalogSync(circle) ?? state.catalogText;
    if (!circleCache.has(circle)) circleCache.set(circle, fallback);
    try {
      if (typeof agent?.ctx?.systemPrompt?.section !== 'function') {
        ctx.logger.debug('eco-memory: agent.ctx 无 systemPrompt，副圈不可用（降级为全局段）');
        return;
      }
      agent.ctx.systemPrompt.section({
        name: SECTION_NAME,
        order: SECTION_ORDER,
        interpolate: false,
        text: () => circleCache.get(circle) ?? fallback,
      });
      ctx.logger.info(`eco-memory: 副圈启用 circle=${circle} chars=${fallback.length}`);
    } catch (e) {
      ctx.logger.debug(`eco-memory: 副圈注册异常（fail-open）: ${e?.message ?? e}`);
    }
  });

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
      const id = String(args?.id ?? '').trim();
      if (!id) return { result: '（id 必填）' };
      const res = await call(['load', '--', id]);
      const payload = parseJsonLine(res.out);
      if (!payload) return { result: `（记忆读取失败：${res.err || '无输出'}）` };
      if (!payload.ok) {
        if (payload.readonly) return { result: `（只读降级：${payload.reason}）` };
        if (payload.error === 'not-found') return { result: `（没有这条记忆：${id}）` };
        return { result: `（记忆读取失败：${payload.error || 'unknown'}）` };
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
      const q = String(args?.query ?? '').trim();
      if (!q) return { result: '（query 必填）' };
      const n = Math.max(1, Math.min(20, Number(args?.limit ?? 8) || 8));
      const argv = ['search', '--limit', String(n)];
      const scopeArg = normScope(args?.scope);
      if (scopeArg && scopeArg !== 'global') argv.push('--scope', scopeArg);
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
  // `runMaintenance` 存在但**不是定时器**：只有 phase 是 idle 时能同步抢占，
  // 否则**同步抛错**（already has active work）。所以这里必须 try/catch——
  // 忙的时候不是错误，只是"这轮跳过"。抢不到也没关系：下一轮还会来，
  // 而且四道门本身幂等（指纹 + 文件状态），漏一次可补。
  ctx.on('agent/turn-stopping', async ({ agent } = {}) => {
    if (!state.ready && !(await ensureReady())) return;
    try {
      if (agent && typeof agent.runMaintenance === 'function') {
        await agent.runMaintenance(async () => {
          await enqueueMaintenance('turn-stopping');
        });
      } else {
        // 没有 runMaintenance 的宿主：直接排进串行链（间隔门控保证不会每轮真跑）
        await enqueueMaintenance('turn-stopping');
      }
    } catch (e) {
      // "already has active work" 是正常路径，不是故障
      ctx.logger.debug(`eco-memory: 维护跳过（agent 忙或不可用）: ${e?.message ?? e}`);
    }
  });
}

export { name, inject };

// 供测试直接验证渲染口径（与核心 retrieve.render_catalog_text 必须逐字节一致）
export const __test__ = { renderCatalog, resolveRoot, normScopeOf: (v) => String(v || '')
  .toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[-._]+|[-._]+$/g, '').slice(0, 40) };


