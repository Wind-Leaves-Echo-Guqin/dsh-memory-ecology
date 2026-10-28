#!/usr/bin/env python3
"""show_catalog —— 把「记忆目录」原文打出来（排查注入是否真的生效）。

两种数据源，故意都给：
  1. **物化视图**（`.eco/catalog/*.json`）—— 插件热路径实际读到的东西；
  2. **会话日志里的系统提示词** —— 模型实际看到的东西。
两者不一致就是 bug（一个是"写了什么"，一个是"装配时读到了什么"）。

用法：
    python tools/show_catalog.py                      # 物化视图 + 最近会话
    python tools/show_catalog.py --session <uuid 片段>
    python tools/show_catalog.py --exclude <uuid>      # 排除正在跑的会话
    python tools/show_catalog.py --materialized-only   # 只看物化视图
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from memory_ecology import measure, paths, retrieve  # noqa: E402
from memory_ecology.root import Root  # noqa: E402

NEEDLE = "记忆目录"


def show_materialized(root: Root, scope: str | None) -> int:
    data = retrieve.read_materialized(root, scope or "global")
    if data is None:
        print("物化视图：缺失或 schema 过期（跑 `materialize`）")
        return 1
    text = retrieve.render_catalog_text(data)
    print("── 物化视图 %s.json ──" % (scope or "global"))
    print("   count=%s total=%s used_chars=%s budget=%s schema=%s"
          % (data.get("count"), data.get("total"), data.get("used_chars"),
             data.get("budget_chars"), data.get("schema")))
    print("   渲染后 %d 字符：" % len(text))
    print(text or "（空）")
    return 0


def pick_session(session: str | None, exclude: str | None) -> Path | None:
    for p in measure.iter_session_files(limit=200):
        if session:
            if session in str(p):
                return p
            continue
        if exclude and exclude in str(p):
            continue
        return p
    return None


def show_session(p: Path) -> int:
    print("\n── 会话 %s ──" % p.parent.name)
    for line in measure.read_session_lines(p):
        if NEEDLE not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") != "system/message":
            continue
        msg = (ev.get("data") or {}).get("message") or {}
        body = "".join(b.get("text", "") for b in (msg.get("content") or [])
                       if isinstance(b, dict))
        idx = body.find(NEEDLE)
        if idx < 0:
            continue
        tail = body[idx:].split("\n")
        last, n = 0, 0
        for i, ln in enumerate(tail):
            if ln.startswith("- mem-"):
                n += 1
                last = i
        block = "\n".join(tail[:last + 1])
        print("   items=%d chars=%d" % (n, len(block)))
        print(block)
        return 0
    print("   该会话的系统提示词里没有记忆目录（可能是副圈遮蔽段为空，或插件未启用）")
    return 0


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="打印记忆目录原文（排查注入）")
    ap.add_argument("--root", default=None)
    ap.add_argument("--scope", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--exclude", default=os.environ.get("EXCLUDE_SESSION"))
    ap.add_argument("--materialized-only", action="store_true", dest="mat_only")
    a = ap.parse_args(argv)

    root = Root(paths.resolve_root(a.root)).open(create=False)
    rc = show_materialized(root, a.scope)
    if a.mat_only:
        return rc
    p = pick_session(a.session, a.exclude)
    if p is None:
        print("\n未找到会话日志")
        return rc
    show_session(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
