# Memory Ecology Compat · 版本

- **v2.5.0**（2026-10-18）—— 入口纪律统一 · 并发写入修复 · 第一公里补齐（用户模拟测试驱动）
  - **`lib/cli_base.py` 新增**：统一 CLI 入口基座（编码兜底 / 数据根 / 目录 / 互斥 / 失败出口）。
    26 个 CLI 脚本接入，解决"四道门四项纪律没有一项齐"的结构性问题。
  - **编码**：22 个脚本缺 stdout 兜底 → 中文 Windows（GBK）下 print emoji 崩；`write_gate`
    崩在收尾位置造成"数据已落盘却 exit 1"的假失败；异常处理里的 print 也会崩，把真正的
    FileNotFoundError 掩盖成编码错误。现全部走 `bootstrap()`（`errors="replace"`）。
  - **数据根**：门④ `eco_review` 自算 `__file__` 无视 `MEMORY_ECOLOGY_ROOT`（本线 2026-10-11
    已修，本版同步进发布物）；门② `eco_quota` 锁文件不建父目录 → 自定义根必崩，改走
    `gatekit.acquire_lock_auto()`。
  - **并发**：`gatekit` 把"本进程持有"当残留锁回收 → 同进程重入不再互斥（多 agent 同进程会踩）；
    `lib/fs.atomic_write` 在 Windows 上 `os.replace` 遇读者占用会抛 WinError 5/32
    （POSIX 允许 rename 覆盖已打开文件，Windows 不允许）→ 三级重试 + 可回滚兜底。
  - **静默丢数**：`eco_note_adopt` 用 `utf-8` 读 pending 会把 BOM 留成 `﻿`，
    正则匹配不到首行 → 第一条候选静默丢失；改 `utf-8-sig`。
  - **报错改进**：`lib/safeio` 的 type 枚举校验报错现在列出合法值。
  - **第一公里**：README 新增「0) 先存一条记忆」与「如何迁移已有记忆」；
    `eco_health_check` 新增 `--no-write`（原来忽略 `--help` 直接执行并写 3 处），空库不再给健康分。
  - **LLM 端点可配**：`lib/llm.py` 的 base_url/model 此前硬编码，只有 key 可配 →
    接第三方 OpenAI 兼容网关时 401 且报错误导。现支持 `MEMORY_ECOLOGY_API_BASE` /
    `MEMORY_ECOLOGY_API_MODEL`（运行期解析，不设时行为不变）。
  - **`config.env_key` 报错改进**：原本文案提到环境变量但该函数只读 .env，已改为列出三种解法。
  - **自适应召回条数（v2.5.1，实测驱动）**：`memory_query` 此前固定 `--top 5`。
    LongMemEval 实测：多会话类题平均需要 **2.6 条**证据，top-5 只能全命中 **66.9%**
    的题，top-20 提到 **87.2%**；端到端 multi-session 正确率因此
    **59.1% → 72.1%（+13.0pp）**，全量 MACRO **78.5% → 90.3%**。
    现按查询形态自适应：聚合类（"总共/多少次/how many"）与多跳类
    （"分别/顺序/对比/respectively"）放宽到 20，其余仍为 5。
    单点查询不受影响（实测 R@1 已达 0.87，加大只增噪声）。
    显式 `--top N` 仍优先。
  - **偏好类查询的读法提示**：实测 `single-session-preference` 从 50.0% → 93.3%，
    关键不在检索（金标 83% 已命中）而在**呈现姿态**——同一份证据，
    当"事实资料"读 vs 当"用户画像"读，差 40pp 以上。已记录为
    `docs/EXP-LOG/` 的实证结论，供下游上下文组装参考。
  - **测试**：新增 `test_concurrent_writes.py`（C1–C5）+ `test_mutation.py`（6 变异体，全抓到）。
  - **CI 门禁**：`check_entry_discipline.py`（D1 编码 / D2 数据根 / D3 建目录 / D4 锁成对）。
- **v2.4.0**（2026-10-07）——向量缓存（灰度）· OR 召回 + RRF 融合（外部调研落地第二期）
  - S6 向量缓存 shadow index：条目级 content-hash 增量/换模型整库失效/prune 镜像；
    缓存一律可丢弃（坏=无损）；`--semantic` 向量路 + 自动回退；灰度不切默认
  - S7 OR 召回 + RRF：原计划 FTS5 实测证伪（porter 英文专用/trigram 需≥3 字符 vs 中文 2 字词/
    unicode61 与子串路重合）→ 改道零依赖 OR 召回 + RRF(K=60) 融合；`--recall` 默认关=行为锁定
  - 新增测试 19 用例；FTS5 登记为 >1000 条规模化路径

- **v2.3.0**（2026-10-07）——时间维检索 · 证据链 · 零结果率观测（外部调研落地第一期）
  - S1 时间维检索：三档半连续衰减（≤7d +10 / ≤30d +5 / 31–120d 线性）+ 发生时间回退链 +
    `--since/--until/--window` 过滤（valid_time→transaction_time→first_seen）
  - S2 矛盾失效带理由：superseded_reason/superseded_at 入条目，检索命中返回理由，
    复活提案带失效原因（隔离区变可查历史时间轴）
  - S3 证据链：CONFLICT 新旧条目 evidence/superseded_by 互指 + 门③ distilled_at 蒸馏状态位
    （源失效/复活清空 → 可重蒸馏）
  - S4 零结果率观测：eco_retrieval_report.py + GUI「检索质量」页签（bge 默认化在线证据）
  - 修复 scan_memory_dirs/revive def 时绑定默认参数；sync_trees_check 行尾归一化

- **v2.2.5**（2026-09-30）——dsh 适配器审计修复（15 条），新增发布守卫
  - **内核 v4 拒收修复（关键）**：注入消息 source.kind 由 `'plugin'` 改为 producer 自己的
    名字（`'eco-note'`）。dsh 0.2.0-rc.2（hostProtocolVersion 4）的
    `assertV4MessageSources` 会抛 `format v4 message requires a producer-owned source kind`；
    官方 dsh-time-context/tmux-context 同款写法。v2.2.4 的发布物在 0.2.0-rc.2 上注入必被拒。
  - 检索工具语义修正：stdout 优先 + 退出码分档（未命中 exit 1 ≠ 检索不可用）；
    位置参数走 `--` 分隔（检索词以 `-` 开头不再被 argparse 当选项）；top 夹到 1..20
  - 数据根默认值对齐核心 `lib/config.py`（开源 `src/memory_ecology` 布局下原会算成
    `<repo>/src`，与核心认定的 `<repo>` 不一致 → 注入永远 no-hit）
  - 子进程加固：解释器解析为绝对路径（堵 Windows 当前目录植入 `python.exe`，CWE-427）；
    默认最小环境（只透传系统 + DSH_/ECO_/MEMORY_ECOLOGY_，`ECO_KEEP_FULL_ENV=1` 可退回）
  - 会话定位改用 `agent.id` 精确匹配（原实现按 mtime 猜"当前会话"，并发会话会串扰）；
    完全不像 id 的形态才回落 mtime；id 形态合法但文件未落盘也**不回落**（否则会拿到别的会话）
  - 报错识别改"只认 tool/result + 强证据"：`message.isError` / `Traceback (most recent call last)`
    / **独占一行**的非零 `[exit code: N]`。真机样本：原整行正则 19 条 tool/result 命中里只有
    3 条是真失败（其余是读源码/写报告/引用文档）。残余路径已写明：agent 用 read 工具读一个
    含 traceback 的日志文件仍会被当成失败（见 integrations/dsh/README 已知边界）
  - 有界解压（流式 + 尾部 2MB + 总长 64MB 上限）；状态**每会话一个文件**（原全局单文件：
    6 并发实测丢 4/6 桶 → 丢冷却与去重，同一 episode 每轮重复注入；旧文件兼容读 + 原子写）
  - 注入载荷卫生：来源与非指令声明、哨兵/注入头中和、总长 1200 字上限；探针 reason 落日志
    （cordis 与 EAC 两端），激活日志带宿主包版本（实测本机 profiles 里的 `@deepseek-ai/dsh-tools`
    是 npm 全局 dsh 0.1.1-rc.2 的 junction，而内核是桌面版 0.2.0-rc.2——漂移进日志便于发现）
  - 核心 `eco_note_query` 加库快照缓存（(mtime,size) 失效）：`rank()` 本机 237 条库 × 20 关键词
    368ms → **31ms**（含首次解析全库）；同一进程内第二次 1ms。注意探针每步是新进程，故每步
    实际开销 ≈150-200ms（其中解释器启动约 130ms），"2ms" 只适用于同进程重复调用
  - 测试：新增 `test_common.js`（12 项）/ `test_cordis.mjs`（11 项，含 apply() 端到端）/
    `test_eco_note_dsh_context_v4.py`（17 项，含 6 并发状态分片用例）；`run_tests.py` 改 glob
    自动纳入 `test_*.js|mjs`；三处核心护栏缺依赖时**显式失败**而非静默跳过
  - **发布守卫**：生成后自动校验（cordis source kind 不得为 `'plugin'`；JS 本地 import 必须
    解析得到（先剥注释）；必需文件齐备（含两端 package.json 与 GUI/Hermes 入口）；发布物 .py
    全部可编译；`from lib.X import` 的模块必须随发）；守卫与敏感词扫描**一律回滚**发布物
    ——v2.2.4 曾因漏登记 lib 模块导致"发布树 import 全炸"，此后有人工清单但无守卫

- **v2.2.4**（2026-09-30）——dsh 官方桌面版适配器（集成层新增，核心脚本零改动）
  - **新增 `integrations/dsh/package-cordis/`**：dsh 官方桌面版（cordis patch 层）适配器——
    `dsh plugin --profile desktop add` 官方路径安装；`agent/pre-step` 每步注入（照官方
    dsh-time-context 模式）+ `ctx.tools.register(defineTool)` 双工具（照官方 dsh-tool-todo 契约）；
    host 端运行期动态解析 `@deepseek-ai/dsh-tools` / `@deepseek-ai/dsh-llm`（link: 安装的包
    裸导入解析不到宿主依赖树）；JS 侧兜底节流可配（`jsThrottleMs`，默认 60s，权威窗口/冷却
    10min/15min 仍在 Python 状态机）；已在官方运行时端到端实测（工具检索命中 + 注入落盘）
  - `package/`（EAC Extension SDK 适配器）不变；Python 侧两包单源共用（package-cordis 零复制）
  - 同步结论（2026-09-30 对齐核查）：兼容版核心与生产树 v2.2.0 **内容一致**（diff 全量
    仅 EOL/bytecode 差异，EOL 归一仍属 v3 单源化批次）
  - 实机环境：dsh 官方桌面版 desktop/headless 两 profile 已部署并配置共享数据根

- **v2.2.3**（2026-09-16）
- 观测舱 GUI v0.3.3 视觉深化与形象化动效：
  · 技能星图双形态：**3D 星空**（本地 Three.js r160，无 CDN，懒加载）玻璃球体 =
    MeshPhysicalMaterial 真实透射折射 + ior 1.46 + clearcoat + 内部衰减体色 + 内发光，
    配程序化星云环境贴图（equirect → PMREM）提供环境反射/折射（即"简单光追"）、
    枢纽节点泛光、(0,0,0) 血缘边流动光点、星尘 1400 点、雾与 ACES 色调映射；
    材质分档（枢纽=清透水晶 / 叶节点=哑光磨砂）、三种布局、拖拽旋转 / 滚轮推拉 /
    射线拾取悬停点选、HTML 悬浮标签；WebGL 不可用自动回退平铺视图，切走即停 rAF
  · **平铺视图**（保留）：深空观测窗（星云/星尘/视差/暗角）+ 哑光玻璃球体 + 类别软分组
    （聚类引力 + 星云云团 + 组名）+ 边方向箭头与流动光点 + 标签贪心避让 + 收敛自动 fit +
    悬停涟漪光流与邻域高亮 + 单击聚焦
  · 血缘谱系从纯文本升级为**真实谱系图**：手写分层布局（代际最长路径 + 列内父位置排序），
    繁殖（父→子）与合并（子→父）两类边带方向箭头与流动光点，悬停高亮整条链路
  · 形象化动效：记忆库条目错峰入场（轮询只动画变化项）/ 水位流动 / 徽章过渡 / 检索关键词
    高亮 / 隔离区低温视觉 / 惰性入场；候选孵化台待孵化形态；时间线泳道顺序入场与回放事件带；
    体检评分环闪光；动作日志新行高亮
  · 动效基座 viz/anim.js：缓动与插值、可中断错峰入场、IntersectionObserver 封装、
    变化签名比对、CSS 动效 token、三档强度与 prefers-reduced-motion 全覆盖；
    帧率监测寄生在已有循环中（不新增长驻 rAF），低帧率自动降级
  · 图标与空态统一：内联 SVG 图标集（16px 网格 / stroke 1.5 / currentColor）替代 unicode 符号；
    四类专属空态插画；清理遗留死 CSS
  · 回归：九视图 × 三色板 × 三档强度零报错零溢出；单帧 JS 成本 3D 全图 2.19ms /
    平铺全图 1.15ms
- Three.js 3D 星图：**已纳入主线**（v2.2.2 的"暂不纳入"由宿主改判）；库随发布（MIT，1.27MB），
  懒加载且 WebGL 不可用时自动回退平铺视图
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.2**（2026-09-15）
- 观测舱 GUI v0.3.2 视觉重构与体验升级：
  · 视觉体系：色板收敛至 3 套精修（雾白/青瓷/深空大屏）、自托管 Inter + Noto Sans SC
    屏显字体（SIL OFL，无 CDN）、亚克力毛玻璃质感、响应式凝练布局（≤1400px 窄图标导航，
    桌面 1280×860 窗口自动命中）、次要文字对比度按 4.5:1 重校
  · 新增功能：键盘快捷键（g+字母跳九视图、? 参考表、/ 聚焦检索）、Ctrl+K 命令面板
    （模糊匹配视图/动作/操作，写动作仍走确认闸门 + dry-run）、检索历史持久化、
    一键导出视图数据（JSON/Markdown/纯文本）、诊断包一键复制、
    浏览器桌面通知（仅后台标签页推送）、新手引导扩至五步
  · 动画系统：视图切换 300ms 过渡（消除闪烁）、星图 D3-force 同级物理引擎
    （斥力/弹簧/碰撞/退火/拖拽惯性，零依赖，质心对齐视图中心）、Canvas 粒子光晕
    （仅深色大屏与巡航激活）、数字计数递增、图表入场过渡、动画强度三档（轻/中/满载）
  · 长期观测与桌面集成：自动轮询（15/30/60 秒/关，无变化不重渲染）、
    全屏巡航双模式（轮播九视图 / 固定驾驶舱大屏）、setup_ecology.py 一键安装、
    tray.py 托盘常驻 + 开机自启（当前用户级，默认关，可逆，缺依赖时优雅降级）
- Three.js 3D 星图评估：**已评估、暂不纳入主线**（避免 +1.27MB 库体与 WebGL 依赖，
  2D SVG 力导向已覆盖核心任务；原型页保留在开发树，不随发布）
- 发布流水线：GUI 发布集新增 setup_ecology.py / tray.py
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.1**（2026-09-15）
- 集成层新增：观测舱 GUI v0.3（`integrations/gui/`，可操作驾驶舱——只读观测 +
  走确认闸门的写操作，全部转交核心 CLI；含急救箱与启动器三入口：cmd / .pyw /
  install_desktop.py 桌面生成器）
- 启动器加固：仓库内 cmd 全部相对路径 + 解释器探测（pythonw → pyw → 报错可读）；
  急救箱修复路径拼接 bug（双位置均不可用）并补失败检测；敏感词扫描扩展名新增
  .cmd/.html/.css/.pyw
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.0**（2026-09-06）
- 阶段 C 评测验收（迭代策略 §3）：伪 gold 构建器（eco_eval_gold.py，gold=实际被使用过的记忆）
  + eco_eval 两条新判定线（INJ 注入超限 ≤5 次/30 天、GOLD top5 相关率 ≥60%）+ --gate 纯规则门禁模式
- 安全写路径加固（v2.2 首项）：lib/safeio.py（路径白名单/防穿越 + schema 校验 + 原子写/备份）
  接线 write_gate / eco_note_adopt / 注入 hook 全部条目写入方
- 注入文本压缩（每条目 1 行）+ 注入遥测新增 chars/error 字段
- 独立 code review 14 项 findings 全修；双树三轮回测全绿、改动文件字节一致
- 历史：v2.1.2（2026-09-06）两树同号同源起点 / v2.1.1-compat.1（2026-09-05）首个兼容版
