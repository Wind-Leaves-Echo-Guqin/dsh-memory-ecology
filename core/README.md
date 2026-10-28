# dsh-memory-ecology — 记忆生态 · dsh 独立插件 v3.2.0

一套跑在 dsh 里的长期记忆治理系统：**四道门**（写入整合 / 配额巩固 / 画像蒸馏 / 复核降级）
\+ 声明式生态圈 + 只读记忆包 + 观测舱。**零 cron、零配置、零第三方依赖、永不物理删除。**

> 版本唯一权威来源：`src/memory_ecology/__init__.py: __version__`。
> 校验：`python -m memory_ecology version`（四处一致才 ok）。

---

## 它是什么（和"记忆库"的区别）

这不是"存更多、召回更准"，是**治理记忆的生死**：

| 门 | 干什么 | 不跑会怎样 |
|---|---|---|
| **① 写入整合** | 候选 → 去重 → 同源合并 → 类型归一 → 取代链 | 候选堆成山、同一件事存十份 |
| **② 配额巩固** | L2 → L1 提升；L1 超限 → 挤出回 L2（**绝不丢**） | L1 变成"只出不进"的单向漏斗 |
| **③ 画像蒸馏** | 跨会话稳定事实 → USER 画像（观察期 / 交叉印证） | 用户画像永远停在三个月前 |
| **④ 复核降级** | 遗忘曲线：active → dormant → archived（**文件一直在**） | 陈旧记忆腐烂，或最活跃的反被归档 |

**两条硬纪律**（机制保证，不是约定）：
- **物理删除在代码层禁用**：只有状态迁移 + 取代标记 + 隔离区移动。`test_no_unlink_of_memories_anywhere` 扫描源码。
- **唯一写者**：一个数据根只允许一个执行方跑门；并发只追加靠 pid 唯一临时名 + 单次 `O_APPEND` + SQLite WAL（三者缺一即丢写）。

---

## 结构

```
src/memory_ecology/        # L1 核心（stdlib only，零宿主依赖，可脱离 dsh 单测）
  paths.py                 # 根解析（函数式，运行期可切）/ 原子写 / 备份 / SQLite
  store.py                 # frontmatter / L1(§) / L2 detail / 候选 / 圈配置
  ledger.py                # 账本（四门 + 注入 + 成本）与「四门实况」判定
  root.py                  # 数据根自证 + 只读降级
  retrieve.py              # 词面检索 / 目录载荷 / 物化视图 / 渲染（唯一实现）
  similarity.py            # 相似度统一层（ngram / difflib / 可选 embedding）
  config.py                # 阈值常量（**每个键都必须有消费者**，由测试钉住）
  gates.py                 # ★ 四道门
  packs.py                 # 记忆包 mount / integrate + 安全边界
  measure.py               # 成本度量（读真实会话日志）
  version.py               # 版本四处对齐自检
  cli.py                   # 唯一对外接口（JSON 进 JSON 出，退出码分档）

plugin/                    # L2 dsh 插件（只决定内容/形态/时点/预算，不含领域规则）
gui/                       # 观测舱 v0.4（人类宿主：单一文件 + 无构建前端）
tests/                     # 核心 / GUI / 兼容与数据安全 / 并发 / 变异
docs/ARCHITECTURE.md       # 架构与设计取舍（含"被六条判据枪毙的方案"）
docs/OPS.md                # 零运维手册（用户不需要看报告）
run_tests.py               # 一条命令跑完全部测试
```

**硬边界**：插件与核心之间只有一条通道 —— 一次子进程、JSON 进 JSON 出。
插件里不许出现领域规则，核心里不许出现宿主概念。

---

## 装（一条命令）

```powershell
dsh plugin --profile <profile> add link:D:/work/dsh-memory-ecology/plugin

# 可选：配数据根（不配就用 %LOCALAPPDATA%\dsh\memory-eco，首次自动建）
# ~/.dsh/profiles/<profile>/cordis.patch.yml:
#   - id: eco-memory
#     config:
#       rootPath: D:/work/_eco_proto_root
#       catalogLimit: 20
#       catalogMaxChars: 600
```

配置全部可被同名环境变量覆盖：`ECO_HOME` / `ECO_CORE` / `ECO_PYTHON`。

---

## 看（观测舱）

```powershell
# GUI 在 companion 仓库 memory-ecology-兼容版：
#   cd memory-ecology-兼容版 && python integrations/gui/eco_gui.py --eco-root <根>
```

八个视图：**四门实况**（哪个门死了、死了多久）/ 圈的视角 / 注入观测 / 成本面板 /
记忆包 / 圈管理 / 急救箱 / 动作日志。

- 只绑 `127.0.0.1`、端口占用自动上扫、Host 头**精确**校验（防 DNS rebinding）；
- 写动作只有 5 个，全部转发给核心 CLI（核心自己备份），走确认闸门 + `action_log.jsonl`；
- **GUI 永不写记忆内容**（不写 detail/MEMORY/user/pending、不跑门改数据）。

**判据只有一条**：打开观测舱，能一眼看出哪个门死了、这次注入有没有用、记忆目录花了多少字符。

---

## 自检

```powershell
cd D:/work/dsh-memory-ecology
python run_tests.py               # 全量（核心/GUI/插件/并发/兼容/变异）
python run_tests.py --fast        # 跳过变异与并发（改代码时的快反馈）

python tests/test_core.py         # 核心 86 条
python tests/test_gui.py          # GUI 35 条（起真实服务，端口随机）
python tests/test_compat.py       # 旧库兼容 + 数据安全 22 条（**只读**真实生产根）
python tests/test_concurrency.py  # 8 进程并发投递 + 4 进程并发整合
python tests/test_mutation.py     # 变异测试：证明上面几套真的能抓 bug
cd plugin; node test_plugin.mjs   # 插件 21 条
```

---

## 成本

| 项 | 数值 | 性质 |
|---|---|---|
| `memory_load` schema | ~230 字符 | 常驻前缀，付一次 |
| `memory_search` schema | ~300 字符 | 常驻前缀，付一次 |
| 目录段（预算 600） | ≤644 字符 | 常驻前缀，走缓存 |
| **每会话追加消息** | **0 条** | 目录是 section，不是 message |

对照同一台机器：`skill-catalog` 一条 6–7k 字符，`time-context` 单会话最多 83 条。
**KPI：≤3 条 / ≤1,200 字符每会话**；本插件默认 0 条追加。

> 为什么必须物化成文件：实测 dsh 在 `agent/created` 之后 **~10ms** 就装配系统提示词，
> 子进程取数要 80ms。**物化视图不是优化，是正确性要求。**

---

## 核心 CLI（手工排查用）

```powershell
python -m memory_ecology --root <R> init|stats|health|version|prune
python -m memory_ecology --root <R> deposit --session s1 --text "一条经验" --kind lesson --scope proj
python -m memory_ecology --root <R> gate 1|2|3|4|all [--dry-run]
python -m memory_ecology --root <R> catalog --limit 20 --max-chars 600 [--scope proj]
python -m memory_ecology --root <R> search "关键词" [--scope proj] | load <id>
python -m memory_ecology --root <R> circles | route --cwd D:/work/proj
python -m memory_ecology --root <R> circles-write --json '<...>' | materialize
python -m memory_ecology --root <R> pack list|install|enable|disable|uninstall|mounted
```

全部输出单行 JSON。退出码：`0` 成功 / `2` 用法错 / `3` 只读降级 / `4` 内部错误 /
`5` 校验失败（pack / 圈配置非法）。

---

## 已知边界（诚实清单）

1. **检索是词面的**：中文按 2-gram + 整串；短查询与同义改写会漏。embedding 后端留了接口，默认关。
2. **目录排序只看命中次数与新旧**，不看语义相关性。新任务若与历史无关，应主动 `memory_search`。
3. **本次会话新增的条目要到下次会话才进目录**（装配在 10ms 内完成）。
4. **`hit` 表会增长**：有 `prune_hit()`（默认 180 天）+ `prune` 子命令，但没有自动定时调用。
5. **门③ 的 USER 替换只按相似度判定**，"语义是否矛盾"仍靠人（没有 LLM 终审）。
6. **门④ 的"使用"信号来自 `hit` 表**，只覆盖通过本插件的检索/加载。

---

## 设计文档

- `docs/ARCHITECTURE.md` —— 分层、数据布局、四道门的判据与"为什么旧实现空转"、被枪毙的方案
- `docs/OPS.md` —— 零运维手册：出问题怎么查、怎么回滚、配置项
- `CHANGELOG.md` —— v3.2.0 完整变更（含迁移注意事项）
