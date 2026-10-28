"""config —— 门槛常量与配置读取（**每一个键都必须有消费者**）。

旧项目最贵的教训（A2A §8-3）：`eco.toml [quota] memory_limit` 被 `REQUIRED_CONFIG`
断言在场，但**没有任何代码读它**——改配置没反应，直到有人花半天查明。所以本模块的
规则是：**加一个键，就必须在同一提交里加它的读取点；`tests/test_config_live.py`
用 AST 扫描把这条变成断言**（无消费者的键 = 测试红）。

配置来源与优先级：
  1. `<root>/configs/eco.toml`（实例级，用户/运维可改）
  2. 代码默认值（本文件常量）

**不读全局环境变量来定根**（那是 `paths.resolve_root()` 的事）；本模块只管阈值。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import store

# ── 默认常量（全部有消费者，见每行注释）─────────────────────────────

# 门①
MERGE_THRESHOLD = 0.86          # 同源合并阈值 → gates.integrate()
BODY_MAX = 800                  # detail 正文上限 → gates.integrate()
# 门②
MEMORY_QUOTA = store.MEMORY_QUOTA          # → gates.quota()
USER_QUOTA = store.USER_QUOTA              # → gates.distill()
QUOTA_TRIGGER_RATIO = store.QUOTA_RATIO    # → gates.quota()
PROMOTE_MIN_OCCURRENCES = 3                # → gates.quota() 快路径
PROMOTE_MIN_SESSIONS = 2                   # → gates.quota() 快路径
PROMOTE_TYPES = ("semantic", "procedural")  # → gates.quota()
PROMOTE_STABLE_DAYS = 14                   # → gates.quota() 稳路径（见下）
PROMOTE_MIN_HITS = 2                       # → gates.quota() 稳路径
EXTRUDE_COOLDOWN_DAYS = 30                 # → gates.quota() 防震荡
PROMOTE_MAX_PER_RUN = 8                    # → gates.quota()
# 门③
DISTILL_MIN_OCCURRENCES = 2     # → gates.distill()
DISTILL_MIN_SESSIONS = 2        # → gates.distill()
DISTILL_STABLE_DAYS = 14        # → gates.distill()（occ 计数不可靠时的替代口径）
DISTILL_OBSERVE_DAYS = 30       # → gates.distill() 观察期
DISTILL_CORROBORATE = 2         # → gates.distill() 交叉印证数（早于观察期的唯一旁路）
DISTILL_USER_WATERMARK = 0.90   # → gates.distill() USER 占用水位
DISTILL_MAX_TRAIT = 80          # → gates.distill()
DISTILL_MAX_PROMOTE = 3         # → gates.distill()
DISTILL_REPLACE_RATIO = 0.80    # → gates.distill() 同义替换
# 门④
REVIEW_DAYS_SEMANTIC = 90       # → gates.review()
REVIEW_DAYS_EPISODIC = 30       # → gates.review()
REVIEW_USE_WINDOW = True        # → gates.review()（窗口内被命中则免降级）
# 检索/目录
CATALOG_LIMIT = 20              # → retrieve.materialize()
CATALOG_BUDGET_CHARS = 600      # → retrieve.materialize()
SEARCH_LIMIT = 8                # → retrieve.search()
# 注入预算（KPI，超标即设计失败，GUI 标红）
INJECT_MAX_MESSAGES = 3         # → plugin + gui.cost_panel()
INJECT_MAX_CHARS = 1200         # → plugin + gui.cost_panel()
# 账本
HIT_KEEP_DAYS = 180             # → ledger.prune_hit()
BACKUP_KEEP = 8                 # → paths.backup_before_write() / cli.cmd_prune()
GATE_STALE_DAYS_DEFAULT = 3.0   # → config.effective_gate_stale_days()（非选项键，只作默认值）
# 包（3.3.0 起用）
PACK_MAX_ENTRIES = 200          # → packs.install()
PACK_MAX_CHARS = 20000          # → packs.install()

_DEFAULTS = {k: v for k, v in list(globals().items())
             if k.isupper() and not k.startswith("_")}

# 配置键别名：把"用户会写的名字"映射到"代码里的常量名"。
# 加一个可配项时**必须在这里登记**，否则配了读不到（死配置）。
_ALIASES = {
    "GATE_STALE_DAYS": "GATE_STALE_DAYS_DEFAULT",
    "BODY_MAX": "BODY_MAX",
    "CATALOG_MAX_CHARS": "CATALOG_BUDGET_CHARS",
}


def _toml_path(root: Path) -> Path:
    return Path(root) / "configs" / "eco.toml"


def load(root: Path | str | None) -> dict:
    """读 `<root>/configs/eco.toml`（缺失/损坏 fail-open 回默认）。返回扁平 dict。"""
    out = dict(_DEFAULTS)
    if root is None:
        return out
    p = _toml_path(Path(root))
    if not p.is_file():
        return out
    try:
        import tomllib
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return out
    flat: dict = {}
    for section, block in (data or {}).items():
        if isinstance(block, dict):
            for k, v in block.items():
                flat["%s.%s" % (section, k)] = v
        else:
            flat[section] = block
    # 段内键名与常量同名即生效（例：[quota] memory_quota = 2000）
    # **`flat` 的键是 `段.键`**，所以这里必须按最后一段比，而且别名表也要按
    # 归一后的短名匹配 —— 直接 `alias in flat` 永远为假（实测：配了不生效）。
    by_name: dict[str, object] = {}
    for key, value in flat.items():
        by_name[key.split(".")[-1].upper()] = value
    for name, value in by_name.items():
        if name in out:
            out[name] = value
    # **别名**：可配键名与"默认值常量"不是一回事。`[gate] gate_stale_days = 30`
    # 归一后是 `GATE_STALE_DAYS`，而默认值常量叫 `GATE_STALE_DAYS_DEFAULT`——
    # 不建这个映射，配了也读不到（正是"死配置"那个最贵的教训）。
    for alias, target in _ALIASES.items():
        if alias in by_name and target in out:
            out[target] = by_name[alias]
    return out


def get(root: Path | str | None, name: str, default=None):
    """取一个配置值。**唯一入口**——所有读取点都走这里，别名也在这里解析。

    `name` 可以写"用户会写的名字"（如 `GATE_STALE_DAYS`），也可以写
    "代码里的默认值常量名"（如 `GATE_STALE_DAYS_DEFAULT`）；两者都认。
    这样"加一个可配项"只需要在 `_ALIASES` 登记一次，不需要在每个读取点记住
    该查哪个名字（那正是死配置的产生方式）。
    """
    data = load(root)
    target = _ALIASES.get(name, name)
    if target in data:
        return data[target]
    if name in data:
        return data[name]
    return _DEFAULTS.get(name, _DEFAULTS.get(target, default))


def effective_gate_stale_days(root: Path | str | None = None) -> float:
    """四门"空转"判定阈值（天）。**唯一读取点** = ledger.gate_status()。

    默认 3 天：门① 每天至少跑一次、门② 每小时都有心跳，3 天不动就是死了。

    配置写法（用户会写小写）：
        [gate]
        gate_stale_days = 30

    曾经的 bug：代码读 `GATE_STALE_DAYS`，而 `_DEFAULTS` 里只有
    `GATE_STALE_DAYS_DEFAULT` —— 于是配什么都是 3.0。这正是本项目最贵的那个
    教训（`[quota] memory_limit` 死配置）被复刻了一遍。现在由 `_ALIASES`
    把可配名映射到默认值常量，并有断言钉住"配了真的生效"。
    """
    value = get(root, "GATE_STALE_DAYS", GATE_STALE_DAYS_DEFAULT)
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(GATE_STALE_DAYS_DEFAULT)
    return out if out > 0 else float(GATE_STALE_DAYS_DEFAULT)
