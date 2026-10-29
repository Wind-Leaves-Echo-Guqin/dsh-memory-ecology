# 版本

- **v3.2.2**（2026-10-29）—— 五条治理缺陷修复（不采信报告结论，逐条独立复核后修）
  - **H1 门②③④ 结构性从不运行**（最严重）：根因在宿主调用顺序——`turn()` 内部派发
    `agent/turn-stopping` → turn 返回 → `kick().finally` 才切 idle，所以处理器运行时
    `phase.kind` 必然是 `running`，而 `runMaintenance` 非 idle 即抛；插件每轮必抛、
    catch 只 `logger.debug`，于是 `gate all`（门②③④ 唯一调用点）从未执行。
    改为直接 `enqueueMaintenance`（不抢 phase），catch 提为 `warn`。
  - **H2 损坏即隔离**：宽松解析对坏 frontmatter 返回 `({}, 全文)` → `scope` 回落
    `"global"`（最宽取值），损坏把隔离单向放宽成全局可见；门④ 又把"全文"当正文回写，
    造出两个 frontmatter 块 + 矛盾 status。新增 `store.damage_reason()`，
    损坏条目**不进目录/检索/门**，`root.item()` 拒绝读，`health` 增加 `damaged[]`。
  - **H3 `merged` 不消费候选** → `occurrences` 灌水（空跑 reconcile 也能 2→5）、
    pending 永久卡住、`hits=0` 的条目被提升进 L1。`merged` 分支补 `known.add(fp)`，
    已消费文件指纹剔出 `known`（防回退），"判重"与"判消费"共用 `candidate_lines()`。
  - **H4 冷路径无圈隔离**：核心 `--scope` 语义本来是对的，缺陷在插件把 scope 交给
    模型填。`memory_search`/`memory_load` 都用已算好的 `state.circle` 兜底；
    显式 `scope=global` 仍放行；跨圈 `load` 回"没有这条记忆"，不确认其存在。
  - **新发现：dormant 永远回不到目录**。目录只收 `active`，而 `review()` 的
    `used_recently` 分支只刷 `last_verified` 不改 `status`，且 `bring_back()`
    一直没有 CLI 入口 → 记忆被反复使用却回不到上下文、用户无恢复通道、目录单向收缩。
    H1 不修时该缺陷被掩埋，H1 一修立刻暴露。改为：被使用 → dormant 回 `active`
    （记 `review_log revive` 账）+ 新增 `bring-back` 命令；`archived` 保持终态。
  - **测试**：修正 N1 三条"把 bug 当规格"的用例（`detail` 1→0、补 `search` 冷路径、
    `dormant`→`active`）；新增插件 T8d/T13、regressions 7 条、core 2 条、mutation
    M9 对齐 + M9b/M9c。每条新用例都在旧代码上验证过会红。
  - 全量 7 组 7/7；`publish_compat` 重建发布树通过（脱敏扫描 + 守卫），`core/` 子树
    源码逐字节一致。已知未修（路线问题而非工程缺陷）：检索词面化、衰减/阈值调参、
    `core/` 与 `src/` 合并。

- **v3.2.1**（2026-10-28）—— v3.2.0 核心引擎并入主仓库 `core/`，版本号对齐
  - 本目录是 **v3.2.0 重写版核心的唯一真源**（14 个模块 / 纯 stdlib / 零宿主依赖）：
    四道门（`gates.py`）+ 账本（`ledger.py`）+ 存储与指纹（`store.py`）+
    圈与路由（`config.py`/`root.py`）+ 检索（`retrieve.py`）+ 只读记忆包（`packs.py`）+
    dsh 插件本体（`plugin/`）。
  - 从独立仓库并入主仓库 `core/` 子目录（原来只在本地 `D:\work\dsh-memory-ecology`，
    **GitHub 上一直没有**）——主仓库 `src/` 下是 v3.1 的 50 个脚本
    （`write_gate.py`/`eco_quota.py`），两套实现完全不同，只有 `__init__.py`/`similarity.py`
    同名，所以并存而非替换。
  - 版本号 3.2.0 → **3.2.1**；四处（`__init__.py`/`pyproject.toml`/`plugin/package.json`/
    `VERSION.md`）对齐。
  - 修 `test_version_report_shape`：原来硬编码 `"3.2.0"`，发版必红；改为断言
    「四处一致 + 形如 X.Y.Z」——这才是"对齐"该验的东西。

- **v3.2.0**（2026-10-27）—— 四道门真正跑起来 + 剪断 Hermes 脐带

  判据（一句话）：**在没有任何 cron、没有任何 Hermes 的机器上，四张账本都持续有新增行，
  且 dsh 会话里能看到本圈记忆；打开观测舱能一眼看出哪个门死了。**

  - **核心重写为单源**（`src/memory_ecology/`，stdlib only、零宿主依赖、运行期可切根）
  - **门② 根治 promote 恒为 0**：提升判据不再依赖"L1 里已有同源条目"（那是循环判据），
    改为「驻留 ≥14 天且被检索命中 ≥2 次」等三条任一成立
  - **门③ 根治 89 候选 0 晋升**：保留 30 天观察期，新增交叉印证旁路（≥2 个来源可提前晋升）
  - **门④ 修掉"最活跃的记忆反被归档"**：`last_verified` 由"被使用"刷新
  - **零 cron**：四门挂 `agent/created` + `agent/turn-stopping`，幂等可补账
  - **声明式圈** `circles.json`：最长前缀路由 + 圈级预算 + 隔离断言
  - **记忆插件化**：mount（只读挂载）/ integrate（走门）两种接入 + 白名单/哈希/不可执行
  - **观测舱 v0.4**：8 视图，四门实况优先；GUI 与核心同源（数字逐值一致）
  - 测试：核心 86 / GUI 35 / 插件 21 / 兼容与数据安全 22 / 并发 1 套 / 变异 24

- v3.1.0（2026-10-25）—— 回原文 + 配置 Guard + 备份加密 + 手艺分圈证据（见 `CHANGELOG.md`）
- v3.0.1（2026-10-24）—— 注入可观测性 + scope 数据地基
- v3.0.0a1（2026-10-24）—— 门② EXTRUDE 前向记账 · v3 线起步

> 版本号唯一权威来源：`src/memory_ecology/__init__.py: __version__`。
> 校验：`python -m memory_ecology version`（四处一致才 ok）。
