# 记忆生态 v3.2.0 · 零运维手册

> 目标：**用户不需要看任何报告、不需要装 cron、不需要配环境变量。**
> 装一次，之后它自己跑；出问题有 GUI 一眼看出来。

---

## 1. 三条命令

```powershell
# ① 装（一条命令，装完就能用）
dsh plugin --profile <你的profile> add link:D:/work/dsh-memory-ecology/plugin

# ② 看（人类观测舱，只绑本机回环）
python integrations/gui/eco_gui.py --eco-root <根>   # 在 companion 仓库里

# ③ 自检（改完代码/怀疑不对时）
cd D:/work/dsh-memory-ecology; python run_tests.py
```

**不需要**：装 cron / 设环境变量 / 建数据根 / 装 Python 依赖（核心只用标准库）。

---

## 2. 它自己会做什么

| 时点 | 动作 | 幂等？ |
|---|---|---|
| 插件启动 | `init`（建根，幂等）+ `materialize`（把每个圈的目录写成文件） | ✅ |
| 每次会话创建（`agent/created`） | 门① 补账（整合 pending 候选）+ 注册本圈目录段 | ✅ |
| 每轮收尾（`agent/turn-stopping`） | 四门顺跑（有 10 分钟间隔门控，不会每轮真跑） | ✅ |
| 任何时刻 | 进程被杀后重启，下一次会话自动补账 | ✅ |

**没有 cron**。四道门挂在会话生命周期上，幂等性由指纹 + 文件状态保证，所以漏一次可补、
重复触发无害。

> ⚠️ **实现层的一个坑（已修，写在这里防复发）**：`agent/session-start` 事件在 dsh 0.2.1-rc.x
> 上**不存在**（它被并入了 `agent/created`）。监听一个永不派发的事件，cordis 既不报错也不告警
> ——上一版插件就是这么"装了但会话开始补账从未生效"的。现在只监听 `agent/created` 与
> `agent/turn-stopping`，并由 `plugin/test_plugin.mjs::T3x` 钉住。

---

## 3. 我该看哪里

打开观测舱，八个页面：

| 页面 | 回答什么问题 |
|---|---|
| **四门实况** | **哪个门死了、死了多久**（空转标红，区分"没跑"与"跑了没产出"） |
| **圈的视角** | 我有几个圈、每圈多少条、目录预算占了多少 |
| **注入观测** | 这次注入有没有用（回取率/死重率） |
| **成本面板** | 工具 schema / 系统提示词 / 目录各花多少字符，KPI 有没有超标 |
| **记忆包** | 挂了哪些只读记忆包、哈希校验过不过 |
| **圈管理** | 改路由前缀 / 预算 / 只读开关（写走确认闸门 + 自动备份） |
| **急救箱** | 根自证 / 物化完整性 / 账本一致性 / 四门活性，一键诊断 |
| **动作日志** | 谁在什么时候改了什么（GUI 自己的审计） |

**判据只有一条**：打开观测舱，能一眼看出哪个门死了、这次注入有没有用、记忆目录花了多少字符。
**看不见的治理等于没有治理。**

---

## 4. 出问题怎么办

### 4.1 记忆目录没出现

按顺序查（GUI 急救箱会直接给出结论）：

1. `python -m memory_ecology health` → `materialized` 是 `false`？
   → 跑 `python -m memory_ecology materialize`。
2. 目录存在但插件不注入 → 看插件日志里的 `eco-memory: 初始化失败（降级…）`。
   最常见是 Python 解释器找不到：设 `ECO_PYTHON` 指向绝对路径。
3. 圈不对（看到的是别的项目的记忆）→ 看「圈的视角」，核对 `circles.json` 的 `cwd_prefix`。
   **它按最长前缀匹配**；没有配置时按启动目录末级名自动划分。

### 4.2 四门显示空转

| 现象 | 含义 | 怎么办 |
|---|---|---|
| `kind=dead`（账本 0 行） | 这一门**从没跑过** | 手动跑一次 `python -m memory_ecology gate all`；若仍 0 行，看核心报错 |
| `kind=idle-spin`（有行全是 idle） | **进程活着但没产出** | 看 `blocked[]` 告诉你为什么没产出（没有候选 / 判据不满足 / 冷却期） |
| `kind=stale`（有动作但太久） | 最近停摆了 | 检查插件是否被禁用；看会话是否还在跑 |

**门②/门③ 长期 0 promote 的历史原因已查清**（见 `docs/ARCHITECTURE.md` §3.2/§3.3）：
不是坏了，是判据在真实数据下不可满足。v3.2.0 已改成"有使用证据即可提升"和"交叉印证可提前晋升"。

### 4.3 数据根被指错了

根自证会拦住它：写前读 `.eco/root.json`，不匹配就**只读降级 + 留痕**，绝不写坏数据、不阻塞会话。
急救箱会显示 `root-selfproof` 失败与具体原因（`root-not-initialized` / `schema-mismatch` / `root-not-writable`）。

### 4.4 想回滚一次改动

| 改了什么 | 怎么回 |
|---|---|
| 门② 改了 `MEMORY.md` | 同目录 `MEMORY.md.bak-配额前-*`（保留最近 8 份，更早的**移入** `_bak-archive/`，**从不删除**） |
| 门③ 改了 `USER.md` | `USER.md.bak-蒸馏前-*` |
| 改了 `circles.json` | `circles.json.bak-改圈前-*` |
| 条目被"取代" | `python -m memory_ecology supersede` 的逆操作 `restore_superseded()`；或直接改 frontmatter 的 `fate` |
| 条目被降级/归档 | 改回 `status: active`（**文件一直还在**，从没被删过） |

**这个项目在代码层禁用物理删除。** `tests/test_core.py::test_no_unlink_of_memories_anywhere`
扫描全部源码，出现 `unlink(`/`os.remove(`/`rmtree` 就红（只放行 `.tmp` 与写探针清理）。

---

## 5. 常见配置（全部可选）

放在 `<root>/configs/eco.toml`：

```toml
[quota]
memory_quota = 3000        # L1 配额（字符）；触发线 = 85%
promote_min_occurrences = 3
promote_stable_days = 14   # 判据 B/C 的"驻留够久"
promote_min_hits = 2       # 判据 B 的"被用过"

[review]
review_days_semantic = 90
review_days_episodic = 30

[distill]
distill_observe_days = 30
distill_corroborate = 2    # 交叉印证数（早于观察期的唯一旁路）
```

**每个键都必须有消费者**——`test_config_keys_have_consumers` 会扫描源码，加了键却没人读
就红。这是把"改了配置没反应"这类事故变成断言。

环境变量（进程级，不进用户环境）：

| 变量 | 作用 |
|---|---|
| `MEMORY_ECOLOGY_ROOT` | 指定数据根（优先于布局派生） |
| `ECO_HOME` | 插件侧的数据根（等价） |
| `ECO_PYTHON` | 指定解释器绝对路径 |
| `ECO_CORE` | 指定核心 src 目录 |
| `MEMORY_ECOLOGY_SIM_BACKEND` | `ngram`（默认）/ `difflib` / `embedding` |

---

## 6. 手工排查速查

```powershell
# 核心 CLI（全部输出单行 JSON）
python -m memory_ecology --root <R> init
python -m memory_ecology --root <R> deposit --session s1 --text "一条经验" --kind lesson --scope proj
python -m memory_ecology --root <R> gate 1|2|3|4|all --dry-run
python -m memory_ecology --root <R> catalog --limit 20 --max-chars 600 [--scope proj]
python -m memory_ecology --root <R> search "关键词" --limit 8 [--scope proj]
python -m memory_ecology --root <R> load <id>
python -m memory_ecology --root <R> circles
python -m memory_ecology --root <R> route --cwd D:/work/proj
python -m memory_ecology --root <R> materialize
python -m memory_ecology --root <R> health        # GUI 的数据源
python -m memory_ecology --root <R> stats
python -m memory_ecology --root <R> version       # 版本四处对齐自检
python -m memory_ecology --root <R> prune         # 账本/备份归档（不做物理删除记忆）
python -m memory_ecology --root <R> pack list|install|enable|disable|uninstall|mounted
```

退出码：`0` 成功 / `2` 用法错 / `3` 只读降级 / `4` 内部错误 / `5` 校验失败（pack / 圈配置非法）。

---

## 7. 自检与验收

```powershell
cd D:/work/dsh-memory-ecology

python run_tests.py                 # 全量（核心 + 插件 + GUI + 变异）
python tests/test_core.py           # 核心
python tests/test_gui.py            # GUI（起真实服务，端口随机，不占 8788）
python tests/test_mutation.py       # 变异测试：证明测试真的能抓 bug
cd plugin; node test_plugin.mjs     # 插件
```

**验收判据（v3.2.0）**：
1. 在**没有任何 cron、没有任何 Hermes** 的机器上，四张账本都有新增行；
2. dsh 会话里能看到本圈记忆（目录段非空且内容正确）；
3. 观测舱打开就能看出哪个门死了、注入有没有用、目录花了多少字符；
4. 全部测试绿，变异测试存活 = 0。
