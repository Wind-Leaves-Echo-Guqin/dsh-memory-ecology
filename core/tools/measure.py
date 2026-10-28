#!/usr/bin/env python3
"""measure —— 成本度量 CLI（**薄壳**，逻辑全在 `memory_ecology.measure`）。

为什么只是薄壳：GUI 的 G6 成本面板与这个命令读**同一批账本、同一份实现**。
如果这里再写一份统计口径，两边数字必然漂移——这正是旧项目"双树漂移"的老病。

用法：
    python tools/measure.py                       # 最近 5 个会话 + 本库目录成本
    python tools/measure.py --json                # 机器可读
    python tools/measure.py --limit 10
    python tools/measure.py --needle 记忆目录      # 额外统计含该关键词的注入块
    python tools/measure.py --no-record           # 只看，不写 cost 账

注意：`zstandard` 是可选依赖（缺它只是读不了压缩会话日志，核心功能不受影响）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from memory_ecology import measure, paths  # noqa: E402
from memory_ecology.root import Root  # noqa: E402


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - pythonw 下 stdout 为 None
            pass
    ap = argparse.ArgumentParser(description="记忆生态 · 成本度量")
    ap.add_argument("--root", default=None)
    ap.add_argument("--limit", type=int, default=5, help="最近 N 个会话（默认 5）")
    ap.add_argument("--needle", default="记忆目录", help="识别本插件注入块的关键词")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--no-record", action="store_true", dest="no_record",
                    help="不写 cost 账（只看）")
    a = ap.parse_args()

    try:
        import zstandard  # noqa: F401
    except ImportError:
        print("⚠ 未安装 zstandard：读不了压缩的会话日志（pip install zstandard）。",
              file=sys.stderr)
        print("  这是可选依赖 —— 核心四门与目录功能不受影响。", file=sys.stderr)

    root = Root(paths.resolve_root(a.root)).open(create=False)
    out = measure.measure_root(root, record=not a.no_record, limit=a.limit,
                               needle=a.needle)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print("记忆生态 · 成本度量")
    print("=" * 68)
    print("  数据根        %s" % out["root"])
    print("  工具 schema   %s 字符（常驻，每次请求都付）" % format(out["tools_schema"], ","))
    print("  系统提示词    %s 字符（前缀，走缓存）" % format(out["system_prompt"], ","))
    print("  目录段        %s 字符 / %d 条（预算 600）"
          % (format(out["catalog_chars"], ","), out["catalog_items"]))
    kpi = out["kpi"]
    flag = "❌ 超标" if out["over_kpi"] else "✅ 达标"
    print("  每会话追加    **本插件** 最多 %d 条 / %s 字符 —— KPI ≤%d 条 / ≤%s 字符  %s"
          % (out["worst_messages"], format(out["worst_chars"], ","),
             kpi["max_messages"], format(kpi["max_chars"], ","), flag))
    print("                （全量注入 最多 %d 条 / %s 字符 —— 含 time-context / skill-catalog 等"
          "非本项目来源，不参与 KPI）"
          % (out["total_messages"], format(out["total_chars"], ",")))
    print()
    for s in out["sessions"]:
        print("  ── %s（%d 行）" % (s["session"], s["lines"]))
        print("     tools=%s sys=%s 追加=%d 条 / %s 字符（本插件 %s）"
              % (format(s["tools_schema"], ","), format(s["system_prompt"], ","),
                 s["injected_msgs"], format(s["injected_chars"], ","),
                 format(s["eco_chars"], ",")))
        for src, n in sorted((s.get("by_source") or {}).items(), key=lambda kv: -kv[1]):
            print("       %-22s %d 条" % (src, n))
    print()
    print("  口径：字符数 = JSON 序列化后的长度（与 provider 实际发送同形）；")
    print("        近似 token ≈ chars/3（英文）/ chars/1.5（中文）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
