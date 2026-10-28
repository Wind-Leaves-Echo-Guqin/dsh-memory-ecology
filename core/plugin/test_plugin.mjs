/**
 * eco-memory-dsh 断言集（v3.2.0 · C5：每条闭环一个可执行检查）。
 *
 * 不依赖模型会话：用 stub ctx 驱动 apply()，**真实调用 Python 核心**。
 *
 * 覆盖：
 *   T2   warm 目录文本在两次 assembly 之间**字节不变**（成本命门）
 *   T3   `agent/created` 触发整合，条目进入目录（**不是** agent/session-start——那个事件
 *        在 dsh 0.2.1-rc.x 上根本不存在，监听它是静默死代码）
 *   T3b  重复触发不产生重复条目
 *   T4   间隔门控生效（连续事件不重复记账）
 *   T5   memory_load / memory_search 返回可读结果
 *   T6   fail-open：解释器缺失 / 核心缺失 / 根只读，都不抛异常
 *   T7   注入写审计账本
 *   T8   多圈隔离（作用域遮蔽段）+ 无 cwd 降级
 *   T9   目录体积受字符预算硬约束
 *   T11  渲染口径与核心 `retrieve.render_catalog_text` **逐字节一致**（防双口径漂移）
 *   T12  section 注册带 `interpolate:false`（否则目录里的 {{...}} 会让装配抛错）
 *   T13  turn-stopping 维护：agent 忙（runMaintenance 抛错）时安全跳过
 *   T14  物化文件 schema 不符 → 不注入（宁可少注入，不可注入旧格式）
 *
 * 运行：node test_plugin.mjs
 */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, existsSync, writeFileSync, mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRCPATH = path.join(HERE, '..', 'src');
const PY = process.env.ECO_PYTHON || 'python';

// ── stub ctx ────────────────────────────────────────────────────────────
function makeCtx() {
  const cap = { sections: {}, tools: {}, handlers: {}, logs: [] };
  const logger = {
    info: (m) => cap.logs.push(['info', m]),
    warn: (m) => cap.logs.push(['warn', m]),
    debug: (m) => cap.logs.push(['debug', m]),
  };
  const ctx = {
    logger,
    systemPrompt: {
      section: (reg) => { cap.sections[reg.name] = reg; return () => {}; },
    },
    tools: { register: (def) => { cap.tools[def.name] = def; return () => {}; } },
    on: (ev, fn) => { (cap.handlers[ev] ||= []).push(fn); return () => {}; },
  };
  return { ctx, cap };
}

/** 直接调核心 CLI（与插件走同一个入口）。 */
function core(args, root, env) {
  const out = execFileSync(PY, ['-m', 'memory_ecology', '--root', root, ...args], {
    encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: SRCPATH, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', ...(env || {}) },
  });
  return JSON.parse(out.trim().split(/\r?\n/).pop());
}

const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push([name, 'ok', '']);
    console.log(`ok   ${name}`);
  } catch (e) {
    results.push([name, 'FAIL', e.message]);
    console.log(`FAIL ${name}\n     ${e.message}`);
  }
}
const settle = (ms = 1500) => new Promise((r) => setTimeout(r, ms));

// ── 主流程 ──────────────────────────────────────────────────────────────
const tmp = mkdtempSync(path.join(os.tmpdir(), 'eco-plugin-'));
const root = path.join(tmp, 'root');

process.env.ECO_HOME = root;
process.env.PYTHONPATH = SRCPATH;
process.env.ECO_PYTHON = PY;

const { apply, __test__ } = await import('./index.js');
const { ctx, cap } = makeCtx();
await apply(ctx, { reconcileIntervalMs: 0, maintenanceIntervalMs: 0, catalogLimit: 20 });

const GLOBAL_SECTION = () => cap.sections['eco:catalog'];

await test('T6 初始化后目录段被注册（name/order/interpolate）', () => {
  const s = GLOBAL_SECTION();
  assert.ok(s, 'section 未注册');
  assert.equal(s.name, 'eco:catalog');
  assert.ok(s.order >= 9000, 'order 应排在交付物引用之后');
  assert.equal(s.interpolate, false, 'interpolate 必须为 false（否则 {{...}} 会让装配抛错）');
  assert.equal(typeof s.text, 'function');
});

await test('空目录时 warm 段为空串（不注入噪音）', () => {
  assert.equal(GLOBAL_SECTION().text({}), '');
});

core(['deposit', '--session', 'test', '--text', '第一条可检索的经验', '--kind', 'lesson'], root);
core(['deposit', '--session', 'test', '--text', '第二条事实', '--kind', 'semantic'], root);

await test('T3 agent/created 触发整合，条目进入目录', async () => {
  const h = cap.handlers['agent/created']?.[0];
  assert.ok(h, '未订阅 agent/created');
  h({ agent: { session: { header: { cwd: '' } }, ctx: { systemPrompt: { section: () => {} } } }, source: 'startup' });
  await settle();
  const text = GLOBAL_SECTION().text({});
  assert.match(text, /记忆目录/, `目录未生成：${JSON.stringify(text)}`);
  assert.match(text, /第一条可检索的经验/);
});

await test('T3x 不再订阅不存在的事件 agent/session-start（死代码回归）', () => {
  assert.equal(cap.handlers['agent/session-start'], undefined,
    '又订阅了 agent/session-start —— 该事件在 dsh 0.2.1-rc.x 上不存在，是静默死代码');
  assert.ok(cap.handlers['agent/turn-stopping'], '未订阅 turn-stopping（唯一的维护时机）');
});

await test('T2 warm 目录文本在两次 assembly 之间字节不变', () => {
  const a = GLOBAL_SECTION().text({});
  const b = GLOBAL_SECTION().text({});
  assert.equal(a, b);
  assert.ok(a.length > 0);
});

await test('T9 目录体积受字符预算硬约束', () => {
  const text = GLOBAL_SECTION().text({});
  const lines = text.split('\n').filter(Boolean);
  assert.ok(lines.length <= 21, `行数 ${lines.length} 超限`);
  assert.ok(text.length <= 750, `字符数 ${text.length} 超过预算 600+头部`);
});

await test('T11 渲染口径与核心逐字节一致（防双口径漂移）', () => {
  // **必须带一条 ≥120 字符的长条目**：否则永远走不到截断分支，省略号口径就测不到
  // （变异实测：把 `…` 改成 `...`，夹具全短时 T11 仍然全绿）。
  core(['deposit', '--session', 't11', '--text',
    '长条目夹具：' + '这一段用于触发目录渲染的行截断与省略号口径。'.repeat(6),
    '--kind', 'semantic'], root);
  core(['gate', '1'], root);
  const payload = core(['catalog', '--limit', '20', '--max-chars', '0'], root);
  const longItem = (payload.items || []).find((it) => (it.chars || 0) >= 120);
  assert.ok(longItem, '夹具里没有 ≥120 字符的条目，测不到截断');
  assert.ok(String(longItem.line).endsWith('…'),
    `长条目没有用 U+2026 截断：${JSON.stringify(String(longItem.line).slice(-8))}`);
  assert.ok(!String(longItem.line).includes('...'), '用了 ASCII 三个点而不是省略号');

  const jsText = __test__.renderCatalog(payload, 20);
  const pyText = execFileSync(PY, ['-c',
    'import sys,json;sys.path.insert(0,sys.argv[1]);'
    + 'from memory_ecology import retrieve;'
    + 'p=json.loads(sys.stdin.read());'
    // 必须走 buffer：Windows 上 print() 会把 \n 翻成 \r\n，那会让"逐字节一致"变成
    // "跨平台不一致"，掩盖真正要防的格式漂移
    + 'sys.stdout.buffer.write(retrieve.render_catalog_text(p).encode("utf-8"))',
    SRCPATH], { encoding: 'utf8', input: JSON.stringify(payload) });
  assert.equal(jsText, pyText, 'JS 与 Python 的目录渲染不一致');
  assert.ok(jsText.includes('…'), 'JS 侧渲染丢了省略号');
});

let firstId = '';
await test('T5 memory_load 返回正文', async () => {
  const m = GLOBAL_SECTION().text({}).match(/mem-\d{8}-[0-9a-f]{8}/);
  assert.ok(m, '目录里没有可用的 id');
  firstId = m[0];
  const out = await cap.tools.memory_load.execute({ id: firstId });
  assert.match(out.result, /第一条可检索的经验|第二条事实/);
});

await test('T5b memory_load 未命中给出可读提示', async () => {
  const out = await cap.tools.memory_load.execute({ id: 'mem-nope' });
  assert.match(out.result, /没有这条记忆|读取失败|只读降级/);
});

await test('T10 memory_search 能找回目录里没有的条目', async () => {
  core(['deposit', '--session', 't', '--text', '冷门条目：量子色动力学与胶子禁闭', '--kind', 'semantic'], root);
  core(['gate', '1'], root);
  const out = await cap.tools.memory_search.execute({ query: '胶子禁闭', limit: 3 });
  assert.match(out.result, /命中 \d+ 条/);
  assert.match(out.result, /量子色动力学/);
});

await test('T10b memory_search 有命中就返回可 load 的 id', async () => {
  const out = await cap.tools.memory_search.execute({ query: '可检索', limit: 3 });
  const m = out.result.match(/mem-\d{8}-[0-9a-f]{8}/);
  assert.ok(m, `检索结果里没有 id：${out.result}`);
  const loaded = await cap.tools.memory_load.execute({ id: m[0] });
  assert.match(loaded.result, /可检索/);
});

await test('T3b 重复触发不产生重复条目', async () => {
  const before = core(['catalog', '--limit', '50'], root).total;
  const h = cap.handlers['agent/created'][0];
  h({ agent: { session: { header: { cwd: '' } }, ctx: { systemPrompt: { section: () => {} } } }, source: 'resume' });
  await settle();
  const after = core(['catalog', '--limit', '50'], root).total;
  assert.equal(after, before, `条目数变化：${before} -> ${after}`);
});

await test('T7 目录注入写入审计账本（source=eco-catalog）', async () => {
  const dbPath = path.join(root, 'eco.db');
  assert.ok(existsSync(dbPath), '账本不存在');
  const probe = execFileSync(PY, ['-c',
    'import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);'
    + 'print(c.execute("select count(*) from inject_event where source=\'eco-catalog\'").fetchone()[0]);c.close()',
    dbPath], { encoding: 'utf8' });
  assert.ok(Number(probe.trim()) >= 1, '目录注入没有写审计行');
});

await test('T14 物化文件 schema 不符时不注入（fail-open）', async () => {
  const p = path.join(root, '.eco', 'catalog', 'global.json');
  const saved = execFileSync(PY, ['-c', 'import sys;print(open(sys.argv[1],encoding="utf-8").read())', p],
    { encoding: 'utf8' });
  writeFileSync(p, JSON.stringify({ ok: true, schema: 999, items: [{ id: 'x', kind: 'a', line: 'b' }] }), 'utf8');
  try {
    // 触发一次物化读取（agent/created 会 readCatalogSync）
    const a = { session: { header: { cwd: '' } }, ctx: { systemPrompt: { section: () => {} } } };
    cap.handlers['agent/created'][0]({ agent: a, source: 'startup' });
    await settle(600);
    // 全局段仍是旧内容（未被 999 schema 污染）
    assert.doesNotMatch(GLOBAL_SECTION().text({}), /^- x │/m, '旧 schema 的物化文件被注入了');
  } finally {
    writeFileSync(p, saved, 'utf8');
  }
});

await test('T13 turn-stopping：agent 忙（runMaintenance 抛错）时安全跳过', async () => {
  const h = cap.handlers['agent/turn-stopping'][0];
  const busy = {
    runMaintenance: () => { throw new Error('agent "x" already has active work'); },
  };
  await h({ agent: busy });   // 不应抛
  const idle = {
    runMaintenance: async (task) => task(new AbortController().signal),
  };
  await h({ agent: idle });   // 也不应抛
});

// ── fail-open ───────────────────────────────────────────────────────────
await test('T6b 解释器不可用时不抛异常且不注入', async () => {
  const saved = process.env.ECO_PYTHON;
  process.env.ECO_PYTHON = path.join(tmp, 'no-such-python');
  const mod = await import(`./index.js?bust=${Date.now()}`);
  const { ctx: c2, cap: cap2 } = makeCtx();
  await mod.apply(c2, {});
  const s = cap2.sections['eco:catalog'];
  assert.ok(!s || s.text({}) === '', '解释器不可用时仍注入了内容');
  assert.ok(cap2.logs.some(([lvl]) => lvl === 'warn'), '降级没有留下告警日志');
  process.env.ECO_PYTHON = saved;
});

await test('T6c 显式 core 路径坏掉时回退到包内 src（不静默失效）', async () => {
  const saved = process.env.ECO_CORE;
  process.env.ECO_CORE = path.join(tmp, 'nope.py');
  const mod = await import(`./index.js?bust=${Date.now() + 1}`);
  const { ctx: c3, cap: cap3 } = makeCtx();
  await mod.apply(c3, { corePath: path.join(tmp, 'nope.py') });
  const s = cap3.sections['eco:catalog'];
  assert.ok(s, '仍应注册段（fail-open）');
  assert.match(s.text({}), /记忆目录/,
    '显式 core 路径坏掉后没有回退到包内 src —— 会变成"装了但永远不工作"');
  process.env.ECO_CORE = saved;
});

// ── T8 多圈（作用域遮蔽）──────────────────────────────────────────────
function fakeAgent(cwd) {
  const box = { reg: null };
  return {
    session: { header: { cwd } },
    ctx: { systemPrompt: { section: (reg) => { box.reg = reg; return () => {}; } } },
    _box: box,
  };
}

await test('T8 不同项目圈看到各自的目录，且都包含全局条目', async () => {
  core(['deposit', '--session', 't', '--text', '全局约定：提交前必须跑全套测试', '--kind', 'lesson'], root);
  core(['deposit', '--session', 't', '--text', '项目甲：部署用 blue-green', '--kind', 'procedural', '--scope', 'proj-alpha'], root);
  core(['deposit', '--session', 't', '--text', '项目乙：数据库固定用 sqlite', '--kind', 'semantic', '--scope', 'proj-beta'], root);
  // **圈路由必须由核心声明**（不是插件按目录名猜）。
  // 这是安全修复：按 basename 判圈会让 `D:/work/alpha` 看到 `alpha` 圈的机密。
  const cfg = {
    global_budget_chars: 600,
    circles: [
      { id: 'proj-alpha', match: { cwd_prefix: ['D:/proj-alpha'] }, budget_chars: 600 },
      { id: 'proj-beta', match: { cwd_prefix: ['D:/proj-beta'] }, budget_chars: 600 },
    ],
  };
  const cw = core(['circles-write', '--json', JSON.stringify(cfg)], root);
  assert.ok(cw.ok, JSON.stringify(cw));
  core(['gate', '1'], root);

  // 真实顺序：数据先存在 → 会话边界物化 + 刷新路由表 → agent 创建时同步读文件
  cap.handlers['agent/created'][0]({
    agent: { session: { header: { cwd: '' } }, ctx: { systemPrompt: { section: () => {} } } },
    source: 'resume',
  });
  await settle(2500);

  const a = fakeAgent('D:/proj-alpha');
  const b = fakeAgent('D:/proj-beta');
  cap.handlers['agent/created'][0]({ agent: a, source: 'startup' });
  cap.handlers['agent/created'][0]({ agent: b, source: 'startup' });
  await settle(1200);

  const ta = a._box.reg ? a._box.reg.text({}) : '';
  const tb = b._box.reg ? b._box.reg.text({}) : '';

  assert.ok(a._box.reg, 'agent alpha 没有注册遮蔽段');
  assert.ok(b._box.reg, 'agent beta 没有注册遮蔽段');
  assert.match(ta, /blue-green/, `alpha 目录缺自己的条目：${ta}`);
  assert.match(ta, /全局约定/, 'alpha 目录缺全局条目');
  assert.doesNotMatch(ta, /sqlite/, `alpha 看到了 beta 的记忆：${ta}`);

  assert.match(tb, /sqlite/, `beta 目录缺自己的条目：${tb}`);
  assert.match(tb, /全局约定/, 'beta 目录缺全局条目');
  assert.doesNotMatch(tb, /blue-green/, `beta 看到了 alpha 的记忆：${tb}`);
});

await test('T8x 未声明的目录**不会**按目录名猜出一个圈（安全回归）', async () => {
  // 这是审查抓到的跨圈泄漏：配置里只有 proj-alpha/proj-beta，
  // 若插件按 basename 判圈，`D:/work/alpha` 就会被判成圈 alpha 并注入其机密。
  const a = fakeAgent('D:/work/alpha');
  cap.handlers['agent/created'][0]({ agent: a, source: 'startup' });
  await settle(400);
  assert.equal(a._box.reg, null,
    '未声明的目录被按目录名判成了圈 —— 会泄漏别的圈的记忆');
});

await test('T8b 遮蔽段名/order/interpolate 与全局段一致（靠同名遮蔽）', () => {
  const a = fakeAgent('D:/proj-alpha');
  cap.handlers['agent/created'][0]({ agent: a, source: 'startup' });
  assert.equal(a._box.reg.name, GLOBAL_SECTION().name);
  assert.equal(a._box.reg.order, GLOBAL_SECTION().order);
  assert.equal(a._box.reg.interpolate, false);
});

await test('T8c 无 cwd 的 agent 不注册遮蔽段（降级为全局）', () => {
  const c = fakeAgent('');
  cap.handlers['agent/created'][0]({ agent: c, source: 'startup' });
  assert.equal(c._box.reg, null);
});

// ── T15 记忆包 mount 段 ────────────────────────────────────────────────
await test('T15 有已挂载 pack 时出现 eco:packs 段（只读引用）', async () => {
  const packDir = path.join(tmp, 'pack-demo');
  mkdirSync(path.join(packDir, 'entries'), { recursive: true });
  const body = '外部只读知识：tomllib 是 3.11+ 的标准库。';
  writeFileSync(path.join(packDir, 'entries', 'a.md'), body, 'utf8');
  const sha = execFileSync(PY, ['-c',
    'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())',
    path.join(packDir, 'entries', 'a.md')], { encoding: 'utf8' }).trim();
  writeFileSync(path.join(packDir, 'manifest.json'), JSON.stringify({
    id: 'demo', version: '1.0.0', provenance: 'test', kind: 'readonly-pack',
    whitelisted: true, entries: [{ path: 'entries/a.md', sha256: sha, title: 'tomllib 常识' }],
  }), 'utf8');
  const out = core(['pack', 'install', '--dir', packDir, '--mode', 'mount'], root);
  assert.ok(out.ok, JSON.stringify(out));

  const mod = await import(`./index.js?bust=${Date.now() + 7}`);
  const { ctx: c4, cap: cap4 } = makeCtx();
  await mod.apply(c4, { catalogLimit: 20 });
  await settle(1200);
  const s = cap4.sections['eco:packs'];
  assert.ok(s, '未注册 eco:packs 段');
  assert.match(s.text({}), /tomllib 常识|外部只读记忆包/, `packs 段内容：${JSON.stringify(s.text({}))}`);
});

console.log('\n==== 汇总 ====');
const failed = results.filter((r) => r[1] !== 'ok');
console.log(`${results.length - failed.length}/${results.length} passed`);
if (failed.length) {
  for (const [n, , m] of failed) console.log(` FAIL ${n}: ${m}`);
}
rmSync(tmp, { recursive: true, force: true });
process.exit(failed.length ? 1 : 0);
