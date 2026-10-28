# 观测舱 · v3.2.0 接入说明

> 这份文档只说一件事：**v2.3 观测舱怎么读到 v3.2.0 的数据**。
> v2.3 原有的九个视图（记忆库/经验/技能/体检/时间线…）不受影响，继续读旧根。

---

## 一、双根并存（这是最需要先理解的一点）

v3.2.0 的数据不在旧 Hermes 根里，所以观测舱同时读**两个数据根**：

| | 旧根 | 新根 |
|---|---|---|
| 路径 | `%LOCALAPPDATA%\hermes` | `%LOCALAPPDATA%\dsh\memory-eco`（或 `--eco-root`） |
| 内容 | 记忆库 / 经验 / 技能 / 体检报告 / 时间线 | 圈 / 四门 / 注入账 / 成本 / 记忆包 |
| 端点前缀 | `/api/*` | `/api/v3.2.0/*` |
| 采集层 | `collector.py`（29 KB，旧 Hermes 线） | `collector_v3_2_0.py`（只调核心 CLI） |
| 视图 | 原有九个 | `gates` `circles` `inject` `packs` `manage` `cost` `firstaid` |

**两边绝不混算。** 每个新视图的角标都写明口径，数字全部来自核心 CLI 的 JSON——
GUI 不自己解析 frontmatter、不自己算圈、不自己判门死活。理由：GUI 自己算一遍，
口径必然与插件/核心漂移，而漂移是**静默**的（两边都"看起来对"，数字不一样）。

---

## 二、怎么启动

```powershell
# 最简：核心自动探测
python integrations/gui/eco_gui.py --eco-root <数据根路径>

# 显式指定核心（自动探测失败时）
python integrations/gui/eco_gui.py --eco-root <根> --eco-core D:\work\eco-dsh\src
```

桌面快捷方式「生态观测舱v2」走的是 `启动生态观测舱v2.3.pyw`，
它会 `--no-browser` 起服务再打开 `http://127.0.0.1:8788/v2.3/`。

### 核心自动探测为什么要「验版本」

`find_eco_core()` 不只检查"目录里有没有 `memory_ecology/cli.py`"，
还会读 `__version__` 确认 **≥ 3.2**（或退而检查 `gates.py`/`packs.py` 是否存在）。

**因为本仓库自己就有一份 v3.1 镜像** `src/memory_ecology/`（没有四门/圈/包）。
先命中它会得到最坏的一种假成功：

> 界面显示"核心可达 ✓"，但每个新视图都 404。

实测踩过。现在 `tests/test_gui_v3_2_0.py::TestCoreProbe` 用断言钉住这条。

探测顺序：`--eco-core` 参数 → `ECO_CORE` 环境变量 → 同仓库兄弟目录 →
`D:\work\eco-dsh\src` / `~/eco-dsh/src` → 已 pip 安装。

---

## 三、七个视图各读什么

| 视图 | 视图 id | 端点 | 核心命令 | 回答什么问题 |
|---|---|---|---|---|
| 四门实况 | `gates` | `/api/v3.2.0/gates` | `health` | **哪个门死了、死了多久** |
| 圈的视角 | `circles` | `/api/v3.2.0/circles` | `circles` | 每个圈多少条、目录预算用了多少 |
| 注入观测 | `inject` | `/api/v3.2.0/inject` | `health`（inject 段） | 这次注入有没有被用回来 |
| 记忆包 | `packs` | `/api/v3.2.0/packs` | `pack list` | 挂了哪些包、哈希对不对 |
| 圈管理 | `manage` | `/api/v3.2.0/circles/config`（读）+ `action`（写） | `circles-write` | 编辑路由与预算 |
| 成本面板 | `cost` | `/api/v3.2.0/cost` | `health`（cost 段） | 常驻开销多少、注入有没有超 KPI |
| 急救箱 | `firstaid` | `/api/v3.2.0/firstaid` | `health` + 本地只读 | 根自证/物化/账本/四门是否正常 |

另有 `/api/v3.2.0/meta`（版本与根自证）与 `/api/v3.2.0/stats`（计数）。

### 四门实况为什么是最重要的一个

旧 GUI 只能看到"门有没有跑过"。v3.2.0 把状态分成五种，
其中四种旧界面**看不出来**：

| kind | 含义 | 该怎么办 |
|---|---|---|
| `ok` | 有近期有效动作 | 不用管 |
| `dead` | 账本**一行都没有** | 查调度/进程根本没跑 |
| `idle-spin` | 有行但全是心跳 | 进程活着没产出 → 查判据输入 |
| `starved` | 有动作但期望的那种**从没成功过** | 判据在真实数据下不可满足 |
| `stale` | 有有效动作但太久 | 查频率 |

判据（写死在核心 `ledger.gate_status()`）：`有效动作 == 0` 或
`最后有效动作距今 > 3 天` 或 `期望动作（promote/demote）== 0`。

**`idle` 心跳与 `SELFCHECK` 自检不算有效动作**——旧项目就是被 246 行 `idle`
骗了，四门停摆两个月没人发现。

---

## 四、写动作（只有 5 个，没有删除）

| 动作 | 风险 | 干什么 |
|---|---|---|
| `circle_write` | strong | 写圈配置（改路由/预算/只读） |
| `pack_enable` | light | 启用记忆包 |
| `pack_disable` | strong | 停用记忆包 |
| `maintain` | light | 顺跑四门（幂等，写前自动备份） |
| `materialize` | light | 重建目录视图（纯派生数据） |

**没有「删除 / 清理 / 裁剪记忆」的动作。** 这不是"暂时没做"，是设计红线：
项目在代码层禁用物理删除。`test_gui_v3_2_0.py::test_no_destructive_action_exists`
扫描源码把这条钉住。

所有写动作的流程都一样：**前端 dry 校验 → 确认闸门（strong 动作）→ 转发核心 CLI**。
GUI 永不直接改 `circles.json` 或 pack 目录——否则就有第二个写者，
校验/备份/一致性全要重实现一遍，必然漂移。

---

## 五、前端契约（改代码前必读）

新增一个视图要动**四处**，漏任何一处都不报错、只静默失效：

1. `lib/ui.tsx` 的 `ViewId` 联合类型
2. `lib/ui.tsx` 的 `VIEW_TITLE`
3. `components/kit.tsx` 的 `APPS`
4. `components/AppWindow.tsx` 的 `ViewRouter`

另外两处建议同步：`components/Switcher.tsx` 的 `ORDER`（巡航轮播）、
`App.tsx` 的 `GOTO`（g 前缀快捷键）。

`tests/test_gui_contract.py`（9 条）断言这四处必须完全一致，
另外还钉住"主屏不许有重名图标"（实测踩过：新增 `firstaid` 视图后，
旧的跳转占位也叫「急救箱」，主屏出现两个同名图标）。

构建与自检：

```powershell
cd integrations/gui-v2.3
npx tsc -b          # 类型检查
npm run build       # 产出 dist/（服务读它，dist 不入库）
```

---

## 六、命名纪律

**代码里不许出现 `v32`。** 它读起来像"版本 32"，与 3.2.0 毫无关联，
已经误导过一次。版本号必须写成 `v3.2.0`（URL 前缀）或语义名（`core_*`、
`fetchCoreMeta`）。`test_gui_contract.py::TestNoVersionLeak` 会扫源码。

---

## 七、排障

| 症状 | 原因 | 处理 |
|---|---|---|
| 新视图全 404，但 `healthz` 说 `core_ok: true` | 命中了 v3.1 镜像 | `--eco-core` 显式指向 v3.2.0 的 `src` |
| `healthz` 的 `core_ok: false` | 找不到核心 | 设 `ECO_CORE` 或传 `--eco-core` |
| 视图空白、控制台报 404 | `dist/` 没重建 | `npm run build` |
| 成本面板显示"这批快照没有分来源字段" | 旧格式 cost 行 | 重跑 `python tools/measure.py` |
| 四门实况全 `dead` | 这个根从没跑过四门 | 点「跑一次四门」或 `python -m memory_ecology gate all` |
