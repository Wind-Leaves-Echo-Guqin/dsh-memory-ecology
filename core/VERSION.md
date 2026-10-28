# 版本

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
