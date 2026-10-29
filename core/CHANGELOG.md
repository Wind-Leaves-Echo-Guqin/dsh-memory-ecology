# 变更记录

版本号唯一权威来源：`src/memory_ecology/__init__.py: __version__`。
一致性由 `python -m memory_ecology version` 校验（四处：核心 / pyproject / plugin/package.json / 本文件）。

---

## v3.2.2 —— 2026-10-29

五条治理缺陷修复。全部**独立复核**（不采信模拟验收报告结论）：宿主根因从
`app.asar` 里解出 `dsh-agent-loop` 源码核对，每个缺陷都写了可执行复现脚本实跑，
修完再跑同一脚本确认"不再复现"。

### 修了什么

- **H1 门②③④ 结构性从不运行**（最严重）。根因是宿主调用顺序：
  `turn()` 内部派发 `agent/turn-stopping` → turn 返回 → `kick().finally` 才切 idle，
  所以事件处理器运行时 `phase.kind` 必然是 `running`；而
  `runMaintenance(job)` 在非 idle 时**同步抛**。插件每轮必抛、catch 只写 debug，
  于是 `gate all`（门②③④ 的唯一调用点）**从未执行**，三张账本恒为空。
  → 不再抢 phase，直接 `enqueueMaintenance` 进插件串行链；catch 提升为 `warn`
  （旧实现把所有失败都藏起来了）。实测 200 条 detail 上 `gate all` 0.27s。
- **H2 损坏即隔离**。宽松解析对坏 frontmatter 返回 `({}, 全文)`，`scope` 因此回落
  成 `"global"`——**最宽的取值**：损坏一个字节就把圈内机密单向放宽成全局可见；
  紧接门④ 又拿"全文"当正文回写，造出两个 frontmatter 块与矛盾 `status`。
  → `store.damage_reason()` 判定；损坏条目不进目录/检索/门，`root.item()` 拒绝读，
  `health` 增加 `damaged[]` 并置 `warn`。口径刻意保守：只认"明确写了 frontmatter
  但坏了"，无 frontmatter 的老文件不算损坏（既有合法形态，有用例钉住）。
- **H3 `merged` 分支不消费候选**。候选指纹不进 `known` → 文件永远判"未消费"、
  pending 永久卡住；且下次 reconcile 又把同一句话当未知再 `+1 occurrences`，
  **不新增任何候选也能灌水**（实测 2→3→4→5），而门② 拿 occurrences 当提升判据
  → `hits=0` 的条目照样进 L1。破坏了"幂等"这个已写进文档的承诺。
  → `merged` 分支补 `known.add(fp)`；已消费文件指纹剔出 `known`；
  新增 `store.candidate_lines()` 让"判重"与"判消费"共用同一口径。
- **H4 冷路径无圈隔离**。核心 `--scope` 语义本来是对的，缺陷全在插件：
  `memory_search` 只认 `args.scope`（省略即全库），`memory_load` 干脆不校验，
  而插件在 `agent/created` 早已算出 `state.circle` 却没用。
  → 两者都用 `state.circle` 兜底；显式 `scope=global` 仍放行（不误伤明确意图）；
  跨圈 `load` 回"没有这条记忆"，不确认其存在，避免被用来探测圈名。
- **新发现（报告与交接文档都没有）：`dormant` 永远回不到目录**。目录只收
  `status == "active"`，而 `review()` 的 `used_recently` 分支只刷新
  `last_verified`、**不改 status**；`bring_back()` 有实现却**没有任何 CLI 入口**。
  三者合起来 = 记忆被反复使用仍不在上下文里，用户无恢复通道，目录单向收缩。
  H1 长期不修把这问题掩埋了，H1 一修立刻暴露（实测降级后连检索 5 次仍是 dormant）。
  → 被使用 → dormant 回 `active`（记 `review_log revive` 账）；新增 `bring-back`
  命令；`archived` 保持终态，撤销需要显式意图而非一次偶然命中。

### 升级须知

门②③④ 开始真正运行后，**第一次 `gate all` 会按遗忘曲线降级一批久未使用的条目**
（实测 10 条里降 5 条），它们不再出现在注入目录里。这是设计行为不是数据丢失：
降级条目**仍可被 `memory_search` 命中、`memory_load` 读正文**，被再次使用会自动
回到目录，也可以用 `python -m memory_ecology bring-back <id>` 手动恢复。

### 测试

- 修正三条把 bug 当成规格的用例：`test_corrupt_frontmatter_degrades_not_crashes`
  （`detail` 1 → 0，并断言 `damaged[]`）、`test_isolation_three_circles`
  （补 `search` 冷路径断言，原来只测 `catalog`）、`test_used_entry_is_not_archived`
  （断言 `active`，原来断言 `dormant`）。
- 新增：插件 T8d（冷路径隔离）/ T13（四门真跑）、`test_regressions.py` 7 条、
  core 冷路径与 archived 终态各 1 条、mutation M9 对齐新结构 + M9b/M9c。
- 纪律：每条新用例都在**旧代码**上验证过会红（5 red + 2 error），不是空断言。
- 全量 7 组 7/7 通过；`publish_compat` 重建发布树通过（脱敏扫描 + 守卫）。

### 明确未修

检索词面化（路线结构性上限）、衰减/阈值调参（无真实问题集前调参只是换一组拍脑袋
的数）、`core/` 与 `src/` 合并——都不是本轮工程缺陷。

---

## v3.2.0 —— 2026-10-27

**主题：把已经设计好的治理真正跑起来，并且不再需要 Hermes 才能跑。**

一句话判据：*在没有任何 cron、没有任何 Hermes 的机器上，四张账本连续都有新增行，且 dsh
会话里能看到本圈记忆；打开观测舱能一眼看出哪个门死了。*

### 新增（Stage A · 四道门真正跑起来）

- **核心包 `src/memory_ecology/` 重写为单源**（stdlib only、零宿主依赖、运行期可切根）。
  这是 v3.2.0 的地基：`scripts/lib` 不再是它的物理副本。
- **门① 写入整合**：指纹去重（**指纹含 scope**）→ 同圈内相似合并 → 类型归一 → 取代链
  （`supersede` / `restore_superseded`）。
- **门② 配额巩固**：L1 视图由核心计算；**提升判据不再依赖 L1 已有同源条目**
  （旧实现的慢路径因此永远空转）。新增判据 B「驻留 ≥14 天且被检索命中 ≥2 次」与
  判据 C「occurrences≥2 且驻留 ≥14 天」。**这是"promote 恒为 0"的根治。**
- **门③ 画像蒸馏**：保留 30 天观察期，新增**交叉印证旁路**（`corroborate≥2` 可提前晋升）。
  **这是"89 候选 0 晋升"的根治**——旧实现 64 个候选全卡在观察期，且没有任何旁路。
- **门④ 复核降级**：`last_verified` 由"被使用"刷新（检索命中/加载即视为已验证），
  修掉"最活跃的记忆反被归档"；窗口内被命中免降级；零动作也写 `verify` 留痕。
- **每个门都返回 `blocked[]`**：不动作时必须能回答"为什么"。旧项目 0 promote、89/0 无人能解释。

### 新增（Stage B · 剪断 Hermes 脐带）

- `paths.resolve_root()` **函数式解析**，每次调用现算 → 运行期可切（旧 `config.py:17` 在
  import 期钉死根，20+ 处 `HERMES = hermes_root()` 固化）。
- **零 cron**：四门挂 `agent/created` + `agent/turn-stopping`，幂等、可补账。
- **版本四处对齐机制化**：`python -m memory_ecology version` 一条命令校验，不再手改四处。
- **配置键必须有消费者**：`test_config_keys_have_consumers` 扫描源码，死配置即红
  （根治 `[quota] memory_limit` 那类事故）。
- 核心零依赖实测：`grep -E "hermes|state\.db|jobs\.json" src/memory_ecology/` 为空。

### 新增（Stage C · 上下文插件化产品化）

- 物化视图加 **schema 版本号**（`schema=2`），不符即当过期（**fail-open：宁可少注入，
  不可注入旧格式**）。
- 目录渲染**单源**：`retrieve.render_catalog_text` 是唯一实现，插件 JS 侧与它**逐字节一致**
  （`plugin/test_plugin.mjs::T11` 用真实 Python 输出比对，防双口径漂移）。
- 成本度量接真实会话日志：工具 schema 39k / 系统提示词 5.9k / 追加注入按 source 聚合；
  `zstd` **必须流式解**（单帧解只能解出 1 行——实测踩到的坑，`test_multiframe_zstd` 钉住）。
- 注入成本落 `cost` 账，GUI G6 读同一批行，数字不可能漂移。

### 新增（Stage D · 声明式圈）

- `circles.json`：`{id, display_name, match.cwd_prefix[], budget_chars, catalog_limit, readonly, sources[]}`。
- **路由 = 最长前缀匹配**，按**路径段边界**（`proj2` 不会并进 `proj`），Windows 下忽略大小写
  （否则同一项目会因 shell 起法不同裂成两个圈）。
- **圈级预算生效**：改 `budget_chars` 后目录条数立刻变化（`test_budget_change_takes_effect`）。
- 三圈两向 + 全局可见写成断言（`test_isolation_three_circles`）。

### 新增（Stage E · 记忆插件化，原定 3.3.0，本轮版本号仍是 3.2.0）

- **两种接入**：`mount`（只读挂载，立即进目录，**不写本库**）/ `integrate`（投候选，走门①）。
- **安全边界写进实现**：只读 + 来源白名单 + 每篇 sha256 + `kind` 只接受 `readonly-pack` +
  装/卸只改状态位（物理文件永远保留）+ 疑似注入内容打标。
- 篡改检测：pack 内容被改 → 哈希不符 → **整体拒载**（不留半加载状态）。

### 新增（Stage G · 观测舱 v2.3 接入 v3.2.0）

**目标 GUI 是哪个，这里先说清**：是 `memory-ecology-compat/integrations/` 下的
**`gui/eco_gui.py`（8788 服务）+ `gui-v2.3/`（React 19 + TS + Vite 的 iOS 外壳）**，
桌面快捷方式「生态观测舱v2」就指它。

> 期间我一度在 `eco-dsh/gui/` 另起了一套单文件前端——那是**错的目标**，
> 已归档到 `_archive/gui-wrong-target-v0.4/`（含它那份 `test_gui.py`）。
> 教训：接手"升级 GUI"时先解析快捷方式/入口，别按目录名猜。

- **七个新视图**（挂在真实 v2.3 外壳里，与原有九视图并存）：
  **四门实况** `gates` / 圈的视角 `circles` / 注入观测 `inject` / 记忆包 `packs` /
  圈管理 `manage` / 成本面板 `cost` / 急救箱 `firstaid`。
  Dock 换成「驾驶舱 · 记忆库 · 四门实况 · 圈的视角 · 体检」——四门实况是一眼最该看的。
- **两级以上红灯**：`dead`（一行都没有 → 根本没跑）/ `idle-spin`（有行全是心跳 → 跑了没产出）/
  `starved`（有动作但期望的那种从没成功过）/ `stale`。旧界面只能看出第一种。
- **口径不混算**：新端点前缀 `/api/v3.2.0/*`，读 dsh 新根；旧九视图继续读 Hermes 旧根。
  每个视图角标写明来源，**绝不把两个根的数字加在一起**。
- **GUI 与核心同源**：所有数字来自 `python -m memory_ecology <cmd>`，GUI 不自己解析
  frontmatter、不自己算圈、不自己判门死活。
- **写动作只有 5 个**（`circle_write` / `pack_enable` / `pack_disable` / `maintain` /
  `materialize`），全部转发核心 CLI，走确认闸门 + `action_log.jsonl`；
  **没有任何删除/清理记忆的动作**（测试扫源码钉住）。
- 修掉真实服务里两个挡路的旧缺陷：① `_utf8_env` 是 **dict 却被当函数调用**
  （9 个写动作里 7 个在 spawn 前就 `TypeError`，写通道整体是死的）；
  ② `--root` 不传导给子进程（沙盒 GUI 的写会落到生产根）。
- **核心探测必须验版本**：本仓库 `src/memory_ecology/` 是 v3.1 镜像，先命中它会得到
  "核心可达 ✓ 但新视图全 404"的假成功（实测踩到，有断言钉住）。
- **命名纪律**：代码里不许出现 `v32`（读起来像"版本 32"，已误导过一次）。
  统一 `v3.2.0` URL 前缀 + 语义名（`core_*` / `fetchCoreMeta`），测试会扫源码。

### 变更（破坏性 / 需要注意）

- **`memory_ecology.__version__` 现在是版本唯一权威来源**；`VERSION.md` / `pyproject.toml` /
  `plugin/package.json` 三处必须与它一致，否则 `version` 子命令红。
- **记忆层状态机收敛为单轴 `status`**（`active` / `dormant` / `archived`）。
  `fate`（`retained`/`superseded`/`merged`/`archived`）仅作取代链标记使用——旧项目把它当
  skills 的两轴状态机，记忆层塞 `fate` 与 `safeio` 的枚举本来就打架。
- **`agent/session-start` 不再被监听**（该事件在 dsh 0.2.1-rc.x 上不存在，监听它是静默死代码）。
  改用 `agent/created`（payload 带 `source`: `startup`|`resume`）+ `agent/turn-stopping`。
- **`systemPrompt.section` 必须带 `interpolate: false`**（否则目录里的 `{{...}}` 会让装配抛错）。
- **备份策略**：保留最近 8 份，更早的**移入** `_bak-archive/`（**不是删除**——红线 1 不变）。
  生产根曾堆积 29 个无保留策略的 `.bak-配额前-*`。

### 修复（迁移自旧项目的真实 bug）

- **`append_line` 在 Windows 上并发丢整行（8% 概率）—— 本轮最重的一个真 bug。**

  原来只靠 `O_APPEND` 就宣称"只追加所以并发安全"。**Windows 上这个前提不成立**：
  MSVCRT 的 `_O_APPEND` 是"每次写之前先 seek 到末尾"，seek 与 write 之间没有锁。
  6 个进程同时写 9 KB 时，两个进程会算出**同一个偏移** N，各自写
  `[N, N+9017)`，后写的整个覆盖先写的 → **恰好丢一整行**，而且两次 `os.write`
  都返回满长度（所以查返回值也发现不了）。

  实测：40 轮 × 6 进程 × 5 行 × 9017 字节 → `{29: 3, 30: 37}`，**8% 丢行**。
  这不是理论风险：并发投递时会静默丢掉用户写下的记忆。

  修法：Windows 上用一个 1 字节的**旁路锁文件**（`<file>.appendlock`）把
  "seek+write"串起来。两个反直觉的取舍都实测过：
  ① **不能锁目标文件本身** —— `msvcrt.locking` 锁字节区间，同一文件另一 fd 再
  `O_APPEND` 写会撞上自己的锁，`PermissionError 13`（40/40 全失败）；
  ② **锁文件不能删** —— A 解锁后删文件、B 已在旧 inode 上等锁、C 创建新文件并
  拿到锁 → B 与 C 同时进临界区，丢行率从 0% **反弹到 10%**（还出现一次丢多行）。
  POSIX 不需要这把锁（`O_APPEND` 本来就原子）。

  同时把 `os.write` 的**短写**接住（契约是"可能写得比要求少"，不续写会静默
  截断行尾）。

  测试补强：并发用例从"跑 1 轮 × 9 KB × 6 进程"改成
  "**跑 10 轮 × 32 KB × 8 进程**"——丢行是概率事件，单轮漏检率太高
  （变异测试里 M2c 反复存活就是因为这个）；另加一条 `os.write` 短写用例。
  变异体从 24 个增加到 **26 个，26/26 CAUGHT**。

- `chars_of` 不过滤 `None` → 门② 挤出时 `AttributeError`（实测踩到）。
- `mark_consumed` 目标名冲突：同一会话同一天的下一批候选会落到同一个 `.done.md` 名字上，
  POSIX 上 `rename` 会**静默覆盖旧档**（已消费记录消失）。现在冲突时追加毫秒时间戳，两边都留。
- `unconsumed_in_file` 与 `integrate` 的指纹口径不一致（一个含 scope 一个不含）→ 文件永远
  判"未消费"，pending 无限堆积。
- `write_atomic` 临时名必须带 pid（固定名在多进程下会写出半个文件，实测 8 并发丢 6/40）。
- 备份时间戳必须带毫秒（秒级在连写时同名覆盖，等于"备份只剩一份"）。
- `measure` 用 `ZstdDecompressor().decompress()` 只能解单帧（13MB 会话只解出 1 行）→ 改流式。

### 测试

| 套件 | 条数 | 所在仓库 | 说明 |
|---|---|---|---|
| `tests/test_core.py` | 101 | 本仓库 | 核心：paths/root/四门/目录/圈/包/版本/数据安全 |
| `tests/test_compat.py` | 24 | 本仓库 | 旧库格式兼容 + 真实生产根**只读**断言 + 损坏输入 |
| `tests/test_regressions.py` | 28 | 本仓库 | 第二轮审查抓到的 7 blocker + 11 major 的回归 |
| `plugin/test_plugin.mjs` | 22 | 本仓库 | 插件：目录稳定性/fail-open/多圈/渲染一致/死代码回归 |
| `tests/test_concurrency.py` | 12 断言 | 本仓库 | 8 进程并发投递 + 4 进程并发整合 + 备份唯一 + 探针隔离 |
| `tests/test_stability.py` | 27 断言 | 本仓库 | 幂等/目录字节稳定/30 轮无泄漏/300 条压测/50 次重开 |
| `tests/test_mutation.py` | **26 变异体** | 本仓库 | 证明上面几套真的能抓 bug：**26/26 CAUGHT，存活 0** |
| `run_tests.py` | **7 组全绿** | 本仓库 | 一条命令跑完，任一失败退出码非 0 |
| `tests/test_gui_v3_2_0.py` | 24 | 兼容版 | 观测舱 v3.2.0 数据层：核心探测验版本/与 CLI 逐值一致/只读零写入/fail-open/写走核心 |
| `tests/test_gui_contract.py` | 9 | 兼容版 | 前端四处注册表一致 + 主屏不重名 + 不许出现 `v32` 写法 |
| `tests/test_gui.py` | ~~38~~ | **已归档** | 那是错目标那套 GUI 的测试（`_archive/gui-wrong-target-v0.4/`） |

> 两个仓库各自 `python run_tests.py`：**dsh-memory-ecology 7/7**、**memory-ecology-compat 57/57**，全绿。

**变异测试是这套测试体系的信任基础**：第一轮跑出 5 个存活 + 1 个无效，说明有 5 条断言是
同义反复（比如"BOM 用例"用 `assertIn` 断言，BOM 残留时子串仍匹配，等于没测）。补强后
24/24 全抓 —— 这才是"测试通过"有意义的前提。

### 数据面（本次实测，用于校准判据）

- 生产 detail 229 篇，`occurrences` 分布 `{1: 228, 2: 1}` → **occ≥3 结构上不可达**；
- `user_candidates` 64 个全部 `status=observing`、`created` 距最新仅 2 天 → 30 天观察期是唯一卡点；
- `gate_log` 209 行 = ADD 127 / EXTRUDE 58 / SELFCHECK 19 / idle 4 / UPDATE **1** → UPDATE 两个月 1 次；
- `quota_log` 302 行 = idle 246 / extrude 52 / extrude-manual 3 / rollback 1 / **promote 0**；
- `review_log` 2 行（cron 月级，2 个周期）；
- `memories/quarantine/` 与 `archive/` **目录根本不存在** → 两个月零 CONFLICT/DISPUTE 移出。

### 第二轮对抗审查修掉的缺陷（详见 `docs/ARCHITECTURE.md` §11）

7 个 blocker + 11 个 major，全部动态复现过，全部有回归断言。最严重的三个：
1. **跨圈同名候选被静默吞掉**（`mark_consumed` 只按文件名匹配）→ 数据丢失；
2. **插件按目录名判圈**绕过 `circles.json` 路由 → 无关项目看到别的圈的机密；
3. **`circles-write --file` 读不到时静默清空全部圈配置** → 一条错命令抹掉路由。

以及两个"立项要修的病被自己复刻"：
- `session_count = max(现有, 1)` 恒为 1 → 门② 的 A 判据在自己的实现里结构性不可达；
- 配置键名读错（`GATE_STALE_DAYS` vs `_DEFAULT`）→ 配了不生效，正是"死配置"那个最贵的教训。

---

## v3.1.0 —— 2026-10-25

见旧仓库 `VERSION.md`（回原文 + 配置 Guard + 备份加密 + 手艺分圈证据）。

