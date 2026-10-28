# 记忆生态 v3.2.0 · 架构

> 一句话：**这是一个治理记忆生死的系统，不是"存更多、召回更准"的系统。**
> 四道门（写入整合 / 配额巩固 / 画像蒸馏 / 复核降级）+ 单轴状态机 + 永不物理删除 + 可回滚。

---

## 1. 分层

```
L3  dsh 内核原语            ctx.systemPrompt / tools / agents / runMaintenance
                            ← 只用，不自造
L2  dsh 插件（plugin/）     只决定「什么内容 / 什么形态 / 什么时点 / 多少预算」
                            不含任何领域规则
L1  核心（src/memory_ecology/）  记忆领域语义：门、状态机、检索、圈、包
                            **stdlib only，零宿主依赖，可脱离 dsh 单测**
L0  数据根（一个目录）       候选 + detail + 账本 + 物化视图
```

**硬边界（机制保证，不靠纪律）**：
- L1 里 `grep -E "hermes|state\.db|jobs\.json|cron"` 为空（由 `tests/test_core.py` 钉住）。
- L2 里没有相似度/状态迁移判断——它只会拼 argv、读 JSON。
- L2 调 L1 只有一个入口：`python -m memory_ecology <cmd> --root <R> ...`，JSON 进 JSON 出。

---

## 2. 数据根布局

```
<root>/
  .eco/root.json              {root_id, schema_version, created_at}   ← 根自证
  .eco/catalog/<scope>.json   物化视图：每圈一份目录（插件热路径只读它）
  .eco/catalog/index.json     {schema, scopes[], generated_at}
  .eco/.write-probe.<pid>     写前自证探针（**名字必须带 pid**）
  circles.json                圈声明（可缺省：缺省 = 按启动目录末级名自动划分）
  packs.json                  记忆包登记（可缺省）
  memories/MEMORY.md          L1 主记忆（§ 分隔，配额 3000 / 触发线 2550）
  memories/USER.md            L1 画像（配额 1500 / 触发线 1275）
  memories/detail/<id>.md     L2，一条一个文件（frontmatter + 正文 ≤800）
  memories/pending/<scope>/   候选（append-only；圈由目录天然表达）
  memories/pending/*.done.md  已消费候选（改名留存，不删）
  memories/quarantine/        隔离区（**只复制/移动进来，从不删除**）
  memories/user_candidates/   门③ 画像候选（observing / promoted / rejected）
  memories/_bak-archive/      超期备份归档（保留策略的落点，**不是删除**）
  eco.db                      账本（见 §5）
```

**根解析优先级**（`paths.resolve_root()`，每次调用现算 → 运行期可切）：
1. 显式 `--root`
2. 环境变量 `MEMORY_ECOLOGY_ROOT`（**进程级**，不是用户级/全局）
3. `~/.dsh/memory-eco-root`（一行路径，给"想固定但不想设 env"的人）
4. 布局派生：Windows `%LOCALAPPDATA%\dsh\memory-eco` / POSIX `~/.local/share/dsh/memory-eco`

> **红线**：任何代码都不得写用户级/全局环境变量。根的选择只能来自上面四条，写前必过自证。

---

## 3. 四道门（v3.2.0 的核心交付）

### 3.1 门① 写入整合 `gates.integrate()`

候选 → detail。四步：**指纹去重 → 同源合并 → 类型归一 → 取代链**。

- **指纹 = `sha1("cand", scope, norm_text(body))`**。含 scope 是刻意的：同一句话在两个
  项目里是两条独立记忆。只按内容做指纹会把跨圈同名条目塌成一条（实测：proj-a / proj-b
  各投一句同样的话，只入库一条，另一个项目永远看不到自己的那条）。
- **同源合并**：只在**同圈内**比对（跨圈绝不合并——同一个词在两个项目里可能指不同东西）。
  `similarity.ratio ≥ MERGE_THRESHOLD(0.86)` → 合并到旧条目（`occurrences+1`、
  `session_count` 取 max、`last_verified` 刷新），**不产生第 2 条**。
- **类型归一**：`fact→semantic`、`decision→procedural`、`error→lesson`…（别名只做单向归一，
  避免以后挂载旧库时每条都退化成同一类）。未知类型落 `semantic`，**不丢条目、不造新词**。
- **取代链**：`supersede(old, new, reason)` 只改 frontmatter（`fate=superseded`、
  `status=archived`、`superseded_by/reason/at`），**文件原地保留**。`restore_superseded()`
  是它的逆操作，且逆操作本身被测（C4）。

### 3.2 门② 配额巩固 `gates.quota()`

L1 视图由核心计算：**提升 L2→L1** 与 **超限挤出 L1→L2**。

**为什么旧实现 promote 恒为 0**（已查清，不再靠猜）：
- 快路径要求 `occurrences≥3 且 session_count≥2`，而 detail 真实分布是
  **228/229 条 occurrences=1**（本次实测）。这两个字段只有"同一事实再被写入且命中相似合并"
  才会增长，而相似合并本身依赖词面命中——**环**。
- 慢路径要求 L2 在 L1 文本里有同源条目才可提升；而 L1 是"只被挤出、从不被写入"的单向漏斗，
  所以慢路径同样空转。

**v3.2.0 的修法**：提升判据**不再依赖 L1 已有同源条目**，三条任一成立即可
（且 `status=active` 且非冷却期）：

| 判据 | 条件 | 语义 |
|---|---|---|
| A | `occurrences≥3 且 session_count≥2` | 旧快路径（保留） |
| **B** | 驻留 `≥14 天` 且 **被检索命中 ≥2 次** | **新**：有使用证据就够 |
| **C** | `occurrences≥2` 且 驻留 `≥14 天` | **新**：被重申过且沉淀够久 |

> B 是这次的关键：**"记忆被用过才算数"是正面判据**，而"必须先在 L1 里"是循环判据。

**防震荡**（照抄旧实现的三条，实测有效）：
1. 挤出时优先排非高价值条目（高价值 = 本身可提升的）；
2. 挤出落 L1 的条目有 `EXTRUDE_COOLDOWN_DAYS(30)` 冷却期，不立刻提升回来；
3. 本轮刚提升的条目在同轮挤出里被保护；若仍超限则**撤销本轮提升**。

**挤出绝不静默丢弃**：每条被挤出的条目先保证 detail 有同源副本，没有就现写一条
（`origin_session_id=extrude-from-L1`）。写前备份 `MEMORY.md.bak-配额前-<ms-pid>`。

**不提升时必须能回答"为什么"**：返回 `blocked[]`（`no-criterion` / `type-not-promotable` /
`cooldown` / `already-in-L1` / `promote-reverted-over-quota`），GUI G2 直接显示。

### 3.3 门③ 画像蒸馏 `gates.distill()`

跨会话稳定事实 → USER 画像候选 → （观察期 **或** 交叉印证）→ USER.md。

**为什么旧实现 89 候选 0 晋升**（已查清）：64 个候选**全部** `status: observing`、
`created` 全是 10-05..10-25（最新距今 2 天），而 `OBSERVE_DAYS=30`——**没有一条通过观察期**。
这不是 bug，是"30 天观察期 + 只跑了 20 天"。真正的问题是**没有任何旁路**。

**v3.2.0 的修法**：保留观察期，加**交叉印证旁路**——`corroborate≥2` 个不同来源 ⇒ 可提前晋升。
并且把"为什么没晋升"写成 `blocked[]`（`observing` 带 `age_days` / `need_days`；`source-gone`；
`similar-not-same` 带 ratio；`batch-limit`）落账。

**LLM 只做措辞**：规则判稳，`llm=None` 时用原文截断。LLM 抛异常也照常跑（**绝不因为没网就不动**）。
**同义替换阈值 0.80**：≥0.80 整块替换（旧条目进 quarantine）；0.60–0.80 相似但非同义 → **不替换**
（防误伤行为规则）。

### 3.4 门④ 复核降级 `gates.review()`

遗忘曲线：`active → dormant → archived`。阈值 = `semantic/procedural/lesson/map 90 天`、
`episodic 30 天`。

**两个修复**：
1. **`last_verified` 由"被使用"刷新**（检索命中 / 加载即视为已验证）。旧实现"从不刷新"，
   导致**最活跃的记忆反而被归档**——它没被"验证"过。这是本项目最反直觉的一个 bug。
2. **窗口内被命中的条目免降级**（使用驱动休眠）。

**永不物理删除**：只把 `status` 改成 `archived` / `dormant`，文件留在原处，进目录时被过滤。

**零动作也要留痕**：没有降级时写一行 `review_log(action='verify')`——否则 GUI 会以为门没跑。

---

## 4. 一次跑全部门 `gates.maintain()`

顺序**有讲究**：先整合（产条目）→ 巩固（挑得出来的）→ 蒸馏（挑稳定的）→ 复核（降级用不到的）。
反过来会把刚进来的条目当场降级。

---

## 5. 账本（`eco.db`）

| 表 | 列 | 谁写 | 谁读 |
|---|---|---|---|
| `gate_log` | ts, fingerprint, action, target, note | 门① | GUI G2 |
| `quota_log` | ts, action, slug, l1_preview, reason | 门② | GUI G2 |
| `distill_log` | ts, action, target, note | 门③ | GUI G2 |
| `review_log` | ts, action, slug, reason | 门④ | GUI G2 |
| `inject_event` | ts, session, source, form, items, chars, ids, tokens | catalog/load/search | GUI G3 |
| `hit` | ts, id, kind, query | load/search | 目录排序 + dead_rate |
| `candidate` | fp, id, kind, scope, text, session, first_seen | 门① | 去重 |
| `event` | ts, kind, target, detail | 各处 | 审计 |
| `cost` | ts, session, tools_schema, system_prompt, catalog, injected_msgs, injected_chars | measure | GUI G6 |
| `pack` | id, version, kind, mode, enabled, hash, provenance, path, … | packs | GUI G5 |

**`idle` 与真实动作必须分开统计**。旧项目的病就是 `quota_log` 246 行 idle 把 52 行 extrude 淹了，
导致"门在跑"看起来像"门在工作"。`ledger.gate_status()` 只数 effective；`idle` 只用来区分
**两种红灯**：

| kind | 含义 | 处置 |
|---|---|---|
| `dead` | 账本一行都没有 → **根本没跑** | 查调度/进程 |
| `idle-spin` | 有行但全是 idle/自检 → **进程活着、没产出** | 查判据/输入 |
| `stale` | 有有效动作但太久 | 查最近是否停摆 |
| `ok` | 正常 | — |

---

## 6. 上下文三档（成本结构）

| 档 | 载体 | 内容 | 预算 | 允许的变化频率 |
|---|---|---|---|---|
| **warm** | `systemPrompt.section(order=9100, interpolate=false)` | 本圈目录 ≤20 条 | ≤600 字符 | **≤1 次/会话**（内容不变就不重建） |
| **cold** | 工具 `memory_load(id)` / `memory_search(q)` | 条目正文 | 按需 | 不限 |
| ~~hot~~ | — | **已取消** | — | 见 §9 |

**注入 KPI：≤3 条消息 / ≤1,200 字符每会话。** 本插件默认 **0 条追加**（目录进前缀）。

**为什么必须物化成文件**：实测 dsh 在 `agent/created` 之后 **~10ms** 就装配系统提示词，
子进程取数要 80ms。任何"异步取数后回填段文本"的方案都输。物化视图**不是优化，是正确性要求**。

**`interpolate: false` 是必须的**：`text` 默认 true，目录正文里只要出现 `{{...}}` 就会被当模板
变量解析，未知变量直接让**整个装配抛错**。

---

## 7. 多生态圈

**圈 = 同一数据根里的 scope，不是多个数据根。** 理由只有一个：多根 = 多门多 cron = N 个写者，
直接违反"唯一写者"。

- 圈由投递目录天然表达（`pending/<scope>/`），不改候选行格式，不需要额外索引；
- `circles.json` 声明式配置：`{id, display_name, match.cwd_prefix[], budget_chars, catalog_limit, readonly, sources[]}`；
- 路由 = **cwd 前缀最长匹配** → 最近祖先圈 → 都不中则 global；Windows 下忽略大小写
  （否则同一个项目会因 shell 起法不同裂成两个圈）；比较按**路径段边界**（`proj2` 不会并进 `proj`）；
- 宿主侧用**同名段遮蔽**（nearest layer wins）：每个 agent 在自己作用域注册 `eco:catalog`，盖掉全局段。

---

## 8. 记忆插件化（原计划 3.3.0，本轮版本号仍是 3.2.0）

两种接入：

| 模式 | 语义 | 违反什么？ |
|---|---|---|
| **mount**（挂载） | 只读引用外部 pack，立即可用，**不写、不过门** | 无——零写入，不违反单写入方 |
| **integrate**（吸收） | 内容投 `pending/` → 门① → 变本库记忆（有取代链、可回滚） | 无——走同一道门 |

**为什么必须两种**：旧契约只允许"投候选→等门①"，而门①是批量/事件驱动的，"装了插件当天没用"
是必然的。mount 补上即时性。

**安全边界（写进实现，不是写进文档）**——第三方 pack 本质是 prompt injection 通道：
1. **只读**：mount 只把内容渲染进目录，不落 detail、不写 L1、不写 hit；
2. **来源白名单**：manifest 必须 `whitelisted: true` 且带 `provenance`；
3. **内容哈希**：每篇声明 sha256，不符即**整体拒载**（不留半加载状态）；
4. **不可执行**：`kind` 只接受 `readonly-pack`；
5. **装/卸只改状态位**：物理文件永远保留（红线 1）；
6. **注入打标**：疑似指令注入的内容加"非指令"免责声明。

---

## 9. 被六条判据枪毙的方案（别复活）

| 方案 | 为何不闭环 |
|---|---|
| **逐块驱逐 + 尾部回执** | 上下文本来就 append，没有"驱逐"这个动作；C1/C3 都不过 |
| **多生态圈 = 独立数据根 + 独立 cron** | N 个写者，直接违反 C1 |
| **读时物化 + append-only 自研** | 宿主已有 `sessionProjections`（纯 fold + 水位 + checkpoint + 变更 feed），自造必然 C5 不过 |
| **独立 hot 档快照** | 与 warm 目录写同一批条目 → 两处状态必然漂移（C1）；且上下文 append-only，每变一次多一条消息 |

**hot 档的替代**：把"主/副记忆"表达为**同一个目录段内的排序**——主 = 目录前几条
（命中次数 → 新旧），副 = 目录外的全部，靠 `memory_search` 按需取。读者、写者、渲染路径
都只有一个。

---

## 10. 六条闭环判据（任何提案先过这六条）

| # | 判据 | 检验方法 |
|---|---|---|
| C1 | **唯一写者** | 能指出唯一写入位置；并发写不产生两种合法结果 |
| C2 | **具名读者** | 能指出谁消费；**无消费者的字段/接口 = 断链** |
| C3 | **可测失败模式** | 失败留痕（账本/日志/notice），不静默 |
| C4 | **可回滚** | 有反向操作或备份，且反向操作本身被测试 |
| C5 | **有断言** | 自动化测试钉住行为，不靠文档约定 |
| C6 | **不依赖纪律** | 正确性由机制保证，不靠"请勿……""记得……" |

**这条项目历史上最贵的一次教训**：`eco.toml [quota] memory_limit` 被 `REQUIRED_CONFIG` 断言
在场，但**没有任何代码读它**——改配置没反应，直到有人花半天查明。所以现在
「加一个配置键，就必须在同一提交里加它的读取点」由 `tests/test_core.py::test_config_keys_have_consumers`
用 AST 扫描变成断言。

---

## 11. 第二轮对抗审查修掉的缺陷（全部动态复现过）

交付前跑了一轮 OCR 级对抗审查 + 变异测试，抓到 7 个 blocker / 11 个 major。每条都写了
回归断言（`tests/test_regressions.py`，28 条）。这里记下来，因为**它们都是"写的时候觉得对"的错**。

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| B1 | `mark_consumed` 只按**文件名**过滤 | 同名不同圈的两个候选只整合一个，另一个也被改名 `.done.md` → **那个圈的记忆永久丢失** | `only` 改传**相对路径**；`scope_of_file` 显式收 `pending_root`（圈名可以合法地叫 `pending`，任何"往上找 pending 目录"的启发式都会算错） |
| B2 | 插件按**目录末级名**判圈 | 配置里 `alpha` 只匹配 `D:/work/secret-project`，但 `D:/work/alpha` 会被判成圈 `alpha` → **无关项目看到别的圈的机密** | 删掉 basename 判圈；改查核心 `circles` 预先算好的路由表。**没声明就不猜** |
| B3 | pack 只检查 body，`title` 直通系统提示词 | title 里塞"忽略之前的所有指令"能零告警通过 | title 一起过 `SUSPICIOUS`；命中则打标（mount）或拒收（integrate） |
| B4 | pack `integrate` 把不受信内容洗成本库记忆 | 同一条内容走 mount 有免责、走 integrate 就变成"用户自己的记忆" | 命中注入模式的条目**拒绝 integrate**（返回 `integrate_skipped_suspicious`） |
| B5 | `circles-write --file` 读不到时 `or "{}"` | 一条错误命令**静默清空全部圈配置**（有备份但信号是"成功"） | 读不到/空文件/无输入一律 `{ok:false}`；fail-closed |
| B6 | 指纹/截断口径三处不同 | 配置调小 `body_max` 后候选**永远不被消费**（pending 无限堆积）；老库无 `fp` 的条目被"相似合并"而非去重 | 抽 `store.candidate_fp(scope, text, body_max)` **唯一实现**，三处共用 |
| B7 | `session_count = max(现有, 1)` —— 恒为 1 | 门② 的 A 判据（`sess>=2`）**在自己的实现里结构性不可达** —— 立项要修的病被复刻了一遍 | 记 `sessions` 会话集合指纹，按集合大小计数；**同会话重申不算多个会话** |
| M1 | `memory_load` 的 id 直接拼路径 | `load ../PRIVATE` 读根内其它文件，`../../secret` 读根外文件 | id 白名单正则；`write_item` 同样校验 |
| M2 | `restore_superseded` 无状态前置 | 对门④ 归档的条目 restore 会**直接复活**，抹掉归档痕迹 | 只接受 `fate=superseded`；复活归档走 `bring_back()`（留账） |
| M3 | `supersede` 允许自指 | 条目把自己标成 archived 且 `superseded_by` 指向自己 | 显式拒绝 `self-reference` |
| M4 | 配置键名读错（`GATE_STALE_DAYS` vs `_DEFAULT`） | 配 `[gate] gate_stale_days=30` 恒返回 3.0 —— **死配置**，正是本项目最贵的那个教训 | `_ALIASES` 映射 + `get()` 统一解析；断言"配了真的生效" |
| M5 | 挤出排序 `-idx` | 把**最新**的条目排到最前先挤出，与注释的"近似 LRU"正好相反 | 同类内 `idx` 升序（越老越先挤） |
| M6 | 挤出落 detail 硬编码 `scope="global"` | proj-a 的条目经 L1 挤出后**所有圈可见** | 从原条目带出圈归属，查不到才退 global |
| M7 | `corroborate` 写死 `"1"` 且无自增点 | "交叉印证可提前晋升"这条旁路**只能靠手工改文件触发** | 初值 = 条目的真实会话集合大小；`_corroborate_candidate` 并入真实会话 |
| M8 | rejected 候选只写日志不改名 | 同一个死候选每轮重复 rejected，**账本被噪音淹没** | `_mark_candidate(cf, "rejected")` 标记终态 |
| M9 | `os.replace` 无重试 | Windows 上并发写同一目标抛 `WinError 5` | `_replace_with_retry`（指数退避）；**绝不"先 unlink 再 replace"** |
| M10 | `read_text` 用默认换行翻译 | CRLF 的 MEMORY.md 被读成 LF，写回时**整文件 diff** | `open(..., newline="")` 保留原换行风格 |
| M11 | KPI 把全量注入算进本插件 | 同一会话本插件 368 字符、全量 92k → **永远超标**，告警一响就没人看 | `cost` 表增 `eco_msgs/eco_chars` 列；KPI 只对本插件注入源成立 |
| M12 | GUI `_utf8_env` 是 dict 却被当函数调用 | 9 个写动作里 7 个在 spawn 前就 `TypeError` | 修调用；并把 `MEMORY_ECOLOGY_ROOT` 透传给子进程（否则沙盒 GUI 的写会落到生产根） |
| M13 | GUI 前端 `$('#refresh-btn')`（HTML 里是 `btn-refresh`） | 顶层 `null.onclick` 抛 TypeError → `route()` 永不执行 → **页面永远停在"正在读取…"** | 绑定全部改 `BINDINGS` + 存在性检查；启动自检把缺失 id 打到控制台；加静态断言 `test_index_bindings_match_dom_ids` |
| M14 | `agent/session-start` 在 dsh 0.2.1-rc.x **不存在** | 监听它是**静默死代码**（cordis 对未派发事件不报错不告警）→ "会话开始补账"从未生效 | 改 `agent/created`（payload 带 `source`）+ `agent/turn-stopping`；插件测试 T3x 钉住"不许再订阅那个事件" |
| M15 | `systemPrompt.section` 未设 `interpolate: false` | 目录正文里的 `{{...}}` 被当模板变量 → **整个装配抛错** | 两个段都显式 `interpolate: false` |

---

## 12. 已知边界（诚实清单）

1. **检索是词面的**：中文按 2-gram + 整串，短查询（2 字）与同义改写会漏。embedding 后端留了
   接口（`similarity._embedding_ratio`），默认关闭，缺依赖自动回退 ngram。
2. **目录排序只看命中次数与新旧**，不看语义相关性。新任务若与历史无关，应主动 `memory_search`。
3. **本次会话新增的条目要到下次会话才进目录**（装配在 10ms 内完成，数据要等 reconcile + materialize）。
4. **`hit` 表会增长**：有 `prune_hit()`（默认保留 180 天）+ `prune` 子命令，但没有自动定时调用——
   由维护脚本/人决定。
5. **`distill` 的 USER 替换只按相似度判定，"语义是否矛盾"仍靠人**（没有 LLM 终审）。
6. **门④ 的"使用"信号来自 `hit` 表**，只覆盖通过插件的检索/加载；其它宿主的读取不记账。
