# ARCHITECTURE_TODO — 架构待办与已知裂缝清单（2026-10-04 全量审计产出）

> 目的：让债可见。这是"从个人可用到开源可维护"最重要的一步——不是修完所有债，而是不再隐藏。
> 产出背景：2026-10-04 两轮 AI 评审 + 人工核对 + 三路深扫，全部发现已修复或登记于此。


### 拍板记录（2026-11-05 续，用户授权 agent 权衡全部待决项）

- **#2 R2 双线收敛 ✓ 已执行**：core/ 机械镜像 D 仓 dev 真源（5d951d1，f9b5ebb 多生态圈 + 圈隔离修复进发布线；pytest 216 + mjs 30/30）。此后 **core 线唯一改动入口 = D 仓**，C 仓 core/ 只做镜像同步。
- **#1 生产 profile 切 core/plugin ✓ 已执行（2026-11-05，用户在场拍板「现在就切」）**：按 core/docs/首批副圈部署说明.md 操作单执行——profiles/desktop/package.json bundles `eco-note-dsh-cordis`→`eco-memory-dsh`（link: core/plugin，Junction 已建）+ cordis.patch.yml 配置段 `- id: eco-memory`（rootPath=hermes 生产根，数据连续）；首批副圈 dsh-eco-dev/quant-lab 经 Guard 通道写入（circles-write 自带备份）+ materialize 14 scopes + health 验证 gate_lock/l1_reconcile 在位。备份：`cordis.patch.yml.bak-切core前-*` / `package.json.bak-切core前-*`；回滚=还原两文件+删 Junction+重启宿主。**待用户验证**：dsh 会话内 memory_circle_list/attach/detach 三工具可用（若未见工具，重启 dsh 桌面版——bundles 变更可能需要重启而非热重载）。
- **#3 D 仓归宿：维持现状**——本地开发真源（无 remote），C 仓=发布镜像。建 remote 无增益，不再作为待办。
- **#6 发版：本日 push main + v3.4.0 tag**（仅此二者；local-dev-snapshot 分支永不 push——红线 3）。
- **#7 远端 v3.2.2-release：保留当里程碑**（main 祖先零独有提交，删除是破坏性动作、保留零成本）。
- **#9 R9 四问已定案**：海=新建 memories/sea/、枚举=status: sea、evidence=行号+摘要双列、v1 只海不网（见 D 仓 docs 设计稿 §7）。前置不变：R4 之后动工。
- **#10 autoPaths：维持保守**（tests/docs/*.md）。五道锁先在测试/文档上证明自己，aicr PR 链在宿主内真正跑通后再议放宽。
- **S15 收尾阻塞：维持同步 await**（接受 f9b5ebb 设计辩护：串行链+节流）。登记为观察项——用户报告「收尾慢」时再实测并考虑异步化。
- **S40 PII 口径：折中落地**——补 +86/86 前缀（最高频形态，0f71cc4）；座机/全角/at-email 维持审计「边界」判定并登记不修。
- **#4/#5（runner 沙箱 / 门④频率）：维持暂缓**——需生产观测数据，非本会话可取证。
- **R8（GUI 性能）与 05-P1-1/2/3**：仍闸在观测舱线脏区（红线 9），脏区提交后动刀。**→ 2026-11-05 下午已解闸并全部完成（见 〇-vi 段）**。
## 〇-vi、v3.4.0 R8 + GUI 三件 P1 + 批5 销账（2026-11-05 接手轮）

驱动：3.4.0 收口交接 §6「下一步」第 2-4 项（R8 / GUI 三件 P1 / 批5）。交接时闸门已解除
（观测舱脏区在前一棒代提交 ea20cf6/8b90866/9bda900/1fb0435 落库，C 仓工作区全空）。

### 已销账（每项都带「先证红」的回归）

- **R8 GUI 首屏性能拆包 ✓**（C 仓 `f2bb848`）
  - 18 个视图从 AppWindow.tsx 静态 import 改 `React.lazy` + `<Suspense>`（Dashboard 保持静态——它是首屏默认视图，动态加载会多一跳白屏）。
  - vite.config.ts 用 `build.rolldownOptions.output.codeSplitting.groups` 显式分三组：`react` / `three` / `charts`。**Vite 8 是 rolldown，`manualChunks` 已废弃**（写了也被 codeSplitting 覆盖）。
  - 实测：入口 `index-*.js` **1009.39 kB → 97.6 kB**，新增 `react-*.js` 218.8 kB，`three-*.js` 552.7 kB 独立成块**只在进 3D 视图时拉**。首屏 raw 1009→316 kB（gzip 280→101 kB）。
  - 验收用 CDP 无头 Edge 逐视图跑（脚本 `D:\work\_eco_cdp_check.mjs`）：**18/18 视图真渲染出内容**（innerText>0、无 `.view-loading` 残留），各只拉自己那个 chunk，首屏请求序列里没有 three。这是本项目第一次有 GUI 运行时（而非仅构建体积）的验收证据。
  - 顺手把 gui-v3 `node_modules` 从「指向 `D:\archive\...\gui-v2\node_modules` 的 Junction」换成正常 `npm install` + 落 `package-lock.json`。隐患写进 README：归档是只进不出冷备，`npm install` 会**写穿联接改到另一个前端**，归档一移构建就崩。
  - 遗留（非本次引入）：`BlackHole` 引用的 `textures/milkyway.jpg` 在仓库与 dist 均不存在 → `/v3/textures/milkyway.jpg` 恒 404（星系视界星空贴图缺失），已登记待修。

- **05-P1-1 first_aid 假修复 ✓**（C 仓 `eac0f04`）
  旧口径「ok=False + 带处置文案 + --fix → fixed」把库损坏/数据根缺失/检索 CLI 缺失/cron 报错全报成已修，`all_ok` 恒 True、退出码恒 0。现在 `fixed` 必须由真执行过修复的调用点显式给、且**复评通过**；探针失败（拿不到证据）一律不算修好；`--fix` 输出里 fixed 与 ok 分开显示（旧写法两者都是 ✅，🔧 分支永不可达）。
  回归 `tests/test_gui_security.py::TestFirstAidStatusMachine`（5 条，先证红 3 红）。

- **05-P1-2 publish_compat SystemExit 绕过清场 ✓**（C 仓 `eac0f04`）
  `except SystemExit: raise` 直接重抛，而 build() 内三处 SystemExit 都在**已写入几百个文件之后** → 半新半旧发布树留下。现在同样清场（保 .git）再重抛。
  **关键约束**：清场以 `_PUBLISH_TOUCHED` 为闸——build 开头两道**拒绝型** SystemExit（--out 指向开发树 / 无 .git 陌生目录）时树原封未动，清场会去删「刚刚才拒绝清空的目录」。旗标在**进入破坏性清空循环之前**置位（评审抓到的窗口：置在循环后，循环中途 unlink 抛 OSError 就留下半清空树且无提示），且每次进 build 先复位。
  回归 `tests/test_publish_rollback.py`（6 条，先证红）。

- **05-P1-3 meta() 的 init 写副作用 ✓**（C 仓 `eac0f04`）
  `core_call(["init"])` 在 TTL 缓存外 → healthz(20s)/meta(8s) 每轮 spawn 一个**写**子进程、GET 有建根副作用。现在 meta 纯只读零子进程（读 `.eco/root.json` + 进程内 import 版本），建根拆成 `ensure_root()` 只在 eco_gui 启动调一次。
  只读成因**直接问核心 `Root.open()`**（四种：root.json-corrupt / root-not-initialized / schema-mismatch:N / root-not-writable）——本地重推一种会把「root.json 损坏」误报成「schema 需要迁移」，处置方向相反。`core_ok` 用 `_is_v3_2_0` 判据而非「能 import」，否则仓库里 v3.1 平移镜像会被报成核心可达。
  回归 `tests/test_gui_v3_2_0.py::TestMetaIsReadOnly`（4 条，先证红 3 红）。实测：GUI 起在 8793 连轮询 10 轮 meta+healthz，**init 子进程数 0**。

- **批5 01-P1-2 候选匹配键单源化 ✓**（D 仓 `941cb76` → C 仓镜像 `f4c9646`）
  写入用 trait(80)、查重用完整正文(60)，正文以 `"- " * 50 + 语义` 开头时两键永不相等 → 每轮重造候选（created 归零、观察期结构性失效）+ 2 会话时当轮把 `- - -` 晋升进 USER.md + 每轮多一个 `.promoted.<stamp>.md`。**修复前已独立复现**：3 轮 candidates 各 1、promoted 1/1/2、文件 1→3、USER.md 全是 `- - -`。
  三处修法：`_cand_key()` 成唯一判定口径；`_is_marked_candidate()` 按点号段识别三种终态（认 `.promoted.<stamp>.md`）；`_trait_of()` 截断改「按词字符含量取窗口」（前 80 全是标点时右移到第一个有内容的位置），纯标点正文直接拒收并记 `skip` + blocked `empty-trait`。
  回归 `tests/test_core.py::TestGate3CandidateKeySingleSource`（6 条，先证红 6/6 全红）。全量：D 仓 run_tests 7/7、C 仓 core 7/7 + pytest 42 + src 192。

### 既有 flaky（登记，非本轮引入）
- **mutation 组 M2c（append_line 去并发锁）曾是概率性变异体**：靠 Windows `O_APPEND` 非原子性的 ~8% 竞态触发，测试注释自己记录过「连跑 15 次有 4 次存活」。本轮全量首跑遇到一次该组红、随后连跑 4 次全绿。
  **→ 已在本轮修掉（D 仓 `5583b63` → C 仓镜像 `f556492`）**：新增 `test_append_lock_actually_serializes`，改用**不靠概率**的口径——另一进程持住 `.appendlock` 时 `append_line` 必须阻塞，去掉锁则 100% 必红（0.3 秒出结果）。M2c 的 runners 已加上这条，连跑 3 次 28/28 全抓无存活。原并发用例保留（它验端到端不丢行，新用例验锁语义本身）。

### 本轮额外清掉的一条测试盲区
- **01-P1-1「显式他圈名」绕过（D 仓 `6b6d952` → C 仓镜像 `985b77d`）**：审计指 H4 主轴的实现已由 f9b5ebb 修好（`resolveScopeGuard` 的允许集合 = {routed} ∪ attached，fail-closed），但 T8d **只断言了「省略 scope」与「显式 global」两分支**，"模型主动填他圈名"这条绕过路径零覆盖。补两条断言（search 显式他圈名必须被拒且不回显内容；load 塞未声明 scope 参数不得绕过边界），并做**变异验证**：把 guard 在 search/load 两处分别换回旧写法，T8d 都必红。


### 仍然在案
- **R4 五账一形 / runner 零正则**：未动（体量大，可能跨月）。**这是 R9 的前置**。
- **R9 v1 记忆海**：设计已定案（D 仓 `docs/记忆海与知识网-设计-R9.md` §7），未动工。设计稿 §7 末句写明「实现前置不变：R4 完成前不动工」，而收口交接 §6 把 R9 排在 R4 之前——**两处排序冲突，按设计稿为准（R4 先行）**。
- **01-P1-1 圈隔离族（批3）**：实现侧已由 f9b5ebb 修好，测试盲区本轮已补（见上）。
- **02-P1-1 hermes 水位线（生产线，走 eco-ops-live 部署协议）**：未动。
- **03 线（bridge 独立仓）**：未动。
- **`textures/milkyway.jpg` 缺失**（R8 验收时发现）：星系视界星空贴图恒 404，需补贴图或改代码容忍。


## 〇-v、v3.4.0 更新销账（2026-11-05，3.4.0 重构）

驱动：3.4.0 重构总交接（2026-11-04/05）+ 142 条审计 + REVIEW 50 条 triage + 用户拍板。
详见 `Desktop/项目开发小计/记忆生态开发/2026-11-04 审计产物/`（判红结论、审计汇总、triage 三份）。

### 已销账
- **R0 拆雷**：堆 A（10-31 生产修复 6 文件，44496c8）/ 堆 B（root.py 两档放行，26cdee7）/ 堆 D（文档+忽略规则，6cfd5e6）分堆落库；生产 write_gate.py SHA 基线全程未动。
- **R1 测试可信度**：三条红全判「断言过期」并修复——历史证据改钉合成账本（TestGateStatusSemantics，starved/ok/dead/idle-spin/stale 五语义）；S18 run_tests 假绿四路全堵；S33 三套件入 pytest（收集 167→213，D 仓）；S34 十二条恒真清零；S35 只读指纹补 eco.db/-wal；S47 version.py 精确比对。变异抽检必红实证。
- **S1 两线分叉销账**：C 仓 26cdee7 + D 仓 305f39e，root.py 两仓逐字节一致。
- **S3/S12/S21/S22/S45/S48**（REVIEW 编号）：f9b5ebb 已修，triage 销账（C 仓 core/plugin 未同步 f9b5ebb 是 R2 的事，见下）。
- **R7 版本大一统**：两仓全部版本位收敛 3.4.0（C 仓八处含 gui-v3 3.2.0 并线——待拍板 #8 落定）；version.py 扩面（run_tests 横幅防回潮 + gui-v3 + CHANGELOG 首条）；两仓自检 ok:true。

### 仍然登记在案的债（3.4.0 范围内、带闸门）
- **R2 双线收敛**（最大债）：C 仓 core/ 停在 c86bf54 时代，f9b5ebb 的多生态圈 + 圈隔离修复（resolveScopeGuard/circlesByAgent/circleOf）全部未同步——审计 01/03 的 P1 就是这么来的。**闸门**：待拍板 #2（release 树退役/合并方案）+#3（D 仓归宿）。sync 方案拍板后一次性同步 + 全量回归。
- **R3 反馈链复活 / R5 宿主接入**：L1 pre-step 注入与 inject_events 真实流量。**闸门**：待拍板 #1（生产 profile 切 core/plugin = 热重载 = 掐断活会话，红线 10）。
- **R8 GUI 性能**：986KB 单 chunk 拆包 / node_modules Junction 换正常安装。**闸门**：C 仓 gui-v3/** 载着观测舱线未提交改动（红线 9 不混提交）——等观测舱线提交后动刀。
- **05-P0-1（S14）detail_body 无校验**：唯一 P0。**闸门**：collector.py 载观测舱线改动，等其提交后 5 行修复 + HTTP 层回归。
- **R4 五账一形 / runner 零正则**：未动（交接 §15：体量大，可能跨月——是否纳入 3.4.0 之后版本待拍板）。
- **R9 记忆海/知识网**：设计文档已出（D 仓 docs/记忆海与知识网-设计-R9.md），实现排在 R4 之后（排序约束：都要求五账一形）。
- **S15 门④收尾阻塞**：f9b5ebb 注释已改口称「正确做法」——需实测当前规模收尾耗时后再拍板是否异步化。
- **S40 PII 口径**：REVIEW 判漏检缺陷 / 审计 02 判大陆口径属规则边界——待用户定口径。
- **C 仓 gui 脏区归观测舱线**（24 项）：不混提交，随观测舱线自行提交。

## 〇-quater、v3.0.0a1 更新销账（2026-10-24，溯源回填 a1）

驱动：AUDIT-2026-10-23 三树独立审计（P0 清单）+ 用户拍板「数据面收尾→进蓝图 B」。

### 已销账
- **P0-1 三树分裂 / 修了没上**：10-23 部署对齐（tag deploy-20261023_174314）+ selfcheck A1 + 宿主外看门狗（Windows 计划任务）——机制性防再犯在位（本日核实：生产 write_gate 与 dev SHA256 一致）。
- **P0-2 沉默截断**：上限配置化+可见标记+22 篇存量标记（10-23）；R-4 硬证据口径收口（10-23 晚收编批）。
- **P0-3 可溯源 0/162**（a1，本批）：门② EXTRUDE 前向记账 + `tools/eco_backfill_origin.py` 存量补账（EXTRUDE 行 + evidence 关联，不伪造会话号）+ eco_extract session sidecar + 门① origin 传播 + selfcheck A4。**根因关闭**：新条目不可能再无账。
- **双树漂移（§四）**：src/ ↔ scripts/lib 同步检查在位（check_shell_sync fail-closed，10-23 晚收编批）。
- **ecosystem.git「无当日提交」口径噪音**：属健康行 12:35 时点 vs 快照落库时序，数据无丢失；待 eco_version 口径微调（低优先，观察项）。

### 仍然登记在案的债
- **P1-2 注入审计**：inject_events 仅 3 行——a2 批次（inject_gate shadow 接线 + last_hit 回写 + D1-D5 记分卡），证据驱动晋升的前置。
- ~~**disputed_by 无消费方**：F-2/红队 round3 产出的大量 DISPUTE 标记无 LLM 复核闭环——a3 批次。~~
  → **2026-10-29 部分销账**：消费方已补（`memory_query --disputes` / `--resolve`，
  `list_disputes()` / `resolve_dispute()`），标记不再"死"在库里、有人工终审入口。
  **仍未做的**：自动 LLM 复核裁决（a3 批次原定内容）——现在只是**人工闭环**，不是自动闭环。
- ~~**门④ last_verified 从不刷新**（最活跃记忆反被归档）：与 a2 last_hit 联动。~~
  → **2026-10-29 销账（v3.1 线）**：`eco_review.refresh_last_verified()` + `refresh_lv` 通路，
  窗口内被使用的条目复核戳随之前进（有 5 条断言锁死，含"只判 actions 测不出回退"的说明）。
  `core/` 线此前已有等价实现（`used_recently` → 刷戳 + dormant 复活）。
  **注意口径**：本条过去被误记为"仍未做"，实际 `core/` 修了而 v3.1 没修——判"修没修"要先定树。
- **看门狗无推送通道**（仅日志+桌面 txt+退出码）：a4。
- **经验线 .watermark 冻结**（2026-08-07）：a4 诊断，注意只动经验线。
- **水位贴顶拉锯**（MEMORY.md 2343/~2550）：指针化试点（a4）。
- **五账一形 / runner 零正则**（蓝图 C/D 阶段）：未动，进 B 后按阶段推进。

## 〇-ter、v2.5.0 更新销账（2026-10-18，用户模拟测试驱动）

驱动：三轮用户模拟测试（P1 纯新手 / P2 英文老手 / P3 大库用户）+ 独立终审。
产出报告见 `Desktop/Agent记忆实验数据/` 同源的群目录（本仓不含）。

### 已销账（4 个 Blocker + 3 个并发/静默缺陷）

| 原问题 | 根因 | 修法 |
|---|---|---|
| `write_gate` 数据已落盘却 exit 1 | 22 个脚本缺 stdout 编码兜底，崩在收尾 print | `lib/cli_base.bootstrap()`，26 脚本接入 |
| 真错被编码错掩盖 | 异常处理里的 print 自身崩 | `errors="replace"` 保证异常路径不二次崩 |
| 门④ 无视 `MEMORY_ECOLOGY_ROOT` | 用 `Path(__file__).parent.parent` 自算 | `cli_base.resolve_root()` |
| 门② 自定义数据根必崩 | `os.open(O_CREAT)` 不建父目录 | `gatekit.acquire_lock_auto()` |
| 同进程锁重入不再互斥 | `pid == getpid()` 被当残留回收 | 显式判 `held`（`gatekit.py`） |
| Windows `os.replace` 遇读者占用失败 | POSIX rename 语义在 Windows 不成立 | 指数退避重试 + 长退避兜底（`fs.py`） |
| BOM 致首条候选静默丢失 | `utf-8` 读取保留 `\ufeff` | `utf-8-sig`（`eco_note_adopt.py`） |

### 新增防线

- `tools/check_entry_discipline.py`：D1 编码 / D2 自算数据根 / D3 写前建目录 / D4 锁成对。
  **29 个脚本 0 违规**，建议接入 CI。
- `test_concurrent_writes.py`：C1–C5，覆盖同门/跨门并发、stale 锁、原子写可见性、同进程重入。
- `test_mutation.py`：6 个变异体，验证上述测试的有效性（全部被抓到）。

### 仍然登记在案的债（本轮未动）

- §一 的 D（`eco_state.VALID` vs `eco_health_check.LEGAL_MATRIX` 双源）与
  E（5+ 套 frontmatter 解析）仍待收敛 —— **本轮只统一了"入口纪律"，没统一"解析口径"**。
- §二 的 `inject_gate` 遥测字段补全、`eco_note_backfill` 并行化、`lib/similarity` simhash 仍待办。
- §三 的 FTS5（>1000 条规模化路径）仍登记未实现；`--semantic`/`--recall` 仍为灰度默认关闭。
- §四 双树漂移 14–16 处待人工核对。

### 一条方法教训（写给未来的自己）

本轮最大的坑是"**把工作区当成发布产物**"：源仓库工作区有 25 删 + 45 改 + 10 未跟踪，
而 `HEAD` 才是完整版本。同一根因在一次任务里犯了三次（判文件存在性 / 判代码行为 / 判优点陈述）。
**任何"用户会看到什么"的结论，必须在 `git clone` 副本上复现，不能在工作区观察。**

---
## 〇-bis、v2.4.0 更新销账（2026-10-07，外部调研落地第二期）

1. **已落地**：S6 向量缓存 shadow index（lib/vector_cache.py，条目级 content-hash 增量 + 换模型整库失效 +
   「缓存=可丢弃派生物」心智模型）+ memory_query `--semantic` 灰度路（embedding 不可用自动回退词面）；
   S7 OR 召回（or_recall：任一 term 命中，补多词 AND 缺口）+ RRF 融合（rrf_fuse，K=60）+
   `--recall` 开关（**默认关闭=行为锁定**）。
2. **默认化决策**：embedding 后端与 OR 召回**均未切默认**——按 §二.4 观察期纪律，先由 S4 零结果率观测
   与实跑对比攒数据；`--semantic`/`--recall` 为用户显式启用入口。
3. **§三 检索层结论更新（FTS5 实测证伪，重要）**：原计划 FTS5 双路三配置实测均不适合本项目中英语料——
   ① porter 是英文词干器，中文零收益；② FTS5 trigram 分词器要求查询 ≥3 字符，而中文常用词恰是 2 字
   （「备份」「框架」「迁移」全零命中）；③ unicode61 与现有子串路结果高度重合。故第二路落为**零依赖
   OR 召回 + RRF**（无需索引、离线可用、无新依赖）。**FTS5 登记为 >1000 条的规模化路径**——
   届时若上 FTS5，应选 unicode61 + 中文分词预处理（或 jieba 类），而非 trigram；且必须先过
   golden 对比（纪律同 embedding）。
4. **观察期后候选动作**（登记，不在本轮）：① 零结果率连续 2 周 >30% → 评估 `--recall` 默认化；
   ② `--semantic` 实跑对比 difflib 的排序差异 → 决定 embedding 默认化。

## 〇、v2.3.0 更新销账（2026-10-07，外部调研落地第一期）

外部记忆系统调研（20+ 项目，`~/.memory-ecology/designs/外部记忆系统调研-v1.0.md`）落地：

1. **已落地**：S1 时间维检索（衰减三档半连续 + 发生时间过滤——首次消费 valid_time/transaction_time 双时态）；
   S2 失效带理由（superseded_reason/superseded_at，隔离区变历史时间轴）；
   S3 证据链（CONFLICT 新旧互指 evidence/superseded_by + 门③ distilled_at 蒸馏状态位可逆重蒸馏）；
   S4 零结果率观测（eco_retrieval_report + GUI 检索质量页签）。
2. **§二.4（embedding 默认化）进展**：在线观测工具就绪（零结果率 = 换内核证据）；
   v2.4.0 计划落 SQLite 向量缓存 shadow index + 灰度开关，观察 1-2 周对比数据后再定默认。
3. **§4.1 双树漂移口径更新**：sync_trees_check 已行尾归一化（CRLF 幻影漂移 34→16）；
   剩余 16 处为**真实内容漂移**（live/兼容版领先：GUI/first_aid/.agent-index 等），维持「不盲同步」纪律，
   逐文件人工确认哪侧为准后回迁；本轮只选择性同步实际改动文件（memory_query/write_gate/distill_stage/
   lib/safeio + 新增测试与观测脚本）。
4. **已回迁**：live 2026-10-05 蒸馏三修复（STABLE_DAYS/预算阶梯/批量 env）进 compat+兼容版；
   live eco_git_commit .lock 排除进兼容版。distill_stage/write_gate/eco_review 三文件经回迁后内容对齐。

## 一、五类"单源"裂缝（修复状态）

| 类型 | 定义 | 案例 | 状态 |
|---|---|---|---|
| A 功能对等、语义不同 | 两份实现都能跑但结论不同 | 门② vs memstore 的 detail_prefixes/is_same_source | ✅ 已收编（memstore 对齐门②生产语义：active-only + 单向包含） |
| B 公共库落后、本地更强 | 强行统一会丢能力 | lib/llm vs eco_note.llm_extract | ✅ 已升级 lib/llm.complete_json（重试/sentinel/三级 JSON 提取/usage）→ eco_note/eco_extract 收编 |
| C 语义模糊、都不完整 | 三处各自实现同一机制 | 水位线推进（signals/extract/note） | ✅ 已抽 lib/watermark.py（(timestamp,id) 复合键，同 ts 截断跳窗根治；旧标量格式兼容读取） |
| D 双源定义、互相矛盾 | 两个模块各自定义同一矩阵 | eco_state.VALID vs eco_health_check.LEGAL_MATRIX（candidate/retained） | ✅ **已修（2026-10-30 补记销账）**：`eco_health_check` 改为 `from eco_state import VALID as _VALID_STATE_FATE` + 形状转换 `LEGAL_MATRIX = {pair: True for pair in _VALID_STATE_FATE}`。运行时实测 `h._VALID_STATE_FATE is s.VALID` → **True**，即同一对象派生，结构上不可能再漂移。生产已部署（A1 漂移清零）。<br>⚠️ 此前该行长期写「⏳ 未修」，实际代码在 2026-10-29 的 0480922 就改完了——**是文档没跟上，不是代码没改**。教训：销账要同时改代码与登记表，否则外部读者会误判 |
| E 最弱版本拖垮整体 | 5+ 套 frontmatter 解析并存，最弱的不兼容 CRLF | eco_state（已修）/safeio/eco_health_check(PyYAML)/eco_eval/eco_note_index | ⏳ 部分修：CRLF 已修；完全收敛到 memstore 宽松/严格两版待做（eco_eval/eco_state/eco_health_check/eco_note_index 四处） |

## 二、"未来会统一"登记（4+ 处）

1. **inject_gate**：drop 灰度只对「occurrences==1 且 last_seen>60d」生效；经验线仅规则 2/5 在跑
   （occurrences/last_seen/quarantine/hit_count_30d 未传）——遥测字段上线后补全。
2. **eco_note_backfill**：`--out` 并行模式（cand-<session>.md 防写竞态）仅工具支持，主流程未并行化。
3. **eco_version**：v3.0.0 于架构升级完成时统一切换并移居 lib。
4. **lib/similarity**：simhash 待实现（原计划的 L2，现仅 difflib/ngram/embedding 三后端）；
   L3 embedding 选型已定（bge-small-zh-v1.5+CLS，见下节），**默认化待定**——需先在生产跑一段
   观察期（对比 difflib 的 UPDATE/NOOP 判定差异），确认无回归再切默认。
5. **eco_note_backfill**：`--max-cands` 是**每会话**限额（2026-09-03 设计变更），docstring 已声明；全局限额与并行调度待重设计。

## 三、相似度/语义层路线（必须离线可用）

- **当前**：lib/similarity.py 统一层，默认后端 difflib（行为锁定：门① SIM_NEAR=0.80 / 门④ 0.70 阈值语义不漂移）。
- **L1 ngram**（纯 stdlib）：已实现。golden v1（182 对）评测：FPR=0 下可回收 43/99 same（最优阈值 0.58）。
- **L3 本地 embedding**：**已落地并选定模型（2026-10-04）**。
  - **选型：`bge-small-zh-v1.5` + CLS pooling**（`MEMORY_ECOLOGY_EMBED_POOLING=cls`）。
    实测（golden 182 对：99 same / 32 similar / 51 distinct）：
    | 后端 | AUC(same\|sim+dis) | AUC(hard\|sim+dis) | 误合并≤10% 时 same 召回 | 耗时 | 体积 |
    |---|---|---|---|---|---|
    | difflib（旧默认） | 0.901 | 0.798 | 54% | ~0.1s | — |
    | ngram | 0.901 | 0.798 | 64% | ~0.1s | — |
    | **bge-small-zh-v1.5[cls]** | **0.931** | **0.858** | **74%** | **5s** | **95MB** |
    | bge-base-zh-v1.5[cls] | 0.914 | 0.831 | 59% | 27s | 407MB |
    | bge-large-zh-v1.5[cls] | 0.926 | 0.841 | 71% | 102s | 1.3GB |
    - **CLS > mean**：三个 bge 模型上 CLS 的 AUC/hard 召回一致更高（BGE 官方推荐，实测印证）。
    - **small 够用**：bge-small 不逊于 base/large 且快 5~20×、小 4~14×；8GB 卡/离线约束下无理由上大模型。
    - **text2vec-base-chinese-paraphrase 淘汰**：分数压缩到 0.98+，判别力尽失（FPR=0 只回收 5/99）。
  - **阈值迁移**：门阈值不再硬编码，统一经 `similarity.gate_threshold(role)` 按生效后端解析
    （`GATE_THRESHOLDS`：suspect / merge / noop / candidate）。迁移原则 = 保持旧 difflib 阈值的
    **操作点**（捕获率/误合并率）不变，只换刻度，杜绝"换内核忘改阈值"的静默漂移。
    embedding 口径：`suspect=0.76 / merge=0.84 / noop=0.97 / candidate=0.88`。
    配置 embedding 但模型缺失时，阈值自动跟随回退后的 ngram（`effective_backend()`），避免
    "阈值按 embedding 校准、分数来自 ngram"的错配。可用 `MEMORY_ECOLOGY_SIM_THRESHOLD_<ROLE>` 显式覆盖。
  - **启用方式**：`MEMORY_ECOLOGY_SIM_BACKEND=embedding` + 模型拉取（`fetch_embed_models.py`，
    走 hf-mirror；模型不入库，见 models/README.md）。生产解释器 hermes-agent/venv 已含 onnxruntime/numpy/tokenizers。
- **评测标尺**：`python eco_sim_eval.py`（golden/similarity_golden.json，**v1-2026-10-04：182 对**，
  150 对生产语料人工标注 + 30 随机负样本 + 2 超短串钉死）。对比脚本：
  `compare_embed_models.py`（多模型×pooling）、`tradeoff_table.py`（误合并预算下召回）、
  `calibrate_thresholds.py` / `migrate_thresholds.py`（阈值迁移）、`analyze_discriminative.py`（难例分层）。
  - **已知边界**：即便 embedding，hard_same（difflib<0.5 的语义改写）在 similar 误合并 ≤6% 约束下
    召回仅 ~30%——**语义层解决"字符级必漏"，但"same vs similar"的细粒度边界仍需 LLM 终审**
    （门①已有 LLM 决策路径，规则仅兜底，与此结论一致）。
- **自研/微调（Phase 6，远期）**：触发线不变（golden>500 对或最佳现成模型 recall<90%）。
  **当前判断：不需要微调**——bge-small 在 golden 上已达 AUC 0.93，瓶颈是标注集规模而非模型能力；
  237 条生产数据比 bge-small 的 1 亿+ 训练对差 4 个数量级，微调难有泛化增益。
- **蒸馏**：**已评估为不必要**（teacher bge-large 随时可调用，蒸馏只为省 90MB；数据量差 4 个数量级）。
  登记为"远期、需满足部署条件（边缘部署/百万级请求）"。
- **reranker**：bge-reranker 是 query-doc 交叉编码器，**只适用于 memory_query 检索路**（top-20→top-5
  重排），对门①/②/④ 的成对去重结构上不适用。是否引入待用户确认（当前未引入）。
- **SQLite 向量索引**：当前 `scan_memory_dirs` 每次全量读文件+现算 embedding，237 条可接受
  （门④ 40 条两两比较 1.3s）；>1000 条应加向量缓存（SQLite BLOB + numpy 余弦）。
- **LLM 终审**：合并候选预审走后台批处理（离线约束允许），人工终审不变。


## 四、双树偏差（重要）

compat 仓库（本仓库）**不包含**捕获链（dev scripts 独有非测试 .py 共 10 个：纯捕获链 9 个
eco_evolve/eco_extract/eco_git_commit/eco_health_alert/eco_l1_audit/eco_note/eco_note_backfill/
eco_note_backfill_runall/eco_note_signals，另有 _ecoreview_selftest.py 自测；dev 独有测试 4 个另计）
——它们依赖宿主 state.db，
留在 兼容版/hermes 生产树。但 README 曾宣称"两线核心功能完全一致"——与文件清单不符。
**待办**：要么 publish_compat 补齐捕获链的可移植化（state.db 访问抽象成宿主接口），
要么 README 明确"compat=治理核心+检索，捕获链=宿主树专属"。本次审计采用后者口径，
并以 `tools/sync_trees_check.py` 防止共有文件继续漂移。

### 4.1 未回迁漂移清单（2026-10-04 sync_trees_check 实测，待人工逐个核对）

以下文件 compat 与 兼容版/hermes 存在**内容级**差异（兼容版侧领先：GUI/first_aid 支持、
`.agent-index` 路径等），本次未盲同步——需人工确认哪侧为准后回迁：
`lib/fs.py`、`lib/metrics.py`、`lib/safeio.py`、`lib/inject_gate.py`、`lib/__init__.py`、
`eco_search.py`、`eco_note_query.py`、`eco_note_error_query.py`、`eco_note_index.py`、
`eco_note_merge_list.py`、`eco_eval_gold.py`、`test_eco_eval_v22.py`、
`test_eco_note_error_query_v2.py`、`test_eco_version.py`（共 14 个）。
本次已同步：四道门+lib 五件套+memory_query+eco_sim_eval+全部新增测试（兼容版/hermes 各 43 文件，均带 .bak）。

## 五、其他已知边界（有意为之，非遗漏）

- 门④合并候选/merge_list 只出清单不执行（人工兜底纪律）。
- 复活是半自动：memory_query 只写复活候选提案，`--revive` 人工确认执行。
- quarantine 账本 `src` 字段区分天数来源（目录日期优先于 mtime）。
- 物理删除代码层禁用；archive 只进不出（基因库模式，存储成本可忽略，注意力成本由检索解决）。
