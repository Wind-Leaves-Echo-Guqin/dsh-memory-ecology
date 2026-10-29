# 🧠 dsh-memory-ecology · 记忆生态

**让 agent 不再每次会话都失忆。**

[![version](https://img.shields.io/badge/version-v3.2.1-2ea043?style=flat-square)](VERSION.md)
[![python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)]()
[![core deps](https://img.shields.io/badge/核心依赖-0-success?style=flat-square)]()
[![local](https://img.shields.io/badge/数据-纯本地-007ec6?style=flat-square)]()
[![deletion](https://img.shields.io/badge/物理删除-代码层禁用-8957e5?style=flat-square)]()
[![hosts](https://img.shields.io/badge/宿主-dsh%20%7C%20Hermes%20%7C%20GUI%20%7C%20任意-8957e5?style=flat-square)]()

> 多数记忆方案在解决「**怎么召回**」；
> 我们解决它前面那层——「**记忆的生命周期怎么治理**」。

源自 [memory-ecology](https://github.com/Wind-Leaves-Echo-Guqin/memory-ecology)（Hermes 版）。
本仓库把**可移植核心**与**宿主适配层**分离，让 Hermes 之外的 agent（当前：dsh）复用同一套记忆生命周期治理与经验笔记本。

---

## 🎯 它是什么

把 agent 的记忆当成一个**生态系统**：有界、可逆、可追溯。

agent 的记忆通常"只进不出、写入靠自觉、超限不可见"。本生态用**四道门**治理记忆生命周期，
用**经验笔记本**沉淀"做事经验"（错误/成功/已验证链路）——规则驱动为主、可回滚：
四道门自动运转零维护，LLM 仅参与门①相似/矛盾终审与门③措辞，其余环节零 LLM、离线可用。

| 门 | 干什么 | 不跑会怎样 |
|---|---|---|
| **① 写入整合** | 候选 → 去重 → 同源合并 → 类型归一 → 取代链 | 候选堆成山、同一件事存十份 |
| **② 配额巩固** | L2 → L1 提升；L1 超限 → 挤出回 L2（**绝不丢**） | L1 变成"只出不进"的单向漏斗 |
| **③ 画像蒸馏** | 跨会话稳定事实 → USER 画像（观察期 / 交叉印证） | 用户画像永远停在三个月前 |
| **④ 复核降级** | 遗忘曲线：active → dormant → archived（**文件一直在**） | 陈旧记忆腐烂，或最活跃的反被归档 |

**两条硬纪律**（机制保证，不是约定）：

- **物理删除在代码层禁用**：只有状态迁移 + 取代标记 + 隔离区移动，`test_no_unlink_of_memories_anywhere` 扫描源码。
- **唯一写者**：一个数据根只允许一个执行方跑门；并发只追加靠 pid 唯一临时名 + 单次 `O_APPEND` + SQLite WAL（三者缺一即丢写）。

系统由两条设计哲学不同的线构成（此前统称"四道门+经验笔记本"，易误读为同质系统）：

| | 自动治理线（四道门） | 半自动沉淀线（经验笔记本） |
|---|---|---|
| 目标 | 自动化运转（提升/挤出/蒸馏/复核） | 低置信度标记 + 人工兜底 |
| 追求 | 可逆、幂等、稳态（防震荡） | 可观测、可追溯、零误伤 |
| 边界 | 只出合并/复活**候选**，不自动裁决 | signals/merge/gold 均只标记不判定 |

**定位澄清**：`eco_health_check.py` 检查的是 skills/ 生态（frontmatter 合法性、引用图、cron 健康），
**不是**四道门的运行状态仪表盘；门级运行状态看 `core/` 的账本（`eco health`）与各门日志。
`memory_query.py` 是记忆（detail+archive）的检索入口。

---

## 🔀 为什么不一样

多数记忆方案回答的核心问题是「**怎么召回**」（embedding → 向量库 → 检索拼接）；本项目的重心在它
前面的一层：「**记忆的生命周期怎么治理**」——写入、合并、挤出、蒸馏、遗忘、复核，检索只是治理
结果的消费端。

| 维度 | 典型向量 RAG 记忆 | 本项目 |
|---|---|---|
| 增长模型 | 只进不出，越攒越脏 | 配额恒有界（门②）+ 遗忘曲线（门④） |
| 事实冲突 | 新旧并存，召回看运气 | 矛盾失效 superseded → 隔离区，历史可追溯 |
| 删除 | 物理删除或放任 | 代码层禁用物理删除，一切状态转换可逆 |
| 做事经验 | 与事实混在同一库 | 独立经验笔记本：按需检索、只标记不判定、人工兜底后技能化 |
| 依赖 | 通常绑定 embedding 服务 | 规则优先、离线可用；LLM 仅门①终审与门③措辞 |
| 宿主 | 单应用视角 | 共享数据根 + 单写入方纪律，多宿主并存 |

> 简化对比，仅供定位：各家方案均在演进，以官方文档为准。另注意本项目的检索当前为词面级
> （见「⚠️ 当前边界与已知限制」），召回质量不是它的卖点——生命周期治理才是。

---

## 🏗 架构总览

```mermaid
flowchart TB
    subgraph HOST ["宿主层 · integrations/（薄适配：只读检索 + 向 pending/ 追加候选）"]
        direction LR
        H1["dsh（EAC / cordis 插件）"]
        H2["Hermes 参考实现"]
        H3["观测舱 GUI gui-v3"]
        H4["…任意能 spawn Python 子进程的宿主"]
    end

    subgraph CORE ["治理核心 core/src/memory_ecology（唯一真源 · 纯 stdlib · 零宿主依赖）"]
        direction LR
        G["<b>四道门</b><br/>① 写入整合 → ② 配额巩固<br/>③ 画像蒸馏 → ④ 复核降级"]
        P["<b>生态圈 / 记忆包</b><br/>circles · packs（只读 mount）"]
        Q["<b>检索与视图</b><br/>search · catalog · materialize"]
    end

    R[("<b>数据根</b><br/>memories/ · .eco/ · 门账本")]
    LEG["src/memory_ecology<br/>v3.1 历史线（50 个 CLI 脚本）"]

    HOST -->|"子进程 · JSON 进 JSON 出"| CORE
    CORE -->|"唯一写入方（单写入方纪律）"| R
    LEG -.->|"同一数据根 · 迁移完成后合并"| R
```

- **宿主只做两件事**：spawn 子进程调检索 CLI、向 `pending/` 追加候选——记忆的读写大权只在治理核心手里。
- **插件与核心只有一条通道**：一次子进程、JSON 进 JSON 出。插件里不许出现领域规则，核心里不许出现宿主概念。
- **多宿主并存**：不同宿主把数据根指向同一处即可共享记忆与经验。

---

## 🚀 30 秒上手

### 1) 只用核心 CLI（任何宿主/无宿主）

```bash
cd core/src            # 免安装直接跑（也可 pip install -e core 后在任何目录用）
python -m memory_ecology --root <数据根> init
python -m memory_ecology --root <数据根> deposit --session s1 --text "用户偏好中文回复"
python -m memory_ecology --root <数据根> gate all        # 顺跑四门
python -m memory_ecology --root <数据根> search "中文"    # 词面检索
python -m memory_ecology --root <数据根> materialize     # 物化目录视图到 .eco/catalog/
python -m memory_ecology --root <数据根> health          # 健康快照（GUI 用）
python -m memory_ecology version                        # 版本四处对齐自检

cd .. && python run_tests.py                            # 全量测试（7 组）
```

核心是纯 stdlib、零第三方依赖；每个子命令 JSON 进、JSON 出，退出码分档，便于任何宿主封装。

### 2) dsh 宿主

```powershell
dsh plugin --profile <profile> add link:<本仓库>/core/plugin

# 可选：配数据根（不配就用 %LOCALAPPDATA%\dsh\memory-eco，首次自动建）
# ~/.dsh/profiles/<profile>/cordis.patch.yml:
#   - id: eco-memory
#     config:
#       rootPath: D:/work/_eco_root
#       catalogLimit: 20
#       catalogMaxChars: 600
```

配置全部可被同名环境变量覆盖：`ECO_HOME` / `ECO_CORE` / `ECO_PYTHON`。

### 3) Hermes 宿主

`integrations/hermes/` 为参考实现（state.db 会话增量提取 / pre_llm_call 报错注入 hook / cron 健康告警 /
基因库快照），展示如何把核心接到"有会话库和调度器的宿主"上。其他宿主照此模式写适配器。

### 4) 观测舱 GUI（可选，人类宿主）

**当前发布版 = `integrations/gui-v3/`（v3.2.0，液态玻璃 / iOS 外壳）**，
在原有九视图之外新增七个：四门实况 / 圈的视角 / 注入观测 / 记忆包 / 圈管理 / 成本面板 / 急救箱。

```bash
# gui-v3 是 vite 工程，dist 不入库，首次用需构建：
cd integrations/gui-v3 && npm install && npm run build

# 启动（默认 127.0.0.1:8788，自动开窗口）
python integrations/gui/eco_gui.py
# → 浏览器打开 http://127.0.0.1:8788/v3/   （/ 也指到 v3）
# 也可双击 integrations/gui/启动生态观测舱v3.2.0.pyw（pythonw 直启，无控制台黑框）
# 服务异常自救：双击 integrations/gui/生态急救箱.cmd（自检→修复→重启→开窗）
```

| 前端目录 | 路由 | 状态 |
|---|---|---|
| `integrations/gui-v3/` | `/v3/` | **当前发布版（v3.2.0）** |
| `integrations/gui/`（static） | `/`（v0.3 静态版） | 历史版本，保留 |
| `gui-v2` / `v2.1` / `v2.2` / `v2.3` | 各自路由 | 历史版本，不随发布物分发，按对应 tag checkout |

纯离线、仅绑 `127.0.0.1`；写动作只有 5 个（圈配置 / 记忆包开关 / 维护 / 物化），
全部转发核心 CLI 并走确认闸门，**没有任何删除记忆的动作**。
Hermes 专属动作（cron 重跑等）在未安装 Hermes 的机器上降级为"可复制命令"。

---

## 📦 仓库结构

**v3.2.1 起两层并存**（`core/` 是唯一真源，`src/` 是历史线，尚未合并）：

| 目录 | 是什么 | 版本 |
|---|---|---|
| **`core/`** | **v3.2.0 核心引擎（唯一真源）**：四道门 + 多生态圈 + 只读记忆包 + 检索，纯 stdlib；dsh 插件本体 `core/plugin/` 也在这里 | **3.2.1** |
| `core/src/memory_ecology/` | 14 个模块：`gates.py` / `ledger.py` / `store.py` / `paths.py` / `retrieve.py` / `packs.py` / `config.py` / `root.py` / `similarity.py` / `measure.py` / `version.py` / `cli.py` | 3.2.1 |
| `core/tests/` | 核心 / 兼容与数据安全 / 并发 / 稳定性 / 变异（`run_tests.py` 7 组） | — |
| `core/docs/` | `ARCHITECTURE.md`（含 7 blocker + 11 major 缺陷清单）、`OPS.md`（零运维手册） | — |
| `src/memory_ecology/` | v3.1 治理核心（历史线，50 个 CLI 脚本）：写门/配额/蒸馏/复核/eco_note/向量缓存 | 3.1.0 |
| `integrations/hermes/` | Hermes 参考实现（state.db / cron 耦合件） | — |
| `integrations/dsh/` | dsh 适配器（`package/`＝EAC；`package-cordis/`＝官方桌面版 cordis） | — |
| `integrations/gui-v3/` | 观测舱当前发布版（液态玻璃 / iOS 外壳，七新视图） | 3.2.0 |
| `integrations/gui/` | 观测舱服务端（8788）+ v0.3 静态前端（历史） | — |
| `models/` | 本地 embedding 权重（bge-small/base/large-zh-v1.5 + text2vec；按需 fetch，不入库） | — |
| `tools/` | `sync_trees_check.py`（双树漂移比对） | — |

> **为什么要两层**：`core/` 是重写版（14 个模块，`gates.py`/`ledger.py`/`packs.py`），
> 与 `src/` 下的 v3.1（50 个脚本，`write_gate.py`/`eco_quota.py`）**实现完全不同**，
> 只有 `__init__.py` / `similarity.py` 同名。直接覆盖会毁掉 v3.1 的 50 个脚本以及依赖它们的
> `integrations/hermes` / `integrations/dsh`，所以并存而非替换。迁移完成后再合并。

---

## ⚠️ 当前边界与已知限制

诚实边界，先读再用。

> [!IMPORTANT]
> **① 检索是词面级的，不是语义检索。** 默认后端 difflib（字符级子串 + 词面打分），召回依赖关键词选择。

已有的补强路（均**默认关闭**、可在线观测后再决定默认化）：

- **时间维**：三档半连续衰减 + `--since/--until/--window` 发生时间过滤；零结果率观测（`eco_retrieval_report.py` / GUI「检索质量」页签）。
- **OR 召回兜底**：`--recall` 任一 term 命中即召回，RRF 融合排序（补多词 AND 的召回缺口）。
- **向量检索路**：`--semantic` 本地向量缓存 shadow index（余弦排序；缓存可丢弃，源真源仍是 Markdown）。
- **本地 embedding**（`bge-small-zh-v1.5` + CLS pooling，golden 182 对 AUC 0.93 vs difflib 0.90）为
  **可选后端，尚未默认启用**——选型依据与启用方式见 [models/README.md](models/README.md)、
  `src/memory_ecology/lib/similarity.py` 与 [ARCHITECTURE_TODO.md](ARCHITECTURE_TODO.md) §三。

> [!NOTE]
> **② 经验笔记本是半自动线**：signals/merge/gold 只标记不判定，成熟/合并需人工兜底
> （见「🎯 它是什么」的两条纪律线对照）。

> [!WARNING]
> **③ Status: 🚧 Work in Progress**（2026-10-04 全量审计后进入收敛期）。
> 已知问题与架构待办见 [ARCHITECTURE_TODO.md](ARCHITECTURE_TODO.md)；`core/` 的缺陷清单见
> `core/docs/ARCHITECTURE.md` §11。

---

## 🧩 兼容什么

「兼容」的不是某一个产品，而是「任何 agent 宿主」——三层含义：

| 层 | 兼容对象 | 说明 |
|---|---|---|
| **可移植核心** | 任意能 spawn Python 子进程的宿主 | `core/src/memory_ecology`（四道门 + 生态圈 + 记忆包）零宿主依赖，CLI / 脚本 / MCP 封装均可直接调用；`src/memory_ecology`（v3.1）继续可用 |
| **Hermes 适配层** | 上游 [memory-ecology](https://github.com/Wind-Leaves-Echo-Guqin/memory-ecology) 的宿主（参考实现全套） | `integrations/hermes/`：state.db 会话增量提取、pre_llm_call 报错注入 hook、cron 健康告警、基因库快照 |
| **dsh 适配层** | dsh 双插件体系 | `integrations/dsh/`：`package/`＝EAC 变体（Extension SDK `provideContext`/`registerTool`）；`package-cordis/`＝官方桌面版（cordis patch 层 `agent/pre-step` 注入 + `ctx.tools.register`）——均只读 + fail-open |
| **观测舱 GUI** | 人类（本机浏览器 / pywebview 原生窗口） | `integrations/gui-v3/`：本地可视化驾驶舱（只读观测 + 走确认闸门的写操作），仅绑 127.0.0.1 |
| **共享数据根** | 多宿主并存 | 多宿主指向同一数据根即可共享记忆与经验；**单写入方纪律**保证四道门只有一个调度器 |

**适配新宿主**：照 `integrations/` 现有模式写薄壳（每回合 hook → subprocess 调检索 CLI → 拼注入文本），
核心零改动；接入步骤见 [integrations/dsh/README.md](integrations/dsh/README.md)。

---

## ✅ 测试

```bash
# 核心引擎（v3.2.1，7 组全绿）
cd core && python run_tests.py

# v3.1 历史线（cwd=src/memory_ecology）
python test_eco_gates.py              # 四门持久化回归
python test_eco_note_error_query_v2.py  # 根因分层回归
python test_eco_version.py            # 健康行口径回归
python test_memory_query_time.py      # S1 时间维检索回归
python test_write_gate_conflict_tri.py  # S2 失效理由回归
python test_evidence_chain.py         # S3 证据链回归
python test_retrieval_report.py       # S4 零结果率观测回归
python test_vector_cache.py           # S6 向量缓存 shadow index 回归
python test_recall_rrf.py             # S7 OR 召回 + RRF 融合回归
python test_concurrent_writes.py      # 并发写入（同门/跨门/stale 锁/原子写可见性/同进程重入）
python test_safeio.py                 # 安全写路径回归
python test_similarity.py             # 相似度后端回归

# dsh 适配器（cwd=integrations/dsh）
node test_smoke.js                    # EAC：注册面 / fail-open / 探针 reason 日志
node test_common.js                   # 子进程语义（退出码分档 / `--` 分隔 / 解释器绝对路径 / 最小环境）
node test_cordis.mjs                  # 官方桌面版：纯函数 + apply() 端到端（假宿主包 + 桩 Python）
```

> 以上测试缺 python 或 zstandard 时会**显式失败**而不是静默跳过——它们是关键护栏。

---

## 📄 版本与历史

- 当前版本 **v3.2.1**（2026-10-28）——版本唯一真源：`core/src/memory_ecology/__init__.py::__version__`
  （核心四处：`__init__.py` / `pyproject.toml` / `plugin/package.json` / `VERSION.md`，`python -m memory_ecology version` 校验）。
- 版本要点：[VERSION.md](VERSION.md)（核心层另有 `core/VERSION.md`，两处需同步）；完整变更：[CHANGELOG.md](CHANGELOG.md)。
- 历史版本用 **git 历史 + 标签**管理：`git checkout <tag>` 或 Releases 页下载任意版本。

## 📄 License

MIT © 2026 Wind-Leaves-Echo-Guqin（沿用上游 memory-ecology 许可）
