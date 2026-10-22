#!/usr/bin/env python3
"""eco_scale_check.py — 库规模与检索延迟监控（决定何时该上 FTS5）。

为什么要这个工具
----------------
`memory_query.py` 是全表扫描（每次 glob + 全量 read_text + 全量解析），
没有用仓库里现成的 `lib/sqlindex.py`（FTS5 索引）。

2026-10-22 实测边际成本：**0.122 ms/条**（热态）。

| 库规模 | 预估热态延迟 |
|---|---|
| 162（当前） | 157 ms |
| 1,000 | 259 ms |
| 3,000 | 503 ms |
| 5,000 | 747 ms |
| 7,000 | ~1.0 s ← 此处开始影响体感 |
| 10,000 | 1.36 s |
| 20,000 | 2.58 s |

**现在的决策是"不上 FTS5"**——162 条离痛点（1s）还有 40 倍空间，
提前优化是过度工程。但需要在接近时被提醒。

本工具就是那个提醒器：跑一次，告诉你当前规模、实测延迟、
以及距离"该动手了"还有多远。

用法
----
    python eco_scale_check.py                  # 默认阈值 1000ms
    python eco_scale_check.py --warn-ms 800    # 自定义警戒线
    python eco_scale_check.py --strict         # 超过警戒线返回 1（可进 CI/定时任务）
"""
from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

# 与 memory_query 一致的解析
HERMES = Path(os.environ.get("MEMORY_ECOLOGY_ROOT")
              or Path.home() / ".hermes")
SCRIPTS = HERMES / "scripts"
if not SCRIPTS.is_dir():
    SCRIPTS = Path(__file__).resolve().parent

# 实测边际（ms/条），来自 2026-10-22 盲测：5000 条热态 743ms，162 条 157ms
MARGINAL_MS_PER_ENTRY = 0.122
# "该动手上 FTS5"的规模拐点（热态约 1 秒）
FTS_TRIGGER_ENTRIES = 7000


def count_entries() -> int:
    d = HERMES / "memories" / "detail"
    if not d.is_dir():
        return 0
    return sum(1 for _ in d.glob("*.md"))


def measure(query: str = "备份", runs: int = 3) -> dict:
    """跑 memory_query，返回冷/热耗时（ms）。"""
    py = sys.executable
    script = SCRIPTS / "memory_query.py"
    if not script.is_file():
        return {"ok": False, "error": f"找不到 {script}"}
    env = dict(os.environ)
    env["MEMORY_ECOLOGY_ROOT"] = str(HERMES)
    env["PYTHONIOENCODING"] = "utf-8"
    times = []
    for _ in range(runs):
        t0 = time.time()
        try:
            subprocess.run([py, str(script), query, "--top", "5", "--format", "json",
                            "--no-log", "--no-update"],
                           cwd=str(SCRIPTS), env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        times.append((time.time() - t0) * 1000)
    return {"ok": True, "cold_ms": times[0], "warm_ms": min(times[1:]) if len(times) > 1 else times[0],
            "all": times}


def main() -> int:
    from lib.cli_base import bootstrap as _bootstrap   # v2.5.0：编码兜底
    _bootstrap()
    ap = argparse.ArgumentParser()
    ap.add_argument("--warn-ms", type=float, default=1000.0, help="热态警戒线（默认 1000ms）")
    ap.add_argument("--strict", action="store_true", help="超阈值返回 1")
    ap.add_argument("--query", default="备份")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    print("=" * 72)
    print("库规模与检索延迟监控")
    print("=" * 72)
    print(f"  数据根: {HERMES}")

    n = count_entries()
    print(f"  条目数: {n:,}")
    if n == 0:
        print("  ⚠️ 库里没有条目，无法评估")
        return 0

    r = measure(args.query, max(2, args.runs))
    if not r.get("ok"):
        print(f"  ❌ 测量失败: {r.get('error')}")
        return 1 if args.strict else 0

    warm = r["warm_ms"]
    print(f"  冷态  : {r['cold_ms']:>8.0f} ms")
    print(f"  热态  : {warm:>8.0f} ms（取 {args.runs} 次最小值）")
    print(f"  边际  : {MARGINAL_MS_PER_ENTRY} ms/条（历史实测）")
    print()

    # 距离拐点
    est_trigger_ms = warm + (FTS_TRIGGER_ENTRIES - n) * MARGINAL_MS_PER_ENTRY
    room = FTS_TRIGGER_ENTRIES - n
    print(f"  FTS5 拐点: {FTS_TRIGGER_ENTRIES:,} 条（热态约 1 秒）")
    if room > 0:
        print(f"  距拐点还有: {room:,} 条（约 {room / max(1, n):.1f}× 当前规模）")
    else:
        print(f"  ⚠️ 已超过拐点 {abs(room):,} 条")

    # 外推表
    print()
    print("  外推（按当前边际）：")
    for size in (n, 1000, 3000, 5000, 7000, 10000):
        if size < n:
            continue
        est = warm + (size - n) * MARGINAL_MS_PER_ENTRY
        flag = "✅" if est < args.warn_ms else "⚠️"
        print(f"    {size:>6,} 条 → {est:>7.0f} ms  {flag}")

    print()
    print("=" * 72)
    over = warm >= args.warn_ms
    if over:
        print(f"  结果: ⚠️ 热态 {warm:.0f}ms 已超警戒线 {args.warn_ms:.0f}ms")
        print("  → 该考虑接 lib/sqlindex.py 的 FTS5 索引了")
    else:
        print(f"  结果: ✅ 热态 {warm:.0f}ms < 警戒线 {args.warn_ms:.0f}ms，暂不需要 FTS5")
        print(f"  → 下次评估建议在条目数达到 {FTS_TRIGGER_ENTRIES:,} 条，或热态超过 "
              f"{args.warn_ms:.0f}ms 时")

    return 1 if (over and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
