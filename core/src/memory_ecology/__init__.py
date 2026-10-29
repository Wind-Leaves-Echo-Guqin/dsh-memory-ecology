"""memory_ecology — 记忆生态可移植核心（stdlib only，零宿主依赖）。

**v3.2.0 起本包是唯一真源。** 旧的 `scripts/lib/`（文件级复制品）已改为对本包的
薄转发外壳（`from memory_ecology.x import *`），因此"改一处必须同步另一处"这条债
在机制上消失（A2A §7-7）。`scripts/` 下的门脚本（write_gate / eco_quota /
distill_stage / eco_review）已改为对本包 `gates` 的薄包装。

分层（见 docs/ARCHITECTURE.md）：
  L3 dsh 内核原语        ← 只用，不碰
  L2 dsh 插件（plugin/）  ← 只决定「什么内容/形态/时点/预算」
  L1 本包                ← 记忆领域语义（门、状态机、检索、圈、包），零宿主依赖
  L0 数据根              ← 一个目录：候选 + detail + 账本 + 物化视图

四条不变量（测试钉住，不靠纪律）：
  1. **物理删除在代码层不存在**（只有 status/fate 迁移 + superseded 标记 + 隔离区移动）。
  2. **单写入方**：一个根同时只有一个写者；并发只追加靠 pid 唯一临时名 + 单次 O_APPEND
     + SQLite WAL（三者缺一即丢写，实测 8 进程丢 6/40）。
  3. **核心零依赖**：只 import 标准库；LLM 走 urllib 且可选、缺省不联网。
  4. **根自证**：写前读 `.eco/root.json`，不匹配即只读降级并留痕，不抛错、不阻塞。
"""

__version__ = "3.2.2"

# 版本三处对齐（A2A §B5）由 scripts/check_version.py 校验；此处是唯一权威来源。
VERSION = __version__

__all__ = ["__version__", "VERSION"]
